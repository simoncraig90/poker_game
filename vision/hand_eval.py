"""
7-card hand evaluator — Phase 2 deliverable #4 (foundation for equity).

Given 7 cards (hero's 2 + 5 board cards), return a comparable tuple
representing hero's best 5-card hand. Higher tuples beat lower tuples
under standard Python ordering, so showdown comparison is just `>`.

This is a correctness-first pure Python evaluator. It enumerates all
C(7,5) = 21 five-card subsets, scores each, and returns the max.
Speed target: ~50,000 hands/sec is enough for Monte Carlo equity in
the replay harness. Production use will swap to a lookup-table
evaluator (Cactus Kev / TwoPlusTwo) later — that work is out of scope
for v0.

Tuple format
------------

Each evaluation returns a tuple `(category, *tiebreakers)` where
category is one of the HAND_* integer constants below (higher =
stronger) and tiebreakers are rank values (2..14, A high) ordered so
that ordinary tuple comparison gives the right answer:

  STRAIGHT_FLUSH  (8, high_rank)
  QUADS           (7, quad_rank, kicker)
  FULL_HOUSE      (6, trips_rank, pair_rank)
  FLUSH           (5, r1, r2, r3, r4, r5)         — top 5 of flush suit
  STRAIGHT        (4, high_rank)                  — A-5 wheel: high=5
  TRIPS           (3, trips_rank, k1, k2)
  TWO_PAIR        (2, top_pair, bot_pair, kicker)
  PAIR            (1, pair_rank, k1, k2, k3)
  HIGH_CARD       (0, r1, r2, r3, r4, r5)

Card representation matches the rest of the codebase: 2-character
strings like "As", "Kh", "2c". Both ranks and suits are case-insensitive
on input but normalized to canonical case internally.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable

# Category constants (higher = stronger). Match the conventional
# poker hand ranking, NOT the AdvisorStateMachine.HAND_* constants
# which are an older numbering. Local to this module.
CATEGORY_HIGH_CARD = 0
CATEGORY_PAIR = 1
CATEGORY_TWO_PAIR = 2
CATEGORY_TRIPS = 3
CATEGORY_STRAIGHT = 4
CATEGORY_FLUSH = 5
CATEGORY_FULL_HOUSE = 6
CATEGORY_QUADS = 7
CATEGORY_STRAIGHT_FLUSH = 8

CATEGORY_NAMES = {
    CATEGORY_HIGH_CARD: "high-card",
    CATEGORY_PAIR: "pair",
    CATEGORY_TWO_PAIR: "two-pair",
    CATEGORY_TRIPS: "trips",
    CATEGORY_STRAIGHT: "straight",
    CATEGORY_FLUSH: "flush",
    CATEGORY_FULL_HOUSE: "boat",
    CATEGORY_QUADS: "quads",
    CATEGORY_STRAIGHT_FLUSH: "straight-flush",
}


_RANK_VALUE = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}


def _parse(card: str) -> tuple[int, str]:
    """('As' / 'Ks' → (14, 's'). Returns (-1, '') for invalid input."""
    if not card or len(card) < 2:
        return (-1, "")
    r = _RANK_VALUE.get(card[0].upper(), -1)
    s = card[1].lower()
    if s not in "shdc":
        return (-1, "")
    return (r, s)


def _evaluate_5(cards: tuple) -> tuple:
    """
    Evaluate a 5-card hand and return a comparable tuple.

    Input is a tuple of 5 (rank, suit) pairs. Caller should pre-parse
    so we don't pay parse cost for each of the 21 subsets.
    """
    ranks = sorted((c[0] for c in cards), reverse=True)
    suits = [c[1] for c in cards]

    # Flush check
    is_flush = len(set(suits)) == 1

    # Straight check (handle wheel A-2-3-4-5)
    distinct = sorted(set(ranks), reverse=True)
    is_straight = False
    straight_high = 0
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            is_straight = True
            straight_high = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:  # wheel
            is_straight = True
            straight_high = 5

    if is_straight and is_flush:
        return (CATEGORY_STRAIGHT_FLUSH, straight_high)

    # Rank counts
    counts: dict[int, int] = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    # Group by count, then by rank desc within each count
    by_count = sorted(counts.items(), key=lambda x: (-x[1], -x[0]))

    if by_count[0][1] == 4:
        quad_rank = by_count[0][0]
        kicker = by_count[1][0]
        return (CATEGORY_QUADS, quad_rank, kicker)

    if by_count[0][1] == 3 and by_count[1][1] == 2:
        return (CATEGORY_FULL_HOUSE, by_count[0][0], by_count[1][0])

    if is_flush:
        return (CATEGORY_FLUSH,) + tuple(ranks)

    if is_straight:
        return (CATEGORY_STRAIGHT, straight_high)

    if by_count[0][1] == 3:
        trips_rank = by_count[0][0]
        kickers = sorted(
            (r for r, c in counts.items() if c == 1),
            reverse=True
        )
        return (CATEGORY_TRIPS, trips_rank, kickers[0], kickers[1])

    if by_count[0][1] == 2 and by_count[1][1] == 2:
        top_pair = max(by_count[0][0], by_count[1][0])
        bot_pair = min(by_count[0][0], by_count[1][0])
        kicker = by_count[2][0]
        return (CATEGORY_TWO_PAIR, top_pair, bot_pair, kicker)

    if by_count[0][1] == 2:
        pair_rank = by_count[0][0]
        kickers = sorted(
            (r for r, c in counts.items() if c == 1),
            reverse=True
        )
        return (CATEGORY_PAIR, pair_rank, kickers[0], kickers[1], kickers[2])

    return (CATEGORY_HIGH_CARD,) + tuple(ranks)


def evaluate(cards: Iterable[str]) -> tuple:
    """
    Evaluate a 5/6/7 card hand. Returns the best 5-card score tuple.

    Input: iterable of card strings ("As", "Kh", "2c", ...). Length
    must be 5, 6, or 7. Raises ValueError otherwise.

    The returned tuple is directly comparable: `evaluate(h1) > evaluate(h2)`
    is True if h1 beats h2. Tie returns equal tuples.
    """
    parsed = [_parse(c) for c in cards]
    if any(r < 0 for r, _ in parsed):
        raise ValueError(f"invalid card in {list(cards)}")
    n = len(parsed)
    if n < 5 or n > 7:
        raise ValueError(f"need 5-7 cards, got {n}")
    if n == 5:
        return _evaluate_5(tuple(parsed))
    best = (-1,)
    for combo in combinations(parsed, 5):
        score = _evaluate_5(combo)
        if score > best:
            best = score
    return best


def category_of(score: tuple) -> int:
    """Extract just the category from an evaluate() result."""
    return score[0] if score else -1


def category_name(score: tuple) -> str:
    """Human-readable name for the category of a score."""
    return CATEGORY_NAMES.get(category_of(score), "?")


def compare(score_a: tuple, score_b: tuple) -> int:
    """
    Standard 3-way compare: 1 if a > b, -1 if a < b, 0 if equal.
    Equivalent to (score_a > score_b) - (score_a < score_b) but
    explicit for readability at call sites.
    """
    if score_a > score_b:
        return 1
    if score_a < score_b:
        return -1
    return 0
