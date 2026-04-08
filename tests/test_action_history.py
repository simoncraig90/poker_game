"""
Unit tests for vision/action_history.py.

These are NOT replay-against-real-outcome tests — those come in the
Phase 1 harness. These are pure module-level tests: given a synthetic
snapshot stream, do we extract the correct sequence of Action records?

Run:  python -m pytest tests/test_action_history.py -v
"""

import os
import sys

# Make vision/ importable without an installed package
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from action_history import (  # noqa: E402
    ActionHistory,
    ACTION_POST,
    ACTION_FOLD,
    ACTION_CHECK,
    ACTION_CALL,
    ACTION_BET,
    ACTION_RAISE,
    ACTION_ALLIN,
)


def _snap(hand_id, phase, players, pot=0):
    """Build a minimal snapshot dict matching the runner's format."""
    return {
        "hand_id": hand_id,
        "phase": phase,
        "players": players,
        "pot": pot,
    }


def _p(seat, bet=0, last_action="", stack=10000, user_id=None, name=None):
    return {
        "seat": seat,
        "bet": bet,
        "last_action": last_action,
        "stack": stack,
        "user_id": user_id or (1000 + seat),
        "name": name or f"P{seat}",
    }


# ── basic action detection ────────────────────────────────────────────

def test_blinds_are_tagged_post_not_bet():
    """First-preflop blinds must be POST, not BET/RAISE."""
    h = ActionHistory()
    snap = _snap("h1", "PREFLOP", [
        _p(1, bet=5),    # SB
        _p(2, bet=10),   # BB
        _p(3, bet=0),
        _p(4, bet=0),
    ], pot=15)
    new = h.update(snap)
    assert len(new) == 2
    assert new[0].action == ACTION_POST
    assert new[1].action == ACTION_POST
    assert new[0].seat == 1 and new[0].amount == 5
    assert new[1].seat == 2 and new[1].amount == 10


def test_open_raise_after_blinds_is_raise():
    """Player who first puts chips in beyond the BB → RAISE."""
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=5),
        _p(2, bet=10),
        _p(3, bet=0),
    ], pot=15))
    # Seat 3 opens to 30
    new = h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=5),
        _p(2, bet=10),
        _p(3, bet=30, last_action="Raise"),
    ], pot=45))
    assert len(new) == 1
    assert new[0].seat == 3
    assert new[0].action == ACTION_RAISE
    assert new[0].amount == 30
    assert new[0].total_bet == 30


def test_call_matches_existing_round_max():
    """Bet equal to current round_max → CALL."""
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    # Seat 1 calls (puts in 25 more to match)
    new = h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=30, last_action="Call"),
        _p(2, bet=10),
        _p(3, bet=30, last_action="Raise"),
    ]))
    assert len(new) == 1
    assert new[0].action == ACTION_CALL
    assert new[0].seat == 1
    assert new[0].amount == 25


def test_fold_detected_from_last_action_only():
    """Fold has no bet change; signal comes from last_action transition."""
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    # Seat 1 folds
    new = h.update(_snap("h1", "PREFLOP", [
        _p(1, bet=5, last_action="Fold"),
        _p(2, bet=10),
        _p(3, bet=30, last_action="Raise"),
    ]))
    assert len(new) == 1
    assert new[0].action == ACTION_FOLD
    assert new[0].seat == 1
    assert new[0].amount == 0


def test_check_detected_postflop():
    """Check has no bet change either; only signal is last_action."""
    h = ActionHistory()
    # Skip to flop directly
    h.update(_snap("h1", "FLOP", [_p(1, bet=0), _p(2, bet=0), _p(3, bet=0)]))
    new = h.update(_snap("h1", "FLOP", [
        _p(1, bet=0, last_action="Check"),
        _p(2, bet=0),
        _p(3, bet=0),
    ]))
    assert len(new) == 1
    assert new[0].action == ACTION_CHECK
    assert new[0].seat == 1


def test_bet_when_no_prior_round_bet_is_BET_not_RAISE():
    """First chip in on a fresh street → BET."""
    h = ActionHistory()
    h.update(_snap("h1", "FLOP", [_p(1, bet=0), _p(2, bet=0), _p(3, bet=0)]))
    new = h.update(_snap("h1", "FLOP", [
        _p(1, bet=0, last_action="Check"),
        _p(2, bet=0, last_action="Check"),
        _p(3, bet=50, last_action="Bet"),
    ]))
    # Two checks then a bet
    assert len(new) == 3
    assert new[0].action == ACTION_CHECK and new[0].seat == 1
    assert new[1].action == ACTION_CHECK and new[1].seat == 2
    assert new[2].action == ACTION_BET and new[2].seat == 3
    assert new[2].amount == 50


def test_allin_detected_when_stack_zero():
    """All-in: cur_stack == 0 → ALLIN regardless of round_max."""
    h = ActionHistory()
    h.update(_snap("h1", "TURN", [_p(1, bet=0, stack=10000), _p(2, bet=0, stack=300)]))
    new = h.update(_snap("h1", "TURN", [
        _p(1, bet=0, stack=10000),
        _p(2, bet=300, stack=0, last_action="All In"),
    ]))
    assert len(new) == 1
    assert new[0].action == ACTION_ALLIN
    assert new[0].seat == 2


# ── street transitions ───────────────────────────────────────────────

def test_street_transition_resets_bet_tracking():
    """Bets reset between streets; per-street bet starts at 0."""
    h = ActionHistory()
    # Preflop: SB/BB post, BTN raises, both blinds call
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=30, last_action="Call"), _p(2, bet=30, last_action="Call"), _p(3, bet=30, last_action="Raise")]))

    # Flop: bets are now 0 again
    h.update(_snap("h1", "FLOP", [_p(1, bet=0), _p(2, bet=0), _p(3, bet=0)]))
    new = h.update(_snap("h1", "FLOP", [
        _p(1, bet=0, last_action="Check"),
        _p(2, bet=0, last_action="Check"),
        _p(3, bet=60, last_action="Bet"),
    ]))
    # The seat-3 bet is BET (first chips on flop), not RAISE.
    bet_action = next(a for a in new if a.seat == 3)
    assert bet_action.action == ACTION_BET
    assert bet_action.amount == 60


def test_hand_transition_resets_everything():
    """New hand_id resets the history fully."""
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10)]))
    h.update(_snap("h2", "PREFLOP", [_p(1, bet=5), _p(2, bet=10)]))
    assert h.hand_id == "h2"
    # Only h2's blinds should be in actions
    assert all(a.hand_id == "h2" for a in h.actions)


# ── queries ──────────────────────────────────────────────────────────

def test_last_aggressor_finds_most_recent_bet_or_raise():
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=30, last_action="Call"), _p(2, bet=10), _p(3, bet=30)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=30), _p(2, bet=80, last_action="Raise"), _p(3, bet=30)]))
    agg = h.last_aggressor()
    assert agg is not None
    assert agg.seat == 2
    assert agg.action == ACTION_RAISE
    assert agg.amount == 70  # 80 - 10


def test_last_aggressor_per_street_filtering():
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    h.update(_snap("h1", "FLOP", [_p(1, bet=0), _p(2, bet=0), _p(3, bet=0)]))
    h.update(_snap("h1", "FLOP", [_p(1, bet=0, last_action="Check"), _p(2, bet=50, last_action="Bet"), _p(3, bet=0)]))
    pf_agg = h.last_aggressor("PREFLOP")
    flop_agg = h.last_aggressor("FLOP")
    assert pf_agg.seat == 3
    assert flop_agg.seat == 2


def test_hero_aggressed_query():
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    assert h.hero_aggressed(hero_seat=3, street="PREFLOP") is True
    assert h.hero_aggressed(hero_seat=1, street="PREFLOP") is False
    assert h.hero_aggressed(hero_seat=3, street="FLOP") is False


def test_villain_actions_excludes_hero():
    h = ActionHistory()
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=0)]))
    h.update(_snap("h1", "PREFLOP", [_p(1, bet=5), _p(2, bet=10), _p(3, bet=30, last_action="Raise")]))
    villain = h.villain_actions(hero_seat=3)
    # Hero is seat 3 — only the two blind POSTs should remain
    assert len(villain) == 2
    assert all(a.seat != 3 for a in villain)
    assert all(a.action == ACTION_POST for a in villain)
