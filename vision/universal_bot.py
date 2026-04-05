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
import json


class TableState:
    """Tracks state for one table across frames."""

    def __init__(self, table_id):
        self.table_id = table_id
        self.hero_cards = None
        self.board_cards = []
        self.phase = "PREFLOP"
        self.acted_this_street = False
        self.hand_actions = []      # list of (phase, action) this hand
        self.opponent_actions = []  # tracked opponent actions this hand
        self.hand_number = 0

    def update(self, state):
        """Update with new frame state. Returns True if new hand detected."""
        hero = tuple(sorted(state["hero_cards"])) if state["hero_cards"] else None
        board = tuple(state["board_cards"]) if state["board_cards"] else ()
        phase = state["phase"]

        new_hand = False
        if hero and hero != self.hero_cards:
            # New cards = new hand
            self.hero_cards = hero
            self.board_cards = []
            self.phase = "PREFLOP"
            self.acted_this_street = False
            self.hand_actions = []
            self.opponent_actions = []
            self.hand_number += 1
            new_hand = True

        if phase != self.phase:
            # New street
            self.phase = phase
            self.acted_this_street = False
            self.board_cards = list(board)

        return new_hand

    def record_action(self, action):
        """Record an action taken by the bot."""
        self.hand_actions.append((self.phase, action))
        self.acted_this_street = True

    def should_act(self):
        """Return True if we haven't acted this street yet."""
        return not self.acted_this_street


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

        # Per-table state tracking
        self.table_states = {}  # table_id -> TableState

        # Hand history
        self.hand_log = []

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
        pot = state.get("pot")
        call_amount = state.get("call_amount")
        facing_bet = state.get("facing_bet", False)

        # What actions are available?
        available = set(b["action"] for b in buttons)

        if not hero or len(hero) < 2:
            if "CHECK" in available:
                return "CHECK"
            return "FOLD"

        # Preflop
        if phase == "PREFLOP" and self.preflop_advice:
            card1, card2 = hero[0], hero[1]
            advice = self.preflop_advice(card1, card2, position, facing_raise=facing_bet)
            action = advice.get("action", "FOLD")

            if action == "RAISE" and "RAISE" not in available:
                action = "CALL" if "CALL" in available else "CHECK"
            if action == "CALL" and "CALL" not in available:
                action = "CHECK" if "CHECK" in available else "FOLD"
            if action == "FOLD" and "CHECK" in available:
                action = "CHECK"

            return action

        # Postflop — equity-based decisions
        return self._postflop_decision(hero, board, available, pot, call_amount)

    def _postflop_decision(self, hero, board, available, pot, call_amount):
        """Postflop strategy using equity estimation and pot odds."""

        # Try to use equity model
        equity = self._estimate_equity(hero, board)

        # Pot odds calculation
        pot_odds = 0
        if pot and call_amount and call_amount > 0:
            pot_odds = call_amount / (pot + call_amount)

        # Hand strength categories
        if equity >= 0.70:
            # Strong hand — raise/bet if possible
            if "RAISE" in available:
                return "RAISE"
            if "BET" in available:
                return "BET"
            if "CALL" in available:
                return "CALL"
            return "CHECK"

        elif equity >= 0.50:
            # Medium hand — bet for value, call reasonable bets
            if not call_amount or call_amount == 0:
                # Not facing a bet
                if "BET" in available:
                    return "BET"
                return "CHECK"
            else:
                # Facing a bet — call if pot odds are good
                if equity > pot_odds:
                    return "CALL"
                return "FOLD"

        elif equity >= 0.30:
            # Drawing hand — check/call with good odds
            if "CHECK" in available:
                return "CHECK"
            if equity > pot_odds and "CALL" in available:
                return "CALL"
            return "FOLD"

        else:
            # Weak hand — check or fold
            if "CHECK" in available:
                return "CHECK"
            return "FOLD"

    def _estimate_equity(self, hero, board):
        """Estimate hand equity. Uses equity model if available, otherwise simple heuristic."""
        try:
            from train_equity import EquityModel
            # TODO: integrate equity NN for accurate estimates
            pass
        except ImportError:
            pass

        # Simple heuristic based on hand type
        if not hero or len(hero) < 2:
            return 0.30

        r1, r2 = hero[0][0], hero[1][0]
        s1, s2 = hero[0][1], hero[1][1]
        rank_vals = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'T':10,'J':11,'Q':12,'K':13,'A':14}
        v1, v2 = rank_vals.get(r1, 5), rank_vals.get(r2, 5)
        suited = s1 == s2

        if not board:
            # Preflop equity approximation
            high = max(v1, v2)
            low = min(v1, v2)
            eq = 0.30 + high * 0.02 + (0.04 if v1 == v2 else 0) + (0.03 if suited else 0)
            return min(0.85, eq)

        # Postflop — check if we hit the board
        board_ranks = [c[0] for c in board]
        paired = r1 in board_ranks or r2 in board_ranks
        two_pair = r1 in board_ranks and r2 in board_ranks
        overpair = v1 == v2 and v1 > max(rank_vals.get(r, 2) for r in board_ranks)

        if two_pair or overpair:
            return 0.75
        elif paired:
            return 0.55
        elif max(v1, v2) >= 14:  # Ace high
            return 0.40
        else:
            return 0.25

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

    def play_one_table(self, table_img, table_offset, table_id=0):
        """Process one table: detect state, decide, act."""
        state = self.reader.read_table(table_img)

        if not state["is_hero_turn"]:
            return None

        # Get or create table state tracker
        if table_id not in self.table_states:
            self.table_states[table_id] = TableState(table_id)
        ts = self.table_states[table_id]

        # Update state tracker
        new_hand = ts.update(state)
        if new_hand:
            self.hands_played += 1

        # Skip if already acted this street
        if not ts.should_act():
            return None

        hero = state["hero_cards"]
        board = state["board_cards"]
        position = state["position"]
        pot = state.get("pot")

        # Decide action
        action = self.decide_action(state)

        # Find button to click
        cx, cy = self.find_button_to_click(state, action)

        hero_str = " ".join(hero) if hero else "??"
        board_str = " ".join(board) if board else ""
        pot_str = f"pot=${pot/100:.2f}" if pot else ""
        print(f"  [{position}] {hero_str} | {board_str} {pot_str} -> {action}")

        if cx is not None:
            # Handle bet sizing for raise/bet actions
            if action in ("RAISE", "BET") and pot:
                self._set_bet_amount(state, pot, table_offset)

            self.click(cx, cy, table_offset)
            self.actions_taken += 1
            ts.record_action(action)

            # Log hand
            self.hand_log.append({
                "table": table_id,
                "hand": ts.hand_number,
                "phase": state["phase"],
                "hero": hero,
                "board": board,
                "position": position,
                "action": action,
                "pot": pot,
                "call_amount": state.get("call_amount"),
                "timestamp": time.time(),
            })

        return action

    def _set_bet_amount(self, state, pot, table_offset):
        """Set the raise/bet amount before clicking the button.
        Looks for preset buttons (Pot, 1/2, 3BB) or the bet input field."""
        if self.dry_run:
            return

        # Try to find sizing preset buttons via color detection
        # PS has Max/Pot/3BB buttons on the right side
        # For now, just use the default (min raise) which is what clicking Raise gives
        # TODO: detect and click Pot/Half-Pot preset buttons for better sizing
        pass

    def _check_sit_in(self, screen):
        """Check if we need to sit in or click 'I'm Back'."""
        if self.dry_run:
            return

        # Look for "I'm Back" or "Sit Down" buttons
        # These are typically green buttons with specific text
        # For now, detect via color (bright green/teal button in the lower area)
        h, w = screen.shape[:2]
        bottom = screen[int(h * 0.80):, :]
        hsv = cv2.cvtColor(bottom, cv2.COLOR_BGR2HSV)

        # Bright teal/green button (PS "I'm Back" style)
        teal = cv2.inRange(hsv, np.array([75, 100, 150]), np.array([95, 255, 255]))
        contours, _ = cv2.findContours(teal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area > 1000:
                x, y, bw, bh = cv2.boundingRect(c)
                cx = x + bw // 2
                cy = int(h * 0.80) + y + bh // 2
                print(f"  [SIT-IN] Clicking I'm Back at ({cx}, {cy})")
                if pyautogui:
                    pyautogui.click(cx, cy)
                return True
        return False

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
                    # Check if we need to sit in
                    self._check_sit_in(screen)
                    time.sleep(0.5)
                    continue

                for i, region in enumerate(tables):
                    table_img, offset = self.reader.crop_table(screen, region)
                    result = self.play_one_table(table_img, offset, table_id=i)

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

        # Save hand history
        if self.hand_log:
            log_path = os.path.join(Path(__file__).resolve().parent.parent, "hands", "bot_history.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a") as f:
                for entry in self.hand_log:
                    f.write(json.dumps(entry) + "\n")
            print(f"\n  Hand history saved: {log_path} ({len(self.hand_log)} entries)")

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
