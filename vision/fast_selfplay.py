#!/usr/bin/env python
"""
Fast in-process neural self-play.

Eliminates the HTTP bottleneck by:
  1. Running the policy net directly in-process (no Flask server)
  2. Communicating with the Node.js engine via stdin/stdout JSON lines
  3. Implementing the TAG strategy in Python (no round-trip for TAG decisions)

Target: 100+ hands/sec (vs ~1 hand/sec with HTTP).

Usage:
  python vision/fast_selfplay.py                         # 1000 hands, 1 NN vs 5 TAG
  python vision/fast_selfplay.py --hands 10000
  python vision/fast_selfplay.py --nn-seats 2            # 2 NN vs 4 TAG
  python vision/fast_selfplay.py --greedy                # argmax instead of sampling
  python vision/fast_selfplay.py --seats 2               # heads-up
  python vision/fast_selfplay.py --seed 42               # reproducible
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

# Add vision/ to path so we can import policy_net
VISION_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(VISION_DIR))

from policy_net import PolicyNet, ACTION_NAMES, NUM_ACTIONS, build_feature_tensors

# ── Constants ────────────────────────────────────────────────────────────

ROOT = VISION_DIR.parent
ENGINE_WORKER = ROOT / "scripts" / "engine-worker.js"
DEFAULT_MODEL = VISION_DIR / "models" / "policy_net.pt"

BB = 10
SB = 5

# Action name <-> index mapping (must match policy_net.py)
ACTION_INDEX_TO_NAME = {0: "FOLD", 1: "CHECK", 2: "CALL", 3: "BET", 4: "RAISE"}
ACTION_NAME_TO_INDEX = {v: k for k, v in ACTION_INDEX_TO_NAME.items()}

PHASE_MAP = {"PREFLOP": 0, "FLOP": 1, "TURN": 2, "RIVER": 3}


# ── Engine Worker IPC ────────────────────────────────────────────────────

class EngineWorker:
    """Communicates with engine-worker.js via stdin/stdout JSON lines."""

    def __init__(self):
        self.proc = subprocess.Popen(
            ["node", str(ENGINE_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            bufsize=0,  # unbuffered
        )

    def send(self, cmd: dict) -> dict:
        """Send a JSON command and read a JSON response."""
        line = json.dumps(cmd) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        self.proc.stdin.flush()
        resp_line = self.proc.stdout.readline()
        if not resp_line:
            raise RuntimeError("Engine worker closed unexpectedly")
        return json.loads(resp_line.decode("utf-8"))

    def init(self, seats, stacks, seed=None, names=None):
        cmd = {"cmd": "init", "seats": seats, "stacks": stacks}
        if seed is not None:
            cmd["seed"] = seed
        if names is not None:
            cmd["names"] = names
        return self.send(cmd)

    def start_hand(self):
        return self.send({"cmd": "start_hand"})

    def act(self, seat, action, amount=None):
        cmd = {"cmd": "act", "seat": seat, "action": action}
        if amount is not None:
            cmd["amount"] = amount
        return self.send(cmd)

    def get_state(self):
        return self.send({"cmd": "get_state"})

    def step_tag(self, nn_seats):
        """Run TAG actions in-process until an NN seat needs to act or hand ends."""
        return self.send({"cmd": "step_tag", "nn_seats": nn_seats})

    def quit(self):
        try:
            self.send({"cmd": "quit"})
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


# ── Simple RNG (matching JS version) ────────────────────────────────────

class SimpleRng:
    """Deterministic LCG RNG matching the JS version in self-play.js."""

    def __init__(self, seed=42):
        self.s = seed

    def random(self):
        self.s = (self.s * 1664525 + 1013904223) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF


# ── TAG Strategy (Python port) ──────────────────────────────────────────

def evaluate_hand_strength(cards, board, phase):
    """
    Simple hand strength heuristic. Returns 0-1 score.
    Direct port of evaluateHandStrength from self-play.js.
    """
    if not cards or len(cards) < 2:
        return 0.5

    c1, c2 = cards[0], cards[1]
    r1, r2 = c1["rank"], c2["rank"]
    suited = c1["suit"] == c2["suit"]
    pair = r1 == r2
    high_card = max(r1, r2)
    gap = abs(r1 - r2)
    connected = gap == 1

    # Preflop hand strength
    pf_strength = 0.0

    if pair:
        pf_strength = 0.5 + (r1 / 14) * 0.5  # AA=1.0, 22=0.57
    else:
        pf_strength = (high_card / 14) * 0.4
        if suited:
            pf_strength += 0.08
        if connected:
            pf_strength += 0.06
        if gap <= 3:
            pf_strength += 0.03
        if r1 >= 10 and r2 >= 10:
            pf_strength += 0.15
        if high_card == 14:
            pf_strength += 0.1

    if phase == "PREFLOP":
        return min(1.0, pf_strength)

    # Postflop: check board hits
    board_ranks = [c["rank"] for c in board]
    board_suits = [c["suit"] for c in board]
    post_strength = pf_strength

    # Pair with board
    if r1 in board_ranks:
        post_strength += 0.25
    if r2 in board_ranks:
        post_strength += 0.20

    # Two pair
    if r1 in board_ranks and r2 in board_ranks and not pair:
        post_strength += 0.20

    # Trips
    if pair and r1 in board_ranks:
        post_strength += 0.35

    # Flush draw
    suit_count = sum(1 for s in board_suits if s == c1["suit"])
    if suit_count >= 2 and suited:
        post_strength += 0.12
    if suit_count >= 3 and board_suits and (c1["suit"] == board_suits[0] or c2["suit"] == board_suits[0]):
        post_strength += 0.30  # flush made

    # Overpair
    if pair and board_ranks and r1 > max(board_ranks):
        post_strength += 0.15

    return min(1.0, post_strength)


def tag_strategy(seat_idx, legal, seat_state, hand_state, rng):
    """
    TAG (Tight-Aggressive) strategy. Direct port from self-play-nn.js.
    Returns (action_name, amount) tuple.
    """
    actions = legal["actions"]
    call_amount = legal["callAmount"]
    min_bet = legal["minBet"]
    min_raise = legal["minRaise"]
    max_raise = legal["maxRaise"]
    phase = hand_state["phase"]
    pot_size = hand_state["pot"]
    stack = seat_state["stack"]
    cards = seat_state.get("holeCards") or []
    board = hand_state.get("board") or []

    if not actions:
        return None
    if len(actions) == 1:
        return (actions[0], None)

    strength = evaluate_hand_strength(cards, board, phase)

    if phase == "PREFLOP":
        if strength > 0.7 and "RAISE" in actions:
            raise_amt = min(min_raise + int(pot_size * 0.5), max_raise)
            return ("RAISE", max(min_raise, raise_amt))
        if strength > 0.35 and "CALL" in actions:
            return ("CALL", None)
        if strength > 0.35 and "CHECK" in actions:
            return ("CHECK", None)
        return ("FOLD", None)

    # Postflop: strong hand
    if strength > 0.7:
        if "RAISE" in actions:
            raise_amt = min(min_raise + int(pot_size * 0.75), max_raise)
            return ("RAISE", max(min_raise, raise_amt))
        if "BET" in actions:
            bet_amt = min(int(pot_size * 0.66), stack, max(min_bet, 2))
            return ("BET", max(min_bet, bet_amt))
        if "CALL" in actions:
            return ("CALL", None)
        return ("CHECK", None)

    # Medium hand: check/call
    if strength > 0.35:
        if "CHECK" in actions:
            return ("CHECK", None)
        if "CALL" in actions and call_amount < pot_size * 0.5:
            return ("CALL", None)
        if rng.random() < 0.15 and "BET" in actions:
            return ("BET", min_bet)
        return ("FOLD", None)

    # Weak hand: check or fold
    if "CHECK" in actions:
        return ("CHECK", None)

    # Bluff ~10%
    if rng.random() < 0.10 and "BET" in actions:
        return ("BET", min_bet)

    return ("FOLD", None)


# ── Action Mapping ───────────────────────────────────────────────────────

def map_action_to_legal(action_name, amount, legal):
    """
    Map a chosen action to the closest legal equivalent.

    The engine's external getLegalActions doesn't have the round's currentBet,
    so preflop it reports CHECK/BET instead of CALL/RAISE. We need to map:
      CALL -> CHECK (when CHECK is legal but CALL isn't)
      RAISE -> BET (when BET is legal but RAISE isn't)
      CHECK -> CALL (when CALL is legal but CHECK isn't)
      BET -> RAISE (when RAISE is legal but BET isn't)
    """
    actions = legal["actions"]

    if action_name in actions:
        return action_name, amount

    # CALL <-> CHECK
    if action_name == "CALL" and "CHECK" in actions:
        return "CHECK", None
    if action_name == "CHECK" and "CALL" in actions:
        return "CALL", None

    # RAISE <-> BET
    if action_name == "RAISE" and "BET" in actions:
        # Use BET with appropriate sizing
        min_bet = legal.get("minBet", 0)
        if amount is not None:
            return "BET", max(min_bet, amount)
        return "BET", min_bet
    if action_name == "BET" and "RAISE" in actions:
        min_raise = legal.get("minRaise", 0)
        max_raise = legal.get("maxRaise", 0)
        if amount is not None:
            amt = max(min_raise, min(amount, max_raise))
            return "RAISE", amt
        return "RAISE", min_raise

    # Fallback: FOLD is always legal
    if "FOLD" in actions:
        return "FOLD", None

    # Last resort: first legal action
    return actions[0], None


# ── Neural Net Strategy ─────────────────────────────────────────────────

def encode_card(card):
    """Encode a card dict to 0-51 index, or 52 for empty."""
    if not card:
        return 52
    return (card["rank"] - 2) * 4 + (card["suit"] - 1)


def _eval_hand_strength_py(cards, board, phase):
    """Hand strength for extract_features — handles both dict and list card formats."""
    if not cards or len(cards) < 2:
        return 0.5
    # Cards might be dicts with rank/suit or encoded ints — handle both
    if isinstance(cards[0], dict):
        return evaluate_hand_strength(cards, board, phase)
    return 0.5  # fallback if cards not in dict format


def extract_features(seat_idx, legal, seat_state, hand_state, num_seats):
    """Extract features matching self-play-nn.js extractFeatures()."""
    cards = seat_state.get("holeCards") or []
    board = hand_state.get("board") or []

    hero_card1 = encode_card(cards[0]) if len(cards) >= 1 else 52
    hero_card2 = encode_card(cards[1]) if len(cards) >= 2 else 52

    board_cards = []
    for i in range(5):
        board_cards.append(encode_card(board[i]) if i < len(board) else 52)

    bb100 = BB * 100
    pot_norm = (hand_state.get("pot") or 0) / bb100
    stack_norm = (seat_state.get("stack") or 0) / bb100
    call_norm = (legal.get("callAmount") or 0) / bb100

    pot = hand_state.get("pot") or 0
    ca = legal.get("callAmount") or 0
    pot_odds = ca / (pot + ca) if pot > 0 and ca > 0 else 0.0

    # Count opponents (in hand, not folded, not us)
    # We get this from the full seats dict
    num_opponents = 0  # will be set by caller
    # (passed in via seat_state["_num_opponents"] hack)
    num_opponents = seat_state.get("_num_opponents", 0)

    phase = hand_state.get("phase", "PREFLOP")
    street_idx = PHASE_MAP.get(phase, 0)
    street_one_hot = [0, 0, 0, 0]
    street_one_hot[street_idx] = 1

    max_seats = num_seats or 6
    pos_norm = seat_idx / (max_seats - 1) if max_seats > 1 else 0.0

    # Hand strength heuristic (same as JS evaluateHandStrength)
    hand_strength = _eval_hand_strength_py(cards, board, phase)

    # Bet-to-pot ratio
    bet_to_pot = ca / pot if pot > 0 and ca > 0 else 0.0
    bet_to_pot = min(bet_to_pot, 3.0)

    # Stack-to-pot ratio
    stack = seat_state.get("stack") or 0
    spr = stack / pot if pot > 0 else 10.0
    spr_norm = min(spr / 20.0, 1.0)

    return {
        "heroCard1": hero_card1,
        "heroCard2": hero_card2,
        "boardCards": board_cards,
        "potNorm": pot_norm,
        "stackNorm": stack_norm,
        "callNorm": call_norm,
        "potOdds": pot_odds,
        "numOpponents": num_opponents,
        "streetOneHot": street_one_hot,
        "posNorm": pos_norm,
        "handStrength": hand_strength,
        "betToPot": bet_to_pot,
        "sprNorm": spr_norm,
    }


class NeuralStrategy:
    """In-process neural net strategy. No HTTP, no Flask.

    Uses CPU for single-sample inference (10x faster than GPU for small models)
    and pre-allocates tensors to minimize allocation overhead.
    """

    def __init__(self, model_path, greedy=False):
        self.greedy = greedy
        # CPU is much faster than GPU for single-sample inference with small models
        self.device = torch.device("cpu")
        self.model = None

        if Path(model_path).exists():
            checkpoint = torch.load(str(model_path), map_location=self.device, weights_only=True)
            self.model = PolicyNet().to(self.device)
            self.model.load_state_dict(checkpoint["model_state"])
            self.model.eval()
            print(f"Loaded policy net from {model_path} (device: {self.device})")
        else:
            print(f"WARNING: No model at {model_path} -- neural bot will use random actions")

        # Pre-allocate tensors for zero-alloc inference
        self._cards_buf = torch.zeros(1, 7, dtype=torch.long)
        self._extra_buf = torch.zeros(1, 13, dtype=torch.float32)
        self._legal_buf = torch.zeros(1, NUM_ACTIONS, dtype=torch.bool)

    @torch.no_grad()
    def decide(self, seat_idx, legal, seat_state, hand_state, num_seats, rng):
        """
        Given game state, return (action_name, amount).
        """
        actions = legal["actions"]
        if not actions:
            return None
        if len(actions) == 1:
            return (actions[0], None)

        min_bet = legal.get("minBet", 0)
        min_raise = legal.get("minRaise", 0)
        max_raise = legal.get("maxRaise", 0)
        call_amount = legal.get("callAmount", 0)

        if self.model is None:
            # Random fallback
            idx = int(rng.random() * len(actions))
            idx = min(idx, len(actions) - 1)
            a = actions[idx]
            amount = None
            if a == "BET":
                amount = min_bet
            elif a == "RAISE":
                amount = min_raise
            return (a, amount)

        # Fill pre-allocated tensors (avoids allocation per call)
        features = extract_features(seat_idx, legal, seat_state, hand_state, num_seats)
        cb = self._cards_buf
        cb[0, 0] = features["heroCard1"]
        cb[0, 1] = features["heroCard2"]
        bc = features["boardCards"]
        cb[0, 2] = bc[0]; cb[0, 3] = bc[1]; cb[0, 4] = bc[2]
        cb[0, 5] = bc[3]; cb[0, 6] = bc[4]

        eb = self._extra_buf
        eb[0, 0] = features["potNorm"]
        eb[0, 1] = features["stackNorm"]
        eb[0, 2] = features["callNorm"]
        eb[0, 3] = features["potOdds"]
        eb[0, 4] = features["numOpponents"] / 5.0
        so = features["streetOneHot"]
        eb[0, 5] = so[0]; eb[0, 6] = so[1]; eb[0, 7] = so[2]; eb[0, 8] = so[3]
        eb[0, 9] = features["posNorm"]
        eb[0, 10] = features.get("handStrength", 0.5)
        eb[0, 11] = features.get("betToPot", 0.0)
        eb[0, 12] = features.get("sprNorm", 0.5)

        # Build legal mask
        lb = self._legal_buf
        lb.zero_()
        for a in actions:
            idx = ACTION_NAME_TO_INDEX.get(a, -1)
            if 0 <= idx < NUM_ACTIONS:
                lb[0, idx] = True

        action_probs, sizing = self.model.get_action_probs(cb, eb, lb)
        probs = action_probs[0].numpy()
        size_frac = sizing[0, 0].item()

        # Choose action
        if self.greedy:
            chosen_idx = int(np.argmax(probs))
        else:
            chosen_idx = int(np.random.choice(NUM_ACTIONS, p=probs))

        action_name = ACTION_INDEX_TO_NAME[chosen_idx]

        # Compute bet/raise amount
        amount = None
        pot_size = features["potNorm"] * (BB * 100)  # denormalize

        if action_name == "BET":
            raw = int(pot_size * size_frac)
            stack = seat_state.get("stack", 0)
            amount = max(min_bet, min(raw, stack))
            if amount < min_bet:
                amount = min_bet
        elif action_name == "RAISE":
            raw = int(pot_size * size_frac) + call_amount
            amount = max(min_raise, min(raw, max_raise))
            if amount < min_raise:
                amount = min_raise
            if max_raise > 0 and amount > max_raise:
                amount = max_raise

        # Map to legal actions (handles CHECK<->CALL, BET<->RAISE mismatch)
        action_name, amount = map_action_to_legal(action_name, amount, legal)

        return (action_name, amount)


# ── Self-Play Runner ─────────────────────────────────────────────────────

def run_selfplay(args):
    num_hands = args.hands
    num_seats = args.seats
    nn_seats = args.nn_seats
    start_stack = args.stack
    seed = args.seed
    greedy = args.greedy
    model_path = args.model

    # Set up strategies
    neural = NeuralStrategy(model_path, greedy=greedy)  # uses CPU for speed
    tag_rng = SimpleRng(seed)

    # Bot names and types
    bot_names = []
    is_neural = []
    for i in range(num_seats):
        if i < nn_seats:
            name = "NeuralBot" if nn_seats == 1 else f"Neural_{i}"
            bot_names.append(name)
            is_neural.append(True)
        else:
            bot_names.append(f"TAG_{i}")
            is_neural.append(False)

    # Start engine worker
    engine = EngineWorker()
    stacks = [start_stack] * num_seats
    resp = engine.init(num_seats, stacks, seed=seed, names=bot_names)
    if not resp.get("ok"):
        print(f"Engine init failed: {resp}")
        return

    # Tracking
    results = []
    for name in bot_names:
        results.append({
            "name": name,
            "handsPlayed": 0,
            "profit": 0,
            "wins": 0,
            "vpip": 0,
            "pfr": 0,
        })

    start_time = time.time()
    hands_completed = 0
    errors = 0

    # List of NN seat indices for step_tag command
    nn_seat_list = [i for i in range(num_seats) if is_neural[i]]

    for h in range(num_hands):
        # Start hand (auto-rebuys busted players inside engine-worker)
        resp = engine.start_hand()
        if not resp.get("ok"):
            errors += 1
            continue

        seats_state = resp["seats"]
        hand_state = resp["hand"]

        # Record pre-stacks (stack + totalInvested = original stack before blinds)
        pre_stacks = {}
        for i in range(num_seats):
            si = str(i)
            if si in seats_state:
                pre_stacks[i] = seats_state[si]["stack"] + seats_state[si].get("totalInvested", 0)

        nn_action_count = 0
        max_nn_actions = 30  # safety limit per hand

        # Optimized game loop:
        # 1. step_tag: run all TAG actions in-process (zero IPC)
        # 2. If NN seat needs to act: one IPC for decision + act
        # 3. Repeat until hand complete
        while not hand_state.get("complete") and nn_action_count < max_nn_actions:
            # Step 1: Let TAG players act in-process until NN turn or hand end
            resp = engine.step_tag(nn_seat_list)
            if not resp.get("ok"):
                errors += 1
                break

            seats_state = resp["seats"]
            hand_state = resp["hand"]

            # Accumulate TAG VPIP/PFR stats from engine
            for seat_str, cnt in resp.get("vpipCounts", {}).items():
                results[int(seat_str)]["vpip"] += cnt
            for seat_str, cnt in resp.get("pfrCounts", {}).items():
                results[int(seat_str)]["pfr"] += cnt
            errors += resp.get("tagErrors", 0)

            if hand_state.get("complete"):
                break

            # Step 2: NN seat needs to act
            action_seat = hand_state.get("actionSeat")
            if action_seat is None:
                break

            seat_key = str(action_seat)
            seat_st = seats_state.get(seat_key)
            if not seat_st or not seat_st.get("inHand"):
                break

            legal = hand_state.get("legalActions")
            if not legal or not legal.get("actions"):
                break

            # Count opponents for feature extraction
            num_opp = sum(
                1 for k, s in seats_state.items()
                if s.get("inHand") and not s.get("folded") and int(k) != action_seat
            )
            seat_st["_num_opponents"] = num_opp

            # Neural net decision (in-process, ~0.3ms on CPU)
            decision = neural.decide(
                action_seat, legal, seat_st, hand_state, num_seats, tag_rng
            )
            if decision is None:
                break

            action_name, amount = decision

            # Track NN VPIP/PFR
            if hand_state.get("phase") == "PREFLOP":
                if action_name in ("CALL", "RAISE", "BET"):
                    results[action_seat]["vpip"] += 1
                if action_name == "RAISE":
                    results[action_seat]["pfr"] += 1

            # Send NN action to engine
            resp = engine.act(action_seat, action_name, amount)
            if not resp.get("ok"):
                # Try fold as fallback
                resp = engine.act(action_seat, "FOLD")
                if not resp.get("ok"):
                    errors += 1
                    break
                errors += 1

            seats_state = resp["seats"]
            hand_state = resp["hand"]
            nn_action_count += 1

        hands_completed += 1

        # Calculate profit from post-hand stacks
        for i in range(num_seats):
            si = str(i)
            if si in seats_state and i in pre_stacks:
                post_stack = seats_state[si]["stack"]
                profit = post_stack - pre_stacks[i]
                results[i]["profit"] += profit
                results[i]["handsPlayed"] += 1
                if profit > 0:
                    results[i]["wins"] += 1

        # Progress
        if (h + 1) % 200 == 0 or h == num_hands - 1:
            elapsed = time.time() - start_time
            hps = hands_completed / elapsed if elapsed > 0 else 0
            print(f"\r  {hands_completed}/{num_hands} hands ({hps:.0f} hands/sec)", end="", flush=True)

    elapsed = time.time() - start_time
    hps = hands_completed / elapsed if elapsed > 0 else 0

    engine.quit()

    # ── Print Results ────────────────────────────────────────────────────
    print()
    print()
    print("=" * 60)
    print("FAST NEURAL SELF-PLAY RESULTS")
    print("=" * 60)
    print(f"Hands: {hands_completed} | Time: {elapsed:.1f}s | Speed: {hps:.0f} hands/sec")
    mode_str = f"{nn_seats} NN vs {num_seats - nn_seats} TAG"
    print(f"Mode: {mode_str} | Seats: {num_seats} | Errors: {errors}")
    print("-" * 60)

    for r in results:
        hp = r["handsPlayed"]
        if hp == 0:
            continue
        bb100 = (r["profit"] / BB) / (hp / 100)
        win_pct = (r["wins"] / hp) * 100
        vpip_pct = (r["vpip"] / hp) * 100
        pfr_pct = (r["pfr"] / hp) * 100
        sign = "+" if r["profit"] > 0 else ""
        print(f"  {r['name']}:")
        print(f"    Profit: {sign}{r['profit']} chips ({bb100:.1f} bb/100)")
        print(f"    Win rate: {r['wins']}/{hp} ({win_pct:.1f}%)")
        print(f"    VPIP: {vpip_pct:.1f}% | PFR: {pfr_pct:.1f}%")

    print("=" * 60)

    return {
        "hands_completed": hands_completed,
        "elapsed": elapsed,
        "hands_per_sec": hps,
        "errors": errors,
        "players": [
            {
                "name": r["name"],
                "profit": r["profit"],
                "bb100": (r["profit"] / BB) / (r["handsPlayed"] / 100) if r["handsPlayed"] else 0,
                "win_rate": r["wins"] / r["handsPlayed"] if r["handsPlayed"] else 0,
                "vpip": r["vpip"] / r["handsPlayed"] * 100 if r["handsPlayed"] else 0,
                "pfr": r["pfr"] / r["handsPlayed"] * 100 if r["handsPlayed"] else 0,
            }
            for r in results
        ],
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fast in-process neural self-play")
    parser.add_argument("--hands", type=int, default=1000, help="Number of hands to play")
    parser.add_argument("--seats", type=int, default=6, help="Number of seats")
    parser.add_argument("--nn-seats", type=int, default=1, help="Number of neural net seats")
    parser.add_argument("--stack", type=int, default=1000, help="Starting stack (chips)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--greedy", action="store_true", help="Use argmax instead of sampling")
    parser.add_argument("--model", type=str, default=str(DEFAULT_MODEL), help="Path to policy_net.pt")

    args = parser.parse_args()

    print("Fast Neural Self-Play")
    print("=" * 60)
    print(f"Config: {args.hands} hands, {args.seats} seats, {args.nn_seats} NN seats")
    print(f"Model: {args.model}")
    print(f"Greedy: {args.greedy} | Seed: {args.seed}")
    print()

    result = run_selfplay(args)

    if result:
        print(f"\nThroughput: {result['hands_per_sec']:.0f} hands/sec")


if __name__ == "__main__":
    main()
