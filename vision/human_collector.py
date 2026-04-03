"""
Human baseline data collector for bot detection calibration.

Runs alongside the advisor during live PokerStars sessions. Watches
for hero actions via screen state transitions and records:
  - Decision timing (action prompt → action taken)
  - Bet sizes as pot fractions
  - Action distribution by position and street
  - Session stat drift (VPIP/PFR/AF over rolling windows)
  - Post-loss behavior changes (tilt signal)

Output: vision/data/human_baseline.jsonl (one decision per line)
        vision/data/human_profile.json (aggregate detection profile)

Usage:
  python vision/human_collector.py                # default: watch screen
  python vision/human_collector.py --table 1      # target specific table
  python vision/human_collector.py --debug        # show detection details
"""

import json
import math
import os
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np

VISION_DIR = Path(__file__).resolve().parent
ROOT = VISION_DIR.parent

sys.path.insert(0, str(VISION_DIR))
from advisor import find_table_region, crop_table

# ── Paths ────────────────────────────────────────────────────────────────

DATA_DIR = VISION_DIR / "data"
BASELINE_PATH = DATA_DIR / "human_baseline.jsonl"
PROFILE_PATH = DATA_DIR / "human_profile.json"

# ── Detection signals (matching bot-detector.js feature names) ───────────

COMMON_FRACTIONS = [0.25, 0.33, 0.5, 0.66, 0.67, 0.75, 1.0, 1.5, 2.0]
FRACTION_TOLERANCE = 0.02


class HumanCollector:
    def __init__(self, debug=False, table_id=None):
        self.debug = debug
        self.table_id = table_id
        self.window_rect = None

        # Load YOLO + card_id (shared with advisor)
        self.yolo_model = None
        self.yolo_detect = None
        self.card_identify = None
        self._load_vision()

        # State tracking
        self.prev_hero_turn = False
        self.prev_hero_cards = []
        self.prev_board = []
        self.prev_pot = None
        self.action_prompt_time = None  # when hero was prompted to act
        self.current_phase = "PREFLOP"
        self.current_position = "IP"

        # Per-hand tracking
        self.hand_start_time = None
        self.hand_decisions = []  # decisions in current hand
        self.prev_stack_estimate = None

        # Session data
        self.decisions = []       # all recorded decisions
        self.hand_results = []    # per-hand profit tracking
        self.hands_played = 0
        self.session_start = time.time()

        # Rolling window stats (500-hand blocks, matching bot-detector)
        self.session_blocks = []
        self.current_block = {"hands": 0, "vpip": 0, "pfr": 0,
                              "bets": 0, "raises": 0, "calls": 0,
                              "folds": 0, "checks": 0}

    def _load_vision(self):
        try:
            from yolo_detect import load_model, detect_elements
            model = load_model()
            if model is not None:
                self.yolo_model = model
                self.yolo_detect = detect_elements
                print("[Collector] YOLO model loaded")
        except Exception as e:
            print(f"[Collector] YOLO not available: {e}")
            sys.exit(1)

        try:
            from card_id import identify_cards as _id
            self.card_identify = _id
            print("[Collector] Card ID loaded")
        except Exception as e:
            print(f"[Collector] Card ID not available: {e}")

        if self.table_id is not None:
            try:
                from advisor import find_poker_window_by_table
                self.window_rect = find_poker_window_by_table(self.table_id)
                if self.window_rect:
                    print(f"[Collector] Targeting table {self.table_id}")
            except Exception:
                pass

    def _capture_table(self):
        """Capture screen and crop to the poker table region."""
        with mss.mss() as sct:
            if self.window_rect:
                x, y, w, h = self.window_rect
                monitor = {"left": x, "top": y, "width": w, "height": h}
            else:
                monitor = sct.monitors[1]
            img = np.array(sct.grab(monitor))
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        # Find and crop to table (same as advisor)
        region = find_table_region(frame)
        if region is None:
            return None
        table_img, _ = crop_table(frame, region)
        return table_img

    def _extract_state(self, img):
        """Extract game state — simplified version of advisor._extract_state."""
        if self.yolo_detect is None:
            return None

        elements = self.yolo_detect(img, conf=0.4)
        if elements is None:
            return None

        # Identify cards
        hero_cards = []
        board_cards = []
        h_img, w_img = img.shape[:2]

        for card_list, key in [(hero_cards, "hero_card"), (board_cards, "board_card")]:
            for card in elements.get(key, []):
                if hasattr(self, '_templates') and self._templates:
                    # Template match
                    x1 = max(0, card["x"] - 2)
                    y1 = max(0, card["y"] - 2)
                    x2 = min(w_img, card["x"] + card["w"] + 2)
                    y2 = min(h_img, card["y"] + card["h"] + 2)
                    crop = img[y1:y2, x1:x2]
                    best_label, best_score = "??", 0
                    for label, tmpl in self._templates.items():
                        resized = cv2.resize(tmpl, (crop.shape[1], crop.shape[0]))
                        score = cv2.matchTemplate(crop, resized, cv2.TM_CCOEFF_NORMED)[0][0]
                        if score > best_score:
                            best_score = score
                            best_label = label
                    if best_score > 0.3:
                        card_list.append(best_label)
                elif self.card_identify:
                    try:
                        ids = self.card_identify(img, [card])
                        if ids:
                            card_list.append(ids[0][0])
                    except Exception:
                        pass

        # Load templates on first use
        if not hasattr(self, '_templates'):
            self._templates = {}
            tmpl_dir = VISION_DIR / "templates" / "screen_cards"
            if tmpl_dir.is_dir():
                for f in tmpl_dir.iterdir():
                    if f.suffix == ".png":
                        self._templates[f.stem] = cv2.imread(str(f))

        # Hero turn detection
        hero_turn = len(elements.get("action_button", [])) > 0
        facing_bet = False
        bottom = img[int(h_img * 0.85):, :]
        hsv = cv2.cvtColor(bottom, cv2.COLOR_BGR2HSV)
        red1 = cv2.inRange(hsv, np.array([0, 80, 80]), np.array([10, 255, 255]))
        red2 = cv2.inRange(hsv, np.array([160, 80, 80]), np.array([180, 255, 255]))
        green = cv2.inRange(hsv, np.array([35, 80, 80]), np.array([85, 255, 255]))
        red_px = cv2.countNonZero(red1) + cv2.countNonZero(red2)
        green_px = cv2.countNonZero(green)
        if red_px > 200 or green_px > 200:
            if not hero_turn:
                hero_turn = True
            facing_bet = red_px > 200

        # Pot reading
        pot = None
        if elements.get("pot_text"):
            try:
                from detect import read_text_regions, find_pot
                pt = elements["pot_text"][0]
                crop = img[
                    max(0, pt["y"] - 2):min(h_img, pt["y"] + pt["h"] + 2),
                    max(0, pt["x"] - 2):min(w_img, pt["x"] + pt["w"] + 2),
                ]
                texts = read_text_regions(crop)
                pot_info = find_pot(texts, pt["h"])
                if pot_info and "amount" in pot_info:
                    pot = pot_info["amount"]
            except Exception:
                pass

        # Position from dealer button
        position = "IP"
        for btn in elements.get("dealer_button", []):
            btn_y_pct = btn["y"] / h_img if h_img > 0 else 0
            if btn_y_pct < 0.65:
                position = "OOP"

        # Phase from board count
        n_board = len(board_cards)
        if n_board == 0:
            phase = "PREFLOP"
        elif n_board == 3:
            phase = "FLOP"
        elif n_board == 4:
            phase = "TURN"
        else:
            phase = "RIVER"

        return {
            "hero_cards": hero_cards,
            "board_cards": board_cards,
            "hero_turn": hero_turn,
            "facing_bet": facing_bet,
            "pot": pot,
            "position": position,
            "phase": phase,
        }

    def _infer_action(self, old_state, new_state):
        """Infer what action the hero took from state transition."""
        old_pot = old_state.get("pot")
        new_pot = new_state.get("pot")
        was_facing = old_state.get("facing_bet", False)

        # Hero had turn, now doesn't → action was taken
        if not was_facing:
            # Wasn't facing a bet: CHECK or BET
            if old_pot and new_pot and new_pot > old_pot:
                return "BET", new_pot - old_pot
            return "CHECK", 0
        else:
            # Was facing a bet: FOLD, CALL, or RAISE
            hero_cards = new_state.get("hero_cards", [])
            old_hero = old_state.get("hero_cards", [])

            # If hero cards disappeared, folded
            if len(old_hero) >= 2 and len(hero_cards) == 0:
                return "FOLD", 0

            # If pot grew significantly more than a call, it's a raise
            if old_pot and new_pot and new_pot > old_pot:
                pot_increase = new_pot - old_pot
                # Rough heuristic: if increase > 2x the call amount, likely raise
                return "CALL", pot_increase  # hard to distinguish call vs raise from pot alone

            return "CALL", 0

    def _record_decision(self, action, amount, pot, phase, position, response_time_ms):
        """Record a single decision."""
        pot_fraction = amount / pot if pot and pot > 0 and amount > 0 else 0
        is_exact = any(abs(pot_fraction - cf) < FRACTION_TOLERANCE for cf in COMMON_FRACTIONS) if pot_fraction > 0 else False

        decision = {
            "timestamp": time.time(),
            "action": action,
            "amount": amount,
            "pot": pot,
            "pot_fraction": round(pot_fraction, 4),
            "is_exact_fraction": is_exact,
            "phase": phase,
            "position": position,
            "response_time_ms": round(response_time_ms),
            "hand_number": self.hands_played,
        }

        self.decisions.append(decision)
        self.hand_decisions.append(decision)

        # Update rolling block
        blk = self.current_block
        if phase == "PREFLOP":
            if action in ("CALL", "RAISE", "BET"):
                blk["vpip"] += 1
            if action == "RAISE":
                blk["pfr"] += 1
        if action in ("BET", "RAISE"):
            blk["bets"] += 1
        elif action == "CALL":
            blk["calls"] += 1
        elif action == "FOLD":
            blk["folds"] += 1
        elif action == "CHECK":
            blk["checks"] += 1

        # Write to log
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(BASELINE_PATH, "a") as f:
            f.write(json.dumps(decision) + "\n")

        if self.debug:
            frac_str = f" ({pot_fraction:.0%} pot)" if pot_fraction > 0 else ""
            exact_str = " [EXACT]" if is_exact else ""
            print(f"  >> {action}{frac_str}{exact_str} in {response_time_ms:.0f}ms ({phase}, {position})")

    def _end_hand(self):
        """Called when a hand ends (hero cards disappear)."""
        self.hands_played += 1
        self.current_block["hands"] += 1

        # Save block every 50 hands (smaller window for live play)
        if self.current_block["hands"] >= 50:
            self.session_blocks.append(dict(self.current_block))
            self.current_block = {"hands": 0, "vpip": 0, "pfr": 0,
                                  "bets": 0, "raises": 0, "calls": 0,
                                  "folds": 0, "checks": 0}

        self.hand_decisions = []
        self.hand_start_time = None

    def _compute_profile(self):
        """Compute detection profile from collected data (same format as bot-detector.js)."""
        d = self.decisions
        if len(d) < 20:
            return None

        profile = {}

        # Bet sizing
        bets = [x for x in d if x["action"] in ("BET", "RAISE") and x["pot_fraction"] > 0]
        if bets:
            fracs = [x["pot_fraction"] for x in bets]
            exact_count = sum(1 for x in bets if x["is_exact_fraction"])
            profile["betSizePrecision"] = exact_count / len(bets)

            # Distinct sizes (round to 5-unit buckets like bot-detector.js)
            size_buckets = set(round(x["amount"] / 5) * 5 for x in bets if x["amount"] > 0)
            profile["distinctBetSizes"] = len(size_buckets)

            # Entropy of bet size distribution
            from collections import Counter
            bucket_counts = Counter(round(x["amount"] / 5) * 5 for x in bets if x["amount"] > 0)
            total = sum(bucket_counts.values())
            entropy = 0
            for c in bucket_counts.values():
                p = c / total
                if p > 0:
                    entropy -= p * math.log2(p)
            profile["betSizeEntropy"] = round(entropy, 4)
        else:
            profile["betSizePrecision"] = 0
            profile["distinctBetSizes"] = 0
            profile["betSizeEntropy"] = 0

        # Action distribution
        total_actions = len(d)
        for act in ["fold", "check", "call", "bet", "raise"]:
            count = sum(1 for x in d if x["action"] == act.upper())
            profile[f"{act}Pct"] = round(count / total_actions, 4)

        calls = sum(1 for x in d if x["action"] == "CALL")
        agg = sum(1 for x in d if x["action"] in ("BET", "RAISE"))
        profile["aggressionFactor"] = round(agg / max(1, calls), 4)

        # VPIP/PFR stability across blocks
        if len(self.session_blocks) >= 2:
            vpips = [b["vpip"] / max(1, b["hands"]) for b in self.session_blocks]
            pfrs = [b["pfr"] / max(1, b["hands"]) for b in self.session_blocks]
            profile["vpipStability"] = round(self._stdev(vpips), 6)
            profile["pfrStability"] = round(self._stdev(pfrs), 6)
            profile["vpipMean"] = round(sum(vpips) / len(vpips), 4)
            profile["pfrMean"] = round(sum(pfrs) / len(pfrs), 4)
        else:
            preflop = [x for x in d if x["phase"] == "PREFLOP"]
            vpip_count = sum(1 for x in preflop if x["action"] in ("CALL", "RAISE", "BET"))
            pfr_count = sum(1 for x in preflop if x["action"] == "RAISE")
            profile["vpipMean"] = round(vpip_count / max(1, len(preflop)), 4)
            profile["pfrMean"] = round(pfr_count / max(1, len(preflop)), 4)
            profile["vpipStability"] = 0
            profile["pfrStability"] = 0

        # Timing stats
        times = [x["response_time_ms"] for x in d]
        profile["timingMean"] = round(sum(times) / len(times))
        profile["timingStd"] = round(self._stdev(times))
        profile["timingCV"] = round(profile["timingStd"] / max(1, profile["timingMean"]), 4)

        profile["totalDecisions"] = total_actions
        profile["handsPlayed"] = self.hands_played
        profile["sessionMinutes"] = round((time.time() - self.session_start) / 60, 1)

        return profile

    @staticmethod
    def _stdev(arr):
        if len(arr) < 2:
            return 0
        m = sum(arr) / len(arr)
        var = sum((x - m) ** 2 for x in arr) / (len(arr) - 1)
        return math.sqrt(var)

    def _save_profile(self):
        """Save current aggregate profile."""
        profile = self._compute_profile()
        if profile:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(PROFILE_PATH, "w") as f:
                json.dump({"timestamp": time.time(), "profile": profile}, f, indent=2)
            print(f"\n[Collector] Profile saved: {len(self.decisions)} decisions, {self.hands_played} hands")
            # Print key signals
            print(f"  Bet precision:   {profile['betSizePrecision']:.2%}")
            print(f"  Distinct sizes:  {profile['distinctBetSizes']}")
            print(f"  Bet entropy:     {profile['betSizeEntropy']:.2f}")
            print(f"  VPIP:            {profile['vpipMean']:.1%}")
            print(f"  Timing CV:       {profile['timingCV']:.2f}")
            print(f"  Avg response:    {profile['timingMean']}ms")

    def run(self):
        """Main collection loop."""
        print("\n" + "=" * 55)
        print("  HUMAN BASELINE COLLECTOR — Bot Detection Calibration")
        print("=" * 55)
        print("  Play normally on PokerStars. This records your actions.")
        print("  Ctrl+C to stop and save profile.\n")

        prev_state = None
        frame_count = 0
        last_profile_save = time.time()

        try:
            while True:
                time.sleep(0.3)  # ~3 FPS is enough

                img = self._capture_table()
                if img is None:
                    continue
                state = self._extract_state(img)
                if state is None:
                    continue

                hero_turn = state.get("hero_turn", False)
                hero_cards = state.get("hero_cards", [])

                # Detect new hand
                if len(hero_cards) >= 2 and not self.prev_hero_cards:
                    self.hand_start_time = time.time()
                    if self.debug:
                        print(f"\n[Hand {self.hands_played + 1}] {' '.join(hero_cards)}")

                # Detect action prompt (hero_turn transitions to True)
                if hero_turn and not self.prev_hero_turn:
                    self.action_prompt_time = time.time()
                    self.current_phase = state.get("phase", "PREFLOP")
                    self.current_position = state.get("position", "IP")
                    self.prev_pot = state.get("pot")

                # Detect action taken (hero_turn transitions to False)
                if not hero_turn and self.prev_hero_turn and self.action_prompt_time and prev_state:
                    response_time_ms = (time.time() - self.action_prompt_time) * 1000
                    # Subtract the capture interval (~300ms) since we detect the transition late
                    response_time_ms = max(100, response_time_ms - 300)

                    action, amount = self._infer_action(prev_state, state)
                    pot = self.prev_pot or prev_state.get("pot") or 0

                    self._record_decision(
                        action, amount, pot,
                        self.current_phase, self.current_position,
                        response_time_ms
                    )
                    self.action_prompt_time = None

                # Detect hand end (hero cards disappear)
                if len(self.prev_hero_cards) >= 2 and len(hero_cards) == 0:
                    self._end_hand()
                    if self.hands_played % 10 == 0 and self.hands_played > 0:
                        elapsed = (time.time() - self.session_start) / 60
                        print(f"  [{self.hands_played} hands | {len(self.decisions)} decisions | {elapsed:.0f}min]")

                # Save profile periodically (every 5 min)
                if time.time() - last_profile_save > 300 and len(self.decisions) > 10:
                    self._save_profile()
                    last_profile_save = time.time()

                self.prev_hero_turn = hero_turn
                self.prev_hero_cards = hero_cards
                self.prev_board = state.get("board_cards", [])
                prev_state = state
                frame_count += 1

        except KeyboardInterrupt:
            print("\n\nStopping collector...")

        # Final save
        self._save_profile()
        print(f"\nSession complete: {self.hands_played} hands, {len(self.decisions)} decisions")
        print(f"Data saved to: {BASELINE_PATH}")
        print(f"Profile saved to: {PROFILE_PATH}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Human baseline data collector")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--table", type=int, default=None)
    args = parser.parse_args()

    collector = HumanCollector(debug=args.debug, table_id=args.table)
    collector.run()


if __name__ == "__main__":
    main()
