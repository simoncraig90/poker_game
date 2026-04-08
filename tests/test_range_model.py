"""
Phase 2 unit tests for the starting range model.

These verify range tables are well-formed (no typos, no overlaps where
overlaps don't make sense, sane widths) and that the lookup API
behaves under all the edge cases the harness will hit.

The actual *quality* of the ranges (are they correct for NL10
population?) gets validated by Phase 2's harness BB/100 metric, not
by these tests.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from range_model import (  # noqa: E402
    get_starting_range,
    get_continuing_range,
    range_size,
    normalize_class,
    CLASS_NIT, CLASS_TAG, CLASS_LAG, CLASS_FISH, CLASS_UNKNOWN,
    POS_UTG, POS_MP, POS_CO, POS_BTN, POS_SB, POS_BB,
    ACTION_OPEN, ACTION_3BET, ACTION_4BET, ACTION_CALL, ACTION_LIMP,
    ALL_CLASSES, ALL_POSITIONS,
)


# ── width sanity checks ─────────────────────────────────────────────

def test_nit_open_widens_with_position():
    """NIT opens tighter from EP and wider from late position."""
    utg = range_size(CLASS_NIT, POS_UTG, ACTION_OPEN)
    mp = range_size(CLASS_NIT, POS_MP, ACTION_OPEN)
    co = range_size(CLASS_NIT, POS_CO, ACTION_OPEN)
    btn = range_size(CLASS_NIT, POS_BTN, ACTION_OPEN)
    assert utg < mp < co < btn, f"NIT widths: {utg}/{mp}/{co}/{btn}"


def test_lag_opens_wider_than_tag_wider_than_nit():
    """At every position the LAG > TAG > NIT relationship holds."""
    for pos in (POS_UTG, POS_MP, POS_CO, POS_BTN):
        n = range_size(CLASS_NIT, pos, ACTION_OPEN)
        t = range_size(CLASS_TAG, pos, ACTION_OPEN)
        l = range_size(CLASS_LAG, pos, ACTION_OPEN)
        assert n <= t <= l, f"{pos}: NIT={n} TAG={t} LAG={l}"


def test_fish_open_is_value_heavy_and_narrow():
    """FISH almost never raises — open range is tiny and value-heavy."""
    for pos in (POS_UTG, POS_MP, POS_CO, POS_BTN):
        size = range_size(CLASS_FISH, pos, ACTION_OPEN)
        assert size <= 14, f"FISH {pos} open range too wide: {size}"
    # FISH always has AA in any open range
    for pos in (POS_UTG, POS_MP, POS_CO, POS_BTN):
        assert "AA" in get_starting_range(CLASS_FISH, pos, ACTION_OPEN)
    # FISH never has 22 in an open range from EP
    assert "22" not in get_starting_range(CLASS_FISH, POS_UTG, ACTION_OPEN)


def test_fish_call_range_is_huge():
    """FISH cold-calls 50%+ of hands."""
    for pos in (POS_UTG, POS_MP, POS_CO, POS_BTN):
        size = range_size(CLASS_FISH, pos, ACTION_CALL)
        assert size >= 60, f"FISH {pos} call range too narrow: {size}"


# ── premium hand presence ──────────────────────────────────────────

def test_premium_pairs_in_every_class_open_range_from_btn():
    """AA, KK, QQ should appear in every villain's BTN open range."""
    for cls in ALL_CLASSES:
        r = get_starting_range(cls, POS_BTN, ACTION_OPEN)
        for hand in ("AA", "KK", "QQ"):
            # FISH from EP doesn't open everything; BTN should
            assert hand in r, f"{cls} BTN open missing {hand}"


def test_premium_pairs_in_every_3bet_range():
    """AA, KK should be in every class's 3-bet range from every position."""
    for cls in ALL_CLASSES:
        for pos in (POS_UTG, POS_MP, POS_CO, POS_BTN, POS_SB, POS_BB):
            r = get_starting_range(cls, pos, ACTION_3BET)
            for hand in ("AA", "KK"):
                assert hand in r, f"{cls} {pos} 3bet missing {hand}"


def test_no_4bet_includes_garbage():
    """4-bet ranges are top-of-range only — no 22, no junk hands."""
    for cls in ALL_CLASSES:
        for pos in ALL_POSITIONS:
            r = get_starting_range(cls, pos, ACTION_4BET)
            # No small/medium pair 4-bets
            for trash in ("22", "33", "44", "55", "T2o", "72o"):
                assert trash not in r, f"{cls} {pos} 4bet has {trash}"


# ── BB does not "open" ───────────────────────────────────────────────

def test_bb_open_is_empty_for_all_classes():
    """No one acts after BB preflop, so BB never has an OPEN range."""
    for cls in ALL_CLASSES:
        assert get_starting_range(cls, POS_BB, ACTION_OPEN) == set()


# ── lookup edge cases ──────────────────────────────────────────────

def test_unknown_class_returns_empty():
    assert get_starting_range("WHATEVER", POS_BTN, ACTION_OPEN) == set()


def test_unknown_position_returns_empty():
    assert get_starting_range(CLASS_NIT, "OOPS", ACTION_OPEN) == set()


def test_unknown_action_returns_empty():
    assert get_starting_range(CLASS_NIT, POS_BTN, "WAVE") == set()


def test_unknown_class_defaults_to_nit_via_constant():
    """UNKNOWN is wired to the same tables as NIT."""
    for pos in ALL_POSITIONS:
        for act in (ACTION_OPEN, ACTION_3BET, ACTION_CALL):
            nit = get_starting_range(CLASS_NIT, pos, act)
            unknown = get_starting_range(CLASS_UNKNOWN, pos, act)
            assert nit == unknown, f"UNKNOWN diverges from NIT at {pos}/{act}"


# ── continuing range ───────────────────────────────────────────────

def test_continuing_range_unions_all_actions():
    """get_continuing_range = OPEN ∪ 3BET ∪ CALL ∪ LIMP (not 4BET)."""
    for cls in ALL_CLASSES:
        for pos in ALL_POSITIONS:
            cont = get_continuing_range(cls, pos)
            expected = (
                get_starting_range(cls, pos, ACTION_OPEN) |
                get_starting_range(cls, pos, ACTION_3BET) |
                get_starting_range(cls, pos, ACTION_CALL) |
                get_starting_range(cls, pos, ACTION_LIMP)
            )
            assert cont == expected


def test_continuing_range_for_fish_btn_is_huge():
    """FISH BTN sees a flop with 80+ different hand shapes."""
    cont = get_continuing_range(CLASS_FISH, POS_BTN)
    assert len(cont) >= 80


# ── normalization ──────────────────────────────────────────────────

def test_normalize_class_canonical_passthrough():
    for c in ALL_CLASSES:
        assert normalize_class(c) == c
        assert normalize_class(c.lower()) == c


def test_normalize_class_aliases():
    assert normalize_class("WHALE") == CLASS_FISH
    assert normalize_class("station") == CLASS_FISH
    assert normalize_class("MANIAC") == CLASS_LAG
    assert normalize_class("rock") == CLASS_NIT
    assert normalize_class("REG") == CLASS_TAG


def test_normalize_class_empty_or_none_is_unknown():
    assert normalize_class(None) == CLASS_UNKNOWN
    assert normalize_class("") == CLASS_UNKNOWN
    assert normalize_class("???") == CLASS_UNKNOWN


# ── 3-bet ranges should be subset of "would call or aggress" ────────

def test_3bet_range_does_not_include_garbage_offsuit():
    """No T2o, 72o etc in any 3-bet range (sanity check vs typos)."""
    garbage = {"72o", "82o", "T2o", "J2o", "32o", "42o"}
    for cls in ALL_CLASSES:
        for pos in ALL_POSITIONS:
            r = get_starting_range(cls, pos, ACTION_3BET)
            assert not (r & garbage), f"{cls} {pos} 3bet has garbage: {r & garbage}"
