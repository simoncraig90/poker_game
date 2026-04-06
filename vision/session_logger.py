"""
Session logger for Unibet WebSocket advisor.

Saves every hand to a JSONL file for post-session review.
Tracks: cards, board, actions, equity, CFR recommendation, result.
"""

import json
import os
import time


class SessionLogger:
    """Log hands to JSONL file."""

    def __init__(self, log_dir=None):
        if log_dir is None:
            log_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(log_dir, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"session_{timestamp}.jsonl")
        self.current_hand = None
        self.hand_id = None
        self.start_time = time.time()
        self.hands_logged = 0

        print(f"[Logger] Saving to {self.log_path}")

    def update(self, state, recommendation=None):
        """Update with new game state. Logs when hand completes."""
        hand_id = state.get("hand_id")
        hero = state.get("hero_cards", [])
        board = state.get("board_cards", [])
        phase = state.get("phase", "WAITING")
        facing = state.get("facing_bet", False)
        pot = state.get("pot", 0)
        position = state.get("position", "?")
        hero_stack = state.get("hero_stack", 0)

        if not hand_id or len(hero) < 2:
            # Hand ended or no cards — log previous hand if exists
            if self.current_hand and self.current_hand.get("hero"):
                self._write_hand()
            self.current_hand = None
            self.hand_id = None
            return

        # New hand
        if hand_id != self.hand_id:
            # Log previous hand
            if self.current_hand and self.current_hand.get("hero"):
                self._write_hand()

            self.hand_id = hand_id
            self.current_hand = {
                "hand_id": hand_id,
                "time": time.strftime("%H:%M:%S"),
                "timestamp": time.time(),
                "hero": hero[:],
                "position": position,
                "starting_stack": hero_stack,
                "streets": [],
            }

        # Update current hand with street data
        if self.current_hand:
            street_data = {
                "phase": phase,
                "board": board[:],
                "pot": pot,
                "facing_bet": facing,
                "call_amount": state.get("call_amount", 0),
                "stack": hero_stack,
            }

            if recommendation:
                street_data["rec_action"] = recommendation.get("action", "")
                street_data["rec_equity"] = recommendation.get("equity", 0)
                if recommendation.get("cfr_probs"):
                    street_data["cfr_probs"] = recommendation["cfr_probs"]

            # Only add if phase changed
            streets = self.current_hand["streets"]
            if not streets or streets[-1]["phase"] != phase:
                self.current_hand["streets"].append(street_data)
            else:
                # Update existing street with latest data
                streets[-1].update(street_data)

    def _write_hand(self):
        """Write completed hand to log file."""
        if not self.current_hand:
            return

        # Calculate profit (final stack - starting stack)
        streets = self.current_hand.get("streets", [])
        if streets:
            final_stack = streets[-1].get("stack", 0)
            start_stack = self.current_hand.get("starting_stack", 0)
            self.current_hand["profit_cents"] = final_stack - start_stack

        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(self.current_hand) + "\n")
            self.hands_logged += 1
        except Exception:
            pass

    def get_session_summary(self):
        """Get session summary stats."""
        elapsed = time.time() - self.start_time
        return {
            "hands": self.hands_logged,
            "duration_min": elapsed / 60,
            "log_file": self.log_path,
        }
