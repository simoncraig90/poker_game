#!/usr/bin/env python3
"""Build preflop decision charts for human-clicker mode.

Generates a single JSON file containing preflop action recommendations
for every (position, facing_action, hand, opponent_profile) combination.

The charts are NOT solver output — they are standard NL10 6-max ranges
adjusted per opponent archetype. They encode well-known population
tendencies and exploit-adjusted ranges.

Usage:
  python build_preflop_charts.py --output configs/preflop_charts.json

Output format:
  {
    "version": 1,
    "profiles": {
      "fish":  { "unopened": { "UTG": { "AAo": "OPEN", ... }, ... }, ... },
      "tag":   { ... },
      ...
    },
    "default_profile": "tag"
  }

Hand notation: "AAo" (pair), "AKs" (suited), "AKo" (offsuit).
Actions: FOLD, OPEN, OPEN_LARGE, CALL, RAISE_3X, JAM.
"""

import argparse
import json
from pathlib import Path

# ─── 169 starting hands ─────────────────────────────────────────────────────

RANKS = "AKQJT98765432"

def all_hands():
    """Generate all 169 canonical starting hand names.

    Convention: high card first.  "AKs" not "KAs", "AKo" not "KAo".
    Pairs use "o" suffix: "AAo", "KKo", etc.
    """
    hands = []
    for i, r1 in enumerate(RANKS):
        for j, r2 in enumerate(RANKS):
            if i < j:
                hands.append(f"{r1}{r2}s")  # suited, r1 is higher rank
            elif i > j:
                hands.append(f"{r2}{r1}o")  # offsuit, swap so higher rank first
            else:
                hands.append(f"{r1}{r2}o")  # pair
    return hands

HANDS_169 = all_hands()

# ─── Hand strength tiers ────────────────────────────────────────────────────
# Based on standard 6-max NL10 ranges.  Each tier is a set of hand strings.

def _expand_pairs(lo, hi):
    """Expand pair range like (8, 14) -> {'88o', '99o', ..., 'AAo'}."""
    return {f"{RANKS[14-r]}{RANKS[14-r]}o" for r in range(lo, hi + 1)}

def _expand_suited(hi_rank, lo_from, lo_to):
    """Expand suited range like ('A', 2, 5) -> {'A2s', 'A3s', 'A4s', 'A5s'}."""
    return {f"{hi_rank}{RANKS[14-lo]}s" for lo in range(lo_from, lo_to + 1)}

def _expand_offsuit(hi_rank, lo_from, lo_to):
    return {f"{hi_rank}{RANKS[14-lo]}o" for lo in range(lo_from, lo_to + 1)}

# Premium: AA, KK, QQ, AKs, AKo
PREMIUM = _expand_pairs(12, 14) | {"AKs", "AKo"}

# Strong: JJ, TT, AQs, AQo, AJs, KQs
STRONG = _expand_pairs(10, 11) | {"AQs", "AQo", "AJs", "KQs"}

# Playable: 99-77, ATs-A2s, KJs-KTs, QJs, JTs, T9s, 98s, 87s, 76s, AJo-ATo, KQo
PLAYABLE_PAIRS = _expand_pairs(7, 9)
PLAYABLE_SUITED = (
    _expand_suited("A", 2, 10) |
    _expand_suited("K", 10, 11) |
    {"QJs", "JTs", "T9s", "98s", "87s", "76s", "65s"}
)
PLAYABLE_OFFSUIT = {"AJo", "ATo", "KQo", "KJo"}
PLAYABLE = PLAYABLE_PAIRS | PLAYABLE_SUITED | PLAYABLE_OFFSUIT

# Marginal: 66-22, suited connectors/gappers, weak broadways
MARGINAL_PAIRS = _expand_pairs(2, 6)
MARGINAL_SUITED = (
    {"K9s", "K8s", "K7s", "K6s", "K5s", "K4s", "K3s", "K2s"} |
    {"Q9s", "QTs", "J9s", "T8s", "97s", "86s", "75s", "64s", "54s", "53s", "43s"}
)
MARGINAL_OFFSUIT = {"QJo", "QTo", "JTo", "KTo", "A9o", "A8o", "A7o", "A6o", "A5o"}
MARGINAL = MARGINAL_PAIRS | MARGINAL_SUITED | MARGINAL_OFFSUIT

ALL_PLAYABLE = PREMIUM | STRONG | PLAYABLE | MARGINAL

# ─── Position-based opening ranges ──────────────────────────────────────────

# Standard NL10 6-max opening ranges (% of hands, approximate)
# UTG: ~15%  HJ: ~18%  CO: ~25%  BTN: ~40%  SB: ~35% (open or complete)
OPEN_RANGES = {
    "UTG": PREMIUM | STRONG | PLAYABLE_PAIRS | {"ATs", "AJs", "KQs", "KJs", "QJs", "JTs"},
    "HJ":  PREMIUM | STRONG | PLAYABLE,
    "CO":  PREMIUM | STRONG | PLAYABLE | MARGINAL_PAIRS | MARGINAL_SUITED,
    "BTN": ALL_PLAYABLE,
    "SB":  PREMIUM | STRONG | PLAYABLE | MARGINAL_PAIRS,
}

# Hands that open large (3x+) instead of standard (2.5x)
OPEN_LARGE_ALWAYS = PREMIUM  # always size up premiums from EP

# ─── Facing-open ranges (call vs 3bet vs fold) ─────────────────────────────

# Standard defend vs BTN open from BB
THREEBET_RANGE = PREMIUM | {"AQs", "AQo", "JJ", "TT"}
CALL_VS_OPEN = STRONG | PLAYABLE | MARGINAL_SUITED | MARGINAL_PAIRS
# Everything else: FOLD

# ─── Facing 3bet ranges ────────────────────────────────────────────────────

FOURBET_RANGE = {"AAo", "KKo", "AKs", "AKo"}
CALL_VS_3BET = {"QQo", "JJo", "AQs", "AQo", "AJs", "KQs"}

# ─── Profile adjustments ───────────────────────────────────────────────────

def build_unopened_chart(position, profile):
    """Build open/fold chart for an unopened pot."""
    chart = {}
    base_range = OPEN_RANGES.get(position, set())

    # Profile adjustments
    if profile == "fish":
        # Widen significantly — fish in blinds call too much but fold equity
        # still exists. Open wider from LP.
        if position in ("BTN", "CO", "SB"):
            extra = MARGINAL  # open everything marginal+
            base_range = base_range | extra
    elif profile == "nit":
        # Steal more aggressively — nits overfold blinds
        if position in ("BTN", "CO"):
            base_range = ALL_PLAYABLE | MARGINAL
        elif position == "SB":
            base_range = ALL_PLAYABLE
    elif profile == "lag":
        # Tighten up slightly — LAGs 3bet wide, so our opens need to
        # withstand 3bets more often
        if position in ("UTG", "HJ"):
            base_range = PREMIUM | STRONG
    elif profile == "loose_passive":
        # Similar to fish but less extreme
        if position in ("BTN", "CO"):
            base_range = base_range | MARGINAL_SUITED | MARGINAL_PAIRS

    for hand in HANDS_169:
        if hand in base_range:
            if hand in OPEN_LARGE_ALWAYS and position in ("UTG", "HJ"):
                chart[hand] = "OPEN_LARGE"
            else:
                chart[hand] = "OPEN"
        else:
            chart[hand] = "FOLD"

    return chart


def build_facing_open_chart(position, profile):
    """Build call/3bet/fold chart when facing a single raise."""
    chart = {}

    # Adjust 3bet and call ranges by profile
    threebet = set(THREEBET_RANGE)
    call = set(CALL_VS_OPEN)

    if profile == "fish":
        # Fish call 3bets too wide — 3bet wider for value
        threebet = threebet | STRONG
        # Call tighter — fish don't fold to c-bets, so speculative hands lose value
        call = call - MARGINAL_OFFSUIT
    elif profile == "nit":
        # Nit folds to 3bets — 3bet wider as a bluff
        threebet = threebet | {"A5s", "A4s", "A3s", "A2s", "KJs", "QJs"}
        call = call - MARGINAL_PAIRS  # less set-mining vs nit
    elif profile == "lag":
        # LAG opens wide — widen call and 3bet ranges
        threebet = threebet | STRONG
        call = call | MARGINAL
    elif profile == "loose_passive":
        # Wide range but won't fight back vs 3bet
        threebet = threebet | {"AJs", "ATs", "KQs"}

    # Position adjustments for facing open
    if position in ("UTG", "HJ"):
        # Facing EP open: tighten everything
        threebet = threebet & PREMIUM
        call = call & (STRONG | PLAYABLE_PAIRS)
    elif position == "BB":
        # BB gets best price — widen call range
        call = call | MARGINAL

    for hand in HANDS_169:
        if hand in threebet:
            chart[hand] = "RAISE_3X"
        elif hand in call:
            chart[hand] = "CALL"
        else:
            chart[hand] = "FOLD"

    return chart


def build_facing_3bet_chart(position, profile):
    """Build call/4bet/fold chart when hero opened and got 3bet."""
    chart = {}

    fourbet = set(FOURBET_RANGE)
    call = set(CALL_VS_3BET)

    if profile == "fish":
        # Fish 3bet means strong — tighten up
        fourbet = {"AAo", "KKo"}
        call = {"AKs", "AKo", "QQo", "JJo"}
    elif profile == "nit":
        # Nit 3bet = monsters only — fold almost everything
        fourbet = {"AAo"}
        call = {"KKo", "AKs"}
    elif profile == "lag":
        # LAG 3bets wide — widen 4bet bluffs and calls
        fourbet = fourbet | {"QQo", "AQs"}
        call = call | {"TTo", "99o", "AJs", "KQs"}
    elif profile == "loose_passive":
        # Loose passive rarely 3bets — when they do, respect it
        fourbet = {"AAo", "KKo"}
        call = {"AKs", "AKo", "QQo", "JJo", "AQs"}

    for hand in HANDS_169:
        if hand in fourbet:
            chart[hand] = "RAISE_3X"  # 4bet
        elif hand in call:
            chart[hand] = "CALL"
        else:
            chart[hand] = "FOLD"

    return chart


def build_facing_limp_chart(position, profile):
    """Build raise/limp-behind/fold chart when facing limpers."""
    chart = {}

    # Vs limpers: raise for isolation with strong hands, limp behind
    # with speculative hands, fold trash.
    iso_raise = PREMIUM | STRONG | PLAYABLE_PAIRS | {"ATs", "AJs", "AQs", "KQs"}
    limp_behind = PLAYABLE_SUITED | MARGINAL_SUITED | MARGINAL_PAIRS

    if profile == "fish":
        # Fish limp/call everything — iso-raise tighter for value,
        # limp behind with more speculative hands
        iso_raise = PREMIUM | STRONG
        limp_behind = PLAYABLE | MARGINAL_SUITED | MARGINAL_PAIRS
    elif profile == "nit":
        # Nit limps = trapping or genuinely weak. Iso wide.
        iso_raise = PREMIUM | STRONG | PLAYABLE
    elif profile == "loose_passive":
        # Passive limpers — iso for value
        iso_raise = PREMIUM | STRONG | PLAYABLE_PAIRS

    # BB special: can check behind limpers
    if position == "BB":
        for hand in HANDS_169:
            if hand in iso_raise:
                chart[hand] = "RAISE_3X"
            else:
                chart[hand] = "CALL"  # check/see flop free
    else:
        for hand in HANDS_169:
            if hand in iso_raise:
                chart[hand] = "RAISE_3X"
            elif hand in limp_behind:
                chart[hand] = "CALL"
            else:
                chart[hand] = "FOLD"

    return chart


# ─── Chart builders per facing scenario ─────────────────────────────────────

FACING_BUILDERS = {
    "unopened":    build_unopened_chart,
    "facing_open": build_facing_open_chart,
    "facing_3bet": build_facing_3bet_chart,
    "facing_limp": build_facing_limp_chart,
}

PROFILES = ["fish", "loose_passive", "tag", "nit", "lag"]
POSITIONS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]


def build_all_charts():
    """Build the complete preflop chart structure."""
    charts = {"version": 1, "profiles": {}, "default_profile": "tag"}

    for profile in PROFILES:
        charts["profiles"][profile] = {}
        for facing, builder in FACING_BUILDERS.items():
            charts["profiles"][profile][facing] = {}
            for pos in POSITIONS:
                chart = builder(pos, profile)
                charts["profiles"][profile][facing][pos] = chart

    return charts


def print_summary(charts):
    """Print a summary of the chart coverage."""
    print("=" * 60)
    print("PREFLOP CHART SUMMARY")
    print("=" * 60)

    for profile in PROFILES:
        print(f"\n--- Profile: {profile} ---")
        for facing in FACING_BUILDERS:
            print(f"  {facing}:")
            for pos in POSITIONS:
                chart = charts["profiles"][profile][facing][pos]
                actions = {}
                for hand, action in chart.items():
                    actions[action] = actions.get(action, 0) + 1
                parts = [f"{a}={c}" for a, c in sorted(actions.items())]
                print(f"    {pos:4s}: {', '.join(parts)}")

    total = sum(
        len(charts["profiles"][p][f][pos])
        for p in PROFILES
        for f in FACING_BUILDERS
        for pos in POSITIONS
    )
    print(f"\nTotal entries: {total:,}")


def main():
    parser = argparse.ArgumentParser(description="Build preflop charts")
    parser.add_argument("--output", required=True, help="Output JSON file")
    parser.add_argument("--summary", action="store_true", help="Print summary")
    args = parser.parse_args()

    charts = build_all_charts()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(charts, indent=1, sort_keys=False) + "\n")
    print(f"Wrote preflop charts to {out}")

    if args.summary:
        print_summary(charts)


if __name__ == "__main__":
    main()
