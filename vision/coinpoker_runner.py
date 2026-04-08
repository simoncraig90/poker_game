"""
CoinPoker advisor runner.

Reads frames from the JSONL stream produced by the patched PBClient.dll
(``C:\\Users\\Simon\\coinpoker_frames.jsonl``), feeds them through
``CoinPokerStateBuilder``, and dispatches each new snapshot to a callable
(typically ``AdvisorStateMachine.process_state``).

Two modes:
  --replay     Read the file once start-to-finish then exit (used by tests
               and offline what-if analysis).
  --follow     Tail the file, polling for new lines. The default for live
               play.

The runner is split into a pure ``CoinPokerSession`` class (no I/O against
the advisor — takes an injected ``on_snapshot`` callable) and a ``main()``
that wires the real ``AdvisorStateMachine`` up. This lets the session loop
be tested with a mock callback against the captured fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Iterator, Optional

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

from coinpoker_adapter import CHIP_SCALE, CoinPokerStateBuilder

DEFAULT_LOG = r"C:\Users\Simon\coinpoker_frames.jsonl"
HERO_USER_ID_DEFAULT = 1571120  # precious0864449 — hero on the practice table


# ── file iterators ────────────────────────────────────────────────────────────

def replay_iter(path: str) -> Iterator[str]:
    """Yield each non-empty line of the file once. Closes the file at EOF."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line:
                yield line


def follow_iter(path: str, poll: float = 0.2) -> Iterator[str]:
    """
    Tail a growing file. Yields each new line as it appears. Reopens the
    file if it shrinks (process truncation / log rotation). Blocks
    indefinitely; caller should run inside a try/KeyboardInterrupt.
    """
    f = open(path, "r", encoding="utf-8")
    try:
        # Start from the end so we don't replay history during a live tail.
        f.seek(0, 2)
        leftover = ""
        while True:
            chunk = f.read()
            if not chunk:
                # Detect truncation: if file size < current pos, reopen.
                try:
                    if os.path.getsize(path) < f.tell():
                        f.close()
                        f = open(path, "r", encoding="utf-8")
                        leftover = ""
                except OSError:
                    pass
                time.sleep(poll)
                continue
            data = leftover + chunk
            lines = data.split("\n")
            leftover = lines[-1]
            for line in lines[:-1]:
                if line:
                    yield line
    finally:
        try:
            f.close()
        except Exception:
            pass


# ── session loop ──────────────────────────────────────────────────────────────

class CoinPokerSession:
    """
    Orchestrates the per-frame loop. Pure: no advisor instantiation.

    Constructor takes:
      - hero_user_id: int — passed through to the builder
      - on_snapshot: callable(snapshot: dict) -> Any — invoked for each
        builder snapshot AFTER state has actually changed (we coalesce
        no-op frames so the advisor isn't woken up for every server_lag).
        Return value is ignored.
      - bb_chips: int or None — if given, overrides the auto-detected BB
        amount that the runner exposes via ``self.bb_cents``.
    """

    # Keys we treat as the meaningful "state changed" signature. Frames
    # that don't move any of these are passed through to the builder but
    # don't trigger on_snapshot. (AdvisorStateMachine has its own change
    # detection too, but we suppress noise here so its counters and the
    # console output stay readable.)
    _CHANGE_KEYS = (
        "hand_id", "phase", "hero_cards", "board_cards",
        "facing_bet", "call_amount", "pot", "hero_stack",
        "hero_turn", "position",
    )

    def __init__(self, hero_user_id: int,
                 on_snapshot: Callable[[dict], Any],
                 bb_chips: Optional[int] = None):
        self.builder = CoinPokerStateBuilder(hero_user_id)
        self.on_snapshot = on_snapshot
        self._override_bb = bb_chips
        self._last_signature: Optional[tuple] = None
        self.frames_seen = 0
        self.snapshots_dispatched = 0

    @property
    def bb_cents(self) -> int:
        """
        Big blind in scaled chip units, suitable for AdvisorStateMachine's
        ``bb_cents`` constructor arg. Falls back to the practice-table
        default (100 chips × CHIP_SCALE) if we haven't seen a hand yet.
        """
        if self._override_bb is not None:
            return self._override_bb * CHIP_SCALE
        if self.builder.bb_amount > 0:
            return self.builder.bb_amount
        return 100 * CHIP_SCALE  # safe default for the practice table

    def feed_line(self, line: str) -> Optional[dict]:
        """
        Parse + ingest one JSONL line. Returns the snapshot if it changed
        meaningfully, else None. Bad JSON is silently dropped (the patch
        wraps File.AppendAllText in a try/catch and may rarely write a
        partial line on shutdown).
        """
        if not line:
            return None
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            return None
        return self.feed_frame(frame)

    def feed_frame(self, frame: dict) -> Optional[dict]:
        self.frames_seen += 1
        self.builder.ingest(frame)
        snap = self.builder.snapshot()
        if snap is None:
            return None
        sig = tuple(self._sig_value(snap[k]) for k in self._CHANGE_KEYS)
        if sig == self._last_signature:
            return None
        self._last_signature = sig
        self.snapshots_dispatched += 1
        try:
            self.on_snapshot(snap)
        except Exception as e:
            # Swallow advisor errors so a buggy strategy module doesn't
            # take down the whole runner mid-session. Print + continue.
            import traceback
            print(f"[on_snapshot ERROR] {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
        return snap

    @staticmethod
    def _sig_value(v):
        # Lists/dicts aren't hashable; reduce them to a hashable shape.
        if isinstance(v, list):
            return tuple(v)
        if isinstance(v, dict):
            return tuple(sorted(v.items()))
        return v

    def run(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.feed_line(line)


# ── overlay client ────────────────────────────────────────────────────────────

class OverlayClient:
    """
    Talks to vision/overlay_process.py via JSON-on-stdin.

    Two construction modes:
      OverlayClient.spawn()    — launches overlay_process.py as a subprocess
      OverlayClient(stream=f)  — writes to a file-like (used by tests)

    The ``send`` method is a no-op if the underlying stream is dead.
    """

    def __init__(self, stream=None, process: Optional[subprocess.Popen] = None,
                 table_id: str = "coinpoker_t1"):
        self._stream = stream
        self._proc = process
        self.table_id = table_id

    @classmethod
    def spawn(cls, table_id: str = "coinpoker_t1") -> "OverlayClient":
        overlay_script = os.path.join(VISION_DIR, "overlay_process.py")
        if not os.path.exists(overlay_script):
            print(f"[overlay] script not found at {overlay_script}, disabling")
            return cls(stream=None, process=None, table_id=table_id)
        # Kill any orphaned overlay first — pattern copied from advisor_ws.py.
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process python* -ErrorAction SilentlyContinue | "
                 "Where-Object { (Get-WmiObject Win32_Process -Filter "
                 "\"ProcessId=$($_.Id)\").CommandLine -match 'overlay_process' } | "
                 "Stop-Process -Force"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        proc = subprocess.Popen(
            [sys.executable, "-u", overlay_script],
            stdin=subprocess.PIPE, text=True,
        )
        print(f"[overlay] spawned (PID {proc.pid})")
        return cls(stream=proc.stdin, process=proc, table_id=table_id)

    def alive(self) -> bool:
        if self._stream is None:
            return False
        if self._proc is not None and self._proc.poll() is not None:
            return False
        return True

    def send(self, msg: dict) -> None:
        if not self.alive():
            return
        try:
            self._stream.write(json.dumps(msg) + "\n")
            self._stream.flush()
        except Exception as e:
            # Don't print every line — overlay died, give up on it.
            print(f"[overlay] send failed, stopping overlay output: {type(e).__name__}: {e}")
            self._stream = None

    def remove_table(self) -> None:
        self.send({"type": "table_remove", "table_id": self.table_id})

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


def parse_room_stake(room_name: str) -> str:
    """
    Pull a short human-readable stake string out of the CoinPoker room name.
    e.g. ``PR-NL 50-100 EV-INRIT-ANTE (A) 246519`` → ``50/100``.
    """
    if not room_name:
        return ""
    m = re.search(r"(\d+)\s*[-/]\s*(\d+)", room_name)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return room_name[:30]


def snapshot_to_overlay_msg(snap: dict, advisor_out, table_id: str,
                            room_name: str = "") -> dict:
    """
    Build the table_update message the overlay expects from a CoinPoker
    snapshot + AdvisorOutput.

    The overlay renders pot/stack/call with a euro prefix — for CoinPoker
    play-chip tables this displays as "€<chips>.<dd>" which is visually
    quirky but unambiguous. We embed "(chips)" in the site name so the
    user knows the units.
    """
    # Convert scaled-int "cents" back to chip floats so the overlay's
    # ``f"€{pot:.2f}"`` formatting renders the chip count.
    from coinpoker_adapter import CHIP_SCALE
    def to_chips(v):
        return (v or 0) / CHIP_SCALE

    rec = advisor_out.action if advisor_out else ""
    color = "neutral"
    rec_upper = rec.upper()
    if any(k in rec_upper for k in ("RAISE", "BET", "CALL")):
        color = "green"
    elif "FOLD" in rec_upper:
        color = "red"
    elif "CHECK" in rec_upper:
        color = "blue"

    cards_str = " ".join(snap["hero_cards"])
    board_str = " ".join(snap["board_cards"])

    eq = getattr(advisor_out, "equity", None) if advisor_out else None

    pot_chips = to_chips(snap.get("pot", 0))
    stack_chips = to_chips(snap.get("hero_stack", 0))
    call_chips = to_chips(snap.get("call_amount", 0))
    pot_odds = None
    if snap.get("facing_bet") and call_chips > 0 and pot_chips > 0:
        pot_odds = call_chips / (pot_chips + call_chips)

    return {
        "type": "table_update",
        "table_id": table_id,
        "site": "CoinPoker (chips)",
        "stake": parse_room_stake(room_name),
        "position": snap.get("position", ""),
        "cards": cards_str,
        "board": board_str,
        "phase": snap.get("phase", ""),
        "equity": eq,
        "pot": pot_chips,
        "stack": stack_chips,
        "facing_bet": bool(snap.get("facing_bet")),
        "call": call_chips,
        "pot_odds": pot_odds,
        "rec": rec,
        "rec_color": color,
        "opponent": "",
    }


# ── default snapshot printer (used when --print-only) ─────────────────────────

def make_console_printer() -> Callable[[dict], None]:
    """A snapshot consumer that just prints a one-line summary. No advisor."""
    def _print(snap: dict) -> None:
        hero = " ".join(snap["hero_cards"]) or "??"
        board = " ".join(snap["board_cards"]) or "-"
        flag = "*" if snap["hero_turn"] else " "
        print(f"{flag}[{snap['phase']:7}] hand={snap['hand_id']:>10} "
              f"pos={snap['position']:3} hero={hero:5} board={board:14} "
              f"pot={snap['pot']:>9} call={snap['call_amount']:>7} "
              f"facing={snap['facing_bet']!s:5} stack={snap['hero_stack']:>9}")
    return _print


# ── opponent tracker adapter ──────────────────────────────────────────────────

class _CoinPokerTrackerAdapter:
    """
    Wraps OpponentTracker so it can consume CoinPoker-format snapshots.

    The original OpponentTracker was built for the Unibet WS reader, where
    ``state["players"]`` is a list of player names indexed by seat-position
    in the list, and ``state["bets"]`` is a parallel list. CoinPoker
    snapshots, in contrast, have ``players`` as a list of dicts (each
    with ``seat``, ``user_id``, ``name``, ``bet``, ``last_action``).

    This adapter translates the CoinPoker shape into the Unibet shape on
    every call. The wrapped tracker is shared with Unibet code, so we
    must NOT mutate it — all conversion happens at the boundary.
    """

    def __init__(self, inner_tracker, hero_user_id: int):
        self._inner = inner_tracker
        self._hero_user_id = int(hero_user_id)

    def _convert(self, state: dict) -> dict:
        """Translate a CoinPoker snapshot to Unibet tracker format.
        Pass-through if already converted (or if state is empty)."""
        if not state:
            return state
        cp_players = state.get('players')
        if not cp_players or not isinstance(cp_players[0], dict):
            return state  # already in tracker format

        names = [(p.get('name') or '') for p in cp_players]
        bets = [p.get('bet', 0) for p in cp_players]
        hero_idx = -1
        for i, p in enumerate(cp_players):
            if p.get('user_id') == self._hero_user_id:
                hero_idx = i
                break
        # Shallow copy + override the three fields the tracker reads
        out = dict(state)
        out['players'] = names
        out['bets'] = bets
        out['hero_seat'] = hero_idx
        return out

    def update(self, state: dict) -> None:
        try:
            self._inner.update(self._convert(state))
        except Exception as e:
            print(f"[tracker] update failed: {type(e).__name__}: {e}")

    def classify_villain(self, state: dict) -> str:
        try:
            return self._inner.classify_villain(self._convert(state)) or "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def get_table_summary(self, hero_seat, players):
        """
        AdvisorStateMachine calls this with ``state["hero_seat"]`` and
        ``state["players"]`` directly. The state is in CoinPoker format
        at that point — players is a list of dicts. Convert here.
        """
        try:
            if players and isinstance(players[0], dict):
                hero_idx = -1
                for i, p in enumerate(players):
                    if p.get('user_id') == self._hero_user_id:
                        hero_idx = i
                        break
                names = [(p.get('name') or '') for p in players]
                return self._inner.get_table_summary(hero_idx, names)
            return self._inner.get_table_summary(hero_seat, players)
        except Exception:
            return ""

    def flush(self) -> None:
        try:
            self._inner.flush()
        except Exception as e:
            print(f"[tracker] flush failed: {type(e).__name__}: {e}")


# ── advisor wiring (deferred imports so tests don't pay the cost) ─────────────

def make_advisor_callback(session: CoinPokerSession,
                          overlay: Optional[OverlayClient] = None,
                          room_name: str = ""):
    """
    Lazy-load AdvisorStateMachine and return a callback that drives it
    on each snapshot. Uses ``session.bb_cents`` so the BB scale matches
    the table being observed.

    OpponentTracker is wired via _CoinPokerTrackerAdapter so VPIP/PFR/AF
    accumulate per villain across hands and persist to HandDB. SessionLogger
    is still skipped (Unibet-specific format).

    If ``overlay`` is provided, each snapshot also renders to it via the
    table_update protocol.
    """
    print("[runner] loading advisor dependencies ...")
    from advisor import Advisor as BaseAdvisor  # noqa: WPS433
    from preflop_chart import preflop_advice    # noqa: WPS433
    from advisor_state_machine import AdvisorStateMachine  # noqa: WPS433

    try:
        from strategy.postflop_engine import PostflopEngine
        postflop = PostflopEngine()
        print("[runner]   PostflopEngine loaded")
    except Exception as e:
        print(f"[runner]   PostflopEngine SKIPPED: {e}")
        postflop = None

    try:
        from advisor import assess_board_danger
    except ImportError:
        assess_board_danger = lambda h, b: {"warnings": []}

    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
    print("[runner]   base Advisor loaded")

    # Opponent tracker — wraps the shared OpponentTracker with a CoinPoker
    # adapter that converts dict-format snapshots to the Unibet name-list
    # format the tracker expects. Persistent across sessions via HandDB.
    tracker = None
    try:
        from opponent_tracker import OpponentTracker
        from hand_db import HandDB
        db = HandDB()
        inner_tracker = OpponentTracker(db=db)
        tracker = _CoinPokerTrackerAdapter(
            inner_tracker, hero_user_id=session.builder.hero_user_id)
        print(f"[runner]   OpponentTracker loaded "
              f"({len(inner_tracker.players)} known players)")
    except Exception as e:
        print(f"[runner]   OpponentTracker SKIPPED: {type(e).__name__}: {e}")
        tracker = None

    sm = AdvisorStateMachine(
        base_advisor=base,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        assess_board_danger_fn=assess_board_danger,
        tracker=tracker,
        bb_cents=session.bb_cents,
    )
    print(f"[runner]   AdvisorStateMachine ready (bb_cents={session.bb_cents})")

    # Register flush on shutdown so any in-session VPIP/PFR updates land
    # in HandDB. Safe even if tracker is None.
    if tracker is not None:
        import atexit
        atexit.register(tracker.flush)

    # Sticky cache: the most recent advisor recommendation for the
    # current hand. We only call the advisor when it's hero's turn,
    # because between hero's action and the next user_turn the snapshot
    # has stale facing/call values from the perspective of "what should
    # hero do" — hero just acted, not deciding right now. Caching the
    # last actionable rec lets the overlay keep displaying it until the
    # next decision point.
    cache: dict = {"hand": None, "out": None}

    def on_snapshot(snap: dict) -> None:
        # Refresh bb_cents in case the table BB changed (e.g. user
        # switched practice → real money mid-session).
        if sm.bb_cents != session.bb_cents:
            sm.bb_cents = session.bb_cents

        # Feed the opponent tracker on every snapshot — VPIP/PFR/AF
        # accumulate from preflop bet patterns, so we need to see the
        # snapshot stream regardless of hero_turn. Failures are logged
        # by the adapter; do not block the recommendation pipeline.
        if tracker is not None:
            tracker.update(snap)

        # New hand → drop the cached rec.
        if cache["hand"] != snap["hand_id"]:
            cache["hand"] = snap["hand_id"]
            cache["out"] = None

        # Compute a fresh recommendation only when it's hero's turn AND
        # hero has cards (not spectating). Otherwise reuse the cached
        # rec for display so the overlay stays informative.
        out = None
        if snap["hero_turn"] and len(snap["hero_cards"]) >= 2:
            out = sm.process_state(snap)
            if out is not None and out.action:
                cache["out"] = out

        display_out = cache["out"]

        # Console line — only print when the advisor JUST produced a fresh
        # action this turn. Avoids spamming the same rec on every villain
        # bet update.
        if out is not None and out.action:
            hero = " ".join(snap["hero_cards"]) or "??"
            board = " ".join(snap["board_cards"]) or "-"
            print(f"*** [{out.phase:7}] {hero:5}  board={board:14}  "
                  f"pos={snap['position']:3}  eq={out.equity:.0%}  "
                  f"=> {out.action}")

        # Overlay update — fire on every state change so the HUD reflects
        # current cards/board/phase even when the advisor has no fresh rec.
        if overlay is not None:
            try:
                msg = snapshot_to_overlay_msg(
                    snap, display_out,
                    table_id=overlay.table_id,
                    room_name=room_name,
                )
                overlay.send(msg)
            except Exception as e:
                print(f"[overlay] build failed: {type(e).__name__}: {e}")

    return on_snapshot


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CoinPoker advisor runner")
    p.add_argument("--file", default=DEFAULT_LOG,
                   help="JSONL frame log to read (default: %(default)s)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--follow", action="store_true",
                      help="Tail the file (live mode, default)")
    mode.add_argument("--replay", action="store_true",
                      help="Read once start-to-finish then exit")
    p.add_argument("--hero-id", type=int, default=HERO_USER_ID_DEFAULT,
                   help="CoinPoker userId to treat as hero (default: %(default)s)")
    p.add_argument("--bb-chips", type=int, default=None,
                   help="Override big-blind chip amount (default: auto-detect)")
    p.add_argument("--print-only", action="store_true",
                   help="Skip advisor wiring; just print state snapshots")
    p.add_argument("--no-overlay", action="store_true",
                   help="Don't spawn the Tk overlay (default: spawn in --follow, "
                        "skip in --replay/--print-only)")
    args = p.parse_args(argv)

    if not args.follow and not args.replay:
        args.follow = True  # default

    if not os.path.exists(args.file):
        print(f"frame log not found: {args.file}", file=sys.stderr)
        return 2

    # Overlay: spawn only in live mode unless explicitly disabled.
    overlay: Optional[OverlayClient] = None
    want_overlay = (args.follow and not args.print_only and not args.no_overlay)
    if want_overlay:
        overlay = OverlayClient.spawn()

    # Build session with a placeholder that's swapped after we know bb_cents.
    callback_holder: list = [None]
    session = CoinPokerSession(
        hero_user_id=args.hero_id,
        on_snapshot=lambda snap: callback_holder[0](snap) if callback_holder[0] else None,
        bb_chips=args.bb_chips,
    )

    if args.print_only:
        callback_holder[0] = make_console_printer()
        print(f"[runner] print-only mode, hero_id={args.hero_id}, file={args.file}")
    else:
        callback_holder[0] = make_advisor_callback(session, overlay=overlay)

    print("=" * 60)
    print(f"  CoinPoker Advisor Runner — {'follow' if args.follow else 'replay'} mode")
    print("=" * 60)

    import atexit
    def _cleanup():
        if overlay is not None:
            try:
                overlay.remove_table()
            except Exception:
                pass
            overlay.close()
    atexit.register(_cleanup)

    try:
        if args.replay:
            session.run(replay_iter(args.file))
            print(f"\n[runner] replay done. frames={session.frames_seen} "
                  f"snapshots={session.snapshots_dispatched}")
        else:
            # Warmup: silently feed the existing file into the builder so
            # it knows the current hand_id / hero_seat / blinds before we
            # start dispatching. Without this, --follow seeks to EOF and
            # snapshot() returns None for every new frame because the
            # builder never sees the seed events.
            print(f"[runner] warming up builder from {args.file} ...")
            warmup_n = 0
            with open(args.file, "r", encoding="utf-8") as wf:
                for raw in wf:
                    raw = raw.rstrip("\n")
                    if not raw:
                        continue
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    session.builder.ingest(frame)
                    warmup_n += 1
            print(f"[runner] warmup ingested {warmup_n} frames; "
                  f"hand={session.builder.hand_id} "
                  f"hero_seat={session.builder.hero_seat} "
                  f"phase={session.builder.phase}")
            # Force one dispatch of the current state so the overlay is
            # populated immediately rather than empty until the next change.
            current = session.builder.snapshot()
            if current is not None and callback_holder[0] is not None:
                try:
                    callback_holder[0](current)
                except Exception as e:
                    print(f"[runner] warmup snapshot dispatch failed: {e}")
            print(f"[runner] tailing {args.file} — Ctrl+C to stop\n")
            session.run(follow_iter(args.file))
    except KeyboardInterrupt:
        print(f"\n[runner] stopped. frames={session.frames_seen} "
              f"snapshots={session.snapshots_dispatched}")
    finally:
        _cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
