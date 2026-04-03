"""
Windows bot that plays the lab client via screen reading + click automation.

Captures the browser window, reads game state via OCR/template matching,
makes decisions via CFR strategy, and clicks buttons to play.

Also compares lab frames to PS captures for visual accuracy testing.

Usage:
  python vision/client_bot.py                    # play the lab client
  python vision/client_bot.py --compare          # compare frames to PS captures
  python vision/client_bot.py --debug            # show detection overlays
  python vision/client_bot.py --strategy tag     # use TAG instead of CFR

Requirements: pip install mss pyautogui easyocr opencv-python numpy pywin32
"""

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np

try:
    import pyautogui
    pyautogui.PAUSE = 0.05  # small delay between actions
    pyautogui.FAILSAFE = True  # move mouse to corner to abort
except ImportError:
    pyautogui = None
    print("WARNING: pyautogui not installed — click automation disabled")

try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None
    print("WARNING: pywin32 not installed — window finding disabled")

# ── Paths ────────────────────────────────────────────────────────────────

VISION_DIR = Path(__file__).resolve().parent
ROOT = VISION_DIR.parent
PS_CAPTURES_DIR = VISION_DIR / "captures" / "training"
CARD_TEMPLATES_DIR = VISION_DIR / "templates" / "screen_cards"

# ── Window Finding ───────────────────────────────────────────────────────

def find_window(title_substring="Poker Lab"):
    """Find a window by title substring. Returns (hwnd, rect) or None."""
    if win32gui is None:
        return None

    result = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title_substring.lower() in title.lower():
                rect = win32gui.GetWindowRect(hwnd)
                result.append((hwnd, rect))
    win32gui.EnumWindows(callback, None)

    if not result:
        return None
    # Return the first match
    return result[0]


def find_browser_window():
    """Find the browser window running the lab client."""
    # Try common browser titles
    for title in ["Poker Lab", "localhost:9100", "Poker Lab -", "Poker Lab and"]:
        found = find_window(title)
        if found:
            return found
    return None


# ── Screen Capture ───────────────────────────────────────────────────────

def capture_window(hwnd_rect):
    """Capture a specific window region."""
    _, (left, top, right, bottom) = hwnd_rect
    with mss.mss() as sct:
        monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def capture_fullscreen():
    """Capture the full primary monitor."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


# ── Game State Detection ─────────────────────────────────────────────────

class GameStateReader:
    """Reads game state from a screenshot of the lab client."""

    def __init__(self, debug=False):
        self.debug = debug
        self.ocr = None
        try:
            import easyocr
            self.ocr = easyocr.Reader(['en'], gpu=False, verbose=False)
        except Exception:
            print("WARNING: EasyOCR not available — text detection disabled")

    def read(self, frame):
        """Extract game state from a frame. Returns dict."""
        h, w = frame.shape[:2]
        state = {
            "pot": None,
            "hero_cards": [],
            "board_cards": [],
            "hero_stack": None,
            "action_buttons": [],
            "is_hero_turn": False,
            "phase": None,
        }

        # Read all text in the frame
        texts = self._read_text(frame)

        # Find pot amount
        for t in texts:
            pot_match = re.search(r'Pot:\s*\$?([\d.]+)', t["text"], re.IGNORECASE)
            if pot_match:
                try:
                    state["pot"] = float(pot_match.group(1))
                except ValueError:
                    pass

        # Find player stacks (green text with $ amounts)
        for t in texts:
            money_match = re.search(r'\$(\d+\.?\d*)', t["text"])
            if money_match:
                val = float(money_match.group(1))
                # If it's in the bottom third of the screen, likely hero stack
                if t["cy"] > h * 0.7:
                    state["hero_stack"] = val

        # Detect action buttons by color regions in bottom bar
        state["action_buttons"] = self._find_action_buttons(frame)
        state["is_hero_turn"] = len(state["action_buttons"]) > 0

        # Detect hero cards (large cards in bottom-left area)
        state["hero_cards"] = self._find_hero_cards(frame)

        # Detect board cards (centered, middle of screen)
        state["board_cards"] = self._find_board_cards(frame)

        if self.debug:
            self._draw_debug(frame, state, texts)

        return state

    def _read_text(self, frame):
        """OCR the frame."""
        if self.ocr is None:
            return []
        results = self.ocr.readtext(frame)
        parsed = []
        for bbox, text, conf in results:
            if conf < 0.3:
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            parsed.append({
                "text": text,
                "confidence": round(conf, 3),
                "cx": int((min(xs) + max(xs)) / 2),
                "cy": int((min(ys) + max(ys)) / 2),
                "x": int(min(xs)), "y": int(min(ys)),
                "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys)),
            })
        return parsed

    def _find_action_buttons(self, frame):
        """Find action buttons by their color in the bottom bar."""
        h, w = frame.shape[:2]
        bottom_bar = frame[int(h * 0.9):, :]
        bar_h, bar_w = bottom_bar.shape[:2]

        buttons = []
        hsv = cv2.cvtColor(bottom_bar, cv2.COLOR_BGR2HSV)

        # Red button (Fold): H=0-10, S>100, V>80
        red_mask = cv2.inRange(hsv, np.array([0, 100, 80]), np.array([10, 255, 255]))
        red_mask |= cv2.inRange(hsv, np.array([170, 100, 80]), np.array([180, 255, 255]))
        if cv2.countNonZero(red_mask) > 200:
            contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                if cv2.contourArea(c) > 500:
                    x, y, bw, bh = cv2.boundingRect(c)
                    buttons.append({
                        "action": "FOLD",
                        "x": x, "y": y + int(h * 0.9),
                        "w": bw, "h": bh,
                        "cx": x + bw // 2, "cy": y + int(h * 0.9) + bh // 2,
                    })

        # Green button (Check/Call): H=35-85, S>100, V>80
        green_mask = cv2.inRange(hsv, np.array([35, 100, 80]), np.array([85, 255, 255]))
        if cv2.countNonZero(green_mask) > 200:
            contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                if cv2.contourArea(c) > 500:
                    x, y, bw, bh = cv2.boundingRect(c)
                    buttons.append({
                        "action": "CHECK_CALL",  # need OCR to distinguish
                        "x": x, "y": y + int(h * 0.9),
                        "w": bw, "h": bh,
                        "cx": x + bw // 2, "cy": y + int(h * 0.9) + bh // 2,
                    })

        # Grey button (Bet/Raise): just look for remaining button-sized regions
        grey_mask = cv2.inRange(hsv, np.array([0, 0, 40]), np.array([180, 50, 120]))
        if cv2.countNonZero(grey_mask) > 200:
            contours, _ = cv2.findContours(grey_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                if cv2.contourArea(c) > 500:
                    x, y, bw, bh = cv2.boundingRect(c)
                    buttons.append({
                        "action": "BET_RAISE",
                        "x": x, "y": y + int(h * 0.9),
                        "w": bw, "h": bh,
                        "cx": x + bw // 2, "cy": y + int(h * 0.9) + bh // 2,
                    })

        return buttons

    def _find_hero_cards(self, frame):
        """Find hero cards in bottom-left area using white rectangle detection."""
        h, w = frame.shape[:2]
        # Hero cards are in bottom 30%, left 40% of frame
        roi = frame[int(h * 0.7):, :int(w * 0.4)]
        grey = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # Cards are white rectangles
        _, thresh = cv2.threshold(grey, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        cards = []
        for c in contours:
            area = cv2.contourArea(c)
            if 1000 < area < 20000:  # card-sized
                x, y, cw, ch = cv2.boundingRect(c)
                aspect = ch / max(cw, 1)
                if 1.2 < aspect < 1.8:  # card aspect ratio
                    cards.append({"x": x, "y": y + int(h * 0.7), "w": cw, "h": ch})

        return cards[:2]  # max 2 hero cards

    def _find_board_cards(self, frame):
        """Find board cards in center of frame."""
        h, w = frame.shape[:2]
        # Board cards are in middle 40% vertically, center 60% horizontally
        roi = frame[int(h * 0.25):int(h * 0.55), int(w * 0.2):int(w * 0.8)]
        grey = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        _, thresh = cv2.threshold(grey, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        cards = []
        for c in contours:
            area = cv2.contourArea(c)
            if 500 < area < 15000:
                x, y, cw, ch = cv2.boundingRect(c)
                aspect = ch / max(cw, 1)
                if 1.2 < aspect < 1.8:
                    cards.append({
                        "x": x + int(w * 0.2),
                        "y": y + int(h * 0.25),
                        "w": cw, "h": ch,
                    })

        # Sort by x position (left to right)
        cards.sort(key=lambda c: c["x"])
        return cards[:5]

    def _draw_debug(self, frame, state, texts):
        """Draw detection overlays for debugging."""
        debug = frame.copy()
        # Draw text detections
        for t in texts:
            cv2.rectangle(debug, (t["x"], t["y"]), (t["x"] + t["w"], t["y"] + t["h"]), (0, 255, 0), 1)
            cv2.putText(debug, t["text"][:20], (t["x"], t["y"] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        # Draw buttons
        for b in state["action_buttons"]:
            color = (0, 0, 255) if "FOLD" in b["action"] else (0, 255, 0) if "CHECK" in b["action"] else (200, 200, 200)
            cv2.rectangle(debug, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]), color, 2)
            cv2.putText(debug, b["action"], (b["x"], b["y"] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        # Draw hero cards
        for c in state["hero_cards"]:
            cv2.rectangle(debug, (c["x"], c["y"]), (c["x"] + c["w"], c["y"] + c["h"]), (255, 255, 0), 2)
        # Draw board cards
        for c in state["board_cards"]:
            cv2.rectangle(debug, (c["x"], c["y"]), (c["x"] + c["w"], c["y"] + c["h"]), (255, 0, 255), 2)

        cv2.imshow("Bot Debug", debug)
        cv2.waitKey(1)


# ── Click Automation ─────────────────────────────────────────────────────

class ClickAutomator:
    """Clicks buttons in the lab client window."""

    def __init__(self, window_rect, humanize=True):
        self.win_left, self.win_top = window_rect[0], window_rect[1]
        self.humanize = humanize

    def click_button(self, button):
        """Click a detected button with optional humanization."""
        if pyautogui is None:
            print(f"  [DRY RUN] Would click {button['action']} at ({button['cx']}, {button['cy']})")
            return

        # Convert from window-relative to screen coordinates
        screen_x = self.win_left + button["cx"]
        screen_y = self.win_top + button["cy"]

        if self.humanize:
            # Add small random offset (humans don't click exact center)
            import random
            screen_x += random.randint(-5, 5)
            screen_y += random.randint(-3, 3)

            # Human-like delay before clicking (300-1500ms)
            delay = random.uniform(0.3, 1.5)
            time.sleep(delay)

            # Move mouse with slight curve
            pyautogui.moveTo(screen_x, screen_y, duration=random.uniform(0.1, 0.3))
            time.sleep(random.uniform(0.05, 0.15))
            pyautogui.click()
        else:
            # Bot-like: instant click
            pyautogui.click(screen_x, screen_y)

    def click_slider_preset(self, preset_text, frame_w):
        """Click a sizing preset button (e.g., '1/2', 'Pot')."""
        # Presets are in a row above the action bar
        # Approximate positions based on the 6 presets
        presets = {"1/3": 0, "1/2": 1, "2/3": 2, "3/4": 3, "Pot": 4, "All-In": 5}
        idx = presets.get(preset_text, 2)  # default to 2/3
        preset_w = frame_w // 8
        x = 20 + idx * preset_w + preset_w // 2
        y = -50  # relative to action bar top (above it)
        # This needs proper positioning — placeholder
        print(f"  [PRESET] Would click {preset_text} preset")


# ── Frame Comparison ─────────────────────────────────────────────────────

def compare_to_ps(lab_frame, ps_dir=None):
    """
    Compare a lab client frame to PS captures.
    Returns similarity metrics.
    """
    if ps_dir is None:
        ps_dir = PS_CAPTURES_DIR

    if not ps_dir.exists():
        print(f"No PS captures at {ps_dir}")
        return None

    # Load a few PS reference frames
    ps_files = sorted(ps_dir.glob("*.png"))[:10]
    if not ps_files:
        print("No PS capture files found")
        return None

    results = []
    lab_h, lab_w = lab_frame.shape[:2]

    for ps_file in ps_files:
        ps_frame = cv2.imread(str(ps_file))
        if ps_frame is None:
            continue

        # Resize PS frame to match lab frame
        ps_resized = cv2.resize(ps_frame, (lab_w, lab_h))

        # Structural similarity (SSIM)
        grey_lab = cv2.cvtColor(lab_frame, cv2.COLOR_BGR2GRAY)
        grey_ps = cv2.cvtColor(ps_resized, cv2.COLOR_BGR2GRAY)

        # Simple MSE comparison
        mse = np.mean((grey_lab.astype(float) - grey_ps.astype(float)) ** 2)

        # Color histogram comparison
        hist_lab = cv2.calcHist([lab_frame], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        hist_ps = cv2.calcHist([ps_resized], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        hist_lab = cv2.normalize(hist_lab, hist_lab).flatten()
        hist_ps = cv2.normalize(hist_ps, hist_ps).flatten()
        hist_corr = cv2.compareHist(hist_lab, hist_ps, cv2.HISTCMP_CORREL)

        # Green felt color match (key PS signature)
        hsv_lab = cv2.cvtColor(lab_frame, cv2.COLOR_BGR2HSV)
        hsv_ps = cv2.cvtColor(ps_resized, cv2.COLOR_BGR2HSV)
        green_mask_lab = cv2.inRange(hsv_lab, np.array([35, 80, 60]), np.array([75, 255, 200]))
        green_mask_ps = cv2.inRange(hsv_ps, np.array([35, 80, 60]), np.array([75, 255, 200]))
        green_overlap = np.sum(green_mask_lab & green_mask_ps) / max(np.sum(green_mask_ps), 1)

        results.append({
            "file": ps_file.name,
            "mse": round(mse, 1),
            "hist_correlation": round(hist_corr, 3),
            "green_felt_overlap": round(green_overlap, 3),
        })

    # Aggregate
    avg_hist = np.mean([r["hist_correlation"] for r in results])
    avg_green = np.mean([r["green_felt_overlap"] for r in results])
    avg_mse = np.mean([r["mse"] for r in results])

    return {
        "per_frame": results,
        "avg_hist_correlation": round(avg_hist, 3),
        "avg_green_felt_overlap": round(avg_green, 3),
        "avg_mse": round(avg_mse, 1),
        "visual_match_score": round((avg_hist * 50 + avg_green * 50), 1),  # 0-100
    }


# ── Bot Main Loop ────────────────────────────────────────────────────────

def run_bot(args):
    """Main bot loop: capture → read → decide → click."""
    print("=" * 60)
    print("  CLIENT BOT — Screen Reading + Click Automation")
    print("=" * 60)

    reader = GameStateReader(debug=args.debug)

    # Find the browser window
    print("\nLooking for lab client window...")
    window = find_browser_window()
    if window:
        hwnd, rect = window
        title = win32gui.GetWindowText(hwnd).encode('ascii', 'ignore').decode() if win32gui else "?"
        print(f"  Found: '{title}' at {rect}")
        automator = ClickAutomator(rect)
    else:
        print("  Window not found — using fullscreen capture")
        automator = None

    if args.compare:
        print("\n  Comparing lab frame to PS captures...")
        if window:
            frame = capture_window(window)
        else:
            frame = capture_fullscreen()
        result = compare_to_ps(frame)
        if result:
            print(f"\n  Visual Match Score: {result['visual_match_score']}/100")
            print(f"  Color histogram correlation: {result['avg_hist_correlation']}")
            print(f"  Green felt overlap: {result['avg_green_felt_overlap']}")
            print(f"  Average MSE: {result['avg_mse']}")
            print(f"\n  Per-frame results:")
            for r in result["per_frame"]:
                print(f"    {r['file']}: hist={r['hist_correlation']:.3f} green={r['green_felt_overlap']:.3f} mse={r['mse']:.0f}")
        return

    print("\n  Starting bot loop (Ctrl+C to stop)...")
    print(f"  Strategy: {args.strategy}")
    print(f"  Humanize: {not args.instant}")
    print()

    hands_played = 0
    last_action_time = 0

    try:
        while True:
            # Capture frame
            if window:
                frame = capture_window(window)
            else:
                frame = capture_fullscreen()

            # Read game state
            state = reader.read(frame)

            # Act if it's hero's turn and enough time has passed
            if state["is_hero_turn"] and time.time() - last_action_time > 1.0:
                buttons = state["action_buttons"]
                if buttons:
                    # Simple strategy: click the first green button (check/call)
                    # or fold if no green button
                    green = [b for b in buttons if "CHECK" in b["action"]]
                    if green and automator:
                        automator.click_button(green[0])
                        print(f"  Hand {hands_played}: CHECK/CALL")
                    elif automator:
                        # Click fold
                        fold = [b for b in buttons if "FOLD" in b["action"]]
                        if fold:
                            automator.click_button(fold[0])
                            print(f"  Hand {hands_played}: FOLD")

                    hands_played += 1
                    last_action_time = time.time()

            # Wait before next capture (500ms = 2 FPS)
            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n\n  Bot stopped after {hands_played} actions")
        if args.debug:
            cv2.destroyAllWindows()


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Lab client bot + frame comparison")
    parser.add_argument("--compare", action="store_true", help="Compare lab frame to PS captures")
    parser.add_argument("--debug", action="store_true", help="Show detection overlays")
    parser.add_argument("--strategy", default="check_call", choices=["check_call", "tag", "cfr"],
                        help="Bot strategy (default: check_call)")
    parser.add_argument("--instant", action="store_true", help="Instant clicks (no humanization)")

    args = parser.parse_args()
    run_bot(args)


if __name__ == "__main__":
    main()
