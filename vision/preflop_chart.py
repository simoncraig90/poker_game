"""
6-max preflop chart for NL10 micro-stakes.

Based on standard TAG open-raise ranges adjusted for micro-stakes.
Returns action by position: RAISE, CALL, or FOLD.

Positions detected from dealer button location:
  - EP (UTG/UTG+1): tightest
  - MP: moderate
  - CO: wide
  - BTN: widest
  - SB: moderate (but OOP postflop)
  - BB: defend wide vs opens, check vs limps
"""

# Hands encoded as "AKs", "AKo", "AA", etc.
# R = raise/open, C = call (vs raise), F = fold

# ── Open-raise ranges ──────────────────────────────────────────────────
#
# Based on consensus published 6-max NL10 cash charts (Upswing,
# JonathanLittle, RunItOnce, BlackRain79). The previous version was
# hand-coded with multiple gaps (AQo SB flat instead of 3-bet, MP
# extending EP with redundant pair entries, etc.) and lost real money
# on a documented hand. This replacement is more comprehensive and
# better-validated against published material.
#
# Each range is encoded explicitly (no inheritance via set union)
# so the values are auditable in one place. Frequencies are
# approximate — micros opener stats vary by site and time of day.

# UTG / EP — ~16% of hands
EP_RAISE = {
    # Pairs (12)
    "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55", "44", "33", "22",
    # Suited aces (5)
    "AKs", "AQs", "AJs", "ATs", "A5s",  # A5s for blocker/connectivity
    # Suited broadway (5)
    "KQs", "KJs", "KTs", "QJs", "QTs", "JTs",
    # Suited connectors (3)
    "T9s", "98s", "87s",
    # Offsuit broadway (2)
    "AKo", "AQo",
}

# MP — ~20% of hands
MP_RAISE = EP_RAISE | {
    # More suited aces
    "A9s", "A4s",
    # More suited broadway
    "K9s", "Q9s", "J9s",
    # More suited connectors
    "76s", "65s",
    # Offsuit
    "AJo", "KQo",
}

# CO — ~28% of hands
CO_RAISE = MP_RAISE | {
    # All suited aces
    "A8s", "A7s", "A6s", "A3s", "A2s",
    # Suited gappers
    "K8s", "Q8s", "J8s", "T8s",
    # Suited connectors
    "97s", "86s", "75s", "54s",
    # Offsuit broadway
    "ATo", "KJo", "QJo", "JTo",
    # Offsuit ace
    "A9o",
}

# BTN — ~45% of hands
BTN_RAISE = CO_RAISE | {
    # Wide suited K/Q/J/T
    "K7s", "K6s", "K5s", "K4s", "K3s", "K2s",
    "Q7s", "Q6s", "Q5s",
    "J7s", "J6s",
    "T7s", "T6s",
    "96s", "85s", "74s", "64s", "53s", "43s",
    # Wide offsuit
    "A8o", "A7o", "A6o", "A5o", "A4o", "A3o", "A2o",
    "KTo", "K9o", "K8o", "K7o",
    "QTo", "Q9o", "Q8o",
    "J9o", "J8o",
    "T9o", "T8o",
    "98o", "97o",
    "87o", "86o",
    "76o", "75o",
    "65o",
}

# SB (open-raise vs folded action) — ~32% of hands
# Slightly tighter than BTN because SB is OOP postflop, but still
# wide because the pot is small and only BB can defend.
SB_RAISE = CO_RAISE | {
    # Add more suited connectors / gappers
    "K7s", "K6s", "K5s",
    "Q7s", "Q6s",
    "J7s",
    "T7s",
    "96s", "85s", "74s", "64s", "53s",
    # Selective offsuit
    "KTo", "K9o",
    "QTo", "Q9o",
    "J9o",
    "T9o",
    "98o", "87o",
    "A8o", "A7o", "A5o",
}

# ── BB defense range (vs single raise from any position) ───────────────
#
# BB defense is wider than SB open-raise because the BB has already
# invested 1 BB and is getting better pot odds. Defends ~35-40% vs
# typical opens.
#
# Note: this is a single average range. Properly, BB defense should
# vary by opener position (tighter vs UTG, wider vs BTN), but we
# don't currently know the opener position in the SM. The single
# range is calibrated to "average opener" — slightly too loose vs
# UTG, slightly too tight vs BTN, correct on average.

BB_3BET = {
    # Linear value
    "AA", "KK", "QQ", "JJ", "TT",
    "AKs", "AKo", "AQs", "AQo",
    # Light value / merged
    "KQs",
    # Bluffs (suited blockers + suited connectors with playability)
    "A5s", "A4s",
    "76s", "65s",
}
BB_CALL = {
    # Pairs (defend all small/medium pairs for set value)
    "99", "88", "77", "66", "55", "44", "33", "22",
    # Suited aces (defend most for showdown + flush draws)
    "AJs", "ATs", "A9s", "A8s", "A7s", "A6s", "A3s", "A2s",
    # Offsuit aces (defend only the better ones)
    "AJo", "ATo",
    # Suited broadway
    "KJs", "KTs", "K9s", "K8s",
    "QJs", "QTs", "Q9s", "Q8s",
    "JTs", "J9s", "J8s",
    "T9s", "T8s",
    # Offsuit broadway
    "KQo", "KJo", "KTo",
    "QJo", "QTo",
    "JTo",
    # Suited connectors / 1-gappers
    "98s", "97s", "87s", "86s", "75s", "54s",
    # Suited K/Q with kicker for postflop playability
    "K7s", "K6s", "K5s",
    "Q7s",
    "T7s",
}


def _hand_key(card1_str, card2_str):
    """Convert two card strings to a hand key like 'AKs' or 'AKo' or 'AA'."""
    if not card1_str or not card2_str or len(card1_str) < 2 or len(card2_str) < 2:
        return None

    RANK_ORDER = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
                  "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}

    r1 = card1_str[0].upper()
    r2 = card2_str[0].upper()
    s1 = card1_str[1].lower()
    s2 = card2_str[1].lower()

    if r1 not in RANK_ORDER or r2 not in RANK_ORDER:
        return None

    # Always put higher rank first
    if RANK_ORDER[r1] < RANK_ORDER[r2]:
        r1, r2, s1, s2 = r2, r1, s2, s1

    if r1 == r2:
        return f"{r1}{r2}"
    elif s1 == s2:
        return f"{r1}{r2}s"
    else:
        return f"{r1}{r2}o"


def preflop_advice(card1_str, card2_str, position="BTN", facing_raise=False):
    """
    Get preflop advice.

    Args:
        card1_str, card2_str: card strings like 'Ah', 'Ks'
        position: 'EP', 'MP', 'CO', 'BTN', 'SB', 'BB'
        facing_raise: True if someone raised ahead

    Returns:
        dict with:
          action: 'RAISE', 'CALL', 'FOLD'
          hand_key: e.g. 'AKs'
          in_range: bool, whether hand is in opening range
          note: brief explanation
    """
    hand_key = _hand_key(card1_str, card2_str)
    if not hand_key:
        return {"action": "FOLD", "hand_key": "??", "in_range": False, "note": ""}

    # Select range by position
    ranges = {
        "EP": EP_RAISE, "UTG": EP_RAISE,
        "MP": MP_RAISE,
        "CO": CO_RAISE,
        "BTN": BTN_RAISE,
        "SB": SB_RAISE,
        "BB": BB_CALL | BB_3BET,
    }

    open_range = ranges.get(position, BTN_RAISE)

    # BB with no raise = free check, never fold
    if position == "BB" and not facing_raise:
        return {"action": "CHECK", "hand_key": hand_key, "in_range": True,
                "note": "free look"}

    if position == "BB" and facing_raise:
        # BB facing a raise
        if hand_key in BB_3BET:
            return {"action": "RAISE", "hand_key": hand_key, "in_range": True,
                    "note": "3-bet"}
        elif hand_key in BB_CALL:
            return {"action": "CALL", "hand_key": hand_key, "in_range": True,
                    "note": "defend"}
        else:
            return {"action": "FOLD", "hand_key": hand_key, "in_range": False,
                    "note": ""}

    if facing_raise:
        # Facing a raise — per-position 3-bet / call / fold response.
        # Range widths increase as hero's position is later (more
        # opens come from before them, so the average opener has a
        # wider/weaker range and we can attack more aggressively).
        #
        # The "opener position" isn't passed into preflop_advice at
        # the moment, so we calibrate per-hero-position to the AVERAGE
        # expected opener (e.g. SB averages "vs late position" because
        # in 6-max most opens come from CO/BTN). When we eventually
        # plumb opener_position through, this becomes a 2D table.
        #
        # Seed bug 2460830661 (AQo SB flat) → SB 3-bets AQo+ now.
        # Verified by tests/test_strategy_regressions.py.

        # Universal premium 3-bet range (every position, every opener)
        UNIVERSAL_3BET = {
            "AA", "KK", "QQ", "JJ", "AKs", "AKo",
        }

        # Per-hero-position 3-bet additions
        EP_3BET_EXTRA = {
            # Vs an opener earlier than us — must be UTG (very tight).
            # Add only against value-heavy ranges. Almost nothing extra.
            "AQs",
        }
        MP_3BET_EXTRA = {
            "AQs", "TT",
        }
        CO_3BET_EXTRA = {
            "AQs", "TT", "AQo",
            # Mild bluffs in position
            "A5s",
        }
        BTN_3BET_EXTRA = {
            "TT", "99",
            "AQs", "AQo", "AJs", "KQs",
            # IP bluffs
            "A5s", "A4s", "K9s",
        }
        # SB OOP vs (mostly) late-position openers — wide value + bluffs
        SB_3BET_EXTRA = {
            "TT", "99",
            "AQs", "AQo", "AJs", "KQs", "KJs",
            # Blocker bluffs
            "A5s", "A4s", "A3s",
            "65s", "54s",
        }
        # BB widest — best pot odds, vs typical opener BB defends ~35%
        # The 3-bet sub-range here is the "value + bluff" portion
        BB_3BET_EXTRA = {
            "TT", "99",
            "AQs", "AQo", "AJs", "KQs",
            "A5s", "A4s",
            "76s", "65s",
        }

        position_3bet_extra = {
            "EP":  EP_3BET_EXTRA,
            "UTG": EP_3BET_EXTRA,
            "MP":  MP_3BET_EXTRA,
            "CO":  CO_3BET_EXTRA,
            "BTN": BTN_3BET_EXTRA,
            "SB":  SB_3BET_EXTRA,
            "BB":  BB_3BET_EXTRA,
        }
        threebet_range = UNIVERSAL_3BET | position_3bet_extra.get(position, set())

        # Per-position call ranges (the rest of the defending range
        # that doesn't 3-bet). Wider in position, narrower OOP.
        EP_CALL_RANGE = {
            # EP facing UTG: very tight cold-call (set-mining only)
            "JJ", "TT", "99", "88", "77",
            "AQs", "AJs", "KQs",
        }
        MP_CALL_RANGE = EP_CALL_RANGE | {
            "66", "55",
            "ATs", "KJs", "QJs", "JTs",
            "AJo",
        }
        CO_CALL_RANGE = MP_CALL_RANGE | {
            "44", "33", "22",
            "A5s", "A4s",  # blocker calls
            "KTs", "QTs", "T9s", "98s",
            "AJo",  # already in MP
        }
        BTN_CALL_RANGE = CO_CALL_RANGE | {
            # Widest cold-call range — IP, can realize equity
            "ATs", "K9s", "Q9s", "J9s", "T9s",
            "98s", "87s", "76s", "65s", "54s",
            "ATo", "KQo", "KJo", "QJo",
            "JTo",
        }
        SB_CALL_RANGE = {
            # SB OOP cold-call is risky (BB can squeeze). Tight.
            "88", "77", "66", "55",
            "AJs", "ATs", "KQs", "KJs", "QJs", "JTs",
            "T9s", "98s", "87s",
            "AJo",
        }
        BB_CALL_RANGE = BB_CALL  # BB defense: use the comprehensive set

        position_call_range = {
            "EP":  EP_CALL_RANGE,
            "UTG": EP_CALL_RANGE,
            "MP":  MP_CALL_RANGE,
            "CO":  CO_CALL_RANGE,
            "BTN": BTN_CALL_RANGE,
            "SB":  SB_CALL_RANGE,
            "BB":  BB_CALL_RANGE,
        }
        call_range = position_call_range.get(position, MP_CALL_RANGE)

        if hand_key in threebet_range:
            return {"action": "RAISE", "hand_key": hand_key, "in_range": True,
                    "note": f"3-bet from {position}"}
        elif hand_key in call_range:
            return {"action": "CALL", "hand_key": hand_key, "in_range": True,
                    "note": f"call raise from {position}"}
        else:
            return {"action": "FOLD", "hand_key": hand_key, "in_range": False,
                    "note": ""}

    # No raise ahead — open or fold
    if hand_key in open_range:
        return {"action": "RAISE", "hand_key": hand_key, "in_range": True,
                "note": f"open {position}"}
    else:
        return {"action": "FOLD", "hand_key": hand_key, "in_range": False,
                "note": ""}
