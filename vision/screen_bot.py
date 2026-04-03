"""
Screen-reading bot — plays the lab client by reading pixels and clicking.

No OCR or neural networks — uses fast color/template detection only.
Designed to be lightweight and responsive (~100ms per frame).

Detection approach:
  1. Find the Poker Lab browser window
  2. Detect action buttons by color (red=fold, green=check/call, grey=raise)
  3. Read pot/stack amounts from known pixel regions (TODO: template digits)
  4. Click buttons via pyautogui with humanized timing

Usage:
  python vision/screen_bot.py                    # play with TAG decisions
  python vision/screen_bot.py --instant          # no humanization (bot-like)
  python vision/screen_bot.py --hands 50         # stop after N hands
"""

import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

import cv2
import mss
import numpy as np

try:
    import pyautogui
    pyautogui.PAUSE = 0.02
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None
    print("ERROR: pip install pyautogui")
    sys.exit(1)

try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None
    print("ERROR: pip install pywin32")
    sys.exit(1)


# ── Window Management ────────────────────────────────────────────────────

def find_poker_window():
    """Find the Poker Lab browser window. Returns (hwnd, rect) or None."""
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "Poker Lab" in title:
                rect = win32gui.GetWindowRect(hwnd)
                result.append((hwnd, rect))
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def capture_window(rect):
    """Capture a window region. Returns BGR numpy array."""
    left, top, right, bottom = rect
    with mss.mss() as sct:
        img = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


# ── Button Detection (fast, color-based) ─────────────────────────────────

def find_buttons(frame):
    """
    Find action buttons in the bottom 15% of the frame by color.
    Returns list of {action, cx, cy, x, y, w, h}.
    """
    h, w = frame.shape[:2]
    # Action bar is in the bottom ~12% of the window
    bar_top = int(h * 0.88)
    bar = frame[bar_top:, :]
    bar_h, bar_w = bar.shape[:2]
    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)

    buttons = []

    # Red button (Fold): H in [0,10] or [170,180], S>80, V>60
    red1 = cv2.inRange(hsv, np.array([0, 80, 60]), np.array([10, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([170, 80, 60]), np.array([180, 255, 255]))
    red_mask = red1 | red2
    fold_btn = _largest_contour(red_mask, min_area=300)
    if fold_btn:
        x, y, bw, bh = fold_btn
        buttons.append({
            "action": "FOLD",
            "cx": x + bw // 2, "cy": bar_top + y + bh // 2,
            "x": x, "y": bar_top + y, "w": bw, "h": bh,
        })

    # Green button (Check/Call): H in [35,85], S>80, V>60
    green_mask = cv2.inRange(hsv, np.array([35, 80, 60]), np.array([85, 255, 255]))
    green_btn = _largest_contour(green_mask, min_area=300)
    if green_btn:
        x, y, bw, bh = green_btn
        buttons.append({
            "action": "CHECK_CALL",
            "cx": x + bw // 2, "cy": bar_top + y + bh // 2,
            "x": x, "y": bar_top + y, "w": bw, "h": bh,
        })

    # Grey buttons (Bet/Raise): H any, S<50, V in [30,130]
    # These are harder — look for rectangular regions right of the green button
    grey_mask = cv2.inRange(hsv, np.array([0, 0, 30]), np.array([180, 50, 130]))
    contours, _ = cv2.findContours(grey_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        area = cv2.contourArea(c)
        if area > 300:
            x, y, bw, bh = cv2.boundingRect(c)
            # Must be button-shaped (wider than tall, reasonable size)
            if bw > bh * 0.5 and bw < bar_w * 0.5:
                buttons.append({
                    "action": "BET_RAISE",
                    "cx": x + bw // 2, "cy": bar_top + y + bh // 2,
                    "x": x, "y": bar_top + y, "w": bw, "h": bh,
                })

    return buttons


def _largest_contour(mask, min_area=100):
    """Find the largest contour in a mask. Returns (x,y,w,h) or None."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < min_area:
        return None
    return cv2.boundingRect(largest)


# ── Active Seat Detection ────────────────────────────────────────────────

def detect_active_seat(frame):
    """
    Check if there's a green glow around the hero seat (bottom of screen),
    indicating it's hero's turn.
    """
    h, w = frame.shape[:2]
    # Hero seat is in bottom 25%, center 40%
    roi = frame[int(h * 0.72):int(h * 0.88), int(w * 0.3):int(w * 0.7)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # Green highlight glow: bright green pixels
    green = cv2.inRange(hsv, np.array([40, 100, 150]), np.array([80, 255, 255]))
    green_pct = cv2.countNonZero(green) / max(green.size, 1)

    return green_pct > 0.005  # >0.5% green pixels = active highlight


# ── Timer Detection ──────────────────────────────────────────────────────

def detect_timer(frame):
    """Detect the green/yellow timer bar at the bottom of the hero seat."""
    h, w = frame.shape[:2]
    # Timer bar region: just below hero seat, thin strip
    roi = frame[int(h * 0.85):int(h * 0.87), int(w * 0.3):int(w * 0.7)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    colored = cv2.inRange(hsv, np.array([20, 80, 100]), np.array([85, 255, 255]))
    return cv2.countNonZero(colored) > 50


# ── Click Automation ─────────────────────────────────────────────────────

def click_button(button, win_rect, humanize=True):
    """Click a button with optional humanization."""
    left, top, _, _ = win_rect
    screen_x = left + button["cx"]
    screen_y = top + button["cy"]

    if humanize:
        # Human-like: random offset, variable delay, curved mouse movement
        screen_x += random.randint(-4, 4)
        screen_y += random.randint(-2, 2)
        delay = random.uniform(0.4, 2.5)
        time.sleep(delay)
        pyautogui.moveTo(screen_x, screen_y, duration=random.uniform(0.08, 0.25))
        time.sleep(random.uniform(0.03, 0.1))
        pyautogui.click()
    else:
        # Bot-like: instant
        time.sleep(0.05)
        pyautogui.click(screen_x, screen_y)


# ── Strategy ─────────────────────────────────────────────────────────────

def choose_action(buttons):
    """
    Simple strategy based on available buttons.
    Prefers: CHECK_CALL > FOLD (for now — just to test the loop).
    """
    by_action = {b["action"]: b for b in buttons}

    # If check/call is available, take it
    if "CHECK_CALL" in by_action:
        return by_action["CHECK_CALL"]
    # Otherwise fold
    if "FOLD" in by_action:
        return by_action["FOLD"]
    # Last resort: any button
    if buttons:
        return buttons[0]
    return None


# ── WS Dealer (auto-deals hands via WebSocket) ──────────────────────────

class WSDealerThread(threading.Thread):
    """Background thread that deals hands and tracks game state via WS."""

    def __init__(self):
        super().__init__(daemon=True)
        self.ws = None
        self.state = {}
        self.is_hero_turn = False
        self.hand_active = False
        self.hands_played = 0
        self.running = True

    def run(self):
        from websocket import create_connection, WebSocketTimeoutException
        try:
            self.ws = create_connection("ws://localhost:9100")
            self.ws.settimeout(1.0)
            msg = json.loads(self.ws.recv())
            self.state = msg.get("state", {})
            print(f"  [Dealer] Connected, hands={self.state.get('handsPlayed', 0)}")
        except Exception as e:
            print(f"  [Dealer] Connection failed: {e}")
            return

        while self.running:
            try:
                raw = self.ws.recv()
                msg = json.loads(raw)

                # Update state
                if msg.get("state") and "seats" in msg.get("state", {}):
                    self.state = msg["state"]
                if msg.get("welcome"):
                    self.state = msg.get("state", self.state)

                # Track events
                for evt in msg.get("events", []):
                    if evt.get("type") == "HAND_END":
                        self.hands_played += 1
                        self.hand_active = False
                    if evt.get("type") == "HAND_START":
                        self.hand_active = True

                # Update hero turn status
                hand = self.state.get("hand")
                if hand and hand.get("phase") not in (None, "COMPLETE"):
                    self.hand_active = True
                    self.is_hero_turn = hand.get("actionSeat") == 0
                else:
                    self.hand_active = False
                    self.is_hero_turn = False

                # Auto-deal if no active hand
                if not self.hand_active:
                    time.sleep(2.0)  # PS-like delay between hands
                    try:
                        self.ws.send(json.dumps({"id": f"deal-{self.hands_played}", "cmd": "START_HAND", "payload": {}}))
                    except Exception:
                        pass

            except WebSocketTimeoutException:
                # If no messages and no active hand, try dealing
                if not self.hand_active:
                    try:
                        self.ws.send(json.dumps({"id": f"deal-{self.hands_played}", "cmd": "START_HAND", "payload": {}}))
                    except Exception:
                        pass
            except Exception:
                break

    def stop(self):
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


# ── Main Loop ────────────────────────────────────────────────────────────

def run(args):
    print("=" * 50)
    print("  SCREEN BOT — Pixel Reading + Click")
    print("=" * 50)

    window = find_poker_window()
    if not window:
        print("  Poker Lab window not found!")
        print("  Open http://localhost:9100 in a browser first.")
        return

    hwnd, rect = window
    title = win32gui.GetWindowText(hwnd).encode("ascii", "ignore").decode()
    print(f"  Window: {title}")
    print(f"  Rect: {rect}")
    print(f"  Humanize: {not args.instant}")
    print(f"  Max hands: {args.hands or 'unlimited'}")

    # Start WS dealer thread (auto-deals hands)
    dealer = WSDealerThread()
    dealer.start()
    time.sleep(3)  # let it connect + deal first hand
    print()

    actions_taken = 0
    hands_at_start = dealer.hands_played
    last_action_time = 0
    cooldown = 1.5  # minimum seconds between actions
    start_time = time.time()

    print("  Running... (Ctrl+C to stop, move mouse to corner to abort)")
    print()

    try:
        while True:
            # Check hand limit
            hands_done = dealer.hands_played - hands_at_start
            if args.hands > 0 and hands_done >= args.hands:
                break

            # Capture
            frame = capture_window(rect)

            # Detect buttons
            buttons = find_buttons(frame)

            # Only act if buttons visible, cooldown elapsed, and dealer says it's our turn
            now = time.time()
            if buttons and (now - last_action_time) > cooldown:
                is_turn = dealer.is_hero_turn or detect_active_seat(frame)

                if is_turn and len(buttons) >= 1:
                    btn = choose_action(buttons)
                    if btn:
                        click_button(btn, rect, humanize=not args.instant)
                        actions_taken += 1
                        last_action_time = now
                        elapsed = now - start_time
                        print(f"  [{actions_taken}] {btn['action']} ({hands_done} hands) | {elapsed:.0f}s")

            time.sleep(0.2)

    except KeyboardInterrupt:
        pass

    dealer.stop()
    hands_done = dealer.hands_played - hands_at_start
    elapsed = time.time() - start_time
    print(f"\n  Done: {hands_done} hands, {actions_taken} actions in {elapsed:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="Screen-reading poker bot")
    parser.add_argument("--instant", action="store_true", help="No humanization")
    parser.add_argument("--hands", type=int, default=0, help="Stop after N hands (0=unlimited)")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
