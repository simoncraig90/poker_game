"""
Phase 2 unit tests for hand combo expansion.

Verifies the 169-shape ↔ 1326-combo mapping is correct in both
directions and that blocker removal handles dead cards properly.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from hand_combos import (  # noqa: E402
    expand_hand_key,
    combo_count,
    combo_to_key,
    expand_range,
    range_combo_count,
    remove_blockers,
    range_combos_after_blockers,
    ALL_CARDS,
)


# ── single-key expansion ─────────────────────────────────────────────

def test_pocket_pair_expands_to_6_combos():
    combos = expand_hand_key("AA")
    assert len(combos) == 6
    # All combos should be (A?, A?) and all distinct
    for a, b in combos:
        assert a[0] == "A" and b[0] == "A"
        assert a != b
    # Each unique pair appears once
    seen = {tuple(sorted([a, b])) for a, b in combos}
    assert len(seen) == 6


def test_suited_expands_to_4_combos():
    combos = expand_hand_key("AKs")
    assert len(combos) == 4
    suits_seen = set()
    for a, b in combos:
        assert a[0] == "A" and b[0] == "K"
        assert a[1] == b[1]  # same suit
        suits_seen.add(a[1])
    assert suits_seen == {"s", "h", "d", "c"}


def test_offsuit_expands_to_12_combos():
    combos = expand_hand_key("AKo")
    assert len(combos) == 12
    for a, b in combos:
        assert a[0] == "A" and b[0] == "K"
        assert a[1] != b[1]  # different suits


def test_unordered_input_canonicalizes_to_high_first():
    """KAs becomes the same expansion as AKs."""
    a = expand_hand_key("KAs")
    b = expand_hand_key("AKs")
    assert set((x, y) for x, y in a) == set((x, y) for x, y in b)


def test_invalid_keys_return_empty():
    assert expand_hand_key("") == []
    assert expand_hand_key("X") == []
    assert expand_hand_key("XY") == []
    assert expand_hand_key("AKx") == []  # bad suffix
    assert expand_hand_key("AAo") == []  # pair can't be offsuit
    assert expand_hand_key("AAs") == []  # pair can't be suited


# ── combo count ──────────────────────────────────────────────────────

def test_combo_count_matches_expansion_length():
    for key in ("AA", "KK", "22", "AKs", "AKo", "T9s", "72o"):
        assert combo_count(key) == len(expand_hand_key(key))


def test_combo_count_invalid_returns_zero():
    assert combo_count("") == 0
    assert combo_count("AAs") == 0
    assert combo_count("XKs") == 0


def test_combo_count_total_169_shapes_equals_1326():
    """Sanity: 13 pairs × 6 + 78 suited × 4 + 78 offsuit × 12 = 1326."""
    total = 0
    ranks = "23456789TJQKA"
    for r1 in ranks:
        for r2 in ranks:
            if r1 == r2:
                total += combo_count(r1 + r2)
            else:
                # Skip duplicates: only count high-low order
                pass
    # Just pairs: 13 × 6 = 78
    assert total == 78
    # Now suited
    suited_total = 0
    for i, r1 in enumerate(ranks):
        for r2 in ranks[:i]:
            suited_total += combo_count(r1 + r2 + "s")
    # 78 suited shapes × 4 = 312
    assert suited_total == 312
    # And offsuit
    offsuit_total = 0
    for i, r1 in enumerate(ranks):
        for r2 in ranks[:i]:
            offsuit_total += combo_count(r1 + r2 + "o")
    # 78 offsuit shapes × 12 = 936
    assert offsuit_total == 936
    # Grand total
    assert total + suited_total + offsuit_total == 1326


# ── reverse direction ───────────────────────────────────────────────

def test_combo_to_key_pocket_pair():
    assert combo_to_key("Ah", "Ad") == "AA"
    assert combo_to_key("As", "Ah") == "AA"
    assert combo_to_key("2h", "2d") == "22"


def test_combo_to_key_suited():
    assert combo_to_key("Ah", "Kh") == "AKs"
    assert combo_to_key("Kc", "Tc") == "KTs"
    assert combo_to_key("5d", "4d") == "54s"


def test_combo_to_key_offsuit():
    assert combo_to_key("Ah", "Kd") == "AKo"
    assert combo_to_key("Kh", "Tc") == "KTo"


def test_combo_to_key_canonicalizes_order():
    """Lower rank first should still produce the high-rank-first key."""
    assert combo_to_key("Ks", "As") == "AKs"
    assert combo_to_key("2h", "Ah") == "A2s"


def test_combo_to_key_round_trip():
    """expand_hand_key followed by combo_to_key recovers the key."""
    for key in ("AA", "KK", "AKs", "AKo", "T9s", "T9o", "72o", "22"):
        for c1, c2 in expand_hand_key(key):
            assert combo_to_key(c1, c2) == key, f"{key}: {c1},{c2}"


def test_combo_to_key_invalid():
    assert combo_to_key("Ah", "Ah") == ""  # same card
    assert combo_to_key("X", "Y") == ""
    assert combo_to_key("", "Ah") == ""


# ── range expansion ─────────────────────────────────────────────────

def test_expand_range_dedups_and_counts():
    r = {"AA", "KK"}
    combos = expand_range(r)
    assert len(combos) == 12  # 6 + 6


def test_expand_range_handles_overlap():
    """If the same key appears twice, no duplicate combos."""
    r = ["AKs", "AKs"]
    assert len(expand_range(r)) == 4


def test_range_combo_count_sums_keys():
    assert range_combo_count({"AA", "AKs"}) == 6 + 4
    assert range_combo_count({"AA", "KK", "QQ", "JJ", "TT"}) == 30


# ── blocker removal ──────────────────────────────────────────────────

def test_remove_blockers_pocket_pair():
    """AA with As dead leaves 3 combos (the pairs not involving As)."""
    survivors = remove_blockers({"AA"}, {"As"})
    assert len(survivors) == 3
    for a, b in survivors:
        assert "As" not in (a, b)


def test_remove_blockers_suited_with_one_blocker():
    """AKs with Ks dead leaves 3 (AhKh, AdKd, AcKc)."""
    survivors = remove_blockers({"AKs"}, {"Ks"})
    assert len(survivors) == 3


def test_remove_blockers_suited_with_two_blockers_same_suit():
    """AKs with both As and Ks dead leaves 3 (AhKh, AdKd, AcKc)."""
    survivors = remove_blockers({"AKs"}, {"As", "Ks"})
    # As blocks the AsKs combo. Other 3 still valid.
    assert len(survivors) == 3
    for a, b in survivors:
        assert "As" not in (a, b)
        assert "Ks" not in (a, b)


def test_remove_blockers_offsuit():
    """AKo (12 combos) with As dead → 9 (the ones not using As)."""
    survivors = remove_blockers({"AKo"}, {"As"})
    assert len(survivors) == 9


def test_remove_blockers_with_board():
    """A board of As-Ks-Qd blocks specific combos in a range."""
    range_keys = {"AA", "KK", "QQ", "AKs", "AKo"}
    board = ["As", "Ks", "Qd"]
    survivors = remove_blockers(range_keys, board)
    # AA: 3 left (without As)
    # KK: 3 left (without Ks)
    # QQ: 3 left (without Qd)
    # AKs: 2 left (Ah Kh, Ad Kd, Ac Kc minus the blocked ones — As Ks gone, but other suits OK)
    # Actually AKs has 4 combos (As Ks, Ah Kh, Ad Kd, Ac Kc). As blocks AsKs. Ks doesn't block any others. So 3 left.
    # AKo has 12. As blocks 3 (As paired with K of any non-s suit). Ks blocks 3 (Ks paired with A of any non-s suit). One overlap (the As Ks combo isn't in offsuit). So 12 - 6 = 6.
    # Total: 3 + 3 + 3 + 3 + 6 = 18
    assert len(survivors) == 18


def test_remove_blockers_zero_dead_cards():
    """Empty dead set returns the full expansion."""
    range_keys = {"AA", "KK"}
    assert range_combos_after_blockers(range_keys, []) == 12


def test_all_cards_constant_complete():
    assert len(ALL_CARDS) == 52
    assert len(set(ALL_CARDS)) == 52
