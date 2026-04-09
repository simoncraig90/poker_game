"""
6-max 100bb GTO preflop ranges.

Keyed by (hero_pos, scenario) where scenario encodes the action sequence.
Each entry is a dict mapping hand -> frequency (0.0-1.0).

Positions: UTG, MP, CO, BTN, SB, BB
Scenarios:
  "open"           - first in (no prior raise)
  "vs_open_<pos>"  - facing an open from <pos>: 3bet/call/fold frequencies
  "vs_3bet_<pos>"  - facing a 3bet after we opened: 4bet/call/fold
  "squeeze_<pos>"  - facing open + call, we squeeze from <pos>
  "bb_defend_vs_<pos>" - BB facing an open from <pos>

Frequencies are approximate GTO equilibrium for 100bb 6-max cash.
Sources: composite of GTO Wizard, Upswing Poker, MonkerSolver public outputs.

Hand notation: "AA", "AKs", "AKo", "T9s", etc. (169 canonical hands)
"""

# ============================================================
# All 169 canonical starting hands
# ============================================================
RANKS = "AKQJT98765432"
ALL_HANDS = []
for i, r1 in enumerate(RANKS):
    for j, r2 in enumerate(RANKS):
        if i < j:
            ALL_HANDS.append(f"{r1}{r2}s")
            ALL_HANDS.append(f"{r1}{r2}o")
        elif i == j:
            ALL_HANDS.append(f"{r1}{r2}")
ALL_HANDS.sort(key=lambda h: (-RANKS.index(h[0]), -RANKS.index(h[1]), h[-1:]))

def _parse_range_str(s: str) -> dict[str, float]:
    """Parse a range string like 'AA,KK,AKs,AQs:0.5,JTs:0.3' into {hand: freq}."""
    result = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        freq = 1.0
        if ":" in part:
            part, f = part.rsplit(":", 1)
            freq = float(f)

        # Handle ranges like "22+" or "A2s+"
        if part.endswith("+"):
            base = part[:-1]
            if len(base) == 2 and base[0] == base[1]:
                # Pair range: "77+" means 77,88,...,AA
                idx = RANKS.index(base[0])
                for i in range(0, idx + 1):
                    result[f"{RANKS[i]}{RANKS[i]}"] = freq
            elif len(base) == 3:
                # Suited/offsuit range: "A9s+" means A9s,ATs,...,AKs
                high = base[0]
                low = base[1]
                suit = base[2]
                hi = RANKS.index(high)
                lo = RANKS.index(low)
                for i in range(hi + 1, lo + 1):
                    result[f"{high}{RANKS[i]}{suit}"] = freq
        elif "-" in part:
            # Range like "A9s-A5s" or "99-77"
            lo_part, hi_part = part.split("-")
            if len(lo_part) >= 2 and lo_part[0] == lo_part[1] and len(hi_part) == 2:
                # Pair range
                top = RANKS.index(lo_part[0])
                bot = RANKS.index(hi_part[0])
                for i in range(top, bot + 1):
                    result[f"{RANKS[i]}{RANKS[i]}"] = freq
            elif len(lo_part) == 3 and len(hi_part) == 3:
                high = lo_part[0]
                suit = lo_part[2]
                top_lo = RANKS.index(lo_part[1])
                bot_lo = RANKS.index(hi_part[1])
                for i in range(top_lo, bot_lo + 1):
                    result[f"{high}{RANKS[i]}{suit}"] = freq
        else:
            result[part] = freq
    return result


# ============================================================
# OPENING RANGES (RFI - Raise First In)
# Standard 6-max 100bb GTO open-raise ranges
# ============================================================

OPEN_RANGES = {
    "UTG": _parse_range_str(
        "AA,KK,QQ,JJ,TT,99,88,77:0.5,"
        "AKs,AQs,AJs,ATs,A9s:0.5,A5s,A4s,"
        "AKo,AQo,AJo:0.5,"
        "KQs,KJs,KTs,"
        "QJs,QTs,"
        "JTs,J9s:0.5,"
        "T9s,T8s:0.3,"
        "98s,97s:0.3,"
        "87s,76s:0.5,65s:0.5,54s:0.5"
    ),
    "MP": _parse_range_str(
        "AA,KK,QQ,JJ,TT,99,88,77,66:0.5,"
        "AKs,AQs,AJs,ATs,A9s,A8s:0.5,A5s,A4s,A3s:0.5,"
        "AKo,AQo,AJo,ATo:0.5,"
        "KQs,KJs,KTs,K9s:0.5,"
        "QJs,QTs,Q9s:0.5,"
        "JTs,J9s,"
        "T9s,T8s:0.5,"
        "98s,97s:0.5,"
        "87s,76s,65s,54s"
    ),
    "CO": _parse_range_str(
        "AA,KK,QQ,JJ,TT,99,88,77,66,55,44:0.5,"
        "AKs,AQs,AJs,ATs,A9s,A8s,A7s,A6s,A5s,A4s,A3s,A2s,"
        "AKo,AQo,AJo,ATo,A9o:0.5,"
        "KQs,KJs,KTs,K9s,K8s:0.5,"
        "KQo,KJo:0.5,"
        "QJs,QTs,Q9s,Q8s:0.5,"
        "QJo:0.5,"
        "JTs,J9s,J8s:0.5,"
        "T9s,T8s,T7s:0.3,"
        "98s,97s,96s:0.3,"
        "87s,86s:0.5,76s,75s:0.5,65s,64s:0.5,54s,53s:0.3"
    ),
    "BTN": _parse_range_str(
        "AA,KK,QQ,JJ,TT,99,88,77,66,55,44,33,22,"
        "AKs,AQs,AJs,ATs,A9s,A8s,A7s,A6s,A5s,A4s,A3s,A2s,"
        "AKo,AQo,AJo,ATo,A9o,A8o:0.5,A7o:0.3,A5o:0.3,A4o:0.3,"
        "KQs,KJs,KTs,K9s,K8s,K7s,K6s,K5s:0.5,K4s:0.3,"
        "KQo,KJo,KTo,K9o:0.5,"
        "QJs,QTs,Q9s,Q8s,Q7s:0.5,Q6s:0.3,"
        "QJo,QTo:0.5,"
        "JTs,J9s,J8s,J7s:0.5,"
        "JTo:0.5,"
        "T9s,T8s,T7s,T6s:0.3,"
        "98s,97s,96s,95s:0.3,"
        "87s,86s,85s:0.3,76s,75s,74s:0.3,65s,64s,63s:0.3,54s,53s,52s:0.3,43s:0.3"
    ),
    "SB": _parse_range_str(
        "AA,KK,QQ,JJ,TT,99,88,77,66,55,44,33:0.5,22:0.5,"
        "AKs,AQs,AJs,ATs,A9s,A8s,A7s,A6s,A5s,A4s,A3s,A2s,"
        "AKo,AQo,AJo,ATo,A9o,A8o:0.5,A7o:0.3,A5o:0.3,"
        "KQs,KJs,KTs,K9s,K8s,K7s,K6s:0.5,K5s:0.3,"
        "KQo,KJo,KTo:0.5,K9o:0.3,"
        "QJs,QTs,Q9s,Q8s,Q7s:0.5,"
        "QJo,QTo:0.5,"
        "JTs,J9s,J8s,J7s:0.3,"
        "JTo:0.3,"
        "T9s,T8s,T7s:0.3,"
        "98s,97s,96s:0.3,"
        "87s,86s:0.3,76s,75s:0.3,65s,64s:0.3,54s,53s:0.3"
    ),
}

# ============================================================
# 3-BET RANGES (facing an open)
# Key: (hero_pos, opener_pos) -> {"3bet": {hand:freq}, "call": {hand:freq}}
# ============================================================

THREE_BET_RANGES = {
    # BB vs UTG open
    ("BB", "UTG"): {
        "3bet": _parse_range_str("AA,KK,QQ:0.5,AKs,AKo:0.5,A5s:0.5,A4s:0.5"),
        "call": _parse_range_str(
            "QQ:0.5,JJ,TT,99,88,77,66,55:0.5,"
            "AQs,AJs,ATs,A9s,A8s:0.5,A5s:0.5,A4s:0.5,A3s:0.5,A2s:0.5,"
            "AQo:0.5,AJo:0.5,"
            "KQs,KJs,KTs,K9s:0.5,"
            "QJs,QTs,Q9s:0.5,"
            "JTs,J9s,"
            "T9s,T8s:0.5,"
            "98s,97s:0.5,"
            "87s,76s,65s,54s"
        ),
    },
    # BB vs CO open
    ("BB", "CO"): {
        "3bet": _parse_range_str(
            "AA,KK,QQ,JJ:0.5,AKs,AQs:0.5,AKo,"
            "A5s,A4s,A3s:0.5,"
            "KJs:0.3,K9s:0.3,"
            "Q9s:0.3,J9s:0.3,T8s:0.3,97s:0.3,86s:0.3,75s:0.3,64s:0.3"
        ),
        "call": _parse_range_str(
            "JJ:0.5,TT,99,88,77,66,55,44,33:0.5,"
            "AQs:0.5,AJs,ATs,A9s,A8s,A7s,A6s,A5s:0.5,A4s:0.5,A3s:0.5,A2s,"
            "AQo,AJo,ATo:0.5,"
            "KQs,KJs,KTs,K9s,K8s:0.5,"
            "KQo,KJo:0.5,"
            "QJs,QTs,Q9s,Q8s:0.5,"
            "JTs,J9s,J8s:0.5,"
            "T9s,T8s,T7s:0.3,"
            "98s,97s,96s:0.3,"
            "87s,86s,76s,75s:0.5,65s,64s:0.5,54s,53s:0.3"
        ),
    },
    # BB vs BTN open (wide defense)
    ("BB", "BTN"): {
        "3bet": _parse_range_str(
            "AA,KK,QQ,JJ,TT:0.3,"
            "AKs,AQs,AJs:0.5,AKo,AQo:0.5,"
            "A5s,A4s,A3s,A2s:0.5,"
            "K9s:0.5,K8s:0.3,"
            "Q9s:0.3,J8s:0.3,T8s:0.3,97s:0.3,86s:0.3,75s:0.3,64s:0.3,53s:0.3"
        ),
        "call": _parse_range_str(
            "TT:0.7,99,88,77,66,55,44,33,22,"
            "AJs:0.5,ATs,A9s,A8s,A7s,A6s,A5s:0.5,A4s:0.5,A3s:0.5,A2s:0.5,"
            "AQo:0.5,AJo,ATo,A9o,A8o:0.5,A7o:0.3,"
            "KQs,KJs,KTs,K9s:0.5,K8s,K7s,K6s:0.5,K5s:0.3,"
            "KQo,KJo,KTo,K9o:0.5,"
            "QJs,QTs,Q9s,Q8s,Q7s:0.5,Q6s:0.3,"
            "QJo,QTo:0.5,"
            "JTs,J9s,J8s,J7s:0.3,"
            "JTo:0.3,"
            "T9s,T8s,T7s,T6s:0.3,"
            "98s,97s,96s,"
            "87s,86s,85s:0.3,76s,75s,65s,64s,54s,53s,43s:0.3"
        ),
    },
    # SB vs BTN open (3bet or fold, no calling from SB vs BTN)
    ("SB", "BTN"): {
        "3bet": _parse_range_str(
            "AA,KK,QQ,JJ,TT,99:0.5,88:0.3,77:0.3,66:0.3,55:0.3,"
            "AKs,AQs,AJs,ATs,A9s:0.5,A8s:0.3,A5s,A4s,A3s,A2s:0.5,"
            "AKo,AQo,AJo,ATo:0.5,"
            "KQs,KJs,KTs:0.5,K9s:0.3,"
            "KQo:0.5,"
            "QJs,QTs:0.5,"
            "JTs,J9s:0.3,"
            "T9s:0.3,98s:0.3,87s:0.3,76s:0.3,65s:0.3,54s:0.3"
        ),
        "call": {},  # SB should not flat BTN open (OOP, squeezed by BB)
    },
    # CO vs UTG open
    ("CO", "UTG"): {
        "3bet": _parse_range_str("AA,KK,QQ:0.5,AKs,AKo:0.5,A5s:0.3,A4s:0.3"),
        "call": _parse_range_str(
            "QQ:0.5,JJ,TT,99,88,77:0.5,"
            "AQs,AJs,ATs:0.5,"
            "AQo:0.3,"
            "KQs,KJs:0.5,"
            "QJs,QTs:0.5,"
            "JTs,T9s,98s,87s,76s,65s"
        ),
    },
    # BTN vs CO open
    ("BTN", "CO"): {
        "3bet": _parse_range_str(
            "AA,KK,QQ,JJ:0.5,TT:0.3,"
            "AKs,AQs,AJs:0.5,A5s,A4s,"
            "AKo,AQo:0.5,"
            "K9s:0.3,Q9s:0.3,J9s:0.3,T8s:0.3,97s:0.3,86s:0.3,75s:0.3"
        ),
        "call": _parse_range_str(
            "JJ:0.5,TT:0.7,99,88,77,66,55,44:0.5,"
            "AJs:0.5,ATs,A9s,A8s,A7s,A6s,"
            "AQo:0.5,AJo,ATo:0.5,"
            "KQs,KJs,KTs,K9s,K8s:0.5,"
            "KQo,KJo:0.5,"
            "QJs,QTs,Q9s,"
            "JTs,J9s,"
            "T9s,T8s,"
            "98s,97s,"
            "87s,76s,65s,54s"
        ),
    },
    # BTN vs UTG open
    ("BTN", "UTG"): {
        "3bet": _parse_range_str("AA,KK,QQ:0.3,AKs,AKo:0.3,A5s:0.3"),
        "call": _parse_range_str(
            "QQ:0.7,JJ,TT,99,88,77,66:0.5,"
            "AQs,AJs,ATs,"
            "AQo:0.5,"
            "KQs,KJs:0.5,"
            "QJs,QTs,"
            "JTs,T9s,98s,87s,76s,65s,54s:0.5"
        ),
    },
}


def get_open_range(position: str) -> dict[str, float]:
    """Get the RFI opening range for a position. Returns {hand: freq}."""
    return OPEN_RANGES.get(position.upper(), {})


def get_vs_open(hero_pos: str, opener_pos: str) -> dict:
    """Get 3bet and call ranges when facing an open.
    Returns {"3bet": {hand:freq}, "call": {hand:freq}}."""
    key = (hero_pos.upper(), opener_pos.upper())
    return THREE_BET_RANGES.get(key, {"3bet": {}, "call": {}})


def range_to_solver_str(range_dict: dict[str, float]) -> str:
    """Convert {hand: freq} dict to solver range string like 'AA,AKs,AQs:0.5'."""
    parts = []
    for hand, freq in sorted(range_dict.items(), key=lambda x: -x[1]):
        if freq <= 0:
            continue
        if freq >= 0.99:
            parts.append(hand)
        else:
            parts.append(f"{hand}:{freq:.2f}")
    return ",".join(parts)


def count_combos(range_dict: dict[str, float]) -> float:
    """Count weighted combos in a range."""
    total = 0.0
    for hand, freq in range_dict.items():
        if len(hand) == 2:  # pair
            total += 6 * freq
        elif hand.endswith("s"):
            total += 4 * freq
        elif hand.endswith("o"):
            total += 12 * freq
    return total


if __name__ == "__main__":
    print("=== 6-Max 100bb GTO Opening Ranges ===\n")
    for pos in ["UTG", "MP", "CO", "BTN", "SB"]:
        r = get_open_range(pos)
        combos = count_combos(r)
        pct = combos / 1326 * 100
        print(f"{pos}: {combos:.0f} combos ({pct:.1f}%)  [{len(r)} hands]")

    print("\n=== 3-Bet / Call Ranges ===\n")
    for (hero, villain), data in sorted(THREE_BET_RANGES.items()):
        c3 = count_combos(data["3bet"])
        cc = count_combos(data["call"])
        print(f"{hero} vs {villain} open: 3bet {c3:.0f} combos, call {cc:.0f} combos")

    print("\n=== Solver range strings (for postflop-solver input) ===\n")
    print(f"UTG open: {range_to_solver_str(get_open_range('UTG'))[:100]}...")
    print(f"BB call vs BTN: {range_to_solver_str(get_vs_open('BB','BTN')['call'])[:100]}...")
