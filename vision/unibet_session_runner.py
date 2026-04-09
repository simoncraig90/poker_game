"""
Unibet end-to-end session orchestrator.

Wraps the existing pieces (start.bat / Chrome launch, auto-login.js,
auto_player.py) with a state machine that handles:

  - Launch Chrome with debug port + stealth flags
  - Auto-login (with reCAPTCHA hand-off if needed)
  - Navigate to cash game lobby
  - Select a table (TODO: full automation; for now uses OCR-click on
    a table tile or accepts a manually-clicked first table)
  - Buy in (TODO: OCR-click defaults on the buy-in dialog)
  - Wait until seated at the table (poll WS for hero_seat)
  - Optionally wait until BB before unleashing the auto-clicker
  - Run the auto-player, monitoring the WS stream for events:
      * stack_low → trigger re-buy
      * no_state_for_X_seconds → table closed → return to lobby
      * session.should_end_session() → graceful exit
  - On table-closed: return to lobby and pick another table
  - On session-end: leave the table cleanly and close Chrome

Design notes
------------

This is a CONTROLLER, not a re-implementation. It launches the existing
auto_player.py as a subprocess and monitors the live WS state via its
own UnibetWSReader instance. When events fire (stack low, table closed,
break time), the controller:
  - Sets the .autoplay_pause flag (auto_player.py respects this and
    will not click while paused)
  - Performs the click sequence for the event (rebuy / leave / etc)
  - Clears the pause flag once the click sequence is done

This separation keeps auto_player.py focused on per-hand decisions and
the orchestrator focused on between-hand state transitions.

Usage
-----

  python vision/unibet_session_runner.py [--no-launch-chrome] [--max-hands N]
                                          [--max-buyins N] [--wait-bb]

CLI flags:
  --no-launch-chrome  Don't launch Chrome; assume it's already up on 9222
  --max-hands N       Stop after N hands played (default: SessionManager limit)
  --max-buyins N      Maximum re-buys per session (default: 3)
  --wait-bb           Use Unibet's "Wait for BB" option (default: post BB
                      immediately so we don't lose orbits)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

PAUSE_FLAG = os.path.join(ROOT, ".autoplay_pause")
SESSION_LOG = os.path.join(ROOT, "unibet_session.log")
START_BAT = os.path.join(ROOT, "start.bat")
AUTO_LOGIN_JS = os.path.join(ROOT, "scripts", "auto-login.js")
AUTO_PLAYER_PY = os.path.join(VISION_DIR, "auto_player.py")
OCR_CLICK_PY = os.path.join(ROOT, "scripts", "ocr-click.py")

# Stack threshold for re-buy: trigger when stack drops below 60% of initial
# buy-in. At NL2 with 200-cent buy-in that's 120 cents. Avoids re-buying
# after every minor loss but catches the "down to fumes" state before
# we're forced into bad shoves.
REBUY_THRESHOLD_PCT = 0.60

# Table-closed detection: if the WS state hasn't changed in this many
# seconds, assume the table closed and we need to return to lobby.
TABLE_CLOSED_SECS = 60

# Hero-seated detection: poll up to this many seconds for hero_seat to
# be assigned (means we're at the table) before giving up.
SEAT_WAIT_SECS = 90


# ── helpers ───────────────────────────────────────────────────────────


def log(msg: str):
    """Print + append to session log."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(SESSION_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def set_pause():
    try:
        with open(PAUSE_FLAG, "w", encoding="utf-8") as f:
            f.write("session-runner\n")
    except Exception:
        pass


def clear_pause():
    try:
        if os.path.exists(PAUSE_FLAG):
            os.unlink(PAUSE_FLAG)
    except Exception:
        pass


def chrome_alive() -> bool:
    """True if Chrome is responding on port 9222."""
    try:
        import urllib.request as ur
        with ur.urlopen("http://127.0.0.1:9222/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def chrome_tab_url() -> Optional[str]:
    """Return the URL of the first Unibet page tab, or None."""
    try:
        import urllib.request as ur
        import json as _json
        with ur.urlopen("http://127.0.0.1:9222/json", timeout=2) as r:
            tabs = _json.loads(r.read())
        for t in tabs:
            if t.get("type") == "page" and "unibet" in t.get("url", "").lower():
                return t["url"]
    except Exception:
        pass
    return None


# ── phases ────────────────────────────────────────────────────────────


def phase_launch_chrome(skip: bool = False) -> bool:
    """Launch Chrome via start.bat (or skip if --no-launch-chrome)."""
    if skip:
        log("phase_launch_chrome: skipped (--no-launch-chrome)")
        return chrome_alive()
    if chrome_alive():
        log("phase_launch_chrome: Chrome already alive on 9222")
        return True
    log("phase_launch_chrome: launching via start.bat")
    # start.bat does a lot more than launch Chrome — it kills existing,
    # writes the preferences fix, then launches with the right flags,
    # then runs auto-login + ocr-click + advisor. We only want the
    # Chrome-launch portion. Easiest: spawn it but don't wait, then
    # check chrome_alive() in a loop.
    subprocess.Popen(
        ["cmd", "/c", START_BAT],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(1)
        if chrome_alive():
            log("phase_launch_chrome: Chrome ready")
            return True
    log("phase_launch_chrome: TIMEOUT — Chrome did not come up")
    return False


def phase_auto_login() -> bool:
    """Run scripts/auto-login.js. Will pause for reCAPTCHA solve if needed."""
    log("phase_auto_login: running auto-login.js")
    try:
        result = subprocess.run(
            ["node", AUTO_LOGIN_JS],
            cwd=ROOT,
            timeout=180,
            capture_output=True,
            text=True,
        )
        log(f"phase_auto_login: exit={result.returncode}")
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                log(f"  | {line}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log("phase_auto_login: TIMEOUT (180s) — reCAPTCHA may need manual solve")
        return False
    except Exception as e:
        log(f"phase_auto_login: error {type(e).__name__}: {e}")
        return False


def phase_navigate_to_cash_lobby() -> bool:
    """Click the CASH GAME tab via OCR."""
    log("phase_navigate_to_cash_lobby: ocr-click 'CASH GAME'")
    try:
        result = subprocess.run(
            [sys.executable, OCR_CLICK_PY, "CASH GAME"],
            cwd=ROOT, timeout=30, capture_output=True, text=True,
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                log(f"  | {line}")
        return result.returncode == 0
    except Exception as e:
        log(f"phase_navigate_to_cash_lobby: error {e}")
        return False


def phase_select_table() -> bool:
    """
    Select a table from the cash game lobby.

    TODO: full automation. For now this is the manual hand-off point —
    the orchestrator pauses and asks the user to manually pick a table
    + complete the buy-in dialog. Once seated, the orchestrator detects
    via hero_seat being assigned in the WS state.

    Future implementation:
      1. OCR-click a table row (filter by stake from CLI arg)
      2. Buy-in dialog: OCR-click default buy-in amount + "Buy In"
      3. Wait for the table window to load
    """
    log("phase_select_table: MANUAL HAND-OFF — please select a table")
    log("                   and complete the buy-in dialog manually.")
    log("                   The runner will detect hero_seat assignment")
    log("                   in the WS stream and continue automatically.")
    return True


def phase_wait_for_seat(reader, timeout: int = SEAT_WAIT_SECS) -> bool:
    """Poll the WS reader for hero_seat to be assigned."""
    log(f"phase_wait_for_seat: polling up to {timeout}s for hero_seat")
    start = time.time()
    while time.time() - start < timeout:
        state = reader.get_state()
        hs = state.get("hero_seat", -1)
        if hs >= 0 and state.get("players"):
            log(f"phase_wait_for_seat: hero seated at seat {hs}")
            return True
        time.sleep(2)
    log("phase_wait_for_seat: TIMEOUT — hero never seated")
    return False


def phase_wait_for_bb(reader, timeout: int = 90) -> bool:
    """
    Wait for hero to be the BB before starting the auto-player.
    Avoids posting an early dead BB if --wait-bb was set in the lobby.

    NOTE: this is OPTIONAL. Without it, hero may post a dead BB on
    the first orbit. With it, we wait one orbit (~60s at 6-max).
    """
    log(f"phase_wait_for_bb: waiting up to {timeout}s for hero=BB")
    start = time.time()
    while time.time() - start < timeout:
        state = reader.get_state()
        if state.get("position") == "BB" and state.get("hero_cards"):
            log("phase_wait_for_bb: hero is BB with cards — go")
            return True
        time.sleep(2)
    log("phase_wait_for_bb: timed out, starting anyway")
    return False


def phase_run_auto_player() -> subprocess.Popen:
    """Spawn auto_player.py as a subprocess. Return the Popen handle."""
    log("phase_run_auto_player: spawning auto_player.py")
    proc = subprocess.Popen(
        [sys.executable, "-u", AUTO_PLAYER_PY],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return proc


# ── monitoring loop ───────────────────────────────────────────────────


@dataclass
class SessionState:
    starting_stack: int = 0
    initial_buyin_chips: int = 0
    rebuys_used: int = 0
    last_state_change: float = field(default_factory=time.time)
    last_hand_id: Optional[str] = None
    hands_played: int = 0


def monitor_loop(reader, auto_proc: subprocess.Popen,
                 max_hands: int, max_buyins: int) -> str:
    """
    Main monitoring loop. Watches the WS state for events while the
    auto-player runs. Returns a reason string when the loop exits:
      "max_hands"     — reached requested hand count
      "table_closed"  — no WS state changes for TABLE_CLOSED_SECS
      "rebuys_exhausted" — out of rebuys after busting
      "auto_proc_died" — auto_player.py exited
      "user_quit"     — Ctrl+C
    """
    s = SessionState()

    initial = reader.get_state()
    s.starting_stack = initial.get("hero_stack", 0)
    s.initial_buyin_chips = s.starting_stack
    s.last_hand_id = initial.get("hand_id")
    log(f"monitor: starting stack={s.starting_stack} (=initial buy-in)")

    last_state_sig = None

    try:
        while True:
            # auto_player.py died? game over
            if auto_proc.poll() is not None:
                log(f"monitor: auto_player.py exited with code {auto_proc.returncode}")
                return "auto_proc_died"

            state = reader.get_state()

            # Detect state change for table-closed timeout
            sig = (
                state.get("hand_id"),
                state.get("phase"),
                tuple(state.get("hero_cards") or []),
                tuple(state.get("board_cards") or []),
                state.get("pot"),
                state.get("hero_stack"),
            )
            if sig != last_state_sig:
                s.last_state_change = time.time()
                last_state_sig = sig

            # Hand-id transition counts a hand
            cur_hand = state.get("hand_id")
            if cur_hand and cur_hand != s.last_hand_id:
                s.hands_played += 1
                s.last_hand_id = cur_hand
                if s.hands_played % 10 == 0:
                    log(f"monitor: {s.hands_played} hands played, "
                        f"stack={state.get('hero_stack', 0)}")

            # Max hands check
            if max_hands > 0 and s.hands_played >= max_hands:
                log(f"monitor: reached max-hands={max_hands}")
                return "max_hands"

            # Stack-low → re-buy check (only between hands)
            cur_stack = state.get("hero_stack", 0)
            if (cur_stack > 0
                    and cur_stack < s.initial_buyin_chips * REBUY_THRESHOLD_PCT
                    and not state.get("hero_cards")):
                if s.rebuys_used >= max_buyins:
                    log(f"monitor: rebuys exhausted ({max_buyins}) — exit")
                    return "rebuys_exhausted"
                log(f"monitor: stack {cur_stack} < {REBUY_THRESHOLD_PCT*100:.0f}% "
                    f"of buy-in {s.initial_buyin_chips} — triggering rebuy")
                if trigger_rebuy():
                    s.rebuys_used += 1
                    s.starting_stack = state.get("hero_stack", 0)
                    log(f"monitor: rebuy {s.rebuys_used}/{max_buyins} done")
                else:
                    log("monitor: rebuy click failed — pausing 30s before retry")
                    time.sleep(30)

            # Table-closed check
            idle = time.time() - s.last_state_change
            if idle > TABLE_CLOSED_SECS:
                log(f"monitor: no WS state changes for {idle:.0f}s — assuming "
                    f"table closed")
                return "table_closed"

            time.sleep(1.5)

    except KeyboardInterrupt:
        log("monitor: KeyboardInterrupt — user quit")
        return "user_quit"


# ── click sequences for orchestrator-side events ──────────────────────


def trigger_rebuy() -> bool:
    """
    Click the rebuy / top-up button on the Unibet table UI.

    TODO: needs OCR-click target identified. Unibet's rebuy flow:
      1. When stack is low, an "Add chips" / "Top up" button appears
         in the player area
      2. Clicking it opens a dialog with default top-up amount
      3. Clicking "Confirm" / "OK" tops up to full buy-in

    For now this is a stub that pauses, calls ocr-click for likely
    button text, and returns success if any click landed.
    """
    set_pause()
    time.sleep(0.5)
    success = False
    for label in ("Top up", "Add chips", "Re-buy", "Rebuy", "Buy in"):
        try:
            r = subprocess.run(
                [sys.executable, OCR_CLICK_PY, label],
                cwd=ROOT, timeout=10, capture_output=True, text=True,
            )
            if r.returncode == 0 and "Found" in (r.stdout or ""):
                success = True
                log(f"  rebuy: clicked '{label}'")
                break
        except Exception:
            pass
    if success:
        time.sleep(2)
        # Confirm dialog
        for confirm in ("Confirm", "OK", "Buy In"):
            try:
                subprocess.run(
                    [sys.executable, OCR_CLICK_PY, confirm],
                    cwd=ROOT, timeout=10, capture_output=True, text=True,
                )
            except Exception:
                pass
        time.sleep(2)
    clear_pause()
    return success


def trigger_leave_table() -> bool:
    """
    Click the Leave Table button. Used at session end and on
    table-closed events to clean up the previous table.

    TODO: identify the actual leave button. For now uses ocr-click
    on common labels.
    """
    set_pause()
    time.sleep(0.5)
    success = False
    for label in ("Leave table", "Leave Table", "Exit"):
        try:
            r = subprocess.run(
                [sys.executable, OCR_CLICK_PY, label],
                cwd=ROOT, timeout=10, capture_output=True, text=True,
            )
            if r.returncode == 0 and "Found" in (r.stdout or ""):
                success = True
                break
        except Exception:
            pass
    clear_pause()
    return success


def close_chrome():
    """Kill Chrome at session end."""
    log("close_chrome: terminating chrome.exe")
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue"],
            timeout=10,
        )
    except Exception:
        pass


# ── main ──────────────────────────────────────────────────────────────


def main(argv=None):
    p = argparse.ArgumentParser(description="Unibet end-to-end session runner")
    p.add_argument("--no-launch-chrome", action="store_true",
                   help="Don't launch Chrome (assume already running on 9222)")
    p.add_argument("--max-hands", type=int, default=0,
                   help="Stop after N hands (0 = no limit, use SessionManager)")
    p.add_argument("--max-buyins", type=int, default=3,
                   help="Maximum re-buys per session (default 3)")
    p.add_argument("--wait-bb", action="store_true",
                   help="Wait for hero=BB before starting clicks (no dead BB)")
    p.add_argument("--no-orchestrate-tables", action="store_true",
                   help="Skip the table-find loop on table-closed events. "
                        "When set, exit after the first table closes.")
    args = p.parse_args(argv)

    log("=" * 60)
    log("Unibet session runner starting")
    log(f"  max_hands={args.max_hands or 'no-limit'} "
        f"max_buyins={args.max_buyins} wait_bb={args.wait_bb}")
    log("=" * 60)

    # Phase 1: Chrome
    if not phase_launch_chrome(skip=args.no_launch_chrome):
        log("FATAL: Chrome failed to launch")
        return 1

    # Phase 2: Auto-login
    if not phase_auto_login():
        log("WARN: auto-login may have failed (check logs above)")
        # Don't fail hard — sometimes the script reports failure but
        # we're actually logged in via existing cookies.

    # Phase 3: Navigate to cash game lobby
    phase_navigate_to_cash_lobby()
    time.sleep(2)

    # Build the WS reader so we can monitor state
    from unibet_ws import UnibetWSReader
    reader = UnibetWSReader(cdp_port=9222)
    reader.start()

    while True:
        # Phase 4: Table selection (manual hand-off for now)
        phase_select_table()

        # Phase 5: Wait for hero to be seated
        if not phase_wait_for_seat(reader):
            log("FATAL: never got seated at a table")
            reader.stop()
            return 1

        # Phase 6: Wait for BB if requested
        if args.wait_bb:
            phase_wait_for_bb(reader)

        # Phase 7: Run auto-player + monitoring loop
        auto_proc = phase_run_auto_player()
        try:
            reason = monitor_loop(
                reader, auto_proc,
                max_hands=args.max_hands,
                max_buyins=args.max_buyins,
            )
        finally:
            log("monitor: stopping auto_player subprocess")
            try:
                auto_proc.terminate()
                auto_proc.wait(timeout=5)
            except Exception:
                try:
                    auto_proc.kill()
                except Exception:
                    pass

        log(f"monitor: exit reason = {reason}")

        # Decide what to do next based on exit reason
        if reason in ("max_hands", "rebuys_exhausted", "user_quit"):
            log("Phase 8: leaving table + closing Chrome")
            trigger_leave_table()
            time.sleep(2)
            close_chrome()
            reader.stop()
            return 0

        if reason == "auto_proc_died":
            log("auto_player.py died unexpectedly — exiting safely")
            trigger_leave_table()
            close_chrome()
            reader.stop()
            return 1

        if reason == "table_closed":
            if args.no_orchestrate_tables:
                log("--no-orchestrate-tables set — exiting")
                close_chrome()
                reader.stop()
                return 0
            log("Table closed — returning to lobby for next table")
            phase_navigate_to_cash_lobby()
            time.sleep(2)
            # Loop continues — phase_select_table prompts for the next one
            continue

        # Unknown exit reason
        log(f"Unknown reason {reason!r} — exiting safely")
        close_chrome()
        reader.stop()
        return 1


if __name__ == "__main__":
    sys.exit(main())
