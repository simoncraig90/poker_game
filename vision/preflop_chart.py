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

# ── Position ranges ────────────────────────────────────────────────────

# EP (UTG) — ~18% of hands
EP_RAISE = {
    "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55", "44", "33", "22",
    "AKs", "AQs", "AJs", "ATs",
    "KQs", "KJs",
    "AKo", "AQo",
}

# MP — ~22% of hands
MP_RAISE = EP_RAISE | {
    "88", "77", "66", "55", "44", "33", "22",
    "A9s", "A8s",
    "KTs", "QJs", "QTs", "JTs",
    "AJo", "KQo",
}

# CO — ~30% of hands
CO_RAISE = MP_RAISE | {
    "A7s", "A6s", "A5s", "A4s", "A3s", "A2s",
    "K9s", "Q9s", "J9s", "T9s", "98s", "87s", "76s",
    "KJo", "QJo", "ATo", "A9o", "A8o", "T9o",
}

# BTN — ~45% of hands
BTN_RAISE = CO_RAISE | {
    "K8s", "K7s", "K6s", "K5s",
    "Q8s", "J8s", "T8s", "97s", "86s", "75s", "65s", "54s",
    "KTo", "QTo", "JTo",
    "A7o", "A6o", "A5o", "A4o", "A3o", "A2o",
    "K9o", "K8o", "K7o",
    "Q9o", "Q8o",
    "J9o", "J8o",
    "T9o", "T8o",
    "98o", "97o",
    "87o", "86o",
    "76o", "75o",
}

# SB — ~30% open-raise, tighter 3-bet
SB_RAISE = CO_RAISE | {
    "44", "33",
    "K8s", "Q8s", "J8s", "T8s", "97s", "86s", "75s",
    "KTo", "A9o",
}

# BB defend range vs single raise — ~40% call, ~10% 3-bet
BB_3BET = {
    "AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs",
}
BB_CALL = {
    # Pairs — all defend
    "TT", "99", "88", "77", "66", "55", "44", "33", "22",
    # Suited aces — drop A2s-A4s (leak: lost €2.54 on A4s)
    "AJs", "ATs", "A9s", "A8s", "A7s", "A6s", "A5s",
    # Suited Broadways
    "KQs", "KJs", "KTs", "K9s",
    "QJs", "QTs", "Q9s",
    "JTs", "J9s",
    "T9s", "T8s",
    # Suited connectors
    "98s", "97s", "87s", "76s", "65s", "54s",
    # Offsuit Broadways
    "AQo", "AJo", "ATo",
    "KQo", "KJo",
    "QJo",
    "JTo",
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
        # Facing a raise. The 3-bet range is position-specific because the
        # opener's expected range (and therefore our equity) depends on
        # where the raise came from. SB and BB widen their 3-bet ranges
        # vs the average non-EP opener (which is most opens at 6-max and
        # all opens at 4/5-max).
        #
        # 2026-04-08: AQo SB used to flat-call here, costing the user a
        # buy-in on hand 2460830661. Now SB 3-bets AQo+, AJs+, KQs+, JJ+
        # for value plus a thin bluff range.
        # See tests/test_strategy_regressions.py::test_2460830661...
        premiums = {"AA", "KK", "QQ", "AKs", "AKo"}

        # Position-specific 3-bet additions (composed with premiums).
        # SB OOP vs late open: standard 3-bet for value/protection on
        # AQo+, AJs+, KQs+, plus value 3-bets with JJ/TT and a few
        # blocker bluffs.
        SB_3BET_EXTRA = {
            "JJ", "TT",
            "AQs", "AQo", "AJs", "KQs",
            # Blocker bluffs — A-blockers and unblocked suited connectors
            "A5s", "A4s",
            "65s", "54s",
        }
        # CO/BTN have an opener that's likely tighter (only EP/MP can
        # have raised before them), so they don't widen as much. The
        # hands that ARE wider are speculative IP plays, not value
        # 3-bets.
        IP_3BET_EXTRA = {
            "JJ", "AQs",
        }
        # MP/UTG facing a raise = facing an even-tighter opener; don't
        # widen the 3-bet range — call or fold.
        EP_3BET_EXTRA = set()

        position_3bet_extra = {
            "SB": SB_3BET_EXTRA,
            "BTN": IP_3BET_EXTRA,
            "CO":  IP_3BET_EXTRA,
            "MP":  EP_3BET_EXTRA,
            "EP":  EP_3BET_EXTRA,
            "UTG": EP_3BET_EXTRA,
        }

        threebet_range = premiums | position_3bet_extra.get(position, set())

        call_vs_raise = {
            "JJ", "TT", "99", "88", "77", "66", "55", "44", "33", "22",
            "AQs", "AJs", "ATs", "KQs", "KJs", "QJs", "JTs",
            "AQo",
        }

        if hand_key in threebet_range:
            return {"action": "RAISE", "hand_key": hand_key, "in_range": True,
                    "note": f"3-bet from {position}"}
        elif hand_key in call_vs_raise:
            return {"action": "CALL", "hand_key": hand_key, "in_range": True,
                    "note": "call raise"}
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
