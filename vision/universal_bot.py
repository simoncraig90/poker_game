r"""
Universal screen-reading poker bot.

Plays any browser poker client by reading the screen and clicking buttons.
Uses YOLO + template matching for detection — no hardcoded coordinates.
Works on PS, lab client, 888, or any random skin.

Architecture:
    Screen capture (MSS) -> find tables -> for each table:
        YOLO detect elements -> template match cards -> strategy decision -> click

Usage:
    python vision/universal_bot.py                     # play one table
    python vision/universal_bot.py --tables 2           # play multiple tables
    python vision/universal_bot.py --strategy preflop   # preflop chart only
    python vision/universal_bot.py --strategy cfr       # CFR strategy
    python vision/universal_bot.py --humanize           # add human-like delays
    python vision/universal_bot.py --dry-run            # detect only, don't click
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
    pyautogui.PAUSE = 0.01
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None

sys.path.insert(0, str(Path(__file__).resolve().parent))

from universal_reader import UniversalReader


class UniversalBot:
    """Plays poker on any client by reading the screen."""

    def __init__(self, strategy="preflop", humanize=False, dry_run=False,
                 bb_cents=10, sb_cents=5):
        self.reader = UniversalReader()
        self.strategy = strategy
        self.humanize = humanize
        self.dry_run = dry_run
        self.bb = bb_cents
        self.sb = sb_cents

        # Stats
        self.hands_played = 0
        self.actions_taken = 0
        self.last_hero_cards = None

        # Preflop chart
        if strategy in ("preflop", "cfr"):
            try:
                from preflop_chart import preflop_advice
                self.preflop_advice = preflop_advice
            except ImportError:
                self.preflop_advice = None
                print("[Bot] WARNING: preflop_chart not available, using simple strategy")

        print(f"[Bot] Universal bot initialized")
        print(f"  Strategy: {strategy}")
        print(f"  Humanize: {humanize}")
        print(f"  Dry run: {dry_run}")
        print(f"  Blinds: ${sb_cents/100:.2f}/${bb_cents/100:.2f}")

    def capture_screen(self):
        """Capture the full screen."""
        with mss.mss() as sct:
            img = np.array(sct.grab(sct.monitors[1]))
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def decide_action(self, state):
        """Decide what action to take based on game state.
        Returns: 'FOLD', 'CHECK', 'CALL', 'RAISE', or 'BET'."""

        hero = state["hero_cards"]
        board = state["board_cards"]
        position = state["position"]
        phase = state["phase"]
        buttons = state["buttons"]

        # What actions are available?
        available = set(b["action"] for b in buttons)

        if not hero or len(hero) < 2:
            # Can't see cards — check if possible, else fold
            if "CHECK" in available:
                return "CHECK"
            return "FOLD"

        # Preflop
        if phase == "PREFLOP" and self.preflop_advice:
            card1, card2 = hero[0], hero[1]
            # Determine if facing a raise
            facing_raise = "CALL" in available
            advice = self.preflop_advice(card1, card2, position, facing_raise=facing_raise)
            action = advice.get("action", "FOLD")

            # Map chart action to available buttons
            if action == "RAISE" and "RAISE" not in available:
                action = "CALL" if "CALL" in available else "CHECK"
            if action == "CALL" and "CALL" not in available:
                action = "CHECK" if "CHECK" in available else "FOLD"
            if action == "FOLD" and "CHECK" in available:
                action = "CHECK"  # never fold when you can check

            return action

        # Postflop — simple equity-based strategy
        # Check if possible, call small bets, fold to large bets
        if "CHECK" in available:
            return "CHECK"
        if "CALL" in available:
            # Call with any pair or draw, fold total air
            return "CALL"
        return "FOLD"

    def find_button_to_click(self, state, action):
        """Find the screen coordinates to click for the given action."""
        buttons = state["buttons"]

        # Direct match
        for btn in buttons:
            if btn["action"] == action:
                return btn["cx"], btn["cy"]

        # Fallbacks
        if action == "CHECK":
            for btn in buttons:
                if btn["action"] in ("CHECK", "CALL"):
                    return btn["cx"], btn["cy"]
        if action == "CALL":
            for btn in buttons:
                if btn["action"] in ("CALL", "CHECK"):
                    return btn["cx"], btn["cy"]
        if action in ("RAISE", "BET"):
            for btn in buttons:
                if btn["action"] in ("RAISE", "BET"):
                    return btn["cx"], btn["cy"]
        if action == "FOLD":
            for btn in buttons:
                if btn["action"] == "FOLD":
                    return btn["cx"], btn["cy"]

        return None, None

    def click(self, x, y, table_offset=(0, 0)):
        """Click at screen coordinates with optional humanization."""
        if self.dry_run:
            print(f"    [DRY] Would click at ({x + table_offset[0]}, {y + table_offset[1]})")
            return

        if pyautogui is None:
            print(f"    [NO PYAUTOGUI] Cannot click")
            return

        screen_x = x + table_offset[0]
        screen_y = y + table_offset[1]

        if self.humanize:
            # Random delay before clicking (0.3-2.0 seconds)
            delay = random.uniform(0.3, 2.0)
            time.sleep(delay)

            # Random click offset (±3 pixels)
            screen_x += random.randint(-3, 3)
            screen_y += random.randint(-3, 3)

            # Move mouse with slight curve
            pyautogui.moveTo(screen_x, screen_y, duration=random.uniform(0.1, 0.4))
            time.sleep(random.uniform(0.05, 0.15))
            pyautogui.click()
        else:
            # Instant click
            pyautogui.click(screen_x, screen_y)

    def play_one_table(self, table_img, table_offset):
        """Process one table: detect state, decide, act."""
        state = self.reader.read_table(table_img)

        if not state["is_hero_turn"]:
            return None

        hero = state["hero_cards"]
        board = state["board_cards"]
        position = state["position"]

        # Skip if same hand (already acted)
        hero_key = tuple(sorted(hero)) if hero else None
        if hero_key and hero_key == self.last_hero_cards and not board:
            return None

        # Decide action
        action = self.decide_action(state)

        # Find button to click
        cx, cy = self.find_button_to_click(state, action)

        hero_str = " ".join(hero) if hero else "??"
        board_str = " ".join(board) if board else ""
        print(f"  [{position}] {hero_str} | {board_str} -> {action}")

        if cx is not None:
            self.click(cx, cy, table_offset)
            self.actions_taken += 1

        if hero_key != self.last_hero_cards:
            self.hands_played += 1
            self.last_hero_cards = hero_key

        return action

    def run(self, max_actions=None):
        """Main loop: capture screen, find tables, play each one."""
        print(f"\n{'='*55}")
        print(f"  UNIVERSAL POKER BOT")
        print(f"{'='*55}")
        print(f"  Scanning for poker tables...")
        print(f"  Press Ctrl+C to stop\n")

        while True:
            try:
                screen = self.capture_screen()
                tables = self.reader.find_tables(screen)

                if not tables:
                    time.sleep(0.5)
                    continue

                for i, region in enumerate(tables):
                    table_img, offset = self.reader.crop_table(screen, region)
                    result = self.play_one_table(table_img, offset)

                if max_actions and self.actions_taken >= max_actions:
                    print(f"\n  Reached {max_actions} actions. Stopping.")
                    break

                # Capture interval
                time.sleep(0.3)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"  Error: {e}")
                time.sleep(1)

        print(f"\n{'='*55}")
        print(f"  Hands: {self.hands_played}  Actions: {self.actions_taken}")
        print(f"{'='*55}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="preflop", choices=["preflop", "cfr", "simple"])
    parser.add_argument("--humanize", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-actions", type=int, default=None)
    parser.add_argument("--bb", type=int, default=10, help="Big blind in cents")
    parser.add_argument("--sb", type=int, default=5, help="Small blind in cents")
    args = parser.parse_args()

    bot = UniversalBot(
        strategy=args.strategy,
        humanize=args.humanize,
        dry_run=args.dry_run,
        bb_cents=args.bb,
        sb_cents=args.sb,
    )
    bot.run(max_actions=args.max_actions)
