"""
Screen-reading bot — plays the poker client by reading pixels and clicking.

Behaves exactly like a real bot would on PokerStars:
  - No server/API access — only sees what's on screen
  - Detects game state purely from pixels (button colors, seat highlights)
  - Clicks buttons via mouse automation
  - Optional humanization (random delays, mouse curves, click offset)

Detection approach:
  1. Find the browser window by title
  2. Capture the window at ~5 FPS
  3. Detect action buttons by color (red=fold, green=check/call, grey=raise)
  4. Detect hero's turn via green seat highlight glow
  5. Choose action and click the button
  6. Wait for next turn

Usage:
  python vision/screen_bot.py                    # play like a human
  python vision/screen_bot.py --instant          # bot-like instant clicks
  python vision/screen_bot.py --hands 50         # stop after ~50 actions
  python vision/screen_bot.py --save-frames      # save each frame for analysis
"""

import argparse
import os
import random
import sys
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
    print("ERROR: pip install pyautogui")
    sys.exit(1)

try:
    import win32gui
    import win32con
except ImportError:
    print("ERROR: pip install pywin32")
    sys.exit(1)

VISION_DIR = Path(__file__).resolve().parent
FRAMES_DIR = VISION_DIR / "captures" / "bot_frames"


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


# ── Pixel Detection ──────────────────────────────────────────────────────

def find_buttons(frame):
    """
    Find action buttons in the bottom of the frame by color.
    Returns list of {action, cx, cy, w, h}.
    """
    h, w = frame.shape[:2]
    bar_top = int(h * 0.88)
    bar = frame[bar_top:, :]
    bar_h, bar_w = bar.shape[:2]
    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)

    buttons = []

    # Red (Fold)
    red1 = cv2.inRange(hsv, np.array([0, 80, 60]), np.array([10, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([170, 80, 60]), np.array([180, 255, 255]))
    btn = _find_button_rect(red1 | red2)
    if btn:
        x, y, bw, bh = btn
        buttons.append({"action": "FOLD", "cx": x + bw // 2, "cy": bar_top + y + bh // 2, "w": bw, "h": bh})

    # Green (Check / Call)
    green_mask = cv2.inRange(hsv, np.array([35, 80, 60]), np.array([85, 255, 255]))
    btn = _find_button_rect(green_mask)
    if btn:
        x, y, bw, bh = btn
        buttons.append({"action": "CHECK_CALL", "cx": x + bw // 2, "cy": bar_top + y + bh // 2, "w": bw, "h": bh})

    # Grey (Bet / Raise) — look for button-shaped grey regions
    grey_mask = cv2.inRange(hsv, np.array([0, 0, 30]), np.array([180, 50, 130]))
    contours, _ = cv2.findContours(grey_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        area = cv2.contourArea(c)
        if 400 < area < bar_w * bar_h * 0.3:
            x, y, bw, bh = cv2.boundingRect(c)
            if bw > 30 and bh > 10 and bw < bar_w * 0.5:
                buttons.append({"action": "BET_RAISE", "cx": x + bw // 2, "cy": bar_top + y + bh // 2, "w": bw, "h": bh})

    return buttons


def _find_button_rect(mask, min_area=300):
    """Find the largest contour in a mask. Returns (x,y,w,h) or None."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < min_area:
        return None
    return cv2.boundingRect(largest)


def is_hero_turn(frame):
    """
    Detect if it's hero's turn by looking for the green highlight glow
    around the hero seat panel (bottom center of screen).
    """
    h, w = frame.shape[:2]
    # Hero seat area: bottom 25%, center 50%
    roi = frame[int(h * 0.70):int(h * 0.90), int(w * 0.25):int(w * 0.75)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # The active seat has a bright green glow/border
    green = cv2.inRange(hsv, np.array([40, 100, 140]), np.array([80, 255, 255]))
    green_pct = cv2.countNonZero(green) / max(green.size, 1)
    return green_pct > 0.003  # >0.3% bright green pixels


def is_between_hands(frame):
    """
    Detect if we're between hands — no action buttons visible,
    and the "Deal" button or empty board area.
    """
    buttons = find_buttons(frame)
    return len(buttons) == 0


# ── Action Logger ────────────────────────────────────────────────────────

class ActionLogger:
    """Records detailed per-action data for humanness scoring."""

    def __init__(self):
        self.actions = []
        self.session_start = time.time()

    def record(self, action, think_time, move_time, hesitation,
               click_x, click_y, btn_cx, btn_cy, btn_action):
        self.actions.append({
            "timestamp": time.time(),
            "session_elapsed": time.time() - self.session_start,
            "action": btn_action,
            "think_time": round(think_time, 4),
            "move_time": round(move_time, 4),
            "hesitation": round(hesitation, 4),
            "total_time": round(think_time + move_time + hesitation, 4),
            "click_x": click_x,
            "click_y": click_y,
            "btn_center_x": btn_cx,
            "btn_center_y": btn_cy,
            "click_offset_x": click_x - btn_cx,
            "click_offset_y": click_y - btn_cy,
        })

    def save(self, path):
        import json
        with open(path, "w") as f:
            json.dump({
                "session_duration": time.time() - self.session_start,
                "total_actions": len(self.actions),
                "actions": self.actions,
            }, f, indent=2)


# ── Click Automation ─────────────────────────────────────────────────────

def click_button(button, win_rect, humanize=True, logger=None):
    """Click a detected button on screen. Returns timing data."""
    left, top, _, _ = win_rect
    btn_cx = left + button["cx"]
    btn_cy = top + button["cy"]
    screen_x = btn_cx
    screen_y = btn_cy

    if humanize:
        screen_x += random.randint(-6, 6)
        screen_y += random.randint(-3, 3)

        # Log-normal think time (median ~1s, can be 0.5-4s)
        think_time = random.lognormvariate(0, 0.5)
        think_time = max(0.4, min(think_time, 4.0))
        time.sleep(think_time)

        # Move mouse with slight duration variation
        move_time = random.uniform(0.06, 0.2)
        pyautogui.moveTo(screen_x, screen_y, duration=move_time)

        # Small pause before click (finger hesitation)
        hesitation = random.uniform(0.02, 0.08)
        time.sleep(hesitation)
        pyautogui.click()
    else:
        # Bot-like: consistent fast timing (detectable!)
        think_time = 0.05
        move_time = 0.0
        hesitation = 0.0
        time.sleep(think_time)
        pyautogui.click(screen_x, screen_y)

    if logger:
        logger.record(action=button["action"], think_time=think_time,
                      move_time=move_time, hesitation=hesitation,
                      click_x=screen_x, click_y=screen_y,
                      btn_cx=btn_cx, btn_cy=btn_cy, btn_action=button["action"])


# ── Strategy ─────────────────────────────────────────────────────────────

def choose_action(buttons):
    """
    Choose which button to click based on what's available.
    Pure check/call strategy for testing the loop.
    """
    by_action = {b["action"]: b for b in buttons}

    if "CHECK_CALL" in by_action:
        return by_action["CHECK_CALL"]
    if "FOLD" in by_action:
        return by_action["FOLD"]
    if buttons:
        return buttons[0]
    return None


# ── Main Loop ────────────────────────────────────────────────────────────

def run(args):
    print("=" * 50)
    print("  SCREEN BOT — Pure Pixel Reading")
    print("=" * 50)
    print("  No server access — screen only, like a real bot")
    print()

    # Find the browser window
    window = find_poker_window()
    if not window:
        print("  ERROR: Poker Lab window not found!")
        print("  Open http://localhost:9100 in a browser first.")
        return

    hwnd, rect = window
    title = win32gui.GetWindowText(hwnd).encode("ascii", "ignore").decode()
    print(f"  Window: {title}")
    print(f"  Rect: {rect}")
    print(f"  Humanize: {not args.instant}")
    print(f"  Max actions: {args.hands or 'unlimited'}")
    if args.save_frames:
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  Saving frames to: {FRAMES_DIR}")
    print()
    print("  Watching screen... (Ctrl+C to stop)")
    print()

    logger = ActionLogger()
    actions_taken = 0
    frames_captured = 0
    last_action_time = 0
    min_action_gap = 1.0  # don't click faster than 1/sec even in instant mode
    start_time = time.time()
    consecutive_idle = 0

    try:
        while args.hands == 0 or actions_taken < args.hands:
            # Refresh window rect (in case it was moved/resized)
            window = find_poker_window()
            if not window:
                print("  Window lost! Waiting...")
                time.sleep(2)
                continue
            _, rect = window

            # Capture the window
            frame = capture_window(rect)
            frames_captured += 1

            # Save frame if requested
            if args.save_frames and frames_captured % 20 == 0:
                cv2.imwrite(str(FRAMES_DIR / f"frame_{frames_captured:05d}.png"), frame)

            # Detect buttons
            buttons = find_buttons(frame)
            now = time.time()

            if buttons and (now - last_action_time) > min_action_gap:
                # If action buttons are visible, it's hero's turn
                # (the client hides them when it's not — same as PS)
                has_fold_or_check = any(b["action"] in ("FOLD", "CHECK_CALL") for b in buttons)

                if has_fold_or_check:
                    btn = choose_action(buttons)
                    if btn:
                        click_button(btn, rect, humanize=not args.instant, logger=logger)
                        actions_taken += 1
                        last_action_time = now
                        elapsed = now - start_time
                        print(f"  [{actions_taken}] {btn['action']} | {elapsed:.0f}s elapsed | {frames_captured} frames")
                        consecutive_idle = 0
                else:
                    consecutive_idle += 1
            else:
                consecutive_idle += 1

            # Periodic status
            if consecutive_idle > 0 and consecutive_idle % 50 == 0:
                elapsed = now - start_time
                print(f"  ... waiting ({actions_taken} actions, {elapsed:.0f}s, {frames_captured} frames)")

            # ~5 FPS capture rate
            time.sleep(0.2)

    except KeyboardInterrupt:
        pass

    elapsed = time.time() - start_time
    print(f"\n  Done: {actions_taken} actions in {elapsed:.0f}s ({frames_captured} frames)")

    # Save action log for humanness scoring
    log_path = VISION_DIR / "data" / "bot_action_log.json"
    logger.save(str(log_path))
    print(f"  Action log saved to {log_path}")


def main():
    parser = argparse.ArgumentParser(description="Screen-reading poker bot (pure pixel, no API)")
    parser.add_argument("--instant", action="store_true", help="Bot-like instant clicks (no humanization)")
    parser.add_argument("--hands", type=int, default=0, help="Stop after N actions (0=unlimited)")
    parser.add_argument("--save-frames", action="store_true", help="Save captured frames for analysis")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
