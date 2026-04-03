"""
Real-time poker advisor with Tkinter overlay.

Captures the screen continuously, detects the PokerStars table via YOLO,
identifies hero cards and board, calculates hand strength, looks up CFR
strategy recommendations, and displays them in a small always-on-top overlay.

Usage:
  python vision/advisor.py                # default: YOLO + overlay
  python vision/advisor.py --terminal     # terminal output only (no overlay)
  python vision/advisor.py --debug        # show detection details
"""

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import cv2
import mss
import numpy as np

# ── Paths ────────────────────────────────────────────────────────────────

VISION_DIR = Path(__file__).resolve().parent
ROOT = VISION_DIR.parent
CFR_STRATEGY_PATH = VISION_DIR / "models" / "cfr_strategy.json"
HAND_STRENGTH_MODEL_PATH = VISION_DIR / "models" / "hand_strength.pt"

sys.path.insert(0, str(VISION_DIR))

# ── Card encoding helpers ────────────────────────────────────────────────

RANK_MAP = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
    "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}
SUIT_MAP = {"c": 1, "d": 2, "h": 3, "s": 4}
RANK_CHARS = {v: k for k, v in RANK_MAP.items()}
SUIT_CHARS = {v: k for k, v in SUIT_MAP.items()}


def card_str_to_int(s):
    """Convert 'Ah' -> int 0-51. Returns None on failure."""
    if not s or len(s) < 2 or s == "??":
        return None
    rank_ch = s[0].upper()
    suit_ch = s[1].lower()
    if rank_ch not in RANK_MAP or suit_ch not in SUIT_MAP:
        return None
    rank = RANK_MAP[rank_ch]
    suit = SUIT_MAP[suit_ch]
    return (rank - 2) * 4 + (suit - 1)


def card_str_to_dict(s):
    """Convert 'Ah' -> {'rank': 14, 'suit': 3}. Returns None on failure."""
    if not s or len(s) < 2 or s == "??":
        return None
    rank_ch = s[0].upper()
    suit_ch = s[1].lower()
    if rank_ch not in RANK_MAP or suit_ch not in SUIT_MAP:
        return None
    return {"rank": RANK_MAP[rank_ch], "suit": SUIT_MAP[suit_ch]}


def card_display(s):
    """Pretty-print a card string with unicode suit symbols."""
    if not s or len(s) < 2 or s == "??":
        return "??"
    suit_symbols = {"c": "c", "d": "d", "h": "h", "s": "s"}
    rank = s[0].upper()
    suit = s[1].lower()
    sym = suit_symbols.get(suit, suit)
    return f"{rank}{sym}"


# ── Hand strength evaluation (heuristic, matching JS CFR abstraction) ────

def evaluate_hand_strength(cards, board, phase):
    """
    Evaluate hand strength as 0..1 value.
    Direct port of evaluateHandStrength from scripts/cfr/abstraction.js.
    cards: list of {'rank': int, 'suit': int}
    board: list of {'rank': int, 'suit': int}
    phase: 'PREFLOP', 'FLOP', 'TURN', 'RIVER'
    """
    if not cards or len(cards) < 2:
        return 0.5

    c1, c2 = cards[0], cards[1]
    r1, r2 = c1["rank"], c2["rank"]
    suited = c1["suit"] == c2["suit"]
    pair = r1 == r2
    high_card = max(r1, r2)
    gap = abs(r1 - r2)

    pf = 0.0
    if pair:
        pf = 0.5 + (r1 / 14) * 0.5
    else:
        pf = (high_card / 14) * 0.4
        if suited:
            pf += 0.08
        if gap <= 1:
            pf += 0.06
        if gap <= 3:
            pf += 0.03
        if r1 >= 10 and r2 >= 10:
            pf += 0.15
        if high_card == 14:
            pf += 0.1

    if not phase or phase == "PREFLOP" or not board or len(board) == 0:
        return min(1.0, pf)

    # Postflop
    board_ranks = [c["rank"] for c in board]
    # Count occurrences of each rank on the board
    board_rank_counts = {}
    for r in board_ranks:
        board_rank_counts[r] = board_rank_counts.get(r, 0) + 1

    r1_board_count = board_rank_counts.get(r1, 0)
    r2_board_count = board_rank_counts.get(r2, 0)

    post = pf

    if pair:
        # Pocket pair
        if r1_board_count >= 2:
            # Quads: pocket pair + 2 on board
            post += 0.95
        elif r1_board_count == 1:
            # Set (pocket pair + one on board)
            post += 0.70
            # Full house: set + board has another pair
            board_has_other_pair = any(
                cnt >= 2 for rank, cnt in board_rank_counts.items() if rank != r1
            )
            if board_has_other_pair:
                post += 0.15  # full house
        else:
            # Overpair / underpair
            if board_ranks and r1 > max(board_ranks):
                post += 0.30  # overpair
            else:
                post += 0.15  # underpair
    else:
        # Unpaired hole cards
        hit1 = r1_board_count > 0
        hit2 = r2_board_count > 0

        if hit1 and r1_board_count >= 2:
            # Trips: hero card matches a board pair (e.g., hero 8x, board 8 8 Q)
            post += 0.70
            # Check for full house: trips + other hole card pairs with board
            if hit2:
                post += 0.20  # full house
        elif hit2 and r2_board_count >= 2:
            # Trips with second card matching board pair
            post += 0.70
            if hit1:
                post += 0.20  # full house
        elif hit1 and hit2:
            # Two pair (both cards hit board, no board-pair overlap)
            post += 0.55
        elif hit1:
            # One pair with r1
            post += 0.25
        elif hit2:
            # One pair with r2
            post += 0.20

    # Flush detection
    all_suits = [c["suit"] for c in cards] + [c["suit"] for c in board]
    suit_counts = {}
    for s in all_suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1
    max_suit = max(suit_counts.values()) if suit_counts else 0
    if max_suit >= 5:
        post += 0.30
    elif max_suit == 4:
        post += 0.10

    # Straight detection
    all_ranks = sorted(set([c["rank"] for c in cards] + board_ranks))
    max_consec = 1
    cur_consec = 1
    for i in range(1, len(all_ranks)):
        if all_ranks[i] == all_ranks[i - 1] + 1:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 1
    if max_consec >= 5:
        post += 0.25
    elif max_consec == 4:
        post += 0.08

    return min(1.0, post)


def strength_to_bucket(strength, num_buckets=20):
    """Map strength 0..1 to bucket 0..num_buckets-1."""
    bucket = int(strength * num_buckets)
    return min(bucket, num_buckets - 1)


# ── Neural hand strength (optional, higher accuracy) ─────────────────────

_nn_model = None
_nn_device = None


def load_nn_model():
    """Lazy-load the neural hand strength model."""
    global _nn_model, _nn_device
    if _nn_model is not None:
        return _nn_model, _nn_device
    if not HAND_STRENGTH_MODEL_PATH.exists():
        return None, None
    try:
        import torch
        from hand_strength import HandStrengthNet
        device = torch.device("cpu")  # CPU is faster for single inference
        checkpoint = torch.load(str(HAND_STRENGTH_MODEL_PATH), map_location=device, weights_only=True)
        model = HandStrengthNet(
            embed_dim=checkpoint.get("embed_dim", 16),
            hidden=checkpoint.get("hidden", 256),
        ).to(device)
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        _nn_model = model
        _nn_device = device
        return model, device
    except Exception as e:
        print(f"[Advisor] Could not load NN hand strength model: {e}")
        return None, None


def nn_hand_strength(hero_ints, board_ints, num_opponents=1):
    """
    Neural network hand strength prediction.
    hero_ints: list of 2 ints (0-51)
    board_ints: list of 0-5 ints (0-51)
    Returns float 0-1 or None if model unavailable.
    """
    model, device = load_nn_model()
    if model is None:
        return None
    import torch
    board_padded = list(board_ints) + [52] * (5 - len(board_ints))
    cards = torch.tensor([hero_ints + board_padded], dtype=torch.long, device=device)
    opp = torch.tensor([(num_opponents - 1) / 4.0], dtype=torch.float32, device=device)
    with torch.no_grad():
        prob = model(cards, opp).item()
    return prob


# ── CFR strategy lookup ──────────────────────────────────────────────────

def phase_from_board_count(n):
    """Determine street from number of board cards."""
    if n == 0:
        return "PREFLOP"
    elif n == 3:
        return "FLOP"
    elif n == 4:
        return "TURN"
    elif n >= 5:
        return "RIVER"
    return "PREFLOP"


ACTION_ENCODE = {
    "fold": "f", "check": "k", "call": "c",
    "bet": "bh", "raise": "rh",
    "bet_half": "bh", "bet_pot": "bp", "bet_allin": "ba",
    "raise_half": "rh", "raise_pot": "rp", "raise_allin": "ra",
    # Engine action names (uppercase)
    "FOLD": "f", "CHECK": "k", "CALL": "c",
    "BET": "bh", "RAISE": "rh",
    "BET_HALF": "bh", "BET_POT": "bp", "BET_ALLIN": "ba",
    "RAISE_HALF": "rh", "RAISE_POT": "rp", "RAISE_ALLIN": "ra",
}


class CFRLookup:
    """Loads CFR strategy and provides action recommendations."""

    def __init__(self, path=None):
        path = path or CFR_STRATEGY_PATH
        self.strategy = {}
        if Path(path).exists():
            print(f"[Advisor] Loading CFR strategy from {path}...")
            t0 = time.time()
            with open(path) as f:
                self.strategy = json.load(f)
            elapsed = time.time() - t0
            print(f"[Advisor] Loaded {len(self.strategy):,} info sets in {elapsed:.1f}s")
        else:
            print(f"[Advisor] WARNING: CFR strategy not found at {path}")

    def lookup(self, hero_cards_str, board_cards_str, pot, stack, bb=0.10,
               action_history_str="", num_opponents=1, position="IP"):
        """
        Look up CFR recommendation.

        Args:
            hero_cards_str: list of card strings like ['Ah', 'Ks']
            board_cards_str: list of card strings like ['Td', '5c', '2h']
            pot: float, current pot size
            stack: float, hero stack
            bb: float, big blind size
            action_history_str: encoded action history string (e.g. 'rh')
            num_opponents: int

        Returns:
            dict with:
              - action_probs: dict of action -> probability
              - recommended: str, highest probability action
              - rec_prob: float, probability of recommended action
              - equity: float, hand strength (heuristic)
              - nn_equity: float or None, neural net equity
              - info_key: str, the info set key used
              - bucket: int, hand strength bucket
        """
        # Parse cards
        hero_dicts = [card_str_to_dict(c) for c in hero_cards_str]
        board_dicts = [card_str_to_dict(c) for c in board_cards_str]

        # Filter out failed parses
        hero_dicts = [c for c in hero_dicts if c is not None]
        board_dicts = [c for c in board_dicts if c is not None]

        if len(hero_dicts) < 2:
            return None

        phase = phase_from_board_count(len(board_dicts))

        # Hand strength (heuristic — matching CFR abstraction)
        strength = evaluate_hand_strength(hero_dicts, board_dicts, phase)
        bucket = strength_to_bucket(strength, 50)

        # Position-based bucket adjustment (IP plays wider preflop)
        if phase == "PREFLOP":
            if position == "IP":
                bucket = min(49, bucket + 5)  # BTN/CO: play 5 buckets wider
            elif position == "OOP":
                bucket = max(0, bucket - 3)   # UTG/MP: play 3 buckets tighter

        # Neural net equity (optional, for display)
        hero_ints = [card_str_to_int(c) for c in hero_cards_str]
        board_ints = [card_str_to_int(c) for c in board_cards_str]
        hero_ints = [x for x in hero_ints if x is not None]
        board_ints = [x for x in board_ints if x is not None]
        nn_eq = None
        if len(hero_ints) == 2:
            nn_eq = nn_hand_strength(hero_ints, board_ints, num_opponents)

        # Stack bucket — the trained CFR strategy only uses s0
        # so we always use s0 for lookup, but compute the real bucket for display
        bbs = stack / bb if bb > 0 else 100
        stack_bucket_real = 0 if bbs < 30 else (1 if bbs < 80 else 2)

        # Build info set key
        # Try position-aware key first, then fallbacks
        key_with_pos = f"{phase}:{bucket}:s0:{position}:{action_history_str}"
        key_primary = f"{phase}:{bucket}:s0:{action_history_str}"
        key_no_stack = f"{bucket}|{action_history_str}"

        # Try position-aware, then primary, then pipe format
        info_key = None
        strat = None
        for candidate in [key_with_pos, key_primary, key_no_stack]:
            if candidate in self.strategy:
                info_key = candidate
                strat = self.strategy[candidate]
                break

        # Try nearby buckets if exact not found
        if strat is None:
            for delta in [1, -1, 2, -2, 3, -3]:
                nb = max(0, min(19, bucket + delta))
                candidate = f"{phase}:{nb}:s0:{action_history_str}"
                if candidate in self.strategy:
                    info_key = candidate + f" (adj from B{bucket})"
                    strat = self.strategy[candidate]
                    break

        if strat is None:
            # Fallback: use heuristic
            return {
                "action_probs": _heuristic_probs(strength),
                "recommended": _heuristic_action(strength),
                "rec_prob": 1.0,
                "equity": strength,
                "nn_equity": nn_eq,
                "info_key": key_primary + " (not found)",
                "bucket": bucket,
                "fallback": True,
            }

        # Aggregate into simple actions for display
        simple_probs = {}
        simple_probs["FOLD"] = strat.get("FOLD", 0)
        simple_probs["CHECK"] = strat.get("CHECK", 0)
        simple_probs["CALL"] = strat.get("CALL", 0)
        simple_probs["BET"] = (strat.get("BET_HALF", 0) +
                                strat.get("BET_POT", 0) +
                                strat.get("BET_ALLIN", 0))
        simple_probs["RAISE"] = (strat.get("RAISE_HALF", 0) +
                                  strat.get("RAISE_POT", 0) +
                                  strat.get("RAISE_ALLIN", 0))

        # Remove zero-probability actions
        simple_probs = {k: v for k, v in simple_probs.items() if v > 0.001}

        # Get recommended action (highest probability)
        recommended = max(simple_probs, key=simple_probs.get) if simple_probs else "CHECK"
        rec_prob = simple_probs.get(recommended, 0)

        # Determine sizing if bet/raise
        sizing_info = ""
        if recommended in ("BET", "RAISE"):
            half_p = strat.get(f"{recommended}_HALF", 0)
            pot_p = strat.get(f"{recommended}_POT", 0)
            all_p = strat.get(f"{recommended}_ALLIN", 0)
            total = half_p + pot_p + all_p
            if total > 0:
                if all_p / total > 0.5:
                    sizing_info = " ALL-IN"
                elif pot_p / total > half_p / total:
                    sizing_info = " pot-size"
                else:
                    sizing_info = " half-pot"

        return {
            "action_probs": simple_probs,
            "raw_probs": strat,
            "recommended": recommended + sizing_info,
            "rec_prob": rec_prob,
            "equity": strength,
            "nn_equity": nn_eq,
            "info_key": info_key,
            "bucket": bucket,
            "fallback": False,
        }


def _heuristic_probs(strength):
    """Fallback probability distribution based on hand strength."""
    if strength > 0.7:
        return {"RAISE": 0.6, "CALL": 0.3, "FOLD": 0.1}
    elif strength > 0.4:
        return {"CALL": 0.5, "CHECK": 0.3, "FOLD": 0.2}
    else:
        return {"FOLD": 0.6, "CHECK": 0.3, "CALL": 0.1}


def _heuristic_action(strength):
    """Fallback action based on hand strength."""
    if strength > 0.7:
        return "RAISE"
    elif strength > 0.4:
        return "CALL/CHECK"
    else:
        return "FOLD"


# ── Real-time Subgame Solver ────────────────────────────────────────────

class SubgameSolver:
    """
    Persistent Node.js solver process for real-time subgame CFR solving.
    Communicates via stdin/stdout JSON lines.
    """

    def __init__(self, timeout_ms=200):
        self.timeout_ms = timeout_ms
        self.proc = None
        self._response_queue = queue.Queue()
        self._start_process()

    def _start_process(self):
        solver_script = str(ROOT / "scripts" / "cfr" / "cfr-solver.js")
        self.proc = subprocess.Popen(
            ["node", solver_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            bufsize=0,
        )
        # Reader thread for non-blocking stdout
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # Wait for ready signal
        resp = self._read_response(timeout=10.0)
        if resp and resp.get("ready"):
            print(f"[Solver] Process started (PID {self.proc.pid})")
        else:
            print("[Solver] WARNING: solver did not send ready signal")

    def _read_loop(self):
        try:
            while self.proc and self.proc.poll() is None:
                line = self.proc.stdout.readline()
                if not line:
                    break
                self._response_queue.put(json.loads(line.decode("utf-8")))
        except Exception:
            pass

    def _read_response(self, timeout=0.2):
        try:
            return self._response_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def solve(self, hero_cards, board_cards, pot, hero_stack, opp_stack,
              street, hero_position, action_history, facing_bet=False):
        """Request a real-time subgame solve. Returns strategy dict or None."""
        hero_dicts = [card_str_to_dict(c) for c in hero_cards]
        board_dicts = [card_str_to_dict(c) for c in board_cards]
        if not hero_dicts or len(hero_dicts) < 2 or None in hero_dicts:
            return None

        bb = 1.0  # solver works in BB units
        pot_bb = pot / 0.10 if pot else 1.5
        hero_bb = hero_stack / 0.10 if hero_stack else 100
        opp_bb = opp_stack / 0.10 if opp_stack else 100

        # Determine invested amounts and current bet from context
        hero_invested = 0
        opp_invested = 0
        current_bet = 0
        raises = 0
        if street == "PREFLOP":
            if facing_bet:
                hero_invested = 1.0  # BB or SB
                opp_invested = 3.0   # typical raise
                current_bet = 3.0
                raises = 1
            else:
                hero_invested = 1.0
                opp_invested = 1.0
                current_bet = 1.0

        cmd = {
            "cmd": "solve",
            "heroCards": hero_dicts,
            "board": board_dicts,
            "pot": pot_bb,
            "heroStack": hero_bb - hero_invested,
            "oppStack": opp_bb - opp_invested,
            "heroInvested": hero_invested,
            "oppInvested": opp_invested,
            "currentBet": current_bet,
            "street": street,
            "heroPosition": 0 if hero_position == "IP" else 1,
            "actionHistory": action_history,
            "raisesThisStreet": raises,
            "timeBudgetMs": self.timeout_ms,
        }

        try:
            line = json.dumps(cmd) + "\n"
            self.proc.stdin.write(line.encode("utf-8"))
            self.proc.stdin.flush()
            return self._read_response(timeout=self.timeout_ms / 1000.0 + 0.1)
        except Exception:
            return None

    def quit(self):
        try:
            self.proc.stdin.write(b'{"cmd":"quit"}\n')
            self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


# ── Screen capture and table detection ───────────────────────────────────

def capture_screen(window_rect=None):
    """Capture the full screen or a specific window region."""
    with mss.mss() as sct:
        if window_rect:
            left, top, right, bottom = window_rect
            monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        else:
            monitor = sct.monitors[1]
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def find_poker_window_by_table(table_id):
    """Find a specific Poker Lab browser window by table ID. Returns rect or None."""
    try:
        import win32gui
        import re
        best = None
        best_area = 0
        def cb(hwnd, _):
            nonlocal best, best_area
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            if w < 300 or h < 400:
                return
            # Match table ID in title
            m = re.search(r'table=(\d+)', title)
            tid = m.group(1) if m else ("1" if "Poker Lab" in title else None)
            if tid == str(table_id) and w * h > best_area:
                best = rect
                best_area = w * h
        win32gui.EnumWindows(cb, None)
        return best
    except ImportError:
        return None


def find_table_region(frame):
    """Find the PokerStars table by green felt detection."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([25, 30, 20])
    upper = np.array([85, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < frame.shape[0] * frame.shape[1] * 0.03:
        return None
    x, y, w, h = cv2.boundingRect(largest)
    return (x, y, w, h)


def crop_table(frame, region):
    """Crop table region with padding. Extra side padding for hero cards at table edges."""
    x, y, w, h = region
    pad_top = 50
    pad_side = 120  # hero cards can extend well beyond the felt oval
    pad_bottom = 180  # action buttons below the felt
    x1 = max(0, x - pad_side)
    y1 = max(0, y - pad_top)
    x2 = min(frame.shape[1], x + w + pad_side)
    y2 = min(frame.shape[0], y + h + pad_bottom)
    return frame[y1:y2, x1:x2], (x1, y1)


# ── Overlay window ───────────────────────────────────────────────────────

class OverlayWindow:
    """Small always-on-top Tkinter window for displaying recommendations."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Poker Advisor")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.85)
        self.root.overrideredirect(True)  # no title bar
        self.root.configure(bg="#1a1a2e")

        # Size and position (bottom-right default, will reposition near table)
        self.width = 260
        self.height = 130
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(f"{self.width}x{self.height}+{screen_w - self.width - 20}+{screen_h - self.height - 60}")

        # Allow dragging
        self._drag_data = {"x": 0, "y": 0}
        self.root.bind("<Button-1>", self._on_press)
        self.root.bind("<B1-Motion>", self._on_drag)

        # Right-click to close
        self.root.bind("<Button-3>", lambda e: self.root.destroy())

        # Main frame
        self.frame = tk.Frame(self.root, bg="#1a1a2e", padx=8, pady=5)
        self.frame.pack(fill=tk.BOTH, expand=True)

        # Title
        self.title_label = tk.Label(
            self.frame, text="POKER ADVISOR", font=("Consolas", 9, "bold"),
            fg="#888888", bg="#1a1a2e", anchor="w"
        )
        self.title_label.pack(fill=tk.X)

        # Cards display
        self.cards_label = tk.Label(
            self.frame, text="Waiting for table...", font=("Consolas", 11),
            fg="#cccccc", bg="#1a1a2e", anchor="w"
        )
        self.cards_label.pack(fill=tk.X, pady=(2, 0))

        # Equity display
        self.equity_label = tk.Label(
            self.frame, text="", font=("Consolas", 10),
            fg="#aaaaaa", bg="#1a1a2e", anchor="w"
        )
        self.equity_label.pack(fill=tk.X)

        # Recommendation display
        self.rec_label = tk.Label(
            self.frame, text="", font=("Consolas", 13, "bold"),
            fg="#ffffff", bg="#1a1a2e", anchor="w"
        )
        self.rec_label.pack(fill=tk.X, pady=(2, 0))

        # Probabilities display
        self.probs_label = tk.Label(
            self.frame, text="", font=("Consolas", 9),
            fg="#999999", bg="#1a1a2e", anchor="w"
        )
        self.probs_label.pack(fill=tk.X)

    def _on_press(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_data["x"]
        y = self.root.winfo_y() + event.y - self._drag_data["y"]
        self.root.geometry(f"+{x}+{y}")

    def position_near_table(self, table_region):
        """Position the overlay near the bottom-right of the detected table."""
        if table_region is None:
            return
        tx, ty, tw, th = table_region
        # Place below and to the right of the table
        x = tx + tw - self.width - 10
        y = ty + th + 10
        # Keep on screen
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, min(x, screen_w - self.width))
        y = max(0, min(y, screen_h - self.height))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def show_waiting(self, msg="Waiting..."):
        """Show waiting state."""
        self.cards_label.config(text=msg, fg="#666666")
        self.equity_label.config(text="")
        self.rec_label.config(text="", bg="#1a1a2e")
        self.probs_label.config(text="")

    def show_no_table(self):
        """Show no table detected."""
        self.cards_label.config(text="No table found", fg="#555555")
        self.equity_label.config(text="Looking for PS table...")
        self.rec_label.config(text="", bg="#1a1a2e")
        self.probs_label.config(text="")

    def show_recommendation(self, hero_cards, board_cards, rec):
        """Display a CFR recommendation."""
        # Cards line
        hero_str = " ".join(card_display(c) for c in hero_cards)
        board_str = " ".join(card_display(c) for c in board_cards) if board_cards else ""
        cards_text = f"{hero_str}"
        if board_str:
            cards_text += f"  |  {board_str}"
        self.cards_label.config(text=cards_text, fg="#e0e0e0")

        # Equity line
        eq_text = f"Equity: {rec['equity']:.0%}"
        if rec.get("nn_equity") is not None:
            eq_text += f"  (NN: {rec['nn_equity']:.0%})"
        eq_text += f"  [B{rec['bucket']}]"
        self.equity_label.config(text=eq_text)

        # Recommendation line — color coded
        action = rec["recommended"]
        action_upper = action.upper().split()[0]

        if action_upper in ("RAISE", "BET"):
            color = "#00e676"  # green
            bg = "#1a3a1e"
        elif action_upper in ("CALL", "CHECK"):
            color = "#42a5f5"  # blue
            bg = "#1a2a3e"
        elif action_upper == "FOLD":
            color = "#ef5350"  # red
            bg = "#3a1a1e"
        else:
            color = "#ffffff"
            bg = "#1a1a2e"

        prob_pct = f"{rec['rec_prob']:.0%}" if rec['rec_prob'] < 1.0 else ""
        fallback_tag = " [heuristic]" if rec.get("fallback") else ""
        rec_text = f">>> {action} {prob_pct}{fallback_tag}"
        self.rec_label.config(text=rec_text, fg=color, bg=bg)

        # Probabilities line
        probs = rec["action_probs"]
        parts = []
        short = {"FOLD": "F", "CHECK": "X", "CALL": "C", "BET": "B", "RAISE": "R"}
        for a in ["FOLD", "CHECK", "CALL", "BET", "RAISE"]:
            if a in probs and probs[a] > 0.01:
                parts.append(f"{short[a]}:{probs[a]:.0%}")
        self.probs_label.config(text="  ".join(parts))

    def update(self):
        """Process pending Tk events (call from main loop)."""
        try:
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            return False  # window destroyed
        return True


# ── Main Advisor ─────────────────────────────────────────────────────────

class Advisor:
    """
    Main advisor loop: capture -> detect -> recommend -> display.
    """

    def __init__(self, use_overlay=True, terminal=False, debug=False, table_id=None):
        self.use_overlay = use_overlay
        self.terminal = terminal
        self.debug = debug
        self.table_id = table_id
        self.window_rect = None
        if table_id is not None:
            self.window_rect = find_poker_window_by_table(table_id)
            if self.window_rect:
                print(f"[Advisor] Targeting table {table_id} at {self.window_rect}")
            else:
                print(f"[Advisor] Table {table_id} window not found — using full screen")

        # Load CFR strategy (table lookup — fast fallback)
        self.cfr = CFRLookup()

        # Start real-time subgame solver (2s solve, leaves 4s to read + act)
        self.solver = None
        try:
            self.solver = SubgameSolver(timeout_ms=2000)
        except Exception as e:
            print(f"[Advisor] Subgame solver not available: {e}")

        # Load YOLO model
        self.yolo_model = None
        self.yolo_detect = None
        try:
            from yolo_detect import load_model, detect_elements
            model = load_model()
            if model is not None:
                self.yolo_model = model
                self.yolo_detect = detect_elements
                print("[Advisor] YOLO model loaded")
        except Exception as e:
            print(f"[Advisor] YOLO not available: {e}")

        # Load card_id
        self.card_identify = None
        try:
            from card_id import identify_cards as _id_cards
            self.card_identify = _id_cards
            print("[Advisor] Card ID templates loaded")
        except Exception as e:
            print(f"[Advisor] Card ID not available: {e}")

        # Pre-load NN hand strength model in background
        if HAND_STRENGTH_MODEL_PATH.exists():
            threading.Thread(target=load_nn_model, daemon=True).start()

        # Overlay
        self.overlay = None
        if use_overlay:
            self.overlay = OverlayWindow()

        # State tracking
        self.prev_hero = []
        self.prev_board = []
        self.prev_hero_turn = False
        self.action_history = ""  # tracks the action sequence for CFR key
        self.last_phase = "PREFLOP"
        self.table_region = None
        self.positioned = False

        # BB/hour tracking
        self.session_start = time.time()
        self.hands_seen = 0
        self.hand_results = []  # list of (timestamp, hero_cards, action, result)

    def _detect_with_yolo(self, table_img):
        """Run YOLO detection on a table image."""
        if self.yolo_detect is None:
            return None
        return self.yolo_detect(table_img, conf=0.4)

    def _identify_cards(self, table_img, card_boxes):
        """Identify cards from detected bounding boxes using full-card template matching."""
        if not card_boxes:
            return []

        # Try screen-captured templates first (exact match for both PS and lab)
        lab_dir = os.path.join(os.path.dirname(__file__), "templates", "screen_cards")
        if os.path.isdir(lab_dir) and not hasattr(self, '_lab_templates'):
            self._lab_templates = {}
            for f in os.listdir(lab_dir):
                if f.endswith('.png'):
                    label = f.replace('.png', '')
                    self._lab_templates[label] = cv2.imread(os.path.join(lab_dir, f))

        results = []
        h, w = table_img.shape[:2]
        for card in card_boxes:
            x1 = max(0, card["x"] - 2)
            y1 = max(0, card["y"] - 2)
            x2 = min(w, card["x"] + card["w"] + 2)
            y2 = min(h, card["y"] + card["h"] + 2)
            crop = table_img[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            best_label = "??"
            best_score = -1
            crop_h, crop_w = crop.shape[:2]
            is_narrow = crop_w < crop_h * 0.55  # overlapping card

            if is_narrow:
                # Narrow/overlapping cards: use corner-based identification
                # Full-card template matching is unreliable here (T vs Q etc.)
                try:
                    from card_id import identify_card
                    label, conf = identify_card(crop, is_narrow=True)
                    if label and label != "??" and conf > 0.2:
                        best_label = label
                        best_score = conf
                except Exception:
                    pass
            else:
                # Full cards: match against lab sprite templates
                if hasattr(self, '_lab_templates'):
                    for label, tmpl in self._lab_templates.items():
                        # Skip narrow-specific templates for full cards
                        if '_narrow' in label:
                            continue
                        tmpl_resized = cv2.resize(tmpl, (crop_w, crop_h))
                        score = cv2.matchTemplate(crop, tmpl_resized, cv2.TM_CCOEFF_NORMED)[0][0]
                        if score > best_score:
                            best_score = score
                            best_label = label

                # Low score fallback: corner-based detection
                if best_score < 0.5:
                    try:
                        from card_id import identify_card
                        label, conf = identify_card(crop)
                        if label and label != "??" and conf > 0.3:
                            best_label = label
                            best_score = conf
                    except Exception:
                        pass

            # Final fallback: card_identify on full image
            if best_score < 0.3 and self.card_identify:
                try:
                    id_results = self.card_identify(table_img, [card])
                    if id_results and id_results[0][1] > 0.3:
                        best_label = id_results[0][0]
                except Exception:
                    pass

            if best_label != "??":
                results.append(best_label)

        return results

    def _detect_with_ocr(self, table_img):
        """Fallback: use the OCR pipeline."""
        try:
            from detect import find_cards_by_color, find_action_buttons, read_text_regions
            from card_id import identify_cards
            cards = find_cards_by_color(table_img)
            hero_ids = [label for label, _ in identify_cards(table_img, cards["hero"])] if cards["hero"] else []
            board_ids = [label for label, _ in identify_cards(table_img, cards["board"])] if cards["board"] else []
            texts = read_text_regions(table_img)
            actions = find_action_buttons(texts)
            return {
                "hero_cards": hero_ids,
                "board_cards": board_ids,
                "hero_turn": len(actions) > 0,
                "pot": None,
                "players": [],
            }
        except Exception as e:
            if self.debug:
                print(f"[OCR fallback error] {e}")
            return None

    def _extract_state(self, table_img):
        """Extract game state from table image using YOLO or OCR fallback."""
        elements = self._detect_with_yolo(table_img)

        if elements is not None:
            # YOLO path (fast) — with fallback for missed detections
            hero_cards = self._identify_cards(table_img, elements.get("hero_card", []))
            board_cards = self._identify_cards(table_img, elements.get("board_card", []))
            hero_turn = len(elements.get("action_button", [])) > 0

            # Fallback: if YOLO missed hero cards, try color-based detection
            if not hero_cards:
                try:
                    from detect import find_cards_by_color
                    from card_id import identify_cards as id_cards
                    color_cards = find_cards_by_color(table_img)
                    if color_cards.get("hero"):
                        hero_cards = [label for label, _ in id_cards(table_img, color_cards["hero"])]
                    if not board_cards and color_cards.get("board"):
                        board_cards = [label for label, _ in id_cards(table_img, color_cards["board"])]
                except Exception:
                    pass

            # Detect action buttons by looking for red/green button colors
            facing_bet = False
            h, w = table_img.shape[:2]
            bottom = table_img[int(h * 0.85):, :]
            hsv_bottom = cv2.cvtColor(bottom, cv2.COLOR_BGR2HSV)
            # Red button (Fold) — present when facing a bet/raise
            red1 = cv2.inRange(hsv_bottom, np.array([0, 80, 80]), np.array([10, 255, 255]))
            red2 = cv2.inRange(hsv_bottom, np.array([160, 80, 80]), np.array([180, 255, 255]))
            # Green button (Check/Call)
            green = cv2.inRange(hsv_bottom, np.array([35, 80, 80]), np.array([85, 255, 255]))
            red_px = cv2.countNonZero(red1) + cv2.countNonZero(red2)
            green_px = cv2.countNonZero(green)
            if red_px > 200 or green_px > 200:
                if not hero_turn:
                    hero_turn = True
                # Red button = Fold = facing a bet/raise
                # Green only (no red) = Check available = not facing a bet
                facing_bet = red_px > 200

            # Try to read pot from pot_text region
            pot = None
            if elements.get("pot_text"):
                try:
                    from detect import read_text_regions, find_pot
                    pt = elements["pot_text"][0]
                    h, w = table_img.shape[:2]
                    crop = table_img[
                        max(0, pt["y"] - 2):min(h, pt["y"] + pt["h"] + 2),
                        max(0, pt["x"] - 2):min(w, pt["x"] + pt["w"] + 2),
                    ]
                    texts = read_text_regions(crop)
                    pot_info = find_pot(texts, pt["h"])
                    if pot_info and "amount" in pot_info:
                        pot = pot_info["amount"]
                except Exception:
                    pass

            # Count active players (player panels + card backs = opponents)
            num_opp = max(1, len(elements.get("card_back", [])))

            # Detect position from dealer button location
            # If dealer button is in bottom half of table → hero is BTN or close to it → IP
            position = "IP"  # default
            dealer_buttons = elements.get("dealer_button", [])
            if dealer_buttons:
                btn = dealer_buttons[0]
                btn_y_pct = btn["y"] / h if h > 0 else 0
                # Bottom 35% = near hero → IP (BTN/CO)
                # Top 65% = away from hero → OOP (UTG/MP/blinds)
                if btn_y_pct < 0.65:
                    position = "OOP"

            return {
                "hero_cards": hero_cards,
                "board_cards": board_cards,
                "hero_turn": hero_turn,
                "facing_bet": facing_bet,
                "pot": pot,
                "num_opponents": num_opp,
                "position": position,
            }
        else:
            # OCR fallback
            result = self._detect_with_ocr(table_img)
            if result:
                result["num_opponents"] = 1
            return result

    def _infer_action_history(self, state):
        """
        Infer the action context from visible UI elements.
        Red fold button visible → facing a bet/raise.
        No red button (only green check) → not facing a bet.
        """
        board = state.get("board_cards", [])
        phase = phase_from_board_count(len(board))
        facing_bet = state.get("facing_bet", False)

        # Street changed — start fresh for current street
        if phase != self.last_phase:
            # Save previous streets' history
            if self.action_history and not self.action_history.endswith("-"):
                self.action_history += "-"
            self.last_phase = phase

        # Split previous streets from current
        parts = self.action_history.rstrip("-").split("-") if self.action_history.rstrip("-") else []

        if phase == "PREFLOP":
            if facing_bet:
                # Fold button visible → someone raised ahead
                self.action_history = "rh"
            else:
                # No fold button → limped to hero or BB option
                self.action_history = "c"
        else:
            # Postflop: preserve previous street history
            prev = "-".join(parts[:-1]) if len(parts) > 1 else (parts[0] if parts else "")
            if facing_bet:
                current = "bh"  # facing a bet
            else:
                current = ""    # checked to hero
            self.action_history = (prev + "-" + current).strip("-") if prev else current

    def _format_solver_result(self, result, hero, board, phase, position):
        """Convert solver response to the same format as CFRLookup.lookup()."""
        strat = result.get("strategy", {})
        # Aggregate into simple actions
        simple = {}
        for a in ["FOLD", "CHECK", "CALL"]:
            if a in strat:
                simple[a] = strat[a]
        bet_sum = sum(strat.get(k, 0) for k in ["BET_HALF", "BET_POT", "BET_ALLIN"])
        raise_sum = sum(strat.get(k, 0) for k in ["RAISE_HALF", "RAISE_POT", "RAISE_ALLIN"])
        if bet_sum > 0.01:
            simple["BET"] = bet_sum
        if raise_sum > 0.01:
            simple["RAISE"] = raise_sum

        # Best action
        best_action = max(simple, key=simple.get) if simple else "CHECK"
        best_prob = simple.get(best_action, 0)

        # Sizing description
        rec_text = best_action
        if best_action == "BET":
            half = strat.get("BET_HALF", 0)
            pot = strat.get("BET_POT", 0)
            allin = strat.get("BET_ALLIN", 0)
            if pot >= half and pot >= allin:
                rec_text = "BET pot-size"
            elif half >= pot:
                rec_text = "BET half-pot"
            else:
                rec_text = "BET all-in"
        elif best_action == "RAISE":
            half = strat.get("RAISE_HALF", 0)
            pot = strat.get("RAISE_POT", 0)
            allin = strat.get("RAISE_ALLIN", 0)
            if pot >= half and pot >= allin:
                rec_text = "RAISE pot-size"
            elif half >= pot:
                rec_text = "RAISE half-pot"
            else:
                rec_text = "RAISE all-in"

        # Hand strength for display
        hero_dicts = [card_str_to_dict(c) for c in hero]
        board_dicts = [card_str_to_dict(c) for c in board]
        hero_dicts = [c for c in hero_dicts if c]
        board_dicts = [c for c in board_dicts if c]
        strength = evaluate_hand_strength(hero_dicts, board_dicts, phase)
        bucket = strength_to_bucket(strength, 50)

        hero_ints = [card_str_to_int(c) for c in hero]
        board_ints = [card_str_to_int(c) for c in board]
        hero_ints = [x for x in hero_ints if x is not None]
        board_ints = [x for x in board_ints if x is not None]
        nn_eq = nn_hand_strength(hero_ints, board_ints, 1) if len(hero_ints) == 2 else None

        solve_ms = result.get("solveTimeMs", 0)
        cached = result.get("cached", False)
        tag = f" [solver {solve_ms}ms]" if not cached else " [cached]"

        return {
            "action_probs": simple,
            "recommended": rec_text,
            "rec_prob": best_prob,
            "equity": strength,
            "nn_equity": nn_eq,
            "info_key": f"solver:{phase}:{bucket}{tag}",
            "bucket": bucket,
            "fallback": False,
        }

    def _get_recommendation(self, state):
        """Get recommendation: try real-time solver first, fall back to table lookup."""
        hero = state["hero_cards"]
        board = state["board_cards"]
        pot = state.get("pot") or 0.10
        num_opp = state.get("num_opponents", 1)
        facing_bet = state.get("facing_bet", False)

        phase = phase_from_board_count(len(board))
        self._infer_action_history(state)
        position = state.get("position", "IP")

        stack = 10.0
        bb = 0.10

        # Tier 1: Real-time subgame solver
        if self.solver and len(hero) >= 2:
            try:
                result = self.solver.solve(
                    hero_cards=hero,
                    board_cards=board,
                    pot=pot,
                    hero_stack=stack,
                    opp_stack=stack,
                    street=phase,
                    hero_position=position,
                    action_history=self.action_history.rstrip("-"),
                    facing_bet=facing_bet,
                )
                if result and result.get("strategy") and len(result["strategy"]) > 0:
                    if self.debug:
                        ms = result.get("solveTimeMs", 0)
                        cached = result.get("cached", False)
                        print(f"[solver] {ms}ms cached={cached} strat={result['strategy']}")
                    return self._format_solver_result(result, hero, board, phase, position)
            except Exception as e:
                if self.debug:
                    print(f"[solver] error: {e}")

        # Tier 2: Pre-trained table lookup
        rec = self.cfr.lookup(
            hero_cards_str=hero,
            board_cards_str=board,
            pot=pot,
            stack=stack,
            bb=bb,
            action_history_str=self.action_history.rstrip("-"),
            num_opponents=num_opp,
            position=position,
        )
        return rec

    def _update_session_display(self):
        """Update overlay title with session stats."""
        elapsed = time.time() - self.session_start
        mins = int(elapsed / 60)
        if self.overlay:
            self.overlay.title_label.config(
                text=f"POKER ADVISOR  |  {self.hands_seen} hands  {mins}m"
            )
        if self.terminal:
            print(f"[Session] {self.hands_seen} hands in {mins}m")

    def _log_recommendation(self, hero, board, state, rec):
        """Log recommendation to file for post-session review."""
        import time
        log_path = os.path.join(os.path.dirname(__file__), "data", "advisor_log.jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "time": time.strftime("%H:%M:%S"),
            "hero": hero,
            "board": board,
            "phase": "PREFLOP" if not board else ("FLOP" if len(board) == 3 else ("TURN" if len(board) == 4 else "RIVER")),
            "recommended_action": rec.get("action", ""),
            "action_probs": {k: round(v, 3) for k, v in rec.get("probs", {}).items() if v > 0.01},
            "equity": rec.get("equity", 0),
            "bucket": rec.get("bucket", 0),
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _print_recommendation(self, state, rec):
        """Print recommendation to terminal."""
        hero_str = " ".join(card_display(c) for c in state["hero_cards"])
        board_str = " ".join(card_display(c) for c in state["board_cards"]) if state["board_cards"] else "(preflop)"

        eq_str = f"{rec['equity']:.0%}"
        nn_str = f" / NN:{rec['nn_equity']:.0%}" if rec.get("nn_equity") is not None else ""

        probs = rec["action_probs"]
        prob_parts = []
        for a in ["FOLD", "CHECK", "CALL", "BET", "RAISE"]:
            if a in probs and probs[a] > 0.01:
                prob_parts.append(f"{a} {probs[a]:.0%}")

        fallback = " [heuristic]" if rec.get("fallback") else ""
        print(f"\n{'='*50}")
        print(f"  Hero: {hero_str}  |  Board: {board_str}")
        print(f"  Equity: {eq_str}{nn_str}  Bucket: {rec['bucket']}")
        print(f"  >>> {rec['recommended']} ({', '.join(prob_parts)}){fallback}")
        print(f"  Key: {rec['info_key']}")
        print(f"{'='*50}")

    def run(self):
        """Main loop: capture, detect, recommend, display."""
        print("\n" + "=" * 50)
        print("  POKER ADVISOR — Real-time CFR Recommendations")
        print("=" * 50)
        print("  Looking for PokerStars table...")
        print("  Right-click overlay to close | Ctrl+C to exit")
        print()

        frame_count = 0
        last_capture = 0
        capture_interval = 0.5  # seconds

        while True:
            try:
                now = time.time()
                if now - last_capture < capture_interval:
                    # Process overlay events between captures
                    if self.overlay:
                        if not self.overlay.update():
                            break  # window closed
                    time.sleep(0.05)
                    continue

                last_capture = now

                # 1. Capture screen (or specific window)
                # Re-find window periodically in case it moved
                if self.table_id is not None and frame_count % 20 == 0:
                    self.window_rect = find_poker_window_by_table(self.table_id)
                frame = capture_screen(self.window_rect)

                # 2. Find table
                region = find_table_region(frame)
                if not region:
                    if frame_count % 10 == 0:
                        if self.overlay:
                            self.overlay.show_no_table()
                        if self.terminal or self.debug:
                            print(".", end="", flush=True)
                    frame_count += 1
                    continue

                self.table_region = region

                # Position overlay near table (once)
                if self.overlay and not self.positioned:
                    self.overlay.position_near_table(region)
                    self.positioned = True

                # 3. Crop table
                table_img, offset = crop_table(frame, region)

                # 4. Detect elements
                state = self._extract_state(table_img)
                if state is None:
                    frame_count += 1
                    continue

                if self.debug and frame_count % 20 == 0:
                    hero = state.get("hero_cards", [])
                    board = state.get("board_cards", [])
                    turn = state.get("hero_turn", False)
                    print(f"[debug] hero={hero} board={board} turn={turn}")

                # 5. Update overlay whenever state changes
                hero = state.get("hero_cards", [])
                board = state.get("board_cards", [])
                hero_turn = state.get("hero_turn", False)

                if len(hero) >= 2:
                    # State changed? Recalculate
                    if hero != self.prev_hero or board != self.prev_board or hero_turn != self.prev_hero_turn:
                        self.prev_hero = hero
                        self.prev_board = board
                        self.prev_hero_turn = hero_turn

                        # Get recommendation
                        rec = self._get_recommendation(state)
                        if rec:
                            if self.overlay:
                                self.overlay.show_recommendation(hero, board, rec)
                            if self.terminal or self.debug:
                                self._print_recommendation(state, rec)
                            # Log recommendation for post-session review
                            if hero_turn:
                                self._log_recommendation(hero, board, state, rec)
                else:
                    if self.prev_hero:
                        # Had hero cards, now gone — new hand
                        self.prev_hero_turn = False
                        if self.overlay:
                            self.overlay.show_waiting("Waiting...")

                    # New hand detection: if board changed to empty, reset history
                    if not board and self.prev_board:
                        self.action_history = ""
                        self.last_phase = "PREFLOP"
                        self.hands_seen += 1
                        self._update_session_display()
                    self.prev_hero = hero
                    self.prev_board = board

                frame_count += 1

            except KeyboardInterrupt:
                print("\n\nAdvisor stopped.")
                break
            except Exception as e:
                if self.debug:
                    import traceback
                    traceback.print_exc()
                else:
                    print(f"\n[Error] {e}")
                time.sleep(1)

        if self.solver:
            try:
                self.solver.quit()
            except Exception:
                pass

        if self.overlay:
            try:
                self.overlay.root.destroy()
            except Exception:
                pass


# ── Demo mode (no live screen capture) ───────────────────────────────────

def demo():
    """Run a demo showing advisor recommendations for sample hands."""
    print("\n" + "=" * 50)
    print("  POKER ADVISOR — Demo Mode")
    print("=" * 50 + "\n")

    cfr = CFRLookup()

    # Sample hands
    samples = [
        {
            "desc": "Preflop: Pocket Aces",
            "hero": ["As", "Ah"],
            "board": [],
            "pot": 0.15,
            "stack": 10.0,
            "history": "",
        },
        {
            "desc": "Preflop: 7-2 offsuit (worst hand)",
            "hero": ["7h", "2c"],
            "board": [],
            "pot": 0.15,
            "stack": 10.0,
            "history": "",
        },
        {
            "desc": "Preflop: AK suited, facing raise",
            "hero": ["Ah", "Kh"],
            "board": [],
            "pot": 0.35,
            "stack": 10.0,
            "history": "rh",
        },
        {
            "desc": "Flop: Top pair, opponent bet",
            "hero": ["Ah", "Kd"],
            "board": ["Ac", "7s", "3d"],
            "pot": 0.60,
            "stack": 9.70,
            "history": "rhc-bh",
        },
        {
            "desc": "Flop: Flush draw",
            "hero": ["Jh", "Th"],
            "board": ["Ah", "5h", "2c"],
            "pot": 0.50,
            "stack": 9.75,
            "history": "rhc-k",
        },
        {
            "desc": "River: Missed draw, opponent bet",
            "hero": ["Jh", "Th"],
            "board": ["Ah", "5h", "2c", "8d", "3s"],
            "pot": 1.50,
            "stack": 8.50,
            "history": "rhc-kbhc-kbhc-bh",
        },
        {
            "desc": "Turn: Set of Kings",
            "hero": ["Kh", "Kd"],
            "board": ["Ks", "9c", "4d", "2h"],
            "pot": 1.20,
            "stack": 8.80,
            "history": "rhc-bhc-k",
        },
    ]

    for s in samples:
        print(f"\n--- {s['desc']} ---")
        hero_str = " ".join(card_display(c) for c in s["hero"])
        board_str = " ".join(card_display(c) for c in s["board"]) if s["board"] else "(preflop)"
        print(f"  Hero: {hero_str}  |  Board: {board_str}")

        rec = cfr.lookup(
            hero_cards_str=s["hero"],
            board_cards_str=s["board"],
            pot=s["pot"],
            stack=s["stack"],
            bb=0.10,
            action_history_str=s["history"],
            num_opponents=1,
        )

        if rec:
            eq_str = f"{rec['equity']:.0%}"
            nn_str = f" / NN:{rec['nn_equity']:.0%}" if rec.get("nn_equity") is not None else ""

            probs = rec["action_probs"]
            prob_parts = []
            for a in ["FOLD", "CHECK", "CALL", "BET", "RAISE"]:
                if a in probs and probs[a] > 0.01:
                    prob_parts.append(f"{a} {probs[a]:.0%}")

            fallback = " [heuristic]" if rec.get("fallback") else ""
            print(f"  Equity: {eq_str}{nn_str}  Bucket: {rec['bucket']}")
            print(f"  >>> {rec['recommended']} ({', '.join(prob_parts)}){fallback}")
            print(f"  Key: {rec['info_key']}")
        else:
            print("  (no recommendation)")

    print("\n" + "=" * 50)


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Poker Advisor — Real-time CFR recommendations")
    parser.add_argument("--terminal", action="store_true", help="Terminal output only (no overlay)")
    parser.add_argument("--debug", action="store_true", help="Show detection debug info")
    parser.add_argument("--demo", action="store_true", help="Run demo mode (no screen capture)")
    parser.add_argument("--no-overlay", action="store_true", help="Disable overlay window")
    parser.add_argument("--table", type=int, default=None, help="Target specific table window by ID (for multi-table)")
    args = parser.parse_args()

    if args.demo:
        demo()
        return

    use_overlay = not args.terminal and not args.no_overlay
    advisor = Advisor(
        use_overlay=use_overlay,
        terminal=args.terminal or args.debug,
        debug=args.debug,
        table_id=args.table,
    )
    advisor.run()


if __name__ == "__main__":
    main()
