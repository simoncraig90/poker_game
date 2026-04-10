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

# Start unpaused — remove any stale pause flag from previous session
try:
    if os.path.exists(PAUSE_FLAG):
        os.remove(PAUSE_FLAG)
except Exception:
    pass

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
    human_mouse_path,
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
    # Re-enabled at 0.5% (was 2% which converted folds to calls too often).
    # The PlayVariation._make_mistake guard requires equity > 0.35 before
    # converting a fold to a call, so the cost-bound is small. 0.5% is
    # below the human-mistake range minimum and just enough to break
    # perfect-play patterns without leaking measurable EV.
    variation = PlayVariation(mistake_rate=0.005)
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
    _cached_iframe = [None]  # cached iframe coords: (if_x, if_y, if_w, if_h)

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
        # Reaction time variance:
        #   - Snap-act ~12% of the time (humans pre-decide on clear hands)
        #   - Otherwise normal think with humanized variance
        # The pure-fixed-floor approach (always >= 2.0s) was a bot tell
        # because real human reaction time distributions have a long tail
        # AND a snap-decision peak — they're bimodal. Capping below 2s
        # eliminated the snap peak entirely.
        import random as _r
        snap_act = _r.random() < 0.12 and "FOLD" in action.upper() or _r.random() < 0.05
        if snap_act:
            think = _r.uniform(0.4, 1.1)
        else:
            base = 2.0
            humanized = get_think_time(phase, action) * 0.3
            think = base + humanized + _r.uniform(0, 1.0)
            if "FOLD" in action.upper():
                think = max(1.5, min(think, 4.0))
            else:
                think = max(2.0, min(think, 6.0))
        print(f"[Auto] Thinking {think:.1f}s for {action}{' (snap)' if snap_act else ''}...")


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

                    # Get render widget screen origin
                    pt = _wt.POINT(0, 0)
                    _ctypes.windll.user32.ClientToScreen(target_widget, _ctypes.byref(pt))

                    # Button positions as % of iframe CSS dimensions
                    btn_pct = {"FOLD": (0.403, 0.935), "CHECK": (0.505, 0.935),
                               "CALL": (0.605, 0.932), "RAISE": (0.609, 0.932),
                               "BET": (0.609, 0.932)}

                    # Get iframe position — use cache if available, else query via Node CDP
                    if _cached_iframe[0]:
                        _if_x, _if_y, _if_w, _if_h = _cached_iframe[0]
                        pct = btn_pct[cmd]
                        sx = _if_x + int(_if_w * pct[0])
                        sy = _if_y + int(_if_h * pct[1])
                        print(f"[Auto] iframe cached: btn=({sx},{sy})")
                    else:
                        _node_js = (
                            "const CDP=require('chrome-remote-interface');"
                            "CDP.List({port:9222}).then(ts=>{"
                            "const p=ts.find(t=>t.type==='page'&&t.url.includes('unibet'));"
                            "if(!p)return console.log('{}');"
                            "CDP({target:p.id,port:9222}).then(async c=>{"
                            "const r=await c.Runtime.evaluate({returnByValue:true,expression:"
                            "'(()=>{const f=Array.from(document.querySelectorAll(\"iframe\")).find(x=>x.src&&x.src.includes(\"relaxg\"));if(!f)return\"{}\";const r=f.getBoundingClientRect();return JSON.stringify({l:r.left,t:r.top,w:r.width,h:r.height,dpr:devicePixelRatio});})()'"
                            "});console.log(r.result.value);await c.close();process.exit(0);"
                            "});}).catch(()=>console.log('{}'));"
                        )
                        try:
                            import subprocess as _sp, json as _json2
                            _nr = _sp.run(['node', '-e', _node_js], capture_output=True,
                                          text=True, timeout=3, cwd='C:/poker-research')
                            _fr = _json2.loads(_nr.stdout.strip() or '{}')
                            if _fr and 'l' in _fr:
                                _dpr = _fr['dpr']
                                _if_x = pt.x + int(_fr['l'] * _dpr)
                                _if_y = pt.y + int(_fr['t'] * _dpr)
                                _if_w = int(_fr['w'] * _dpr)
                                _if_h = int(_fr['h'] * _dpr)
                                _cached_iframe[0] = (_if_x, _if_y, _if_w, _if_h)
                                pct = btn_pct[cmd]
                                sx = _if_x + int(_if_w * pct[0])
                                sy = _if_y + int(_if_h * pct[1])
                                print(f"[Auto] iframe coords: origin=({_if_x},{_if_y}) size={_if_w}x{_if_h} -> btn=({sx},{sy})")
                            else:
                                raise ValueError("no iframe data")
                        except Exception as _ce:
                            print(f"[Auto] coord fallback ({_ce})")
                            rect = _wt.RECT()
                            _ctypes.windll.user32.GetClientRect(target_widget, _ctypes.byref(rect))
                            cw, ch = rect.right, rect.bottom
                            pct = btn_pct[cmd]
                            sx = pt.x + int(cw * (0.1399 + 0.6887 * pct[0]))
                            sy = pt.y + int(ch * (0.1879 + 0.5801 * pct[1]))

                    preset_target = None
                    bet_input = None

                    # Don't touch slider — accept Unibet's default raise/bet sizing.
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

                    # Set bet/raise amount by typing into the Unibet bet input field.
                    # The input is above the action buttons at ~x=61%, y=87.3% of iframe.
                    # Click it, Ctrl+A to select, Ctrl+V to paste the target amount.
                    if cmd in ("RAISE", "BET") and amount > 0 and _cached_iframe[0]:
                        _bif_x, _bif_y, _bif_w, _bif_h = _cached_iframe[0]
                        bix = _bif_x + int(_bif_w * 0.610)
                        biy = _bif_y + int(_bif_h * 0.873)
                        amount_str = f"{amount:.2f}"
                        # Put amount on clipboard for paste.
                        # restype must be c_void_p — default c_int truncates 64-bit handles.
                        _clipboard_ok = False
                        try:
                            CF_UNICODETEXT = 13
                            GMEM_MOVEABLE = 0x0002
                            _cbuf = (amount_str + '\x00').encode('utf-16-le')
                            _ga = _ctypes.windll.kernel32.GlobalAlloc
                            _ga.restype = _ctypes.c_void_p
                            _gl = _ctypes.windll.kernel32.GlobalLock
                            _gl.restype = _ctypes.c_void_p
                            _hg = _ga(GMEM_MOVEABLE, len(_cbuf))
                            if _hg:
                                _ptr = _gl(_hg)
                                if _ptr:
                                    _ctypes.memmove(_ptr, _cbuf, len(_cbuf))
                                    _ctypes.windll.kernel32.GlobalUnlock(_hg)
                                    _ctypes.windll.user32.OpenClipboard(0)
                                    _ctypes.windll.user32.EmptyClipboard()
                                    _ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, _hg)
                                    _ctypes.windll.user32.CloseClipboard()
                                    _clipboard_ok = True
                        except Exception as _be:
                            print(f"[Auto] bet clipboard error: {_be}")
                        # Click bet input field
                        _ctypes.windll.user32.SetCursorPos(bix, biy)
                        time.sleep(0.06)
                        _ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                        time.sleep(0.05)
                        _ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
                        time.sleep(0.15)
                        # Ctrl+A select all existing text
                        _ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)   # Ctrl down
                        _ctypes.windll.user32.keybd_event(0x41, 0, 0, 0)   # A down
                        time.sleep(0.03)
                        _ctypes.windll.user32.keybd_event(0x41, 0, 2, 0)   # A up
                        _ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)   # Ctrl up
                        time.sleep(0.05)
                        # Ctrl+V paste amount
                        _ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)   # Ctrl down
                        _ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)   # V down
                        time.sleep(0.03)
                        _ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)   # V up
                        _ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)   # Ctrl up
                        time.sleep(0.15)
                        print(f"[Auto] bet amount €{amount_str} at ({bix},{biy}) clipboard={'ok' if _clipboard_ok else 'FAILED'}")

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

                        # Screen dimensions (for absolute coord conversion)
                        sw = _ctypes.windll.user32.GetSystemMetrics(0)
                        sh = _ctypes.windll.user32.GetSystemMetrics(1)

                        MOUSEEVENTF_MOVE = 0x0001
                        MOUSEEVENTF_ABSOLUTE = 0x8000
                        MOUSEEVENTF_LEFTDOWN = 0x0002
                        MOUSEEVENTF_LEFTUP = 0x0004
                        INPUT_MOUSE = 0

                        def make_input_at(px, py, flags):
                            ax = int(px * 65535 / sw)
                            ay = int(py * 65535 / sh)
                            inp = _INPUT()
                            inp.type = INPUT_MOUSE
                            inp.ii.mi.dx = ax
                            inp.ii.mi.dy = ay
                            inp.ii.mi.mouseData = 0
                            inp.ii.mi.dwFlags = flags
                            inp.ii.mi.time = 0
                            inp.ii.mi.dwExtraInfo = _ctypes.pointer(_ctypes.c_ulong(0))
                            return inp

                        # ── Click coordinate variance ──
                        # Add Gaussian offset around the button center.
                        # Stdev 5px x / 4px y is well within button bounds
                        # (~80px wide). Real users don't click exact centers.
                        import random as _r2
                        offset_x = int(_r2.gauss(0, 5))
                        offset_y = int(_r2.gauss(0, 4))
                        # Clamp to a 12px box from center to keep clicks
                        # safely on the button
                        offset_x = max(-12, min(12, offset_x))
                        offset_y = max(-12, min(12, offset_y))
                        click_x = sx + offset_x
                        click_y = sy + offset_y

                        # ── Mouse path simulation ──
                        # Walk a Bezier path from the saved cursor position
                        # to the click target instead of jumping. Falls back
                        # to a single jump on the retry path so retry latency
                        # stays low.
                        if retry_n == 0:
                            try:
                                start_pt = (saved_cursor.x, saved_cursor.y)
                                target_pt = (click_x, click_y)
                                path = human_mouse_path(start_pt, target_pt)
                                # Walk the path with small variable delays
                                for px, py in path[:-1]:
                                    move = make_input_at(px, py, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
                                    _ctypes.windll.user32.SendInput(1, _ctypes.byref(move), _ctypes.sizeof(_INPUT))
                                    time.sleep(_r2.uniform(0.005, 0.015))
                                # Final move to the actual click point
                                final_px, final_py = path[-1]
                                click_x, click_y = final_px, final_py
                                move = make_input_at(click_x, click_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
                                _ctypes.windll.user32.SendInput(1, _ctypes.byref(move), _ctypes.sizeof(_INPUT))
                            except Exception as e:
                                # If the path simulation errors, fall back
                                # to single-jump (don't lose the click)
                                print(f"[Auto] path sim err: {e}")
                                move = make_input_at(click_x, click_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
                                _ctypes.windll.user32.SendInput(1, _ctypes.byref(move), _ctypes.sizeof(_INPUT))
                        else:
                            # Retry: just jump straight there
                            move = make_input_at(click_x, click_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
                            _ctypes.windll.user32.SendInput(1, _ctypes.byref(move), _ctypes.sizeof(_INPUT))

                        # Pre-click settling delay
                        time.sleep(_r2.uniform(0.03, 0.07))

                        # Down
                        down = make_input_at(click_x, click_y, MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE)
                        _ctypes.windll.user32.SendInput(1, _ctypes.byref(down), _ctypes.sizeof(_INPUT))

                        # ── Click duration variance ──
                        # Real human mousedown→mouseup ranges 40-150ms with
                        # a fat tail. Fixed 80ms was a tell.
                        time.sleep(_r2.uniform(0.04, 0.14))

                        # Up
                        up = make_input_at(click_x, click_y, MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE)
                        _ctypes.windll.user32.SendInput(1, _ctypes.byref(up), _ctypes.sizeof(_INPUT))
                        if retry_n > 0:
                            print(f"[Auto] {cmd} retry #{retry_n} via SendInput at ({click_x},{click_y})")

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

        # Detect new hand → notify collusion detector + record for SessionManager
        hand_id = state.get('hand_id')
        if hand_id and hand_id != getattr(on_state, '_last_hand', None):
            seated = [p for p in state.get('players', []) if p]
            collusion.hand_started(hand_id, seated)
            on_state._last_hand = hand_id
            session.record_hand()
            # Between-hand break check. The natural moment to step away
            # is when a hand just ended and the next is being dealt.
            # SessionManager.start_break() returns 2-15 minutes; that's
            # too long to do during a single hand but fine between hands.
            # We sit out by setting the pause flag so auto_player doesn't
            # click anything until the break is over.
            if session.should_take_break() and not is_paused():
                break_secs = session.start_break()
                print(f"[Auto] *** TAKING BREAK *** {break_secs/60:.1f} minutes "
                      f"({session.total_hands} hands played, "
                      f"{session.hands_since_break} since last break)")
                # Set pause flag so any in-flight click thread bails out
                try:
                    open(PAUSE_FLAG, "w").write("session-break\n")
                except Exception:
                    pass
                time.sleep(break_secs)
                # Clear pause flag and resume
                try:
                    if os.path.exists(PAUSE_FLAG):
                        os.unlink(PAUSE_FLAG)
                except Exception:
                    pass
                session.end_break()
                print(f"[Auto] *** RESUMED ***")

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
