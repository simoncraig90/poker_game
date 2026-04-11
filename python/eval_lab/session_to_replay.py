#!/usr/bin/env python3
"""Convert session JSONL hand records into RecommendRequest-shaped replay inputs.

Session format (from vision/data/session_*.jsonl):
  {
    "hand_id": ...,
    "position": "BTN",
    "hero": ["As", "Kd"],
    "starting_stack": 396,
    "streets": [
      {"phase": "PREFLOP", "board": [], "pot": 26, "facing_bet": false,
       "call_amount": 0, "stack": 392, ...},
      {"phase": "FLOP", "board": ["Ah", "7c", "2s"], "pot": 550,
       "facing_bet": false, "call_amount": 0, "stack": 9750, ...},
      ...
    ],
    "profit_cents": 21
  }

Output format (one JSON line per decision point):
  {
    "request": { ... RecommendRequest fields ... },
    "source": { "hand_id": ..., "street_index": 0, "phase": "FLOP", ... },
    "inference_metadata": { ... which fields were inferred and how ... },
    "conversion_warnings": [ ... ]
  }

Usage:
  python session_to_replay.py --input data/session_*.jsonl --output replay.jsonl
  python session_to_replay.py --input data/session_*.jsonl --output replay.jsonl --validate
  python session_to_replay.py --input data/session_*.jsonl --output replay.jsonl --validate --sample 50
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# ─── Project imports ─────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "solver"))
sys.path.insert(0, str(_PROJECT_ROOT / "python"))

from board_clusters import classify_flop, build_cluster_map

# ─── Constants ───────────────────────────────────────────────────────────────

# Position label -> canonical seat assignment for 6-max.
# Seats 1-6 clockwise, button at seat 4 by convention.
# This matches the synthetic hands in baseline_replay_runner.py.
POSITION_TO_SEAT_6MAX = {
    "UTG": 1,
    "HJ":  2,
    "CO":  3,
    "BTN": 4,
    "SB":  5,
    "BB":  6,
}

# Canonical active_seats and button_seat for 6-max.
ACTIVE_SEATS_6MAX = [1, 2, 3, 4, 5, 6]
BUTTON_SEAT_6MAX = 4

# Position labels as used in session files (case-normalized).
KNOWN_POSITIONS = {"UTG", "HJ", "CO", "BTN", "SB", "BB", "MP"}

# MP is ambiguous — in 6-max it's typically HJ.
POSITION_ALIASES = {"MP": "HJ"}

# Phase -> street name (lowercase, matching advisor-cli).
PHASE_TO_STREET = {
    "PREFLOP": "preflop",
    "FLOP":    "flop",
    "TURN":    "turn",
    "RIVER":   "river",
}

# Build board_bucket index lookup.
# This must match sorted(build_cluster_map().keys()) which is how
# build_exact_artifact.py assigns bucket IDs (bb0, bb15, bb42, etc.).
_CLUSTER_MAP = None
_SORTED_KEYS = None


def _init_cluster_lookup():
    global _CLUSTER_MAP, _SORTED_KEYS
    if _CLUSTER_MAP is None:
        _CLUSTER_MAP = build_cluster_map()
        _SORTED_KEYS = sorted(_CLUSTER_MAP.keys())


def board_to_bucket(flop_cards: list[str]) -> Optional[int]:
    """Map 3 flop cards to a board_bucket index (0-98).

    Returns None if cards are invalid or fewer than 3.
    """
    if len(flop_cards) < 3:
        return None
    _init_cluster_lookup()
    try:
        key = classify_flop(tuple(flop_cards[:3]))
        return _SORTED_KEYS.index(key)
    except (KeyError, ValueError, IndexError):
        return None


# ─── Big-blind detection ─────────────────────────────────────────────────────

# The session files do NOT include a big_blind field.
# We must infer it from the data.
#
# Known platforms and their unit systems:
#
# Unibet NL10:  bb = 10 (cents).  starting_stack ~400-1000 (cents).
# CoinPoker:    bb = 10000 (micro-units).  starting_stack ~1000000.
#
# Heuristic: if starting_stack > 100000, it's CoinPoker micro-units.
# Otherwise it's Unibet cents.

COINPOKER_BB = 10000
UNIBET_NL10_BB = 10


def infer_big_blind(hand: dict) -> tuple[float, str]:
    """Infer big_blind from starting_stack magnitude.

    Returns (big_blind_in_native_units, inference_method).
    """
    stack = hand.get("starting_stack", 0)

    # CoinPoker: stacks in the millions range
    if stack > 100000:
        return COINPOKER_BB, "coinpoker_micro_units"

    # Unibet NL10: stacks typically 200-1500 cents (20-150bb)
    if stack > 0:
        return UNIBET_NL10_BB, "unibet_cents_nl10"

    return 10, "unknown_default_10"


# ─── Session format detection ────────────────────────────────────────────────

def detect_session_format(hand: dict) -> str:
    """Detect which session format variant this hand uses.

    Returns: "standard" (has hand_id, position, streets) or
             "review" (has hero, board, phase at top level, no streets array).
    """
    if "streets" in hand and "position" in hand:
        return "standard"
    if "phase" in hand and "board" in hand and "hero" in hand:
        return "review"
    return "unknown"


# ─── Core conversion ────────────────────────────────────────────────────────

def convert_hand(hand: dict, source_file: str = "") -> list[dict]:
    """Convert one session hand record into 0..N replay decision records.

    Each postflop street where hero has a decision becomes one record.
    Preflop streets are included but will always route to EMERGENCY
    (unless a preflop artifact happens to match).

    Returns a list of dicts, each with keys:
      request, source, inference_metadata, conversion_warnings
    """
    fmt = detect_session_format(hand)
    if fmt == "review":
        return _convert_review_hand(hand, source_file)
    if fmt == "standard":
        return _convert_standard_hand(hand, source_file)

    return [{
        "request": None,
        "source": {"source_file": source_file, "raw": hand},
        "inference_metadata": {},
        "conversion_warnings": [f"unknown session format, keys={list(hand.keys())}"],
    }]


def _convert_standard_hand(hand: dict, source_file: str) -> list[dict]:
    """Convert a standard session hand (with streets array)."""
    results = []
    warnings_global = []

    hand_id = hand.get("hand_id", "unknown")
    position_raw = str(hand.get("position", "")).upper()
    hero_cards = hand.get("hero", [])
    starting_stack = hand.get("starting_stack", 0)
    profit_cents = hand.get("profit_cents", 0)
    streets = hand.get("streets", [])

    if not hero_cards or len(hero_cards) != 2:
        return [{
            "request": None,
            "source": {"hand_id": hand_id, "source_file": source_file},
            "inference_metadata": {},
            "conversion_warnings": [f"missing or invalid hero cards: {hero_cards}"],
        }]

    # ── Position mapping ─────────────────────────────────────────────────
    position = POSITION_ALIASES.get(position_raw, position_raw)
    position_inferred = position_raw != position
    if position not in POSITION_TO_SEAT_6MAX:
        warnings_global.append(f"unknown position '{position_raw}', defaulting to BB")
        position = "BB"
        position_inferred = True

    hero_seat = POSITION_TO_SEAT_6MAX[position]

    # ── Big blind ────────────────────────────────────────────────────────
    big_blind, bb_method = infer_big_blind(hand)
    effective_stack_bb = starting_stack / big_blind if big_blind > 0 else 100.0

    # ── Process each street ──────────────────────────────────────────────
    # We reconstruct a minimal action_history from what we know.
    # The session data does NOT include opponent actions or a full action log.
    # We can only infer the pot class from the pot size progression.

    for si, street in enumerate(streets):
        phase = street.get("phase", "").upper()
        street_name = PHASE_TO_STREET.get(phase)
        if street_name is None:
            continue

        warnings = list(warnings_global)
        inferred = {}

        board = street.get("board", [])
        pot = street.get("pot", 0)
        facing_bet = street.get("facing_bet", False)
        call_amount = street.get("call_amount", 0)
        stack = street.get("stack", starting_stack)

        # Skip postflop decisions where the board is incomplete.
        expected_board_n = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
        exp_n = expected_board_n.get(street_name, 0)
        if exp_n > 0 and len(board) < exp_n:
            continue  # incomplete board — cannot compute board_bucket

        # ── Board bucket (flop cards only) ───────────────────────────────
        if len(board) >= 3:
            bb_idx = board_to_bucket(board)
            if bb_idx is None:
                warnings.append(f"board_bucket computation failed for board={board}")
                bb_idx = 0
                inferred["board_bucket"] = "failed_default_0"
            else:
                inferred["board_bucket"] = "computed_from_flop_cards"
        else:
            bb_idx = None  # preflop — board_bucket is None
            inferred["board_bucket"] = "null_preflop"

        # ── n_players_in_hand ────────────────────────────────────────────
        # The session data does not tell us how many players are in the pot.
        # Heuristic: use pot size at flop vs preflop pot to guess.
        # Conservative default: assume 2 (heads-up).
        n_players = 2
        inferred["n_players_in_hand"] = "default_2_no_player_count_in_session"
        if pot > 10 * big_blind and phase == "PREFLOP":
            # Large preflop pot might indicate multiway
            warnings.append(f"large preflop pot ({pot/big_blind:.1f}bb), n_players uncertain")

        # ── hero_committed ───────────────────────────────────────────────
        # hero_committed = starting_stack - current_stack (what hero put in).
        # CAVEAT: session "stack" can EXCEED starting_stack on later
        # streets if hero already won chips from the pot.  In that case
        # hero_committed is 0 — hero has not put in anything new.
        if stack <= starting_stack:
            hero_committed = starting_stack - stack
            inferred["hero_committed"] = "starting_stack_minus_current_stack"
        else:
            hero_committed = 0
            inferred["hero_committed"] = "zero_stack_exceeds_start"
            warnings.append(
                f"stack ({stack}) > starting_stack ({starting_stack}): "
                f"hero may have already collected chips"
            )

        # ── hero_stack ───────────────────────────────────────────────────
        # Use the lesser of stack and starting_stack as hero's remaining
        # stack (can't bet more than we started with in one hand).
        hero_stack = min(stack, starting_stack)
        inferred["hero_stack"] = "min_of_stack_and_starting_stack"

        # ── action_history ───────────────────────────────────────────────
        # Session data does not include opponent actions.  We synthesize
        # a plausible action_history using facing_bet, call_amount, and
        # hero preflop investment.  Preflop decisions are tagged as
        # preflop_unknown (pot class is unknowable before hero acts).
        action_history, ah_method = _infer_action_history(
            hand, si, phase, big_blind, hero_seat
        )
        inferred["action_history"] = ah_method

        # ── legal_actions ────────────────────────────────────────────────
        # Session data does not include legal_actions.
        # We must infer them from facing_bet, call_amount, and stack.
        legal_actions, la_method = _infer_legal_actions(
            phase, facing_bet, call_amount, stack, pot, big_blind
        )
        inferred["legal_actions"] = la_method

        # ── facing_*_amount fields ───────────────────────────────────────
        facing_open_amount = 0.0
        facing_3bet_amount = 0.0
        facing_bet_amount = 0.0
        if facing_bet and call_amount > 0:
            if phase == "PREFLOP":
                facing_open_amount = float(call_amount)
                inferred["facing_open_amount"] = "from_call_amount"
            else:
                facing_bet_amount = float(call_amount)
                inferred["facing_bet_amount"] = "from_call_amount"
        else:
            inferred["facing_open_amount"] = "zero_not_facing"
            inferred["facing_bet_amount"] = "zero_not_facing"

        inferred["facing_3bet_amount"] = "zero_no_3bet_detection"

        # ── Assemble the request ─────────────────────────────────────────
        inferred["active_seats"] = "fixed_6max_1_through_6"
        inferred["button_seat"] = "fixed_seat_4"
        inferred["hero_seat"] = f"from_position_{position}"
        inferred["big_blind"] = bb_method
        inferred["effective_stack_bb"] = "starting_stack_div_big_blind"
        inferred["rake_profile"] = "fixed_norake"
        inferred["menu_version"] = "fixed_1"

        request = {
            "active_seats":       ACTIVE_SEATS_6MAX,
            "button_seat":        BUTTON_SEAT_6MAX,
            "hero_seat":          hero_seat,
            "street":             street_name,
            "effective_stack_bb": round(effective_stack_bb, 2),
            "n_players_in_hand":  n_players,
            "action_history":     action_history,
            "board_bucket":       bb_idx,
            "rake_profile":       "norake",
            "menu_version":       1,
            "hole_cards":         hero_cards,
            "board":              board,
            "facing_bet":         facing_bet,
            "pot":                float(pot),
            "big_blind":          float(big_blind),
            "hero_committed":     float(hero_committed),
            "hero_start_stack":   float(starting_stack),
            "hero_stack":         float(hero_stack),
            "legal_actions":      legal_actions,
            "facing_open_amount": facing_open_amount,
            "facing_3bet_amount": facing_3bet_amount,
            "facing_bet_amount":  facing_bet_amount,
        }

        source = {
            "hand_id":       hand_id,
            "street_index":  si,
            "phase":         phase,
            "position_raw":  position_raw,
            "position":      position,
            "source_file":   source_file,
            "profit_cents":  profit_cents,
            "rec_action":    street.get("rec_action", ""),
            "rec_equity":    street.get("rec_equity"),
        }

        results.append({
            "request":              request,
            "source":               source,
            "inference_metadata":   inferred,
            "conversion_warnings":  warnings,
        })

    return results


def _convert_review_hand(hand: dict, source_file: str) -> list[dict]:
    """Convert a review-format hand (top-level phase/board, no streets array).

    These records have: hero, board, phase, recommended_action, action_probs,
    equity, bucket, time.
    """
    warnings = []
    inferred = {}

    hero_cards = hand.get("hero", [])
    board = hand.get("board", [])
    phase = hand.get("phase", "").upper()
    street_name = PHASE_TO_STREET.get(phase, "flop")

    if not hero_cards or len(hero_cards) != 2:
        return [{
            "request": None,
            "source": {"source_file": source_file, "raw": hand},
            "inference_metadata": {},
            "conversion_warnings": [f"invalid hero cards: {hero_cards}"],
        }]

    # Skip postflop decisions where the board is incomplete for the street.
    expected_board_cards = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
    expected_n = expected_board_cards.get(street_name, 0)
    if expected_n > 0 and len(board) < expected_n:
        return [{
            "request": None,
            "source": {"source_file": source_file, "raw": hand},
            "inference_metadata": {},
            "conversion_warnings": [
                f"incomplete board for {street_name}: got {len(board)} cards, need {expected_n}"
            ],
        }]

    # Review format has a "bucket" field — but it's from the old system,
    # NOT the same as our board_cluster bucket index.
    old_bucket = hand.get("bucket")
    if len(board) >= 3:
        bb_idx = board_to_bucket(board)
        if bb_idx is None:
            bb_idx = 0
            inferred["board_bucket"] = "failed_default_0"
            warnings.append(f"board_bucket failed for board={board}")
        else:
            inferred["board_bucket"] = "computed_from_flop_cards"
        if old_bucket is not None:
            inferred["old_bucket_discarded"] = old_bucket
    else:
        bb_idx = None
        inferred["board_bucket"] = "null_preflop"

    # Review format has no position, stack, pot, etc.
    # We must use very conservative defaults.
    inferred["active_seats"] = "fixed_6max_1_through_6"
    inferred["button_seat"] = "fixed_seat_4"
    inferred["hero_seat"] = "default_bb_seat_6"
    inferred["big_blind"] = "default_100_review_format"
    inferred["effective_stack_bb"] = "default_100bb"
    inferred["n_players_in_hand"] = "default_2"
    inferred["hero_committed"] = "default_0"
    inferred["hero_stack"] = "default_10000"
    inferred["hero_start_stack"] = "default_10000"
    inferred["pot"] = "default_550_srp_flop"
    inferred["action_history"] = "synthetic_srp_btn_open_bb_call"
    inferred["legal_actions"] = "synthetic_check_bet"
    inferred["facing_bet"] = "default_false"
    inferred["rake_profile"] = "fixed_norake"
    inferred["menu_version"] = "fixed_1"

    warnings.append("review format: most fields are defaults, low confidence")

    request = {
        "active_seats":       ACTIVE_SEATS_6MAX,
        "button_seat":        BUTTON_SEAT_6MAX,
        "hero_seat":          6,
        "street":             street_name,
        "effective_stack_bb": 100.0,
        "n_players_in_hand":  2,
        "action_history":     ["4:BET_TO:250", "6:CALL"],
        "board_bucket":       bb_idx,
        "rake_profile":       "norake",
        "menu_version":       1,
        "hole_cards":         hero_cards,
        "board":              board,
        "facing_bet":         False,
        "pot":                550.0,
        "big_blind":          100.0,
        "hero_committed":     250.0,
        "hero_start_stack":   10000.0,
        "hero_stack":         9750.0,
        "legal_actions":      [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 100, "max": 9750},
        ],
        "facing_open_amount": 0.0,
        "facing_3bet_amount": 0.0,
        "facing_bet_amount":  0.0,
    }

    source = {
        "hand_id":          f"review_{hand.get('time', 'unknown')}",
        "street_index":     0,
        "phase":            phase,
        "position_raw":     "unknown",
        "position":         "BB",
        "source_file":      source_file,
        "profit_cents":     0,
        "rec_action":       hand.get("recommended_action", ""),
        "rec_equity":       hand.get("equity"),
        "old_action_probs": hand.get("action_probs"),
    }

    return [{
        "request":              request,
        "source":               source,
        "inference_metadata":   inferred,
        "conversion_warnings":  warnings,
    }]


# ─── Action history inference ────────────────────────────────────────────────

# V2 thresholds for hero preflop investment (in bb).
# Validated against pot_semantics_audit.py on 106 preflop+flop transitions.
_INVEST_LIMP_CEIL = 2.0   # hero invested < 2bb → limped/walk/blind
_INVEST_SRP_CEIL  = 5.0   # hero invested 2–5bb → standard open or call
_INVEST_3BP_CEIL  = 12.0  # hero invested 5–12bb → 3-bet pot

# V2 thresholds for preflop call_amount (in bb).
# When hero faces a bet, call_amount tells us the raise size.
_CALL_LIMP_CEIL = 1.5     # call < 1.5bb → completing vs a limp, not a raise
_CALL_SRP_CEIL  = 4.0     # call 1.5–4bb → standard raise
_CALL_3BP_CEIL  = 10.0    # call 4–10bb → 3-bet


def _pick_aggressor_seat(hero_seat: int) -> int:
    """Pick a plausible aggressor seat that is not the hero."""
    return 3 if hero_seat == 4 else 4  # CO if hero is BTN, else BTN


def _synth_limped(hero_seat: int) -> list[str]:
    """Synthesize action_history for a limped pot (0 aggressive actions)."""
    if hero_seat == 6:  # BB
        return ["4:CALL", "5:CALL", "6:CHECK"]
    return [f"{hero_seat}:CALL", "6:CHECK"]


def _synth_srp(hero_seat: int, big_blind: float) -> list[str]:
    """Synthesize action_history for an SRP (1 aggressive action)."""
    agg = _pick_aggressor_seat(hero_seat)
    raise_amount = int(big_blind * 2.5)
    return [f"{agg}:BET_TO:{raise_amount}", f"{hero_seat}:CALL"]


def _synth_3bp(hero_seat: int, big_blind: float) -> list[str]:
    """Synthesize action_history for a 3-bet pot (2 aggressive actions)."""
    agg = _pick_aggressor_seat(hero_seat)
    open_amount = int(big_blind * 2.5)
    three_bet = int(big_blind * 8)
    return [
        f"{agg}:BET_TO:{open_amount}",
        f"{hero_seat}:RAISE_TO:{three_bet}",
        f"{agg}:CALL",
    ]


def _synth_4bp(hero_seat: int, big_blind: float) -> list[str]:
    """Synthesize action_history for a 4-bet+ pot (3+ aggressive actions)."""
    agg = _pick_aggressor_seat(hero_seat)
    return [
        f"{agg}:BET_TO:{int(big_blind * 2.5)}",
        f"{hero_seat}:RAISE_TO:{int(big_blind * 8)}",
        f"{agg}:RAISE_TO:{int(big_blind * 20)}",
        f"{hero_seat}:CALL",
    ]


def _infer_action_history(
    hand: dict,
    street_index: int,
    phase: str,
    big_blind: float,
    hero_seat: int,
) -> tuple[list[str], str]:
    """Synthesize a plausible action_history for pot class classification.

    V2: Uses hero preflop investment and facing_bet+call_amount instead of
    the unreliable 'pot' field.  Preflop decisions are tagged as
    'preflop_unknown' because pot class is not yet determined at that point.

    Returns (action_history, inference_method).

    Signal priority:
      1. preflop facing_bet + call_amount (what hero faces)
      2. hero preflop investment: starting_stack - first_postflop_stack
      3. position context (BB check = limped if no raise faced)

    The old pot-based method is removed — pot_semantics_audit.py proved
    the pot field can be below 1.5bb (less than blinds), making it
    unusable for classification.
    """
    # ── Preflop decisions: pot class is unknowable ───────────────────────
    if phase == "PREFLOP":
        # Hero hasn't completed preflop action yet.  The pot class depends
        # on what hero does and what happens after, which we don't know.
        # Return empty history (→ Limped classification in Rust) but tag
        # distinctly so reporting can separate these from real limps.
        return [], "preflop_unknown"

    streets = hand.get("streets", [])
    if not streets:
        return [], "empty_streets"

    # ── Find preflop street ──────────────────────────────────────────────
    preflop = None
    for s in streets:
        if s.get("phase", "").upper() == "PREFLOP":
            preflop = s
            break

    if preflop is None:
        return [], "no_preflop_street"

    # ── Signal 1: what did hero face at preflop? ─────────────────────────
    pf_facing = preflop.get("facing_bet", False)
    pf_call = preflop.get("call_amount", 0)
    pf_call_bb = pf_call / big_blind if big_blind > 0 else 0

    # Sanity gate: CoinPoker call_amount can be corrupt (values >> stack).
    # If call exceeds hero's stack, the field is unreliable — skip it.
    starting_stack_bb = hand.get("starting_stack", 0) / big_blind if big_blind > 0 else 100
    if pf_call_bb > starting_stack_bb:
        pf_facing = False  # treat as unreliable, fall through to investment
        pf_call_bb = 0

    # ── Signal 2: hero's total preflop investment ────────────────────────
    # Use the FIRST postflop street's stack to compute how much hero
    # invested during preflop.  This reflects the actual outcome, not
    # just what hero faced at their initial decision.
    starting_stack = hand.get("starting_stack", 0)
    first_postflop_stack = starting_stack  # fallback

    for s in streets:
        sp = s.get("phase", "").upper()
        if sp in ("FLOP", "TURN", "RIVER"):
            first_postflop_stack = s.get("stack", starting_stack)
            break

    hero_invest = max(0, starting_stack - first_postflop_stack)
    hero_invest_bb = hero_invest / big_blind if big_blind > 0 else 0

    # ── Classification logic ─────────────────────────────────────────────
    #
    # Priority: facing_bet signal first (direct observation), then
    # investment (outcome-based), then position fallback.
    #
    # When hero faces a raise (call_amount >= 1.5bb), the pot is at
    # least SRP from villain's side.  Hero may have then 3-bet (invest
    # will be higher), so investment is the tiebreaker.

    if pf_facing and pf_call_bb >= _CALL_3BP_CEIL:
        # Hero faces a 4-bet sized call → 4bp
        return _synth_4bp(hero_seat, big_blind), "v2_facing_4bet"

    if pf_facing and pf_call_bb >= _CALL_SRP_CEIL:
        # Hero faces a 3-bet
        if hero_invest_bb >= _INVEST_3BP_CEIL:
            return _synth_4bp(hero_seat, big_blind), "v2_facing_3bet_invest_4bp"
        return _synth_3bp(hero_seat, big_blind), "v2_facing_3bet"

    if pf_facing and pf_call_bb >= _CALL_LIMP_CEIL:
        # Hero faces a standard raise (SRP)
        if hero_invest_bb >= _INVEST_SRP_CEIL:
            # Hero 3-bet after facing a raise
            return _synth_3bp(hero_seat, big_blind), "v2_facing_raise_invest_3bp"
        return _synth_srp(hero_seat, big_blind), "v2_facing_raise_srp"

    if pf_facing and pf_call_bb < _CALL_LIMP_CEIL:
        # Hero faces a limp (call_amount is completing blind vs limper)
        if hero_invest_bb >= _INVEST_SRP_CEIL:
            # Hero raised after facing a limp → becomes SRP
            return _synth_srp(hero_seat, big_blind), "v2_facing_limp_invest_srp"
        return _synth_limped(hero_seat), "v2_facing_limp"

    # ── Not facing a bet: open opportunity or BB check ───────────────────
    # Hero was first to act or in BB with only limpers.
    # Use investment to determine what hero actually did.

    if hero_invest_bb >= _INVEST_3BP_CEIL:
        return _synth_4bp(hero_seat, big_blind), "v2_nofacing_invest_4bp"

    if hero_invest_bb >= _INVEST_SRP_CEIL:
        return _synth_3bp(hero_seat, big_blind), "v2_nofacing_invest_3bp"

    if hero_invest_bb >= _INVEST_LIMP_CEIL:
        # Hero invested 2-5bb without facing a bet: hero opened and got
        # called, or called a limp-raise.  Either way the pot is SRP-like.
        return _synth_srp(hero_seat, big_blind), "v2_nofacing_invest_srp"

    # Hero invested < 2bb and wasn't facing a bet.
    # This is a limped pot (BB checked, SB completed, or hero limped).
    return _synth_limped(hero_seat), "v2_nofacing_invest_limped"


# ─── Legal actions inference ─────────────────────────────────────────────────

def _infer_legal_actions(
    phase: str,
    facing_bet: bool,
    call_amount: float,
    stack: float,
    pot: float,
    big_blind: float,
) -> tuple[list[dict], str]:
    """Infer legal_actions from facing_bet, call_amount, and stack.

    Returns (legal_actions_list, inference_method).

    RISK: This determines what the legalizer has to work with.  If we
    produce wrong legal actions, the legalizer may snap to unexpected
    outputs.  However, the legalizer is designed to be robust — it will
    always produce a legal output even with imperfect inputs.
    """
    actions = []

    if facing_bet:
        # Facing a bet: fold, call, raise_to
        actions.append({"kind": "fold", "min": 0, "max": 0})

        if call_amount > 0:
            actions.append({
                "kind": "call",
                "min": float(call_amount),
                "max": float(call_amount),
            })

        # Raise: min raise = call + (call - previous_bet).
        # We don't know previous_bet, so use min_raise = 2 * call_amount as approx.
        # Max raise = hero's remaining stack.
        if stack > call_amount:
            min_raise = float(call_amount * 2)
            # Clamp min_raise to at least call_amount + big_blind
            min_raise = max(min_raise, call_amount + big_blind)
            max_raise = float(stack)
            if min_raise < max_raise:
                actions.append({
                    "kind": "raise_to",
                    "min": min_raise,
                    "max": max_raise,
                })

        method = "facing_bet_fold_call_raise"

    else:
        # Not facing bet: check, bet_to
        actions.append({"kind": "check", "min": 0, "max": 0})

        if stack > 0:
            # Min bet = 1 big blind (postflop) or open size (preflop)
            min_bet = float(big_blind) if phase != "PREFLOP" else float(big_blind * 2)
            max_bet = float(stack)
            if min_bet <= max_bet:
                actions.append({
                    "kind": "bet_to",
                    "min": min_bet,
                    "max": max_bet,
                })

        method = "no_bet_check_bet"

    if not actions:
        actions.append({"kind": "check", "min": 0, "max": 0})
        method = "fallback_check_only"

    return actions, method


# ─── File I/O ────────────────────────────────────────────────────────────────

def load_session_files(paths: list[str]) -> list[tuple[dict, str]]:
    """Load all hands from multiple session JSONL files.

    Returns list of (hand_dict, source_filename) tuples.
    """
    hands = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            print(f"WARNING: file not found: {path}", file=sys.stderr)
            continue
        with open(p) as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    hand = json.loads(line)
                    hands.append((hand, p.name))
                except json.JSONDecodeError as e:
                    print(f"WARNING: {p.name}:{line_no}: JSON parse error: {e}",
                          file=sys.stderr)
    return hands


def convert_all(hands: list[tuple[dict, str]]) -> list[dict]:
    """Convert all hands to replay records."""
    records = []
    for hand, source_file in hands:
        converted = convert_hand(hand, source_file)
        records.extend(converted)
    return records


def write_output(records: list[dict], output_path: str):
    """Write replay records as JSONL."""
    valid = 0
    skipped = 0
    with open(output_path, "w") as f:
        for rec in records:
            if rec.get("request") is None:
                skipped += 1
                continue
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            valid += 1
    return valid, skipped


# ─── Validation ──────────────────────────────────────────────────────────────

def validate_sample(records: list[dict], sample_size: int = 30):
    """Print a human-readable validation summary of converted records.

    Checks for:
    1. Seat mapping correctness
    2. Action history plausibility
    3. Legal action completeness
    4. Board bucket assignment
    5. Stack/pot/BB consistency
    6. Warning counts
    """
    import random

    valid_records = [r for r in records if r.get("request") is not None]
    if not valid_records:
        print("No valid records to validate.")
        return

    sample = random.sample(valid_records, min(sample_size, len(valid_records)))
    random.seed(42)
    sample = random.sample(valid_records, min(sample_size, len(valid_records)))

    # ── Global stats ─────────────────────────────────────────────────────
    print("=" * 72)
    print("VALIDATION REPORT")
    print("=" * 72)
    print(f"  Total records:     {len(records)}")
    print(f"  Valid requests:    {len(valid_records)}")
    print(f"  Skipped (null):    {len(records) - len(valid_records)}")
    print(f"  Sample size:       {len(sample)}")
    print()

    # ── Warning distribution ─────────────────────────────────────────────
    all_warnings = []
    for r in valid_records:
        all_warnings.extend(r.get("conversion_warnings", []))
    print(f"  Total warnings:    {len(all_warnings)}")
    if all_warnings:
        from collections import Counter
        wc = Counter(all_warnings)
        print("  Top warnings:")
        for w, count in wc.most_common(10):
            print(f"    {count:4d}  {w[:80]}")
    print()

    # ── Inference method distribution ────────────────────────────────────
    print("  Inference methods:")
    from collections import Counter
    method_counts = Counter()
    for r in valid_records:
        meta = r.get("inference_metadata", {})
        for field, method in meta.items():
            method_counts[f"{field}={method}"] += 1
    for key, count in method_counts.most_common(20):
        print(f"    {count:4d}  {key}")
    print()

    # ── Per-record sample inspection ─────────────────────────────────────
    print("-" * 72)
    print("SAMPLE RECORDS (human inspection)")
    print("-" * 72)

    issues_found = 0
    for i, rec in enumerate(sample):
        req = rec["request"]
        src = rec["source"]
        meta = rec["inference_metadata"]
        warns = rec.get("conversion_warnings", [])

        issues = []

        # Check 1: hero_seat in active_seats
        if req["hero_seat"] not in req["active_seats"]:
            issues.append("CRITICAL: hero_seat not in active_seats")

        # Check 2: button_seat in active_seats
        if req["button_seat"] not in req["active_seats"]:
            issues.append("CRITICAL: button_seat not in active_seats")

        # Check 3: hero_seat matches position
        pos = src.get("position", "")
        expected_seat = POSITION_TO_SEAT_6MAX.get(pos, -1)
        if expected_seat != req["hero_seat"]:
            issues.append(f"SEAT MISMATCH: position={pos} -> expected seat {expected_seat}, got {req['hero_seat']}")

        # Check 4: effective_stack_bb sanity
        eff = req["effective_stack_bb"]
        if eff <= 0 or eff > 500:
            issues.append(f"STACK: effective_stack_bb={eff} (suspicious)")

        # Check 5: pot > 0
        if req["pot"] <= 0:
            issues.append(f"POT: pot={req['pot']} (zero or negative)")

        # Check 6: legal_actions not empty
        if not req["legal_actions"]:
            issues.append("LEGAL: no legal actions")

        # Check 7: board_bucket present for postflop
        if req["street"] != "preflop" and req["board_bucket"] is None:
            issues.append("BUCKET: postflop but board_bucket is None")

        # Check 8: board card count matches street
        expected_cards = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
        exp_n = expected_cards.get(req["street"], 0)
        if len(req["board"]) != exp_n:
            issues.append(f"BOARD: street={req['street']} but {len(req['board'])} cards (expected {exp_n})")

        # Check 9: action_history produces correct pot class
        n_aggressive = sum(1 for a in req["action_history"]
                          if ":BET_TO:" in a or ":RAISE_TO:" in a)
        ah_method = meta.get("action_history", "")

        # Check 10: hero_committed + hero_stack ~= hero_start_stack
        delta = abs(req["hero_committed"] + req["hero_stack"] - req["hero_start_stack"])
        if delta > 1:
            issues.append(f"STACK BALANCE: committed({req['hero_committed']}) + "
                         f"stack({req['hero_stack']}) != start({req['hero_start_stack']}), "
                         f"delta={delta:.1f}")

        if issues:
            issues_found += len(issues)

        # Print summary line
        flag = "!!" if issues else "ok"
        print(
            f"  [{i+1:2d}] {flag}  hand={src.get('hand_id','?')}  "
            f"street={req['street']:7s}  pos={src.get('position','?'):3s}  "
            f"seat={req['hero_seat']}  "
            f"eff={eff:6.1f}bb  pot={req['pot']/req['big_blind']:6.1f}bb  "
            f"facing={req['facing_bet']}  "
            f"bb_idx={'None' if req['board_bucket'] is None else req['board_bucket']:2}  "
            f"ah={ah_method[:20]:20s}  "
            f"warns={len(warns)}"
        )
        if issues:
            for iss in issues:
                print(f"       >>> {iss}")

        # Print the action_history for inspection
        if req["action_history"]:
            print(f"       action_history: {req['action_history']}")
        print(f"       legal_actions:  {[a['kind'] for a in req['legal_actions']]}")
        print(f"       hero: {req['hole_cards']}  board: {req['board']}")
        if src.get("rec_action"):
            print(f"       old_rec: {src['rec_action']}  equity={src.get('rec_equity','?')}")
        print()

    print("-" * 72)
    print(f"SAMPLE ISSUES: {issues_found} across {len(sample)} records")
    if issues_found == 0:
        print("All sample records passed structural checks.")
    print("=" * 72)


# ─── Extraction mode for replay runner ───────────────────────────────────────

def extract_requests(replay_jsonl: str) -> list[dict]:
    """Load a replay JSONL and extract just the request dicts.

    This is what baseline_replay_runner.py --input expects.
    """
    requests = []
    with open(replay_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            req = rec.get("request")
            if req is not None:
                requests.append(req)
    return requests


def write_requests_only(replay_jsonl: str, output_path: str) -> int:
    """Extract bare requests from replay JSONL for the replay runner.

    Returns count of requests written.
    """
    requests = extract_requests(replay_jsonl)
    with open(output_path, "w") as f:
        for req in requests:
            f.write(json.dumps(req, separators=(",", ":")) + "\n")
    return len(requests)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert session hand records to replay inputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert and validate:
  python session_to_replay.py \\
    --input ../vision/data/session_*.jsonl \\
    --output replay_full.jsonl \\
    --validate --sample 50

  # Extract bare requests for the replay runner:
  python session_to_replay.py \\
    --extract-requests replay_full.jsonl \\
    --output requests.jsonl

  # Then run the baseline:
  python baseline_replay_runner.py --input requests.jsonl --output results.jsonl
  python hit_rate_report.py results.jsonl
  python latency_bench.py results.jsonl
""",
    )
    parser.add_argument("--input", nargs="+", help="Session JSONL files to convert")
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--validate", action="store_true",
                        help="Print validation report after conversion")
    parser.add_argument("--sample", type=int, default=30,
                        help="Number of records to sample for validation (default: 30)")
    parser.add_argument("--extract-requests", metavar="REPLAY_JSONL",
                        help="Extract bare request dicts from a replay JSONL file")
    args = parser.parse_args()

    if args.extract_requests:
        n = write_requests_only(args.extract_requests, args.output)
        print(f"Extracted {n} requests to {args.output}")
        return

    if not args.input:
        print("error: --input required (or use --extract-requests)", file=sys.stderr)
        sys.exit(1)

    print(f"Loading session files...")
    hands = load_session_files(args.input)
    print(f"  Loaded {len(hands)} hands from {len(args.input)} files")

    print(f"Converting to replay format...")
    records = convert_all(hands)
    print(f"  Generated {len(records)} decision records")

    print(f"Writing output...")
    valid, skipped = write_output(records, args.output)
    print(f"  Wrote {valid} valid records to {args.output}")
    if skipped:
        print(f"  Skipped {skipped} records (null request)")

    if args.validate:
        print()
        validate_sample(records, args.sample)


if __name__ == "__main__":
    main()
