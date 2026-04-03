"""
Multi-table screen-reading bot.

Finds all Poker Lab browser windows and plays them simultaneously,
like a real multi-tabling bot on PokerStars.

Each table is tracked independently. The bot prioritizes tables
where it's hero's turn, acting on the most urgent first.

Usage:
  python vision/multi_table_bot.py                    # play all visible tables
  python vision/multi_table_bot.py --instant           # no humanization
  python vision/multi_table_bot.py --max-actions 100   # stop after N total actions
"""

import argparse
import json
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
    print("ERROR: pip install pyautogui"); sys.exit(1)

try:
    import win32gui
    import win32con
except ImportError:
    print("ERROR: pip install pywin32"); sys.exit(1)

VISION_DIR = Path(__file__).resolve().parent


# ── Reuse detection from screen_bot ──────────────────────────────────────

sys.path.insert(0, str(VISION_DIR))
from screen_bot import find_buttons, click_button, choose_action, ActionLogger


# ── Multi-Window Management ──────────────────────────────────────────────

def find_all_poker_windows():
    """Find all Poker Lab browser windows. Returns list of (hwnd, rect, title)."""
    candidates = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "Poker Lab" in title or "localhost:9100" in title:
                rect = win32gui.GetWindowRect(hwnd)
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                if w > 300 and h > 400:  # real windows only
                    candidates.append((hwnd, rect, title, w * h))
    win32gui.EnumWindows(cb, None)

    if not candidates:
        return []

    # Deduplicate overlapping windows — keep the largest per overlap group
    # Sort by area descending, then skip windows that overlap >80% with a kept one
    candidates.sort(key=lambda x: -x[3])
    kept = []
    for hwnd, rect, title, area in candidates:
        overlap = False
        for _, krect, _, _ in kept:
            # Check overlap
            ox = max(0, min(rect[2], krect[2]) - max(rect[0], krect[0]))
            oy = max(0, min(rect[3], krect[3]) - max(rect[1], krect[1]))
            overlap_area = ox * oy
            if overlap_area > area * 0.5:  # >50% overlap = same window
                overlap = True
                break
        if not overlap:
            kept.append((hwnd, rect, title, area))

    return [(hwnd, rect, title) for hwnd, rect, title, _ in kept]


def capture_window(rect):
    """Capture a window region."""
    left, top, right, bottom = rect
    with mss.mss() as sct:
        img = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


# ── Table State Tracker ──────────────────────────────────────────────────

class TableTracker:
    """Tracks state for one table window."""

    def __init__(self, hwnd, rect, title, table_id):
        self.hwnd = hwnd
        self.rect = rect
        self.title = title
        self.table_id = table_id
        self.logger = ActionLogger()
        self.actions_taken = 0
        self.last_action_time = 0
        self.last_had_buttons = False

    def needs_action(self):
        """Check if enough time has passed since last action."""
        return time.time() - self.last_action_time > 1.0

    def update_rect(self):
        """Refresh window position (in case it moved)."""
        try:
            self.rect = win32gui.GetWindowRect(self.hwnd)
        except Exception:
            pass


# ── Main Loop ────────────────────────────────────────────────────────────

def run(args):
    print("=" * 55)
    print("  MULTI-TABLE SCREEN BOT")
    print("=" * 55)
    print("  Pure pixel reading — no server API")
    print()

    # Find all table windows
    windows = find_all_poker_windows()
    if not windows:
        print("  No Poker Lab windows found!")
        print("  Open tables at http://localhost:9100?table=1 etc.")
        return

    # Create trackers
    tables = []
    for i, (hwnd, rect, title) in enumerate(windows):
        clean_title = title.encode("ascii", "ignore").decode()[:40]
        tracker = TableTracker(hwnd, rect, clean_title, i + 1)
        tables.append(tracker)
        print(f"  Table {i + 1}: {clean_title} @ {rect}")

    print(f"\n  {len(tables)} tables found")
    print(f"  Humanize: {not args.instant}")
    print(f"  Max actions: {args.max_actions or 'unlimited'}")
    print()
    print("  Playing... (Ctrl+C to stop)")
    print()

    total_actions = 0
    start_time = time.time()
    scan_cycle = 0

    try:
        while args.max_actions == 0 or total_actions < args.max_actions:
            scan_cycle += 1

            # Rescan for new/closed windows periodically
            if scan_cycle % 50 == 0:
                windows = find_all_poker_windows()
                existing_hwnds = {t.hwnd for t in tables}
                for hwnd, rect, title in windows:
                    if hwnd not in existing_hwnds:
                        clean_title = title.encode("ascii", "ignore").decode()[:40]
                        tracker = TableTracker(hwnd, rect, clean_title, len(tables) + 1)
                        tables.append(tracker)
                        print(f"  + New table detected: {clean_title}")
                # Remove closed windows
                tables = [t for t in tables if win32gui.IsWindow(t.hwnd)]

            # Scan all tables, find ones needing action
            actionable = []
            for tracker in tables:
                if not tracker.needs_action():
                    continue

                tracker.update_rect()
                try:
                    frame = capture_window(tracker.rect)
                except Exception:
                    continue

                buttons = find_buttons(frame)
                has_fold_or_check = any(b["action"] in ("FOLD", "CHECK_CALL") for b in buttons)

                if has_fold_or_check:
                    actionable.append((tracker, buttons, frame))

            # Act on the first table that needs it (could prioritize by timer)
            if actionable:
                # Pick the table that's been waiting longest
                actionable.sort(key=lambda x: x[0].last_action_time)
                tracker, buttons, frame = actionable[0]

                # Bring window to front before clicking
                try:
                    win32gui.SetForegroundWindow(tracker.hwnd)
                    time.sleep(0.1)
                except Exception:
                    pass

                btn = choose_action(buttons)
                if btn:
                    click_button(btn, tracker.rect, humanize=not args.instant, logger=tracker.logger)
                    tracker.actions_taken += 1
                    tracker.last_action_time = time.time()
                    total_actions += 1
                    elapsed = time.time() - start_time
                    print(f"  [{total_actions}] Table {tracker.table_id}: {btn['action']} | {elapsed:.0f}s")

            # Scan rate: ~5 FPS across all tables
            time.sleep(0.2)

    except KeyboardInterrupt:
        pass

    elapsed = time.time() - start_time
    print(f"\n  Done: {total_actions} total actions across {len(tables)} tables in {elapsed:.0f}s")

    # Save per-table logs
    for tracker in tables:
        if tracker.actions_taken > 0:
            log_path = VISION_DIR / "data" / f"bot_action_log_table{tracker.table_id}.json"
            tracker.logger.save(str(log_path))
            print(f"  Table {tracker.table_id}: {tracker.actions_taken} actions -> {log_path}")

    # Save combined log
    combined = {
        "session_duration": elapsed,
        "tables": len(tables),
        "total_actions": total_actions,
        "per_table": [],
    }
    for tracker in tables:
        combined["per_table"].append({
            "table_id": tracker.table_id,
            "actions": tracker.actions_taken,
            "log": tracker.logger.actions,
        })
    combined_path = VISION_DIR / "data" / "bot_action_log_multitable.json"
    with open(combined_path, "w") as f:
        json.dump(combined, f, indent=2)
    print(f"  Combined log -> {combined_path}")


def main():
    parser = argparse.ArgumentParser(description="Multi-table screen-reading bot")
    parser.add_argument("--instant", action="store_true", help="No humanization")
    parser.add_argument("--max-actions", type=int, default=0, help="Stop after N total actions (0=unlimited)")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
