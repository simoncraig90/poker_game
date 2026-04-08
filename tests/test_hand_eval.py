"""
Phase 2 unit tests for the 7-card hand evaluator.

Verifies the categorization is correct and the comparison ordering
matches standard poker hand rankings. Tests the tie-breaking
logic explicitly because that's where evaluators historically have
bugs.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from hand_eval import (  # noqa: E402
    evaluate, category_of, category_name, compare,
    CATEGORY_HIGH_CARD, CATEGORY_PAIR, CATEGORY_TWO_PAIR,
    CATEGORY_TRIPS, CATEGORY_STRAIGHT, CATEGORY_FLUSH,
    CATEGORY_FULL_HOUSE, CATEGORY_QUADS, CATEGORY_STRAIGHT_FLUSH,
)


# ── basic categorization ────────────────────────────────────────────

def test_high_card():
    score = evaluate(["Ah", "Kc", "Qd", "Jh", "9s"])
    assert category_of(score) == CATEGORY_HIGH_CARD


def test_pair():
    score = evaluate(["Ah", "As", "Kc", "Qd", "Jh"])
    assert category_of(score) == CATEGORY_PAIR


def test_two_pair():
    score = evaluate(["Ah", "As", "Kc", "Kd", "Jh"])
    assert category_of(score) == CATEGORY_TWO_PAIR


def test_trips():
    score = evaluate(["Ah", "As", "Ac", "Kd", "Jh"])
    assert category_of(score) == CATEGORY_TRIPS


def test_straight():
    score = evaluate(["9h", "8c", "7d", "6h", "5s"])
    assert category_of(score) == CATEGORY_STRAIGHT


def test_wheel_straight():
    """A-2-3-4-5 is a straight, A counts as 1."""
    score = evaluate(["Ah", "2c", "3d", "4h", "5s"])
    assert category_of(score) == CATEGORY_STRAIGHT
    # Wheel high is 5, not Ace
    assert score[1] == 5


def test_broadway_straight():
    score = evaluate(["Ah", "Kc", "Qd", "Jh", "Ts"])
    assert category_of(score) == CATEGORY_STRAIGHT
    assert score[1] == 14


def test_flush():
    score = evaluate(["Ah", "Kh", "Qh", "9h", "5h"])
    assert category_of(score) == CATEGORY_FLUSH


def test_full_house():
    score = evaluate(["Ah", "As", "Ac", "Kd", "Kh"])
    assert category_of(score) == CATEGORY_FULL_HOUSE


def test_quads():
    score = evaluate(["Ah", "As", "Ac", "Ad", "Kh"])
    assert category_of(score) == CATEGORY_QUADS


def test_straight_flush():
    score = evaluate(["9h", "8h", "7h", "6h", "5h"])
    assert category_of(score) == CATEGORY_STRAIGHT_FLUSH


def test_royal_flush():
    score = evaluate(["Ah", "Kh", "Qh", "Jh", "Th"])
    assert category_of(score) == CATEGORY_STRAIGHT_FLUSH
    assert score[1] == 14  # high card is A


# ── 7-card best-of-21 selection ─────────────────────────────────────

def test_seven_cards_picks_best_5():
    """Should pick the flush over the lower pairs."""
    # AA + flush of clubs (5 clubs in 7) → flush wins, ignore the pair
    cards = ["Ah", "As", "Kc", "Qc", "Jc", "9c", "5c"]
    score = evaluate(cards)
    assert category_of(score) == CATEGORY_FLUSH


def test_seven_cards_full_house_over_flush():
    """A boat beats a flush — even if both are available."""
    # AAA + 99 + 3 hearts (not a flush of 5 anyway)
    cards = ["Ah", "As", "Ac", "9h", "9d", "8h", "5h"]
    score = evaluate(cards)
    assert category_of(score) == CATEGORY_FULL_HOUSE


def test_seven_cards_straight_flush_beats_quads():
    """SF is the highest category."""
    cards = ["9h", "8h", "7h", "6h", "5h", "5s", "5d"]
    score = evaluate(cards)
    assert category_of(score) == CATEGORY_STRAIGHT_FLUSH


# ── tie-breaking ────────────────────────────────────────────────────

def test_higher_pair_beats_lower_pair():
    a = evaluate(["Ah", "As", "Kc", "Qd", "Jh"])
    b = evaluate(["Kh", "Ks", "Ac", "Qd", "Jh"])
    assert a > b
    assert compare(a, b) == 1


def test_pair_kicker_breaks_tie():
    """KK with A kicker beats KK with Q kicker."""
    a = evaluate(["Kh", "Ks", "Ad", "5c", "2h"])
    b = evaluate(["Kh", "Ks", "Qd", "5c", "2h"])
    assert a > b


def test_two_pair_top_pair_breaks_tie():
    """AA22 beats KK22."""
    a = evaluate(["Ah", "As", "2d", "2c", "5h"])
    b = evaluate(["Kh", "Ks", "2d", "2c", "5h"])
    assert a > b


def test_two_pair_kicker_breaks_tie():
    """AA22 with K kicker beats AA22 with 5 kicker."""
    a = evaluate(["Ah", "As", "2d", "2c", "Kh"])
    b = evaluate(["Ah", "As", "2d", "2c", "5h"])
    assert a > b


def test_full_house_higher_trips_wins():
    """AAA-22 beats KKK-22."""
    a = evaluate(["Ah", "As", "Ac", "2d", "2c"])
    b = evaluate(["Kh", "Ks", "Kc", "2d", "2c"])
    assert a > b


def test_full_house_higher_pair_breaks_tie():
    """AAA-KK beats AAA-22."""
    a = evaluate(["Ah", "As", "Ac", "Kd", "Kh"])
    b = evaluate(["Ah", "As", "Ac", "2d", "2h"])
    assert a > b


def test_higher_straight_wins():
    """T-high beats 9-high straight."""
    a = evaluate(["Th", "9c", "8d", "7h", "6s"])
    b = evaluate(["9h", "8c", "7d", "6h", "5s"])
    assert a > b


def test_wheel_loses_to_six_high_straight():
    """6-5-4-3-2 beats wheel A-2-3-4-5."""
    six_high = evaluate(["6h", "5c", "4d", "3h", "2s"])
    wheel = evaluate(["Ah", "5c", "4d", "3h", "2s"])
    assert six_high > wheel


def test_higher_flush_wins():
    """A-high flush beats K-high flush."""
    a = evaluate(["Ah", "9h", "8h", "5h", "2h"])
    b = evaluate(["Kh", "9h", "8h", "5h", "2h"])
    # Different cards but ignoring duplication for the test, check via 7-card
    ah_flush = evaluate(["Ah", "Kh", "9h", "8h", "5h", "Qc", "2c"])
    kh_flush = evaluate(["Kh", "Qh", "9h", "8h", "5h", "Tc", "2c"])
    assert ah_flush > kh_flush


def test_quad_kicker_breaks_tie():
    """AAAA with K kicker beats AAAA with 2 kicker — needs 7 cards."""
    a = evaluate(["Ah", "As", "Ac", "Ad", "Kh", "5c", "2d"])
    b = evaluate(["Ah", "As", "Ac", "Ad", "5c", "2d", "3h"])
    assert a > b


# ── category names ──────────────────────────────────────────────────

def test_category_names_present():
    sf = evaluate(["9h", "8h", "7h", "6h", "5h"])
    assert category_name(sf) == "straight-flush"
    boat = evaluate(["Ah", "As", "Ac", "Kd", "Kh"])
    assert category_name(boat) == "boat"


# ── input validation ────────────────────────────────────────────────

def test_invalid_card_raises():
    try:
        evaluate(["X", "As", "Kc", "Qd", "Jh"])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_too_few_cards_raises():
    try:
        evaluate(["As", "Kh", "Qd"])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_too_many_cards_raises():
    try:
        evaluate(["As", "Kh", "Qd", "Jc", "Th", "9s", "8h", "7d"])
        assert False, "expected ValueError"
    except ValueError:
        pass
