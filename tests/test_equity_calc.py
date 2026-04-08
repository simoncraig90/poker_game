"""
Phase 2 unit tests for the equity calculator.

Verifies the Monte Carlo equity matches well-known closed-form values
within Monte Carlo tolerance. These are the "ground truth" tests that
validate the entire Phase 2 stack (range model + combo expansion +
narrowing + hand evaluator + equity calculator) end-to-end.

Tolerance is intentionally loose (±5%) because Monte Carlo with 200
samples per combo has real variance. Production runs would tighten
samples + use seeded RNG for reproducibility.
"""

import os
import sys
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from equity_calc import (  # noqa: E402
    hero_equity_vs_combo,
    hero_equity_vs_range,
    hero_equity_vs_multiway,
)
from hand_combos import expand_hand_key  # noqa: E402


def _approx(a, b, tol=0.05):
    return abs(a - b) <= tol


# ── degenerate cases ────────────────────────────────────────────────

def test_empty_villain_range_returns_half():
    eq = hero_equity_vs_range(["Ah", "Ks"], [], [])
    assert eq == 0.5


def test_blocker_conflict_returns_half():
    """If villain combo overlaps hero, equity is 0.5 (filtered)."""
    eq = hero_equity_vs_combo(
        hero_hand=["Ah", "Ks"],
        villain_combo=("Ah", "Kc"),  # Ah conflict
        board=[],
    )
    assert eq == 0.5


# ── well-known preflop matchups (vs all villain combos) ─────────────
#
# Closed-form preflop equities (well-published):
#   AA vs random        ≈ 85%
#   AA vs KK            ≈ 81%
#   AKs vs JJ           ≈ 46% (classic race)
#   AKo vs 22           ≈ 52% (slight favorite)
#   AhKh vs QhQc        — heart removal makes this trickier; skip
#
# Tolerance ±5% covers Monte Carlo noise at 200 samples.

def test_AA_vs_KK_preflop():
    """AA is ~81% vs KK preflop."""
    rng = random.Random(42)
    villain_combos = expand_hand_key("KK")
    eq = hero_equity_vs_range(
        hero_hand=["Ah", "Ad"],
        villain_combos=villain_combos,
        board=[],
        samples_per_combo=400,
        rng=rng,
    )
    assert _approx(eq, 0.81), f"AA vs KK: got {eq:.2%}, expected ~81%"


def test_AKs_vs_JJ_preflop():
    """AKs vs JJ is ~46% (the classic race)."""
    rng = random.Random(42)
    villain_combos = expand_hand_key("JJ")
    eq = hero_equity_vs_range(
        hero_hand=["Ah", "Kh"],
        villain_combos=villain_combos,
        board=[],
        samples_per_combo=400,
        rng=rng,
    )
    assert _approx(eq, 0.46, tol=0.06), f"AKs vs JJ: got {eq:.2%}, expected ~46%"


def test_AKo_vs_22_preflop():
    """AKo vs 22 is ~52% (very slight favorite)."""
    rng = random.Random(42)
    villain_combos = expand_hand_key("22")
    eq = hero_equity_vs_range(
        hero_hand=["Ah", "Kd"],
        villain_combos=villain_combos,
        board=[],
        samples_per_combo=400,
        rng=rng,
    )
    assert _approx(eq, 0.52, tol=0.06), f"AKo vs 22: got {eq:.2%}, expected ~52%"


def test_AA_dominates_KQ_offsuit():
    """AA is ~87% vs KQo preflop."""
    rng = random.Random(42)
    villain_combos = expand_hand_key("KQo")
    eq = hero_equity_vs_range(
        hero_hand=["Ah", "Ad"],
        villain_combos=villain_combos,
        board=[],
        samples_per_combo=200,
        rng=rng,
    )
    assert eq > 0.80, f"AA vs KQo too low: {eq:.2%}"


# ── postflop showdown (5 cards) — deterministic, no MC ──────────────

def test_made_hand_river_showdown_winning():
    """
    Hero has top set on a dry board. Vs a single combo of TT (set of
    tens), hero (KK on KQ4) wins via better set.
    Wait — hero KK on a K-Q-4 board has trips of K. Villain TT has
    just an underpair. Hero crushes.
    """
    eq = hero_equity_vs_combo(
        hero_hand=["Kh", "Kd"],
        villain_combo=("Th", "Td"),
        board=["Ks", "Qs", "4c", "8h", "2d"],
    )
    # Deterministic showdown — set of K vs underpair = hero wins
    assert eq == 1.0


def test_made_hand_river_showdown_losing():
    """
    Hero has KK + board K → trips. Villain has 44 + board 4 → set.
    On a K-4-7 board, set of 4s loses to set of K. Wait, hero wins
    again. Let me make hero LOSE.

    Hero TT on board K-Q-4-T-2. Hero has set of T.
    Villain KQ on the same board. Villain has two pair K-high.
    Hero wins. Try again.

    Hero 44 (set of 4) on board K-4-Q-7-2. Hero has bottom set.
    Villain KK on same board. Villain has trip K's. Hero loses to
    overpair-set. Wait, KK + board K = top set. Yes, top set beats
    bottom set.
    """
    eq = hero_equity_vs_combo(
        hero_hand=["4h", "4d"],
        villain_combo=("Kh", "Kd"),
        board=["Ks", "4c", "Qs", "7h", "2d"],
    )
    # Set of 4 vs set of K → hero loses
    assert eq == 0.0


def test_river_showdown_chop():
    """Both players play the board → chop."""
    eq = hero_equity_vs_combo(
        hero_hand=["2h", "3d"],
        villain_combo=("4h", "5d"),
        board=["Ah", "Kh", "Qh", "Jh", "Th"],  # Royal flush on board
    )
    # Both play the board's royal flush
    assert eq == 0.5


# ── postflop equity vs range ────────────────────────────────────────

def test_overpair_vs_value_range_on_safe_board():
    """
    KK on K-7-2 rainbow vs an opponent's tight value range
    (AA-99 + AK). Hero is way ahead — top set + dominated overpairs.
    Expected equity ~80%+.
    """
    rng = random.Random(42)
    villain_combos = []
    for key in ("AA", "QQ", "JJ", "TT", "99", "AKs", "AKo"):
        villain_combos += expand_hand_key(key)

    eq = hero_equity_vs_range(
        hero_hand=["Kh", "Kd"],
        villain_combos=villain_combos,
        board=["Ks", "7c", "2h"],
        samples_per_combo=100,
        rng=rng,
    )
    assert eq > 0.80, f"KK on K72 vs value range too low: {eq:.2%}"


def test_one_pair_vs_overpair_range_is_dog():
    """
    Hero has 6s on Q-A-8-K-T (the broadway runout from the actual
    66 hand). Villain range = realistic value range (any K, A, Q, two
    pair, sets, broadway).
    Hero has middle pair, no draw, board has straight. Should be ~5-15%
    against this range.
    """
    rng = random.Random(42)
    # A realistic river-betting value range
    villain_combos = []
    for key in ("AA", "KK", "QQ", "AKs", "AKo", "AQs", "AQo",
                "KQs", "KQo", "QJs", "JTs", "AJs", "AJo"):
        villain_combos += expand_hand_key(key)

    eq = hero_equity_vs_range(
        hero_hand=["6s", "6c"],
        villain_combos=villain_combos,
        board=["Qd", "As", "8h", "Kh", "Ts"],
        samples_per_combo=100,
        rng=rng,
    )
    # On the river the equity is binary (we win or lose) per combo.
    # Pocket sixes vs this range — most combos beat us; some make
    # straights for villain too.
    assert eq < 0.20, f"66 vs broadway-river value range too high: {eq:.2%}"
    print(f"\n66 on Q-A-8-K-T vs broadway value range: {eq:.1%} equity")


def test_multiway_empty_returns_half():
    """No villains → half (no information)."""
    assert hero_equity_vs_multiway(["Ah", "Ks"], [], []) == 0.5


def test_multiway_single_villain_delegates_to_heads_up():
    """One villain in the list = heads-up; results should match."""
    rng1 = random.Random(42)
    rng2 = random.Random(42)
    villain_combos = expand_hand_key("KK")
    headsup = hero_equity_vs_range(
        ["Ah", "Ad"], villain_combos, [], samples_per_combo=200, rng=rng1
    )
    multiway = hero_equity_vs_multiway(
        ["Ah", "Ad"], [villain_combos], [], samples=200, rng=rng2
    )
    # Should be in the same ballpark
    assert _approx(headsup, multiway, tol=0.10), \
        f"single-villain mismatch: hu={headsup:.2%} mw={multiway:.2%}"


def test_multiway_AA_vs_two_villains_lower_than_heads_up():
    """
    AA vs ONE random opponent ≈ 85%. AA vs TWO random opponents ≈ 73%.
    Multiway equity always drops as opponents are added.
    """
    rng = random.Random(42)
    # Two villains, both with the same wide range
    wide_range = []
    for key in ("AA", "KK", "QQ", "JJ", "TT", "99", "88",
                "AKs", "AKo", "AQs", "AJs", "KQs", "KJs", "QJs"):
        wide_range += expand_hand_key(key)

    eq = hero_equity_vs_multiway(
        hero_hand=["Ah", "Ad"],
        villain_ranges=[wide_range, wide_range],
        board=[],
        samples=300,
        rng=rng,
    )
    # AA vs 2 wide ranges should still be > 50% but well below 85%
    assert 0.55 < eq < 0.85, f"AA multiway: {eq:.2%}"


def test_multiway_drains_with_more_villains():
    """Same hand vs 1, 2, 3 villains — equity should monotonically drop."""
    rng_seeds = [10, 20, 30]
    villain_range = expand_hand_key("KK") + expand_hand_key("QQ")
    eqs = []
    for n in (1, 2, 3):
        rng = random.Random(rng_seeds[n - 1])
        eq = hero_equity_vs_multiway(
            hero_hand=["Ah", "Ad"],
            villain_ranges=[villain_range] * n,
            board=[],
            samples=200,
            rng=rng,
        )
        eqs.append(eq)
    # AA vs KK/QQ heads-up ≈ 81%; vs 2 vs 3 should keep dropping
    assert eqs[0] > eqs[1] > eqs[2], f"non-monotonic: {eqs}"


def test_multiway_river_chop():
    """Three players, board has the nuts → all chop."""
    eq = hero_equity_vs_multiway(
        hero_hand=["2h", "3d"],
        villain_ranges=[
            [("4h", "5d")],
            [("6c", "7s")],
        ],
        board=["Ah", "Kh", "Qh", "Jh", "Th"],  # royal on board
        samples=10,
    )
    # All three play the royal flush — chop. Hero gets 1/3.
    assert _approx(eq, 1.0 / 3.0, tol=0.05), f"3-way chop: {eq:.2%}"


def test_top_pair_top_kicker_vs_check_raise_range():
    """
    AhJs (TPTK) on 4c-Jh-5d facing a check-raise. Realistic
    check-raise range = sets, two pair, pair+draw, occasional combo
    draw. Hero equity should be ~30-40% — playable but the live
    advisor's '80% vs random' was wildly optimistic.
    """
    rng = random.Random(42)
    villain_combos = []
    # Sets
    for key in ("44", "55", "JJ"):
        villain_combos += expand_hand_key(key)
    # Two pair
    for key in ("J5s", "J4s", "54s"):
        villain_combos += expand_hand_key(key)
    # Combo draws / overpair semi-bluffs
    for key in ("76s", "32s", "AA", "KK", "QQ"):
        villain_combos += expand_hand_key(key)

    eq = hero_equity_vs_range(
        hero_hand=["Ah", "Js"],
        villain_combos=villain_combos,
        board=["4c", "Jh", "5d"],
        samples_per_combo=80,
        rng=rng,
    )
    assert 0.20 < eq < 0.60, f"TPTK vs check-raise range: {eq:.2%}"
    print(f"\nAhJs on 4c Jh 5d vs check-raise range: {eq:.1%} equity")
