"""
Auto-player: AdvisorStateMachine + Humanizer + CDP clicks.

CDP clicks into the Emscripten canvas (no cursor movement).
Key: canvas must be focused before each click.

Usage: python -u vision/auto_player.py
"""

import os
import sys
import json
import time
import subprocess
import threading

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

# Tee output to log file
LOG_PATH = os.path.join(ROOT, "auto_player.log")

# Pause flag — overlay's pause button toggles this file. While it exists,
# auto-player computes recommendations but skips clicks (manual takeover).
PAUSE_FLAG = os.path.join(ROOT, ".autoplay_pause")

def is_paused():
    return os.path.exists(PAUSE_FLAG)

# Safety: every auto_player.py launch starts PAUSED. User must explicitly
# click ▶ RUNNING on the overlay to enable clicks. Prevents accidentally
# letting the (unreliable) auto-clicker loose on a fresh session.
try:
    with open(PAUSE_FLAG, "w") as _f:
        _f.write(str(time.time()))
    print(f"[Auto] Started PAUSED — click ▶ RUNNING on overlay to enable clicks")
except Exception as _e:
    print(f"[Auto] Could not create pause flag: {_e}")

class TeeWriter:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams:
            s.flush()

_log_file = open(LOG_PATH, "w", encoding="utf-8")
sys.stdout = TeeWriter(sys.__stdout__, _log_file)
sys.stderr = TeeWriter(sys.__stderr__, _log_file)

from unibet_ws import UnibetWSReader
from advisor_state_machine import AdvisorStateMachine
from humanizer import (
    get_think_time, SessionManager, PlayVariation,
    get_cursor_pos, click_mouse,
)

MAX_THINK_TIME = 10.0
MIN_THINK_TIME = 2.0

import ctypes as _ctypes
from ctypes import wintypes as _wt

# DO NOT set DPI awareness — causes GetClientRect to return physical pixels
# but SetCursorPos uses CSS-equivalent coordinates at 100% Windows scale.
# DPI-naive matches what SetCursorPos expects.

def _find_chrome_hwnd():
    WNDENUMPROC = _ctypes.WINFUNCTYPE(_ctypes.c_bool, _wt.HWND, _wt.LPARAM)
    found = [None]
    def cb(hwnd, lparam):
        if _ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = _ctypes.create_unicode_buffer(256)
            _ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            t = buf.value.lower()
            if 'unibet' in t or 'relax poker' in t:
                found[0] = hwnd
                return False
        return True
    _ctypes.windll.user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0]


def main():
    print("=" * 50)
    print("  UNIBET AUTO-PLAYER (CDP canvas click)")
    print("=" * 50)

    # ── Load dependencies ──
    from strategy.postflop_engine import PostflopEngine
    try:
        postflop = PostflopEngine()
    except Exception as e:
        print(f"[Auto] PostflopEngine failed: {e}")
        postflop = None

    from advisor import Advisor as BaseAdvisor
    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)

    from preflop_chart import preflop_advice
    from opponent_tracker import OpponentTracker
    from session_logger import SessionLogger
    from hand_db import HandDB
    from collusion_detector import CollusionDetector
    from bot_detector import BotDetector
    from action_inferrer import WSActionInferrer

    db = HandDB()
    tracker = OpponentTracker(db=db)  # persistent across sessions
    collusion = CollusionDetector(db=db)
    bots = BotDetector(db=db)
    action_inf = WSActionInferrer()
    logger = SessionLogger()

    sm = AdvisorStateMachine(
        base_advisor=base,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        tracker=tracker,
        bb_cents=4,
    )

    session = SessionManager()
    variation = PlayVariation(mistake_rate=0.0)  # disabled — was converting folds to calls
    print(f"[Auto] Session length: {session.session_length/60:.0f} min")

    print("[Auto] Using SendInput with focus save/restore")

    # ── Overlay ──
    overlay_script = os.path.join(VISION_DIR, "overlay_process.py")
    overlay = subprocess.Popen(
        [sys.executable, "-u", overlay_script],
        stdin=subprocess.PIPE, text=True
    )

    def send_overlay(cards="", info="", rec="", rec_bg="#1a1a2e", rec_fg="#ffd700"):
        if overlay.poll() is None:
            try:
                overlay.stdin.write(json.dumps({
                    "cards": cards, "info": info, "rec": rec,
                    "rec_bg": rec_bg, "rec_fg": rec_fg
                }) + "\n")
                overlay.stdin.flush()
            except Exception:
                pass

    # ── Action execution ──
    current_action_id = [0]
    action_lock = threading.Lock()
    last_acted_key = [None]  # (hand_id, phase, facing, call_amt) — only act once per situation
    last_play_click = [0]  # timestamp of last PLAY click (avoid spamming)
    no_cards_since = [None]  # when did hero last have cards
    latest_ws_state = [None]  # most recent WS state for click verification
    pending_action = [None]  # currently pending action info

    def click_play_button():
        """Click the PLAY button (center-bottom of canvas) to rejoin after sitting out."""
        if time.time() - last_play_click[0] < 5:
            return  # cooldown
        try:
            hwnd = _find_chrome_hwnd()
            if not hwnd:
                return
            render = _ctypes.windll.user32.FindWindowExW(hwnd, None, "Chrome_RenderWidgetHostHWND", None)
            target_widget = render if render else hwnd
            rect = _wt.RECT()
            _ctypes.windll.user32.GetClientRect(target_widget, _ctypes.byref(rect))
            cw, ch = rect.right, rect.bottom
            pt = _wt.POINT(0, 0)
            _ctypes.windll.user32.ClientToScreen(target_widget, _ctypes.byref(pt))
            # PLAY button at center, ~90% y
            sx = pt.x + int(cw * 0.50)
            sy = pt.y + int(ch * 0.90)

            prev_hwnd = _ctypes.windll.user32.GetForegroundWindow()
            saved = _wt.POINT()
            _ctypes.windll.user32.GetCursorPos(_ctypes.byref(saved))

            for _ in range(3):
                fg_thread = _ctypes.windll.user32.GetWindowThreadProcessId(prev_hwnd, None)
                cur_thread = _ctypes.windll.kernel32.GetCurrentThreadId()
                _ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
                _ctypes.windll.user32.SetForegroundWindow(hwnd)
                _ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
                time.sleep(0.1)
                if _ctypes.windll.user32.GetForegroundWindow() == hwnd:
                    break
            time.sleep(0.15)

            _ctypes.windll.user32.SetCursorPos(sx, sy)
            time.sleep(0.04)
            _ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.07)
            _ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.05)

            _ctypes.windll.user32.SetCursorPos(saved.x, saved.y)
            _ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
            last_play_click[0] = time.time()
            print(f"[Auto] PLAY clicked at ({sx},{sy})")
        except Exception as e:
            print(f"[Auto] PLAY click error: {e}")

    def execute_action(out, action_id):
        action = out.action
        phase = out.phase

        # Pause gate — skip clicking entirely if user has paused via overlay.
        # Recommendation already shown on overlay; user takes over manually.
        if is_paused():
            print(f"[Auto] PAUSED — not clicking {action} (manual takeover)")
            send_overlay(out.cards_text, out.info + " [PAUSED]", out.action, out.rec_bg)
            return

        action, was_mistake = variation.maybe_modify_action(
            action, out.equity, phase, out.facing_bet
        )
        if was_mistake:
            print(f"[Auto] VARIATION: {out.action} -> {action}")

        # Unibet timer is ~10s. Stay under 6s to have 4s safety buffer.
        import random as _r
        base = 2.0
        humanized = get_think_time(phase, action) * 0.3
        think = base + humanized + _r.uniform(0, 1.0)
        # Folds can be slightly faster
        if "FOLD" in action.upper():
            think = max(1.5, min(think, 4.0))
        else:
            think = max(2.0, min(think, 6.0))
        print(f"[Auto] Thinking {think:.1f}s for {action}...")

        # Wait in chunks for cancellation (also poll pause flag mid-think)
        elapsed = 0
        while elapsed < think:
            chunk = min(0.3, think - elapsed)
            time.sleep(chunk)
            elapsed += chunk
            if current_action_id[0] != action_id:
                print(f"[Auto] Cancelled")
                return
            if is_paused():
                print(f"[Auto] PAUSED mid-think — not clicking {action}")
                send_overlay(out.cards_text, out.info + " [PAUSED]", out.action, out.rec_bg)
                return

        if current_action_id[0] != action_id:
            return

        with action_lock:
            if current_action_id[0] != action_id:
                return

            # Map action to button + extract amount for RAISE/BET
            action_upper = action.upper()
            amount = 0
            if "FOLD" in action_upper:
                cmd = "FOLD"
            elif "CHECK" in action_upper:
                cmd = "CHECK"
            elif "CALL" in action_upper:
                cmd = "CALL"
            elif "RAISE" in action_upper or "BET" in action_upper:
                # Extract numeric amount, e.g. "RAISE to 0.10" -> 0.10
                import re as _re
                m = _re.search(r'(\d+\.\d+)', action)
                if m:
                    amount = float(m.group(1))
                cmd = "RAISE" if "RAISE" in action_upper else "BET"
            else:
                cmd = "CHECK"

            # Build command with amount if needed
            if amount > 0:
                cdp_cmd = f"{cmd} {amount:.2f}"
            else:
                cdp_cmd = cmd

            # Click via SendInput with focus save/restore
            try:
                hwnd = _find_chrome_hwnd()
                if not hwnd:
                    print(f"[Auto] Chrome not found")
                else:
                    # Get the Chrome render widget (the actual viewport, not including tab bar)
                    render = _ctypes.windll.user32.FindWindowExW(hwnd, None, "Chrome_RenderWidgetHostHWND", None)
                    target_widget = render if render else hwnd

                    # Get viewport dimensions and screen origin
                    rect = _wt.RECT()
                    _ctypes.windll.user32.GetClientRect(target_widget, _ctypes.byref(rect))
                    cw, ch = rect.right, rect.bottom
                    pt = _wt.POINT(0, 0)
                    _ctypes.windll.user32.ClientToScreen(target_widget, _ctypes.byref(pt))

                    # Button positions:
                    # X: % of render widget width (stable)
                    # Y: ABSOLUTE OFFSET FROM BOTTOM in physical pixels
                    #    (more robust than % when render height changes)
                    # FOLD/CALL center at 74px from bottom, RAISE at 82px from bottom
                    btn_pct = {"FOLD": (0.384, None), "CHECK": (0.485, None),
                               "CALL": (0.485, None), "RAISE": (0.587, None),
                               "BET": (0.587, None)}
                    btn_y_from_bottom = {"FOLD": 74, "CHECK": 74, "CALL": 74,
                                          "RAISE": 82, "BET": 82}
                    # Slider preset positions (in render-widget pct)
                    # Screen y 935 → render y (935-131)/944 = 0.852
                    # x positions from screenshot: 0.373, 0.424, 0.474, 0.525 of full image
                    # In render: subtract origin offset, divide by render width
                    # Image width 1902, render origin x 26, render width 1899
                    # x_render_pct = (image_x - 26) / 1899
                    slider_pct = {
                        25:  (0.360, 0.852),  # (710-26)/1899
                        50:  (0.411, 0.852),  # (806-26)/1899
                        80:  (0.461, 0.852),  # (902-26)/1899
                        100: (0.512, 0.852),  # (998-26)/1899
                    }
                    pct = btn_pct[cmd]
                    sx = pt.x + int(cw * pct[0])
                    # Use absolute Y offset from bottom (more robust)
                    sy = pt.y + ch - btn_y_from_bottom[cmd]

                    # Don't touch slider — accept Unibet's default raise/bet sizing.
                    # Strategy will adapt to whatever amount the button represents.
                    bet_input = None
                    preset_target = None

                    # Save current foreground window
                    prev_hwnd = _ctypes.windll.user32.GetForegroundWindow()
                    saved_cursor = _wt.POINT()
                    _ctypes.windll.user32.GetCursorPos(_ctypes.byref(saved_cursor))

                    # Bring Chrome to foreground via Alt trick
                    _ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                    _ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                    time.sleep(0.02)
                    _ctypes.windll.user32.SetForegroundWindow(hwnd)
                    time.sleep(0.1)

                    # Also tell Chrome to give the Unibet tab keyboard focus via CDP
                    # This is critical — Emscripten only processes input when document.hasFocus()
                    try:
                        import urllib.request as _ur
                        import json as _json
                        with _ur.urlopen("http://localhost:9222/json", timeout=1) as resp:
                            tabs = _json.loads(resp.read())
                        unibet_tab = next((t for t in tabs if 'unibet' in t.get('url', '').lower() and 'pokerweb' in t.get('url', '').lower()), None)
                        if unibet_tab:
                            # Activate the tab
                            _ur.urlopen(f"http://localhost:9222/json/activate/{unibet_tab['id']}", timeout=1)
                            time.sleep(0.1)
                    except Exception as e:
                        print(f"[Auto] CDP activate error: {e}")

                    # Extra wait so Chrome's compositor catches up
                    time.sleep(0.15)

                    # Click slider preset first (safe — never All-in)
                    if preset_target:
                        _ctypes.windll.user32.SetCursorPos(preset_target[0], preset_target[1])
                        time.sleep(0.04)
                        _ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                        time.sleep(0.07)
                        _ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
                        time.sleep(0.15)
                        print(f"[Auto] slider clicked at {preset_target}")

                    # Snapshot state BEFORE clicking
                    pre_state = latest_ws_state[0] or {}
                    pre_hero_bet = pre_state.get("bets", [0]*6)
                    pre_facing = pre_state.get("facing_bet", False)
                    pre_pot = pre_state.get("pot", 0)
                    pre_hand = pre_state.get("hand_id")

                    # Diagnostic screenshot showing where we're about to click
                    try:
                        import mss
                        from PIL import Image, ImageDraw
                        with mss.mss() as sct:
                            shot = sct.grab(sct.monitors[1])
                            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                            draw = ImageDraw.Draw(img)
                            # Draw circle at click target
                            r = 30
                            draw.ellipse((sx-r, sy-r, sx+r, sy+r), outline='red', width=4)
                            draw.line((sx-r-10, sy, sx+r+10, sy), fill='red', width=2)
                            draw.line((sx, sy-r-10, sx, sy+r+10), fill='red', width=2)
                            # Crop to button area
                            crop = img.crop((max(0, sx-300), max(0, sy-200), min(img.width, sx+300), min(img.height, sy+200)))
                            ts = int(time.time())
                            crop.save(f"C:/poker-research/click_debug_{ts}_{cmd}.png")
                    except Exception as e:
                        print(f"[Auto] screenshot err: {e}")

                    def click_button(retry_n=0):
                        # Use SendInput (modern Win32 API) for hardware-level events
                        # MOUSEINPUT structure
                        class _MOUSEINPUT(_ctypes.Structure):
                            _fields_ = [
                                ("dx", _ctypes.c_long),
                                ("dy", _ctypes.c_long),
                                ("mouseData", _ctypes.c_ulong),
                                ("dwFlags", _ctypes.c_ulong),
                                ("time", _ctypes.c_ulong),
                                ("dwExtraInfo", _ctypes.POINTER(_ctypes.c_ulong)),
                            ]
                        class _INPUT_UNION(_ctypes.Union):
                            _fields_ = [("mi", _MOUSEINPUT)]
                        class _INPUT(_ctypes.Structure):
                            _fields_ = [
                                ("type", _ctypes.c_ulong),
                                ("ii", _INPUT_UNION),
                            ]

                        # Convert to absolute coords (0-65535)
                        sw = _ctypes.windll.user32.GetSystemMetrics(0)
                        sh = _ctypes.windll.user32.GetSystemMetrics(1)
                        ax = int(sx * 65535 / sw)
                        ay = int(sy * 65535 / sh)

                        MOUSEEVENTF_MOVE = 0x0001
                        MOUSEEVENTF_ABSOLUTE = 0x8000
                        MOUSEEVENTF_LEFTDOWN = 0x0002
                        MOUSEEVENTF_LEFTUP = 0x0004
                        INPUT_MOUSE = 0

                        def make_input(flags):
                            inp = _INPUT()
                            inp.type = INPUT_MOUSE
                            inp.ii.mi.dx = ax
                            inp.ii.mi.dy = ay
                            inp.ii.mi.mouseData = 0
                            inp.ii.mi.dwFlags = flags
                            inp.ii.mi.time = 0
                            inp.ii.mi.dwExtraInfo = _ctypes.pointer(_ctypes.c_ulong(0))
                            return inp

                        # Move
                        move = make_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
                        _ctypes.windll.user32.SendInput(1, _ctypes.byref(move), _ctypes.sizeof(_INPUT))
                        time.sleep(0.05)
                        # Down
                        down = make_input(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE)
                        _ctypes.windll.user32.SendInput(1, _ctypes.byref(down), _ctypes.sizeof(_INPUT))
                        time.sleep(0.08)
                        # Up
                        up = make_input(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE)
                        _ctypes.windll.user32.SendInput(1, _ctypes.byref(up), _ctypes.sizeof(_INPUT))
                        if retry_n > 0:
                            print(f"[Auto] {cmd} retry #{retry_n} via SendInput at ({sx},{sy})")

                    click_button(0)

                    # Verify: wait up to 2.5s for state to change
                    import time as _t
                    start_wait = _t.time()
                    state_changed = False
                    while _t.time() - start_wait < 2.5:
                        cur = latest_ws_state[0] or {}
                        if (cur.get("hand_id") != pre_hand or
                            cur.get("bets") != pre_hero_bet or
                            cur.get("facing_bet") != pre_facing or
                            cur.get("pot") != pre_pot):
                            state_changed = True
                            break
                        _t.sleep(0.1)

                    # Retry up to 2 times if state didn't change
                    for retry in range(1, 3):
                        if state_changed:
                            break
                        click_button(retry)
                        wait_start = _t.time()
                        while _t.time() - wait_start < 1.5:
                            cur = latest_ws_state[0] or {}
                            if (cur.get("hand_id") != pre_hand or
                                cur.get("bets") != pre_hero_bet or
                                cur.get("facing_bet") != pre_facing or
                                cur.get("pot") != pre_pot):
                                state_changed = True
                                break
                            _t.sleep(0.1)

                    if not state_changed:
                        print(f"[Auto] CLICK FAILED — no state change after 3 attempts")
                    pending_action[0] = None
                    time.sleep(0.05)

                    # Restore cursor and foreground window
                    _ctypes.windll.user32.SetCursorPos(saved_cursor.x, saved_cursor.y)
                    _ctypes.windll.user32.SetForegroundWindow(prev_hwnd)

                    print(f"[Auto] {cmd} clicked at ({sx},{sy})")
            except Exception as e:
                print(f"[Auto] Click error: {e}")

            session.record_hand()

    # ── WS callback ──
    reader = UnibetWSReader()

    def on_state(state):
        latest_ws_state[0] = state
        tracker.update(state)
        logger.update(state)

        # Detect new hand → notify collusion detector
        hand_id = state.get('hand_id')
        if hand_id and hand_id != getattr(on_state, '_last_hand', None):
            seated = [p for p in state.get('players', []) if p]
            collusion.hand_started(hand_id, seated)
            on_state._last_hand = hand_id

        # Infer per-player actions from state diff and feed both detectors
        actions = action_inf.update(state)
        if actions:
            for actor, action_type, amount in actions:
                collusion.record_action(actor, action_type, amount)
                bots.record_action(actor, action_type, amount)

        out = sm.process_state(state)
        if out is None:
            return

        if out.should_update_overlay:
            send_overlay(out.cards_text, out.info + " [AUTO]", out.action, out.rec_bg)

        if out.log_line:
            print(out.log_line)

        if out.hand_id and out.phase:
            db.log_hand_start(out.hand_id, state["hero_cards"], out.position, out.hero_stack)
            db.log_street(out.hand_id, out.phase, out.board, out.pot,
                          out.facing_bet, out.call_amount, out.hero_stack,
                          out.action, out.equity, out.source)

        logger.update(state, {"action": out.action, "equity": out.equity})

        # Track when hero has no cards (for PLAY click logic)
        if len(state.get("hero_cards", [])) >= 2:
            no_cards_since[0] = None
        elif no_cards_since[0] is None:
            no_cards_since[0] = time.time()

        if not out.action or "Waiting" in out.cards_text:
            # Auto-click PLAY only if sat out for >30 seconds AND game is active
            if no_cards_since[0] and (time.time() - no_cards_since[0] > 30):
                if state.get("pot", 0) > 0:
                    if time.time() - last_play_click[0] > 15:
                        last_play_click[0] = time.time()
                        threading.Thread(target=click_play_button, daemon=True).start()
            return

        if session.should_end_session():
            print("[Auto] Session complete.")
            return

        # Verify it's actually hero's turn:
        # - hero has cards
        # - either facing a bet OR hero hasn't bet max
        bets = state.get("bets", [])
        hero_seat = state.get("hero_seat", -1)
        if 0 <= hero_seat < len(bets):
            hero_bet = bets[hero_seat]
            max_bet = max(bets) if bets else 0
            # If hero already matches max bet AND not facing, not your turn
            if not state.get("facing_bet") and hero_bet >= max_bet and hero_bet > 0:
                return  # not your turn — already acted

        # Dedupe: only act ONCE per (hand, phase, facing, call) combination
        action_key = (out.hand_id, out.phase, out.facing_bet, out.call_amount)
        if action_key == last_acted_key[0]:
            return  # already acted on this exact situation
        last_acted_key[0] = action_key

        # Don't cancel pending action if it's still relevant to this hand+phase
        # Only start new if no pending action
        if pending_action[0] and pending_action[0]['key'][:2] == action_key[:2]:
            return  # let the pending action complete

        # Start new
        current_action_id[0] += 1
        aid = current_action_id[0]
        pending_action[0] = {'id': aid, 'key': action_key}
        t = threading.Thread(target=execute_action, args=(out, aid), daemon=True)
        t.start()

    reader.on_state_change(on_state)
    reader.start()

    print("\n[Auto] LIVE — CDP clicks active (no cursor movement).")
    print("[Auto] Ctrl+C to stop.\n")

    import atexit
    def cleanup():
        try:
            tracker.flush()  # save opponent stats to DB
            collusion.flush()
            bots.flush()
        except Exception:
            pass
        try:
            overlay.terminate()
            overlay.kill()
        except Exception:
            pass
    atexit.register(cleanup)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[Auto] Stopping...")
        reader.stop()
        cleanup()


if __name__ == "__main__":
    main()
