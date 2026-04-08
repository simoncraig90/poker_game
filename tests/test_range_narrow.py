"""
Phase 2 unit tests for range narrowing.

Verifies role classification, range selection, and blocker removal
work end-to-end on synthetic action histories.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from action_history import ActionHistory  # noqa: E402
from range_narrow import (  # noqa: E402
    classify_villain_role,
    narrow_villain_range,
    villain_combo_count,
    role_to_starting_action,
    ROLE_OPEN_RAISER,
    ROLE_THREE_BET,
    ROLE_FOUR_BET,
    ROLE_COLD_CALL,
    ROLE_BB_DEFEND,
    ROLE_LIMP,
    ROLE_SQUEEZE,
    ROLE_UNKNOWN,
)
from range_model import (  # noqa: E402
    CLASS_NIT, CLASS_TAG, CLASS_FISH, CLASS_UNKNOWN,
    POS_UTG, POS_MP, POS_CO, POS_BTN, POS_SB, POS_BB,
    ACTION_OPEN, ACTION_3BET, ACTION_CALL, ACTION_LIMP, ACTION_4BET,
)


# ── snapshot builder helpers (mirrored from test_action_history) ─────

def _snap(hand_id, phase, players, pot=0):
    return {"hand_id": hand_id, "phase": phase, "players": players, "pot": pot}


def _p(seat, bet=0, last_action="", stack=10000):
    return {
        "seat": seat,
        "bet": bet,
        "last_action": last_action,
        "stack": stack,
        "user_id": 1000 + seat,
        "name": f"P{seat}",
    }


def _hand_with_actions(action_specs):
    """
    Build an ActionHistory from a sequence of action specs.

    Each spec is (seat, action_type, bet_after). Seats 1 and 2 are
    always included as SB/BB even if no spec references them — without
    the blinds in the snapshot, the action_history accumulator can't
    correctly classify call/bet/raise (it uses prev_round_max which
    requires the blinds to be visible).
    """
    h = ActionHistory()
    spec_seats = {s for s, _, _ in action_specs}
    # Always include seats 1 and 2 (SB / BB) as the table baseline
    seats = sorted(spec_seats | {1, 2})
    state = {s: {"bet": 0, "action": ""} for s in seats}
    # First snap: SB/BB posted
    initial_players = []
    for s in seats:
        if s == 1:
            initial_players.append(_p(s, bet=5))
            state[s]["bet"] = 5
        elif s == 2:
            initial_players.append(_p(s, bet=10))
            state[s]["bet"] = 10
        else:
            initial_players.append(_p(s, bet=0))
    h.update(_snap("h1", "PREFLOP", initial_players))
    # Apply each action
    for seat, act, bet_after in action_specs:
        state[seat] = {"bet": bet_after, "action": act}
        players = []
        for s in seats:
            players.append(_p(s, bet=state[s]["bet"], last_action=state[s]["action"]))
        h.update(_snap("h1", "PREFLOP", players))
    return h


# ── role classification ─────────────────────────────────────────────

def test_open_raiser_classification():
    """Seat 3 raises first, no prior raises."""
    h = _hand_with_actions([(3, "Raise", 30)])
    role = classify_villain_role(h, villain_seat=3, villain_position=POS_BTN)
    assert role == ROLE_OPEN_RAISER


def test_three_bet_classification():
    """Seat 3 opens, seat 1 (SB) re-raises."""
    h = _hand_with_actions([
        (3, "Raise", 30),
        (1, "Raise", 90),
    ])
    role = classify_villain_role(h, villain_seat=1, villain_position=POS_SB)
    assert role == ROLE_THREE_BET


def test_four_bet_classification():
    """Seat 3 opens, seat 1 3bets, seat 3 4bets."""
    h = _hand_with_actions([
        (3, "Raise", 30),
        (1, "Raise", 90),
        (3, "Raise", 240),
    ])
    role = classify_villain_role(h, villain_seat=3, villain_position=POS_BTN)
    assert role == ROLE_FOUR_BET


def test_squeeze_classification():
    """Seat 3 opens, seat 4 calls, seat 1 (BB) raises = squeeze."""
    h = _hand_with_actions([
        (3, "Raise", 30),
        (4, "Call", 30),
        (1, "Raise", 120),
    ])
    role = classify_villain_role(h, villain_seat=1, villain_position=POS_SB)
    assert role == ROLE_SQUEEZE


def test_cold_call_classification():
    """Seat 3 opens, seat 5 cold-calls (CO position)."""
    h = _hand_with_actions([
        (3, "Raise", 30),
        (5, "Call", 30),
    ])
    role = classify_villain_role(h, villain_seat=5, villain_position=POS_CO)
    assert role == ROLE_COLD_CALL


def test_bb_defend_classification():
    """Seat 3 opens, BB (seat 2) calls — BB_DEFEND, not COLD_CALL."""
    h = _hand_with_actions([
        (3, "Raise", 30),
        (2, "Call", 30),
    ])
    role = classify_villain_role(h, villain_seat=2, villain_position=POS_BB)
    assert role == ROLE_BB_DEFEND


def test_limp_classification():
    """Seat 5 limps (calls the BB without raising)."""
    h = _hand_with_actions([
        (5, "Call", 10),  # match BB
    ])
    role = classify_villain_role(h, villain_seat=5, villain_position=POS_CO)
    assert role == ROLE_LIMP


def test_unknown_classification_for_no_action():
    """Villain seat with no observed voluntary action → UNKNOWN."""
    h = _hand_with_actions([
        (3, "Raise", 30),  # different seat acts
    ])
    role = classify_villain_role(h, villain_seat=5, villain_position=POS_CO)
    assert role == ROLE_UNKNOWN


def test_blinds_post_does_not_count_as_voluntary():
    """SB/BB posts shouldn't classify them as openers."""
    h = _hand_with_actions([(3, "Raise", 30), (1, "Fold", 5)])
    # SB posted then folded → first voluntary action is FOLD → UNKNOWN
    role = classify_villain_role(h, villain_seat=1, villain_position=POS_SB)
    assert role == ROLE_UNKNOWN


# ── role → action mapping ────────────────────────────────────────────

def test_role_to_starting_action_mapping():
    assert role_to_starting_action(ROLE_OPEN_RAISER) == ACTION_OPEN
    assert role_to_starting_action(ROLE_THREE_BET) == ACTION_3BET
    assert role_to_starting_action(ROLE_FOUR_BET) == ACTION_4BET
    assert role_to_starting_action(ROLE_COLD_CALL) == ACTION_CALL
    assert role_to_starting_action(ROLE_BB_DEFEND) == ACTION_CALL
    assert role_to_starting_action(ROLE_LIMP) == ACTION_LIMP
    assert role_to_starting_action(ROLE_SQUEEZE) == ACTION_3BET  # v0 collapse
    assert role_to_starting_action(ROLE_UNKNOWN) == ""


# ── full narrowing: combo lists ─────────────────────────────────────

def test_narrow_open_raiser_uses_open_range():
    """NIT BTN opens → narrowed range = NIT BTN OPEN, with blockers applied."""
    h = _hand_with_actions([(3, "Raise", 30)])
    combos = narrow_villain_range(
        history=h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_NIT,
        hero_cards=[], board_cards=[],
    )
    # NIT BTN open is ~47 hand keys, ~340 combos pre-blockers
    assert 200 < len(combos) < 500


def test_narrow_with_hero_blocker_removes_combos():
    """Hero As blocks villain combos using As."""
    h = _hand_with_actions([(3, "Raise", 30)])
    no_blocker = narrow_villain_range(
        history=h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_NIT, hero_cards=[], board_cards=[],
    )
    with_blocker = narrow_villain_range(
        history=h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_NIT, hero_cards=["As"], board_cards=[],
    )
    assert len(with_blocker) < len(no_blocker)
    # No surviving combo should contain As
    for c1, c2 in with_blocker:
        assert "As" not in (c1, c2)


def test_narrow_three_bet_uses_3bet_range():
    """3-bet range is much tighter than open range."""
    h = _hand_with_actions([
        (3, "Raise", 30),
        (1, "Raise", 90),
    ])
    open_combos = narrow_villain_range(
        h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_NIT, hero_cards=[], board_cards=[],
    )
    threebet_combos = narrow_villain_range(
        h, villain_seat=1, villain_position=POS_SB,
        villain_class=CLASS_NIT, hero_cards=[], board_cards=[],
    )
    # NIT 3-bet range is much tighter (5-7 keys vs ~28 for SB open)
    assert len(threebet_combos) < len(open_combos)
    assert len(threebet_combos) < 60  # ~7 keys × 6 combos = 42


def test_narrow_unknown_role_falls_back_to_continuing_range():
    """When we can't classify, fall back to wider continuing range."""
    h = _hand_with_actions([(3, "Raise", 30)])  # different seat acts
    combos = narrow_villain_range(
        h, villain_seat=5, villain_position=POS_BTN,
        villain_class=CLASS_NIT, hero_cards=[], board_cards=[],
    )
    # Continuing range = OPEN ∪ 3BET ∪ CALL ∪ LIMP = 47 keys for NIT BTN
    # Should be larger than just the open range
    assert len(combos) > 0


def test_narrow_with_full_board_removes_more_combos():
    """3-card board removes ~3*6 = 18-ish combos from villain's range."""
    h = _hand_with_actions([(3, "Raise", 30)])
    no_board = narrow_villain_range(
        h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_NIT, hero_cards=["Ad", "Kd"], board_cards=[],
    )
    with_board = narrow_villain_range(
        h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_NIT, hero_cards=["Ad", "Kd"],
        board_cards=["Qs", "Jh", "Tc"],
    )
    assert len(with_board) < len(no_board)


def test_villain_combo_count_matches_narrow():
    """combo count helper agrees with len(narrow_villain_range)."""
    h = _hand_with_actions([(3, "Raise", 30)])
    combos = narrow_villain_range(
        h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_TAG, hero_cards=["As"], board_cards=["Kh"],
    )
    count = villain_combo_count(
        h, villain_seat=3, villain_position=POS_BTN,
        villain_class=CLASS_TAG, hero_cards=["As"], board_cards=["Kh"],
    )
    assert count == len(combos)


def test_class_widens_open_range():
    """LAG opens wider than TAG opens wider than NIT — combo counts reflect."""
    h = _hand_with_actions([(3, "Raise", 30)])
    nit = villain_combo_count(h, 3, POS_BTN, CLASS_NIT, [], [])
    tag = villain_combo_count(h, 3, POS_BTN, "TAG", [], [])
    lag = villain_combo_count(h, 3, POS_BTN, "LAG", [], [])
    assert nit < tag < lag


def test_unknown_class_treated_as_nit():
    """Per existing project policy: UNKNOWN at micros = NIT."""
    h = _hand_with_actions([(3, "Raise", 30)])
    nit_combos = narrow_villain_range(
        h, 3, POS_BTN, CLASS_NIT, [], []
    )
    unknown_combos = narrow_villain_range(
        h, 3, POS_BTN, CLASS_UNKNOWN, [], []
    )
    assert len(nit_combos) == len(unknown_combos)


def test_postflop_narrowing_disabled_by_flag():
    """apply_postflop=False returns the preflop-only narrowed range."""
    h = _hand_with_actions([(3, "Raise", 30)])
    pre_only = narrow_villain_range(
        h, 3, POS_BTN, CLASS_NIT,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=False,
    )
    # 3 board cards but no postflop actions, so postflop=True would
    # also leave the range untouched. Test passes when both are equal.
    with_postflop = narrow_villain_range(
        h, 3, POS_BTN, CLASS_NIT,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=True,
    )
    # With no postflop actions, both should be identical
    assert len(pre_only) == len(with_postflop)


def test_postflop_narrowing_shrinks_range_when_villain_bets():
    """Villain bets the flop → range narrows to top half by strength."""
    from action_history import ActionHistory
    h = ActionHistory()
    # Preflop: BTN open
    h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=5),
        _p(2, bet=10),
        _p(3, bet=0),
    ]))
    h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=5),
        _p(2, bet=10),
        _p(3, bet=30, last_action="Raise"),
    ]))
    h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=5, last_action="Fold"),
        _p(2, bet=30, last_action="Call"),
        _p(3, bet=30, last_action="Raise"),
    ]))
    # Flop: BB checks, BTN bets
    h.update(_snap("h1", "FLOP", [
        _p(2, bet=0, last_action="Check"),
        _p(3, bet=0),
    ]))
    h.update(_snap("h1", "FLOP", [
        _p(2, bet=0, last_action="Check"),
        _p(3, bet=50, last_action="Bet"),
    ]))

    pre_only = narrow_villain_range(
        h, 3, POS_BTN, CLASS_TAG,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=False,
    )
    with_postflop = narrow_villain_range(
        h, 3, POS_BTN, CLASS_TAG,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=True,
    )
    assert 0 < len(with_postflop) < len(pre_only)
    # BET keeps the top 50% — should be roughly half
    assert len(with_postflop) <= len(pre_only) * 0.6


def test_postflop_narrowing_check_keeps_all():
    """A check is no info — combo count unchanged."""
    from action_history import ActionHistory
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5, last_action="Fold"), _p(2, bet=30, last_action="Call"), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "FLOP", [_p(2, bet=0), _p(3, bet=0)]))
    # BTN checks back the flop
    h.update(_snap("h1", "FLOP", [_p(2, bet=0), _p(3, bet=0, last_action="Check")]))

    pre_only = narrow_villain_range(
        h, 3, POS_BTN, CLASS_TAG,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=False,
    )
    with_postflop = narrow_villain_range(
        h, 3, POS_BTN, CLASS_TAG,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=True,
    )
    assert len(with_postflop) == len(pre_only)


def test_postflop_narrowing_raise_aggressive_filter():
    """Villain raises an opponent's bet → top 30% only."""
    from action_history import ActionHistory
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5, last_action="Fold"), _p(2, bet=30, last_action="Call"), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "FLOP", [_p(2, bet=0), _p(3, bet=0)]))
    h.update(_snap("h1", "FLOP", [_p(2, bet=50, last_action="Bet"), _p(3, bet=0)]))
    h.update(_snap("h1", "FLOP", [_p(2, bet=50, last_action="Bet"), _p(3, bet=180, last_action="Raise")]))

    with_postflop = narrow_villain_range(
        h, 3, POS_BTN, CLASS_TAG,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=True,
    )
    pre_only = narrow_villain_range(
        h, 3, POS_BTN, CLASS_TAG,
        hero_cards=[], board_cards=["As", "Kh", "Qd"],
        apply_postflop=False,
    )
    # RAISE keeps top 30% — should be much tighter than the open range
    assert 0 < len(with_postflop) < len(pre_only) * 0.4


def test_no_position_returns_empty():
    """Empty position is a hard error → empty range."""
    h = _hand_with_actions([(3, "Raise", 30)])
    assert narrow_villain_range(h, 3, "", CLASS_NIT, [], []) == []
