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

# EP (UTG) — ~15% of hands
EP_RAISE = {
    "AA", "KK", "QQ", "JJ", "TT", "99",
    "AKs", "AQs", "AJs", "ATs",
    "KQs", "KJs",
    "AKo", "AQo",
}

# MP — ~20% of hands
MP_RAISE = EP_RAISE | {
    "88", "77",
    "A9s", "A8s",
    "KTs", "QJs", "QTs", "JTs",
    "AJo", "KQo",
}

# CO — ~27% of hands
CO_RAISE = MP_RAISE | {
    "66", "55",
    "A7s", "A6s", "A5s", "A4s", "A3s", "A2s",
    "K9s", "Q9s", "J9s", "T9s", "98s", "87s", "76s",
    "KJo", "QJo", "ATo",
}

# BTN — ~40% of hands
BTN_RAISE = CO_RAISE | {
    "44", "33", "22",
    "K8s", "K7s", "K6s", "K5s",
    "Q8s", "J8s", "T8s", "97s", "86s", "75s", "65s", "54s",
    "KTo", "QTo", "JTo", "A9o", "A8o", "A7o", "A6o", "A5o",
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
    "TT", "99", "88", "77", "66", "55", "44", "33", "22",
    "AJs", "ATs", "A9s", "A8s", "A7s", "A6s", "A5s", "A4s", "A3s", "A2s",
    "KQs", "KJs", "KTs", "K9s", "K8s",
    "QJs", "QTs", "Q9s",
    "JTs", "J9s",
    "T9s", "T8s",
    "98s", "97s", "87s", "86s", "76s", "75s", "65s", "54s",
    "AQo", "AJo", "ATo", "A9o",
    "KQo", "KJo", "KTo",
    "QJo", "QTo",
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
        # Facing a raise from other positions — tighten up
        # 3-bet with premiums, call with strong hands, fold the rest
        premiums = {"AA", "KK", "QQ", "AKs", "AKo"}
        call_vs_raise = {
            "JJ", "TT", "99", "88", "77",
            "AQs", "AJs", "ATs", "KQs", "KJs", "QJs", "JTs",
            "AQo",
        }
        if hand_key in premiums:
            return {"action": "RAISE", "hand_key": hand_key, "in_range": True,
                    "note": "3-bet"}
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
