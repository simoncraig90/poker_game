"""
Range model — Phase 2 foundation.

The structural fix for the equity model. Today's leak class
(`PAIR`-class hands on flop/turn/river losing money) traces back to
one architectural fact: equity is computed vs a random hand instead
of vs the range villain *actually* holds when they take a specific
action. This module is the input side of that fix.

Design
------

A "range" is a set of hand combos villain could be holding given:

  - their villain classification (NIT / TAG / LAG / FISH / UNKNOWN)
  - their position (UTG / MP / CO / BTN / SB / BB)
  - their first action (OPEN / 3BET / 4BET / CALL / FOLD)
  - subsequent action sequence (Phase 2.1 — narrowing engine, not
    in this module)

This file defines the **starting ranges** — what villain plays at
all before any board card is dealt. The narrowing engine consumes
these and the action history (from `vision/action_history.py`)
to produce the *current* range at any decision point.

Range representation: for v0 we use the 169-hand "shape" format
(AKs, AKo, AA, etc) without combo counts. Full 1326-combo
representation lands in Phase 2 alongside the range equity calculator
where combo counting matters most.

Sources
-------

Range data is sourced from published 6-max micros material plus
the captured corpus's HUD ground truth. Numbers are explicit and
auditable in one place — no procedural generation. When the harness
shows a specific shape losing systematically, the fix is to edit
the range here and re-run the harness.

This is intentionally NOT a solver. Solver-derived ranges are a
Phase 3 item (`Solver-derived preflop with stack-depth conditioning`).
v0 is hand-curated tight/aggressive defaults that are good enough to
unblock the equity calculation work in Phase 2.
"""

from __future__ import annotations

from typing import Optional

# ── villain classification constants ──────────────────────────────────

CLASS_NIT = "NIT"        # tight passive: VPIP < 18, PFR < 14
CLASS_TAG = "TAG"        # tight aggressive: VPIP 18-25, PFR/VPIP > 0.7
CLASS_LAG = "LAG"        # loose aggressive: VPIP 26-35, PFR/VPIP > 0.65
CLASS_FISH = "FISH"      # loose passive: VPIP > 30, PFR/VPIP < 0.4
CLASS_UNKNOWN = "UNKNOWN"  # no HUD data — default to NIT at micro stakes

ALL_CLASSES = (CLASS_NIT, CLASS_TAG, CLASS_LAG, CLASS_FISH, CLASS_UNKNOWN)

# ── position constants (6-max) ────────────────────────────────────────

POS_UTG = "UTG"
POS_MP = "MP"
POS_CO = "CO"
POS_BTN = "BTN"
POS_SB = "SB"
POS_BB = "BB"

ALL_POSITIONS = (POS_UTG, POS_MP, POS_CO, POS_BTN, POS_SB, POS_BB)

# ── action constants (preflop only for v0) ────────────────────────────

ACTION_OPEN = "OPEN"      # first voluntary chip in the pot beyond blinds
ACTION_3BET = "3BET"      # raise over an open
ACTION_4BET = "4BET"      # raise over a 3-bet
ACTION_CALL = "CALL"      # cold-call an open or 3-bet
ACTION_LIMP = "LIMP"      # call the BB without raising

# ─────────────────────────────────────────────────────────────────────
# NIT — tight passive (VPIP ~14%, PFR ~10%)
# ─────────────────────────────────────────────────────────────────────

# Open ranges by position. Each set is the hands NIT will OPEN-RAISE
# from that seat. Numbers next to each line are approximate combo
# counts in the 169-shape grid (not 1326).
NIT_OPEN: dict[str, set[str]] = {
    POS_UTG: {
        # ~6% — premium pairs and broadway only
        "AA", "KK", "QQ", "JJ", "TT", "99",
        "AKs", "AQs", "AJs", "ATs",
        "KQs", "KJs", "QJs",
        "AKo", "AQo",
    },
    POS_MP: {
        # ~9% — add small pairs, more suited broadway
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77",
        "AKs", "AQs", "AJs", "ATs", "A9s",
        "KQs", "KJs", "KTs", "QJs", "QTs", "JTs",
        "AKo", "AQo", "AJo",
    },
    POS_CO: {
        # ~14% — add suited aces, more connectors
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66",
        "AKs", "AQs", "AJs", "ATs", "A9s", "A8s", "A7s", "A5s",
        "KQs", "KJs", "KTs", "K9s",
        "QJs", "QTs", "Q9s",
        "JTs", "J9s", "T9s", "98s",
        "AKo", "AQo", "AJo", "KQo",
    },
    POS_BTN: {
        # ~22% — wide opens
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55", "44",
        "AKs", "AQs", "AJs", "ATs", "A9s", "A8s", "A7s", "A6s", "A5s", "A4s", "A3s", "A2s",
        "KQs", "KJs", "KTs", "K9s", "K8s",
        "QJs", "QTs", "Q9s", "Q8s",
        "JTs", "J9s", "J8s", "T9s", "T8s", "98s", "87s", "76s",
        "AKo", "AQo", "AJo", "ATo", "KQo", "KJo", "QJo",
    },
    POS_SB: {
        # SB only opens vs BB (everyone folded). Slightly tighter
        # than BTN because SB is OOP postflop.
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55",
        "AKs", "AQs", "AJs", "ATs", "A9s", "A5s",
        "KQs", "KJs", "KTs",
        "QJs", "QTs", "JTs", "T9s", "98s",
        "AKo", "AQo", "AJo", "KQo",
    },
    POS_BB: set(),  # BB doesn't "open" — no one acts after them preflop
}

# 3-bet (re-raise over an open). Linear value plus a few blocker bluffs.
NIT_3BET: dict[str, set[str]] = {
    POS_UTG: {"AA", "KK", "QQ", "AKs", "AKo"},
    POS_MP:  {"AA", "KK", "QQ", "JJ", "AKs", "AKo"},
    POS_CO:  {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs"},
    POS_BTN: {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs", "AQo"},
    POS_SB:  {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs"},
    POS_BB:  {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs"},
}

# 4-bet — only ever the absolute top of range
NIT_4BET: dict[str, set[str]] = {p: {"AA", "KK", "AKs"} for p in ALL_POSITIONS}

# Cold-call (flat) ranges vs an open. Set-mining hands plus playable
# suited broadway / suited aces in late position.
NIT_CALL: dict[str, set[str]] = {
    POS_UTG: {"JJ", "TT", "99", "AQs", "AJs", "KQs"},
    POS_MP:  {"JJ", "TT", "99", "88", "AQs", "AJs", "ATs", "KQs", "KJs", "QJs"},
    POS_CO:  {"TT", "99", "88", "77", "AJs", "ATs", "KQs", "KJs", "QJs", "JTs", "T9s"},
    POS_BTN: {"TT", "99", "88", "77", "66", "55", "AJs", "ATs", "A9s",
              "KQs", "KJs", "KTs", "QJs", "QTs", "JTs", "T9s", "98s",
              "AJo", "KQo"},
    POS_SB:  {"TT", "99", "88", "77", "AJs", "ATs", "KQs", "KJs", "QJs", "JTs"},
    POS_BB:  {"99", "88", "77", "66", "55", "44", "33", "22",
              "AJs", "ATs", "A9s", "A8s",
              "KQs", "KJs", "KTs", "QJs", "QTs", "JTs", "T9s",
              "AJo", "ATo", "KQo", "KJo", "QJo"},
}

# Limp — NIT essentially never limps. Empty.
NIT_LIMP: dict[str, set[str]] = {p: set() for p in ALL_POSITIONS}


# ─────────────────────────────────────────────────────────────────────
# TAG — tight aggressive (VPIP ~22%, PFR ~18%)
# ─────────────────────────────────────────────────────────────────────

TAG_OPEN: dict[str, set[str]] = {
    POS_UTG: NIT_OPEN[POS_MP] | {"66", "K9s", "T9s", "98s", "KJo"},  # ~12%
    POS_MP: NIT_OPEN[POS_CO] | {"55", "44", "K8s", "Q8s", "JTo", "KJo"},  # ~16%
    POS_CO: NIT_OPEN[POS_BTN] - {"K8s"} | {"33", "22", "A8s","A6s","A4s","A3s","A2s",
                                            "K7s", "Q7s", "J8s", "T7s", "97s", "86s", "75s", "65s", "54s",
                                            "ATo", "KJo", "QTo", "JTo", "T9o"},  # ~22%
    POS_BTN: NIT_OPEN[POS_BTN] | {"33", "22", "K7s", "K6s", "K5s", "K4s", "K3s", "K2s",
                                    "Q7s", "Q6s", "Q5s", "J7s", "T7s", "96s", "86s", "75s", "65s", "54s",
                                    "A8o", "A7o", "A6o", "A5o", "A4o", "A3o", "A2o",
                                    "KTo", "K9o", "QTo", "Q9o", "J9o", "T9o", "98o", "87o", "76o"},  # ~30%
    POS_SB: NIT_OPEN[POS_BTN] - {"K8s", "Q8s", "J8s", "T8s", "T9s", "98s", "87s", "76s"} |
            {"K7s", "K5s", "Q7s", "J9s", "T9s", "98s", "87s",
             "ATo", "KJo", "QJo", "JTo"},  # ~22% — tighter than BTN due to OOP
    POS_BB: set(),
}

TAG_3BET: dict[str, set[str]] = {
    POS_UTG: {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs"},
    POS_MP:  {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs"},
    POS_CO:  {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs", "AQo", "A5s"},
    POS_BTN: {"AA", "KK", "QQ", "JJ", "TT", "99", "AKs", "AKo", "AQs", "AQo", "AJs", "KQs",
              "A5s", "A4s"},
    POS_SB:  {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs", "AQo", "AJs", "KQs",
              "A5s", "A4s"},
    POS_BB:  {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs", "AQo", "AJs", "KQs",
              "A5s", "A4s", "76s", "65s"},
}

TAG_4BET: dict[str, set[str]] = {p: {"AA", "KK", "QQ", "AKs", "AKo"} for p in ALL_POSITIONS}

TAG_CALL: dict[str, set[str]] = {
    POS_UTG: NIT_CALL[POS_MP] | {"66", "55", "ATs", "JTs", "T9s"},
    POS_MP: NIT_CALL[POS_CO] | {"66", "55", "44", "ATs", "A9s", "T9s", "98s"},
    POS_CO: NIT_CALL[POS_BTN] | {"33", "22", "98s", "87s", "76s"},
    POS_BTN: NIT_CALL[POS_BTN] | {"44", "33", "22", "A8s", "A7s", "K9s", "Q9s", "J9s",
                                   "98s", "87s", "76s", "65s", "ATo", "KJo", "QTo", "JTo"},
    POS_SB: NIT_CALL[POS_SB] | {"66", "55", "T9s", "98s"},
    POS_BB: NIT_CALL[POS_BB] | {"K9s", "K8s", "Q8s", "J8s", "98s", "87s", "76s", "T8o", "98o"},
}

TAG_LIMP: dict[str, set[str]] = {p: set() for p in ALL_POSITIONS}


# ─────────────────────────────────────────────────────────────────────
# LAG — loose aggressive (VPIP ~30%, PFR ~24%)
# ─────────────────────────────────────────────────────────────────────

LAG_OPEN: dict[str, set[str]] = {
    POS_UTG: TAG_OPEN[POS_MP],   # ~16%
    POS_MP: TAG_OPEN[POS_CO],    # ~22%
    POS_CO: TAG_OPEN[POS_BTN],   # ~30%
    POS_BTN: TAG_OPEN[POS_BTN] | {"J6s", "T6s", "96s", "85s", "74s", "64s", "53s", "43s",
                                    "Q8o", "J8o", "T8o", "97o", "86o", "75o", "65o", "54o"},  # ~40%
    POS_SB: TAG_OPEN[POS_BTN] | {"J7s", "T7s", "97s", "86s", "75s", "64s",
                                  "K9o", "Q9o", "J9o", "98o", "87o"},  # ~32%
    POS_BB: set(),
}

# LAGs 3bet wider (more bluffs)
LAG_3BET: dict[str, set[str]] = {
    POS_UTG: TAG_3BET[POS_UTG] | {"TT", "AJs"},
    POS_MP:  TAG_3BET[POS_MP] | {"99", "AJs", "KQs"},
    POS_CO:  TAG_3BET[POS_CO] | {"99", "AJs", "KQs", "A4s", "76s"},
    POS_BTN: TAG_3BET[POS_BTN] | {"88", "AJo", "KJs", "QJs", "76s", "65s", "54s"},
    POS_SB:  TAG_3BET[POS_SB] | {"88", "KJs", "QJs", "JTs", "76s", "65s"},
    POS_BB:  TAG_3BET[POS_BB] | {"88", "KJs", "QJs", "JTs", "T9s", "54s"},
}

LAG_4BET: dict[str, set[str]] = {p: {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "A5s"} for p in ALL_POSITIONS}

LAG_CALL: dict[str, set[str]] = {p: TAG_CALL[p] | {"T8s", "97s", "86s", "75s", "54s"} for p in ALL_POSITIONS}

LAG_LIMP: dict[str, set[str]] = {p: set() for p in ALL_POSITIONS}


# ─────────────────────────────────────────────────────────────────────
# FISH — loose passive (VPIP > 30, PFR/VPIP < 0.4)
# ─────────────────────────────────────────────────────────────────────

# FISH RARELY raise but when they do, range is value-heavy
FISH_OPEN: dict[str, set[str]] = {
    POS_UTG: {"AA", "KK", "QQ", "JJ", "AKs", "AKo"},
    POS_MP:  {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs"},
    POS_CO:  {"AA", "KK", "QQ", "JJ", "TT", "99", "AKs", "AKo", "AQs", "AQo"},
    POS_BTN: {"AA", "KK", "QQ", "JJ", "TT", "99", "88", "AKs", "AKo", "AQs", "AQo", "AJs", "KQs"},
    POS_SB:  {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs"},
    POS_BB:  set(),
}

FISH_3BET: dict[str, set[str]] = {p: {"AA", "KK", "QQ", "AKs", "AKo"} for p in ALL_POSITIONS}

FISH_4BET: dict[str, set[str]] = {p: {"AA", "KK"} for p in ALL_POSITIONS}

# FISH cold-call WIDE — 50%+ of hands
FISH_CALL: dict[str, set[str]] = {
    p: {
        # All pairs
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55", "44", "33", "22",
        # All suited aces
        "AKs", "AQs", "AJs", "ATs", "A9s", "A8s", "A7s", "A6s", "A5s", "A4s", "A3s", "A2s",
        # All suited broadway
        "KQs", "KJs", "KTs", "K9s", "K8s",
        "QJs", "QTs", "Q9s", "Q8s",
        "JTs", "J9s", "J8s", "T9s", "T8s",
        # Most suited connectors / one-gappers
        "98s", "97s", "87s", "86s", "76s", "65s", "54s",
        # Offsuit broadway
        "AKo", "AQo", "AJo", "ATo", "A9o", "A8o", "A7o", "A6o", "A5o", "A4o", "A3o", "A2o",
        "KQo", "KJo", "KTo", "K9o", "K8o",
        "QJo", "QTo", "Q9o", "JTo", "J9o", "T9o", "98o", "87o", "76o", "65o",
    }
    for p in ALL_POSITIONS
}

# FISH limp wide too — anything they don't call-raise with
FISH_LIMP: dict[str, set[str]] = {
    p: FISH_CALL[p] | {"K7s", "K6s", "K5s", "K4s", "K3s", "K2s",
                        "Q7s", "Q6s", "Q5s", "Q4s", "J7s", "J6s",
                        "T7s", "T6s", "96s", "85s", "75s",
                        "K7o", "Q8o", "J8o", "T8o", "97o", "86o"}
    for p in ALL_POSITIONS
}


# ─────────────────────────────────────────────────────────────────────
# UNKNOWN — default to NIT at micro stakes (per existing project policy)
# ─────────────────────────────────────────────────────────────────────

UNKNOWN_OPEN = NIT_OPEN
UNKNOWN_3BET = NIT_3BET
UNKNOWN_4BET = NIT_4BET
UNKNOWN_CALL = NIT_CALL
UNKNOWN_LIMP = NIT_LIMP


# ─────────────────────────────────────────────────────────────────────
# Lookup table
# ─────────────────────────────────────────────────────────────────────

_RANGE_TABLE: dict[tuple[str, str], dict[str, set[str]]] = {
    (CLASS_NIT, ACTION_OPEN): NIT_OPEN,
    (CLASS_NIT, ACTION_3BET): NIT_3BET,
    (CLASS_NIT, ACTION_4BET): NIT_4BET,
    (CLASS_NIT, ACTION_CALL): NIT_CALL,
    (CLASS_NIT, ACTION_LIMP): NIT_LIMP,
    (CLASS_TAG, ACTION_OPEN): TAG_OPEN,
    (CLASS_TAG, ACTION_3BET): TAG_3BET,
    (CLASS_TAG, ACTION_4BET): TAG_4BET,
    (CLASS_TAG, ACTION_CALL): TAG_CALL,
    (CLASS_TAG, ACTION_LIMP): TAG_LIMP,
    (CLASS_LAG, ACTION_OPEN): LAG_OPEN,
    (CLASS_LAG, ACTION_3BET): LAG_3BET,
    (CLASS_LAG, ACTION_4BET): LAG_4BET,
    (CLASS_LAG, ACTION_CALL): LAG_CALL,
    (CLASS_LAG, ACTION_LIMP): LAG_LIMP,
    (CLASS_FISH, ACTION_OPEN): FISH_OPEN,
    (CLASS_FISH, ACTION_3BET): FISH_3BET,
    (CLASS_FISH, ACTION_4BET): FISH_4BET,
    (CLASS_FISH, ACTION_CALL): FISH_CALL,
    (CLASS_FISH, ACTION_LIMP): FISH_LIMP,
    (CLASS_UNKNOWN, ACTION_OPEN): UNKNOWN_OPEN,
    (CLASS_UNKNOWN, ACTION_3BET): UNKNOWN_3BET,
    (CLASS_UNKNOWN, ACTION_4BET): UNKNOWN_4BET,
    (CLASS_UNKNOWN, ACTION_CALL): UNKNOWN_CALL,
    (CLASS_UNKNOWN, ACTION_LIMP): UNKNOWN_LIMP,
}


def get_starting_range(villain_class: str,
                       position: str,
                       action: str) -> set[str]:
    """
    Look up the starting range for a (villain_class, position, action)
    tuple. Returns a set of 169-shape hand keys (e.g. "AKs", "AKo", "AA").

    Returns the empty set for unknown classes/positions/actions, NEVER
    raises. Callers can compose ranges (e.g., union of OPEN+CALL+LIMP)
    if they want a "what could villain be holding entering the flop"
    range without first knowing what villain did preflop.

    Args:
        villain_class: One of CLASS_NIT/TAG/LAG/FISH/UNKNOWN
        position:      One of POS_UTG/MP/CO/BTN/SB/BB
        action:        One of ACTION_OPEN/3BET/4BET/CALL/LIMP

    Returns:
        Frozen set of hand keys.

    Example:
        >>> sorted(get_starting_range(CLASS_NIT, POS_UTG, ACTION_OPEN))[:5]
        ['AA', 'AJs', 'AKo', 'AKs', 'AQs']
    """
    table = _RANGE_TABLE.get((villain_class, action))
    if table is None:
        return set()
    return set(table.get(position, set()))


def get_continuing_range(villain_class: str,
                         position: str) -> set[str]:
    """
    Convenience: union of all ranges that "continue past preflop" for
    a given villain at a given position. This is what villain could
    have entering the flop = OPEN ∪ 3BET ∪ CALL ∪ LIMP. (4-bet pots
    are a separate, much narrower scenario handled by 4BET-only.)

    Used by Phase 2's range narrowing engine as the starting point
    when we know the villain saw a flop but not exactly what action
    sequence got them there.
    """
    s = set()
    for action in (ACTION_OPEN, ACTION_3BET, ACTION_CALL, ACTION_LIMP):
        s |= get_starting_range(villain_class, position, action)
    return s


def range_size(villain_class: str, position: str, action: str) -> int:
    """Hand-shape count, used by tests and range-width sanity checks."""
    return len(get_starting_range(villain_class, position, action))


def normalize_class(raw: Optional[str]) -> str:
    """
    Map a raw classification string from any source (HUD, OpponentTracker)
    to the canonical CLASS_* constant. Unknown / empty / None becomes
    CLASS_UNKNOWN. Case-insensitive. Common synonyms accepted.
    """
    if not raw:
        return CLASS_UNKNOWN
    s = raw.strip().upper()
    if s in ALL_CLASSES:
        return s
    aliases = {
        "WHALE": CLASS_FISH, "STATION": CLASS_FISH, "PASSIVE": CLASS_FISH,
        "MANIAC": CLASS_LAG, "AGGRO": CLASS_LAG,
        "ROCK": CLASS_NIT, "TIGHT": CLASS_NIT,
        "REG": CLASS_TAG, "REGULAR": CLASS_TAG,
    }
    return aliases.get(s, CLASS_UNKNOWN)
