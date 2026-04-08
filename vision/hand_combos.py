"""
Hand combo expansion — the bridge between 169-shape range keys and
the 1326-combo space.

A range like "AKs" represents 4 specific 2-card combinations
(one per suit). "AKo" represents 12. "AA" represents 6. The 169
hand-shape grid collapses these for human readability, but actual
equity computation needs the 1326-combo level because card removal
matters: if hero holds the As, all 4 of villain's "AA" combos
involving As are impossible — only 3 AA combos remain.

This module provides the expansion in both directions:

  expand_hand_key("AKs") → [(As,Ks), (Ah,Kh), (Ad,Kd), (Ac,Kc)]
  combo_count("AKs") → 4
  combo_to_key((As, Kh)) → "AKo"

Plus higher-level helpers that operate on entire ranges:

  expand_range({"AKs", "QQ"}) → list of all (c1, c2) tuples in the range
  range_combo_count(range) → total combos in the range
  remove_blockers(range, dead_cards) → range minus combos using dead cards

Card representation: 2-character strings like "As", "Kh", "2c". The
existing AdvisorStateMachine and snapshots use the same shape, so
inputs and outputs can flow between modules without conversion.
"""

from __future__ import annotations

from typing import Iterable, Optional

# All 52 card identifiers used as the canonical universe.
RANKS = "23456789TJQKA"
SUITS = "shdc"
ALL_CARDS: tuple[str, ...] = tuple(r + s for r in RANKS for s in SUITS)

# Card → integer rank (2..14 with A high). Used internally for combo
# generation; callers usually don't need this.
_RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}


def _rank_value(card: str) -> int:
    """2..14, A high. None for invalid input."""
    if not card or len(card) < 2:
        return -1
    return _RANK_VALUE.get(card[0].upper(), -1)


def _suit(card: str) -> str:
    if not card or len(card) < 2:
        return ""
    return card[1].lower()


# ── single-direction expansion ────────────────────────────────────────


def expand_hand_key(key: str) -> list[tuple[str, str]]:
    """
    Expand a 169-shape hand key into all 2-card combos it represents.

    Input format:
        "AA"   (pocket pair, 6 combos)
        "AKs"  (suited two-card, 4 combos)
        "AKo"  (offsuit two-card, 12 combos)

    Output is a list of (card1, card2) tuples where card1 is the
    higher rank (or higher of two equal ranks by suit order). Order
    of combos within the list is deterministic but unspecified.

    Returns an empty list for invalid keys.

    Examples:
        >>> expand_hand_key("AA")
        [('Ah', 'Ad'), ('Ah', 'Ac'), ('Ah', 'As'), ...]  # 6 combos
        >>> len(expand_hand_key("AKs"))
        4
        >>> len(expand_hand_key("AKo"))
        12
    """
    if not key or len(key) < 2:
        return []

    r1_char = key[0].upper()
    r2_char = key[1].upper()
    if r1_char not in RANKS or r2_char not in RANKS:
        return []

    # Pocket pair: AA, KK, etc — 6 combos.
    if r1_char == r2_char:
        if len(key) != 2:
            return []
        cards = [r1_char + s for s in SUITS]
        out: list[tuple[str, str]] = []
        for i in range(len(cards)):
            for j in range(i + 1, len(cards)):
                out.append((cards[i], cards[j]))
        return out

    # Two-rank: must end with 's' or 'o'.
    if len(key) != 3:
        return []
    suffix = key[2].lower()
    if suffix not in ("s", "o"):
        return []

    # Ensure r1 is the higher rank for canonical ordering.
    if _RANK_VALUE[r1_char] < _RANK_VALUE[r2_char]:
        r1_char, r2_char = r2_char, r1_char

    if suffix == "s":
        # Same suit, 4 combos.
        return [(r1_char + s, r2_char + s) for s in SUITS]
    else:
        # Different suits, 4*3 = 12 combos.
        out = []
        for s1 in SUITS:
            for s2 in SUITS:
                if s1 == s2:
                    continue
                out.append((r1_char + s1, r2_char + s2))
        return out


def combo_count(key: str) -> int:
    """169-shape → number of combos. 6/4/12/0 for pair/suited/offsuit/invalid."""
    if not key or len(key) < 2:
        return 0
    r1, r2 = key[0].upper(), key[1].upper()
    if r1 not in RANKS or r2 not in RANKS:
        return 0
    if r1 == r2:
        return 6 if len(key) == 2 else 0
    if len(key) != 3:
        return 0
    suffix = key[2].lower()
    if suffix == "s":
        return 4
    if suffix == "o":
        return 12
    return 0


# ── reverse direction: combo → key ────────────────────────────────────


def combo_to_key(card1: str, card2: str) -> str:
    """
    Map a 2-card combo back to its 169-shape key.

    Returns "" for invalid input or when the two cards are the same.

    Examples:
        >>> combo_to_key("Ah", "Ks")
        'AKo'
        >>> combo_to_key("Ah", "Kh")
        'AKs'
        >>> combo_to_key("Ah", "Ad")
        'AA'
    """
    r1 = _rank_value(card1)
    r2 = _rank_value(card2)
    if r1 < 2 or r2 < 2:
        return ""
    s1 = _suit(card1)
    s2 = _suit(card2)
    if s1 not in SUITS or s2 not in SUITS:
        return ""
    if card1 == card2:
        return ""

    # Canonical ordering: higher rank first.
    if r1 < r2:
        r1, r2 = r2, r1
        s1, s2 = s2, s1
        card1, card2 = card2, card1

    rc1 = card1[0].upper()
    rc2 = card2[0].upper()
    if rc1 == rc2:
        return rc1 + rc2  # pocket pair
    if s1 == s2:
        return rc1 + rc2 + "s"
    return rc1 + rc2 + "o"


# ── operations on full ranges ─────────────────────────────────────────


def expand_range(range_keys: Iterable[str]) -> list[tuple[str, str]]:
    """
    Expand a set of hand keys to the full list of 2-card combos.
    Duplicates are eliminated. Order is deterministic but unspecified.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for key in range_keys:
        for combo in expand_hand_key(key):
            # Canonicalize tuple ordering for dedup
            a, b = combo
            t = (a, b) if a <= b else (b, a)
            if t in seen:
                continue
            seen.add(t)
            out.append(combo)
    return out


def range_combo_count(range_keys: Iterable[str]) -> int:
    """Total number of 2-card combos in the range (sum, no dedup)."""
    return sum(combo_count(k) for k in range_keys)


def remove_blockers(range_keys: Iterable[str],
                    dead_cards: Iterable[str]) -> list[tuple[str, str]]:
    """
    Expand the range and drop any combo that uses one of the dead cards.

    Used to remove villain combos blocked by hero's hole cards or by
    visible board cards. Critical for accurate range equity — the
    raw "AA" key has 6 combos, but if the board shows the As, only 3
    AA combos are still possible (the ones with Ah/Ad/Ac plus another
    non-As ace, but the board As also blocks 1 of each → 3 left).

    Returns a list of (card1, card2) tuples that survived the dead-card
    filter.
    """
    dead = {c for c in dead_cards if c}
    survivors: list[tuple[str, str]] = []
    for c1, c2 in expand_range(range_keys):
        if c1 in dead or c2 in dead:
            continue
        survivors.append((c1, c2))
    return survivors


def range_combos_after_blockers(range_keys: Iterable[str],
                                dead_cards: Iterable[str]) -> int:
    """Count surviving combos. Convenience wrapper around remove_blockers."""
    return len(remove_blockers(range_keys, dead_cards))
