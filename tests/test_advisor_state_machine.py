"""
Test suite for AdvisorStateMachine — the extracted on_state logic.

Tests every state transition, recommendation path, and edge case
that caused bugs during the 2026-04-06 live session.

No Chrome, no WS, no overlay subprocess — pure unit tests.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from advisor_state_machine import AdvisorStateMachine, AdvisorOutput


# ══════════════════════════════════════════════════════════════════════
# MOCK DEPENDENCIES
# ══════════════════════════════════════════════════════════════════════

class MockBaseAdvisor:
    """Mimics base advisor's _get_recommendation — returns equity + phase info."""

    def _get_recommendation(self, state):
        hero = state["hero_cards"]
        board = state["board_cards"]

        if len(board) == 0:
            phase = "PREFLOP"
        elif len(board) == 3:
            phase = "FLOP"
        elif len(board) == 4:
            phase = "TURN"
        else:
            phase = "RIVER"

        # Simple equity heuristic for testing
        eq = self._estimate_equity(hero, board)

        result = {
            "phase": phase,
            "equity": eq,
            "position": state.get("position_6max", "MP"),
            "facing_bet": state["facing_bet"],
            "danger": {"warnings": [], "danger": 0, "suppress_raise": False},
            "category": "TEST",
            "action_probs": {},
            "recommended": "",
            "rec_prob": 0,
            "nn_equity": eq,
            "bucket": int(eq * 50),
            "fallback": False,
        }

        if phase == "PREFLOP":
            result["preflop"] = {"action": "FOLD", "hand_key": "??", "in_range": False, "note": ""}

        return result

    def _estimate_equity(self, hero, board):
        """Rough equity for test purposes."""
        if not hero or len(hero) < 2:
            return 0.5
        rank_map = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
                    '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
        r1 = rank_map.get(hero[0][0].upper(), 5)
        r2 = rank_map.get(hero[1][0].upper(), 5)
        eq = (r1 + r2) / 28.0
        if r1 == r2:
            eq += 0.15  # pair bonus
        if hero[0][1] == hero[1][1]:
            eq += 0.05  # suited bonus
        return min(1.0, eq)


class MockPostflopEngine:
    """Mimics PostflopEngine.get_action — returns structured result."""

    def __init__(self, action='CHECK', amount=None, source='test_engine'):
        self._action = action
        self._amount = amount
        self._source = source

    def get_action(self, hero_cards, board_cards, position, facing_bet,
                   call_amount, pot, hero_stack, phase, bb=4,
                   opponent_type='UNKNOWN', action_history=None):
        # All-in cap
        if facing_bet and call_amount >= hero_stack:
            if self._action in ('RAISE', 'BET'):
                return {'action': 'CALL', 'amount': hero_stack, 'probs': None,
                        'source': self._source, 'strength': 0.5}

        # Sanity: facing bet -> never CHECK
        action = self._action
        if facing_bet and call_amount > 0 and action == 'CHECK':
            action = 'FOLD'
        # Not facing -> never CALL
        if not facing_bet and call_amount == 0 and action == 'CALL':
            action = 'CHECK'

        return {
            'action': action,
            'amount': self._amount,
            'probs': {'fold': 0.3, 'call': 0.4, 'check': 0.0, 'raise': 0.3},
            'source': self._source,
            'strength': 0.5,
        }


# ── Real preflop chart ──
from preflop_chart import preflop_advice


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def make_state(hero=None, board=None, hand_id="h1", facing=False,
               call_amt=0, pot=20, num_opp=3, pos="BTN", hero_stack=1000,
               phase=None, bets=None, players=None, hero_seat=1):
    """Build a game state dict for testing."""
    if hero is None:
        hero = ["Ah", "Kh"]
    if board is None:
        board = []
    if phase is None:
        if len(board) == 0:
            phase = "PREFLOP"
        elif len(board) == 3:
            phase = "FLOP"
        elif len(board) == 4:
            phase = "TURN"
        else:
            phase = "RIVER"
    return {
        "hero_cards": hero,
        "board_cards": board,
        "hand_id": hand_id,
        "facing_bet": facing,
        "call_amount": call_amt,
        "pot": pot,
        "num_opponents": num_opp,
        "position": pos,
        "hero_stack": hero_stack,
        "phase": phase,
        "bets": bets or [0]*6,
        "players": players or ["V1", "Hero", "V2", "V3", "V4", "V5"],
        "hero_seat": hero_seat,
    }


def make_sm(postflop=None, tracker=None):
    """Create a state machine with mock dependencies."""
    return AdvisorStateMachine(
        base_advisor=MockBaseAdvisor(),
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        tracker=tracker,
        bb_cents=4,
    )


# ══════════════════════════════════════════════════════════════════════
# TESTS: PREFLOP
# ══════════════════════════════════════════════════════════════════════

def test_preflop_premium_raise():
    """AA, KK from any position should RAISE."""
    sm = make_sm()
    for hero in [["Ah", "As"], ["Kh", "Ks"], ["Ah", "Kh"]]:
        for pos in ["EP", "MP", "CO", "BTN", "SB"]:
            out = sm.process_state(make_state(hero=hero, pos=pos, hand_id=f"h_{hero}_{pos}"))
            if out is None:
                return [f"No output for {hero} {pos}"]
            if "RAISE" not in out.action.upper():
                return [f"{hero} {pos}: expected RAISE, got {out.action}"]
    return []


def test_preflop_trash_fold():
    """72o should FOLD from EP."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["7h", "2c"], pos="EP"))
    if out is None:
        return ["No output"]
    if "FOLD" not in out.action.upper():
        return [f"72o EP: expected FOLD, got {out.action}"]
    return []


def test_preflop_bb_check_no_raise():
    """BB with no raise ahead should CHECK, not FOLD."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["7h", "2c"], pos="BB", facing=False))
    if out is None:
        return ["No output"]
    if "CHECK" not in out.action.upper():
        return [f"72o BB no raise: expected CHECK, got {out.action}"]
    return []


def test_preflop_bb_defend_vs_raise():
    """BB with ATs should CALL vs raise."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["Ah", "Ts"], pos="BB", facing=True, call_amt=12))
    if out is None:
        return ["No output"]
    if "CALL" not in out.action.upper():
        return [f"ATs BB vs raise: expected CALL, got {out.action}"]
    return []


def test_preflop_bb_3bet_premium():
    """BB with AA should RAISE (3-bet) vs raise."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["Ah", "As"], pos="BB", facing=True, call_amt=12))
    if out is None:
        return ["No output"]
    if "RAISE" not in out.action.upper():
        return [f"AA BB vs raise: expected RAISE, got {out.action}"]
    return []


def test_preflop_facing_raise_tightens():
    """Marginal hands fold vs raise from non-BB positions."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["9h", "7c"], pos="CO", facing=True, call_amt=12))
    if out is None:
        return ["No output"]
    if "FOLD" not in out.action.upper():
        return [f"97o CO vs raise: expected FOLD, got {out.action}"]
    return []


def test_preflop_raise_sizing_open():
    """Open raise should be ~2.5x BB."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["Ah", "Kh"], pos="BTN", facing=False, pot=6))
    if out is None:
        return ["No output"]
    # 2.5 * 4 = 10 cents = 0.10
    if "RAISE to 0.10" not in out.action:
        return [f"AKs BTN open: expected 'RAISE to 0.10', got {out.action}"]
    return []


def test_preflop_raise_sizing_3bet():
    """3-bet should be ~3x the raise."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["Ah", "As"], pos="CO", facing=True, call_amt=12, pot=18))
    if out is None:
        return ["No output"]
    # 3x 12 = 36 cents = 0.36
    if "RAISE to 0.36" not in out.action:
        return [f"AA CO 3bet: expected 'RAISE to 0.36', got {out.action}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# TESTS: POSTFLOP — FACING BET SANITY
# ══════════════════════════════════════════════════════════════════════

def test_facing_bet_never_check():
    """When facing_bet=True and call_amount>0, action must NEVER be CHECK."""
    failures = []
    hands = [["Ah", "Kh"], ["9h", "9c"], ["7h", "2c"], ["Qh", "Jh"], ["Ah", "5s"]]
    boards = [["Th", "4d", "9s"], ["Jh", "2s", "9d", "Qh"], ["Th", "4d", "9s", "Qh", "7h"]]

    for hero in hands:
        for board in boards:
            for call_amt in [5, 10, 20, 50, 100]:
                # With postflop engine
                engine = MockPostflopEngine(action='CHECK')  # deliberately try to CHECK
                sm = make_sm(postflop=engine)
                hid = f"h_{hero[0]}_{len(board)}_{call_amt}"
                out = sm.process_state(make_state(
                    hero=hero, board=board, facing=True,
                    call_amt=call_amt, pot=50, hand_id=hid
                ))
                if out and "CHECK" in out.action.upper() and "CHECK /" not in out.action.upper():
                    failures.append(f"Engine: {hero} board={len(board)} call={call_amt}: {out.action}")

                # Without postflop engine (fallback rules)
                sm2 = make_sm(postflop=None)
                out2 = sm2.process_state(make_state(
                    hero=hero, board=board, facing=True,
                    call_amt=call_amt, pot=50, hand_id=hid + "_noeng"
                ))
                if out2 and out2.action.upper() == "CHECK":
                    failures.append(f"Rules: {hero} board={len(board)} call={call_amt}: {out2.action}")
    return failures


def test_not_facing_bet_never_call():
    """When facing_bet=False and call_amount=0, action must NEVER be bare CALL."""
    failures = []
    hands = [["Ah", "Kh"], ["9h", "9c"], ["7h", "2c"], ["Qh", "Jh"]]
    boards = [["Th", "4d", "9s"], ["Jh", "2s", "9d", "Qh"]]

    for hero in hands:
        for board in boards:
            engine = MockPostflopEngine(action='CALL')  # deliberately try to CALL
            sm = make_sm(postflop=engine)
            hid = f"h_{hero[0]}_{len(board)}_nf"
            out = sm.process_state(make_state(
                hero=hero, board=board, facing=False,
                call_amt=0, pot=50, hand_id=hid
            ))
            if out and out.action.upper() == "CALL":
                failures.append(f"{hero} board={len(board)}: got bare CALL when not facing")
    return failures


def test_allin_never_raise():
    """When opponent is all-in (call_amt >= hero_stack), never RAISE."""
    failures = []
    hands = [["Ah", "Kh"], ["Kh", "Kd"], ["9h", "9c"]]
    board = ["Th", "4d", "9s"]

    for hero in hands:
        for stack in [100, 500, 752]:
            engine = MockPostflopEngine(action='RAISE', amount=2000)
            sm = make_sm(postflop=engine)
            hid = f"h_{hero[0]}_allin_{stack}"
            out = sm.process_state(make_state(
                hero=hero, board=board, facing=True,
                call_amt=stack, pot=200, hero_stack=stack,
                hand_id=hid
            ))
            if out and "RAISE" in out.action.upper():
                failures.append(f"{hero} stack={stack}: got RAISE when all-in")
    return failures


# ══════════════════════════════════════════════════════════════════════
# TESTS: POSTFLOP ENGINE INTEGRATION
# ══════════════════════════════════════════════════════════════════════

def test_postflop_engine_bet_action():
    """Postflop engine BET action formats correctly."""
    engine = MockPostflopEngine(action='BET', amount=33)
    sm = make_sm(postflop=engine)
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"],
        facing=False, call_amt=0, pot=50
    ))
    if out is None:
        return ["No output"]
    if "BET 0.33" not in out.action:
        return [f"Expected 'BET 0.33', got {out.action}"]
    return []


def test_postflop_engine_call_action():
    """Postflop engine CALL action formats correctly."""
    engine = MockPostflopEngine(action='CALL')
    sm = make_sm(postflop=engine)
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"],
        facing=True, call_amt=15, pot=50
    ))
    if out is None:
        return ["No output"]
    if "CALL 0.15" not in out.action:
        return [f"Expected 'CALL 0.15', got {out.action}"]
    return []


def test_postflop_engine_raise_action():
    """Postflop engine RAISE formats with amount."""
    engine = MockPostflopEngine(action='RAISE', amount=100)
    sm = make_sm(postflop=engine)
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"],
        facing=True, call_amt=15, pot=50
    ))
    if out is None:
        return ["No output"]
    if "RAISE to 1.00" not in out.action:
        return [f"Expected 'RAISE to 1.00', got {out.action}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# TESTS: STATE TRANSITIONS
# ══════════════════════════════════════════════════════════════════════

def test_no_output_when_nothing_changes():
    """Duplicate state should return None (no update)."""
    sm = make_sm()
    state = make_state()
    out1 = sm.process_state(state)
    out2 = sm.process_state(state)  # same state again
    if out2 is not None:
        return ["Expected None for duplicate state, got output"]
    return []


def test_output_on_facing_change():
    """Changing facing_bet should trigger new output."""
    sm = make_sm()
    out1 = sm.process_state(make_state(facing=False, board=["Th", "4d", "9s"]))
    out2 = sm.process_state(make_state(facing=True, call_amt=15, board=["Th", "4d", "9s"]))
    if out2 is None:
        return ["Expected output when facing_bet changes"]
    return []


def test_output_on_board_change():
    """New board cards trigger output."""
    sm = make_sm()
    out1 = sm.process_state(make_state(board=["Th", "4d", "9s"]))
    out2 = sm.process_state(make_state(board=["Th", "4d", "9s", "Qh"]))
    if out2 is None:
        return ["Expected output when board changes"]
    if out2.phase != "TURN":
        return [f"Expected TURN, got {out2.phase}"]
    return []


def test_output_on_new_hand():
    """New hand_id triggers output even with same cards."""
    sm = make_sm()
    out1 = sm.process_state(make_state(hand_id="h1"))
    out2 = sm.process_state(make_state(hand_id="h2"))
    if out2 is None:
        return ["Expected output on new hand"]
    return []


def test_waiting_clears_on_no_hero():
    """When hero has no cards, should show waiting."""
    sm = make_sm()
    # First give hero cards
    sm.process_state(make_state(hero=["Ah", "Kh"]))
    # Then clear them
    out = sm.process_state(make_state(hero=[], hand_id="h2"))
    if out is None:
        return ["Expected waiting output when hero cards cleared"]
    if "Waiting" not in out.cards_text:
        return [f"Expected 'Waiting' text, got {out.cards_text}"]
    return []


def test_hands_counted():
    """hands_played increments on new hand_id."""
    sm = make_sm()
    sm.process_state(make_state(hand_id="h1"))
    sm.process_state(make_state(hand_id="h2"))
    sm.process_state(make_state(hand_id="h3"))
    if sm.hands_played != 3:
        return [f"Expected 3 hands, got {sm.hands_played}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# TESTS: EQUITY ADJUSTMENT
# ══════════════════════════════════════════════════════════════════════

def test_equity_adjustment_shown_when_facing():
    """When facing a bet, adjusted equity should appear in info string."""
    sm = make_sm(postflop=None)
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"],
        facing=True, call_amt=50, pot=100  # bet_ratio=0.5, medium discount
    ))
    if out is None:
        return ["No output"]
    if "(adj:" not in out.info:
        return [f"Expected adjusted equity in info, got: {out.info}"]
    return []


def test_no_equity_adjustment_when_not_facing():
    """When not facing a bet, no adjustment shown."""
    sm = make_sm(postflop=None)
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"],
        facing=False, call_amt=0, pot=100
    ))
    if out is None:
        return ["No output"]
    if "(adj:" in out.info:
        return [f"Should not show adjusted equity when not facing, got: {out.info}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# TESTS: REGRESSION (bugs from 2026-04-06 live session)
# ══════════════════════════════════════════════════════════════════════

def test_regression_j3o_bb_check():
    """J3o in BB with no raise should CHECK, not RAISE (legacy CFR bug)."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["Jh", "3c"], pos="BB", facing=False))
    if out is None:
        return ["No output"]
    if "RAISE" in out.action.upper():
        return [f"J3o BB: should CHECK not {out.action}"]
    if "CHECK" not in out.action.upper():
        return [f"J3o BB: expected CHECK, got {out.action}"]
    return []


def test_regression_stale_overlay_no_duplicate():
    """State machine should not produce output for unchanged states."""
    sm = make_sm()
    state = make_state(hero=["Ah", "Kh"], board=["Th", "4d", "9s"])
    out1 = sm.process_state(state)
    out2 = sm.process_state(state)
    if out1 is None:
        return ["First call should produce output"]
    if out2 is not None:
        return ["Duplicate state should not produce output (stale overlay bug)"]
    return []


def test_regression_check_facing_bet():
    """NEVER show CHECK when facing a bet — the critical live bug."""
    sm = make_sm(postflop=None)
    # High equity hand facing a bet — rules fallback path
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Ah", "4d", "9s"],
        facing=True, call_amt=30, pot=60
    ))
    if out is None:
        return ["No output"]
    if out.action.upper() == "CHECK":
        return [f"CHECK when facing bet! Got: {out.action}"]
    return []


def test_regression_kk_vs_allin_no_raise():
    """KK vs all-in should CALL or FOLD, never RAISE."""
    engine = MockPostflopEngine(action='RAISE', amount=2000)
    sm = make_sm(postflop=engine)
    out = sm.process_state(make_state(
        hero=["Kh", "Kd"], board=["9h", "6h", "2h"],
        facing=True, call_amt=752, pot=200, hero_stack=752
    ))
    if out is None:
        return ["No output"]
    if "RAISE" in out.action.upper():
        return [f"KK vs all-in: should not RAISE, got {out.action}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# TESTS: FULL HAND SEQUENCES
# ══════════════════════════════════════════════════════════════════════

def test_full_hand_preflop_to_river():
    """Play through a full hand: preflop -> flop -> turn -> river."""
    engine = MockPostflopEngine(action='CHECK')
    sm = make_sm(postflop=engine)
    failures = []

    # Preflop: AKs BTN
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], pos="BTN", hand_id="fullhand"
    ))
    if out is None or "RAISE" not in out.action.upper():
        failures.append(f"Preflop: expected RAISE, got {out.action if out else 'None'}")

    # Flop: no bet
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"], pos="BTN",
        facing=False, hand_id="fullhand"
    ))
    if out is None:
        failures.append("No output on flop")
    elif out.phase != "FLOP":
        failures.append(f"Expected FLOP phase, got {out.phase}")

    # Turn: opponent bets
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s", "Qh"], pos="BTN",
        facing=True, call_amt=20, pot=60, hand_id="fullhand"
    ))
    if out is None:
        failures.append("No output on turn")
    elif out.phase != "TURN":
        failures.append(f"Expected TURN phase, got {out.phase}")

    # River
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s", "Qh", "7h"], pos="BTN",
        facing=False, hand_id="fullhand"
    ))
    if out is None:
        failures.append("No output on river")
    elif out.phase != "RIVER":
        failures.append(f"Expected RIVER phase, got {out.phase}")

    return failures


def test_two_hands_no_state_bleed():
    """State from hand 1 should not leak into hand 2."""
    sm = make_sm()
    failures = []

    # Hand 1: AKs on wet board
    sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"],
        facing=True, call_amt=30, pot=60, hand_id="hand1"
    ))

    # Hand 2: new hand, preflop — should have clean state
    out = sm.process_state(make_state(
        hero=["7h", "2c"], board=[], facing=False,
        pot=6, hand_id="hand2", pos="EP"
    ))
    if out is None:
        failures.append("No output for hand 2")
    elif out.phase != "PREFLOP":
        failures.append(f"Hand 2 phase should be PREFLOP, got {out.phase}")
    elif len(out.board) != 0:
        failures.append(f"Hand 2 board should be empty, got {out.board}")

    return failures


def test_fold_then_new_hand():
    """After hero folds (cards cleared), new hand should work."""
    sm = make_sm()
    failures = []

    # Hand 1: get cards
    sm.process_state(make_state(hero=["7h", "2c"], hand_id="h1"))

    # Hero folds (cards cleared)
    sm.process_state(make_state(hero=[], hand_id="h1"))

    # Hand 2: new cards
    out = sm.process_state(make_state(hero=["Ah", "As"], hand_id="h2", pos="BTN"))
    if out is None:
        failures.append("No output after fold + new hand")
    elif "RAISE" not in out.action.upper():
        failures.append(f"AA BTN after fold: expected RAISE, got {out.action}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# TESTS: OVERLAY OUTPUT FORMAT
# ══════════════════════════════════════════════════════════════════════

def test_overlay_green_for_action():
    """RAISE/CALL/BET should have green background."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["Ah", "As"], pos="BTN"))
    if out is None:
        return ["No output"]
    if out.rec_bg != "#1a3a1a":
        return [f"RAISE should be green (#1a3a1a), got {out.rec_bg}"]
    return []


def test_overlay_red_for_fold():
    """FOLD should have red background."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["7h", "2c"], pos="EP"))
    if out is None:
        return ["No output"]
    if out.rec_bg != "#3a1a1a":
        return [f"FOLD should be red (#3a1a1a), got {out.rec_bg}"]
    return []


def test_overlay_blue_for_check():
    """CHECK should have blue background."""
    sm = make_sm()
    out = sm.process_state(make_state(hero=["7h", "2c"], pos="BB", facing=False))
    if out is None:
        return ["No output"]
    if out.rec_bg != "#1a1a3a":
        return [f"CHECK should be blue (#1a1a3a), got {out.rec_bg}"]
    return []


def test_cards_text_format():
    """Cards text should show hero + board separated by |."""
    sm = make_sm()
    out = sm.process_state(make_state(
        hero=["Ah", "Kh"], board=["Th", "4d", "9s"]
    ))
    if out is None:
        return ["No output"]
    if "Ah Kh" not in out.cards_text:
        return [f"Missing hero cards in: {out.cards_text}"]
    if "Th 4d 9s" not in out.cards_text:
        return [f"Missing board cards in: {out.cards_text}"]
    if "|" not in out.cards_text:
        return [f"Missing separator in: {out.cards_text}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# EXHAUSTIVE: every position × facing × hand type
# ══════════════════════════════════════════════════════════════════════

def test_exhaustive_preflop_no_crash():
    """All 6 positions × 2 facing × sample hands — no crashes, valid actions."""
    sm = make_sm()
    failures = []
    positions = ["EP", "MP", "CO", "BTN", "SB", "BB"]
    hands = [
        ["Ah", "As"], ["Kh", "Qh"], ["9h", "9c"], ["Th", "9h"],
        ["Ah", "5s"], ["7h", "2c"], ["Jh", "Tc"], ["6h", "5h"],
    ]
    valid_actions = {"RAISE", "CALL", "FOLD", "CHECK"}
    hand_counter = 0

    for hero in hands:
        for pos in positions:
            for facing in [False, True]:
                hand_counter += 1
                hid = f"exhaust_{hand_counter}"
                try:
                    out = sm.process_state(make_state(
                        hero=hero, pos=pos, facing=facing,
                        call_amt=12 if facing else 0,
                        hand_id=hid
                    ))
                    if out is not None:
                        action_word = out.action.split()[0].upper()
                        if action_word not in valid_actions:
                            failures.append(f"{hero} {pos} facing={facing}: invalid action '{out.action}'")
                except Exception as e:
                    failures.append(f"{hero} {pos} facing={facing}: CRASH {e}")

    return failures


def test_exhaustive_postflop_facing_sanity():
    """Postflop: facing_bet=True must never produce CHECK. facing_bet=False must never produce bare CALL."""
    sm = make_sm(postflop=None)
    failures = []
    hands = [["Ah", "Kh"], ["9h", "9c"], ["Qh", "Jh"], ["7h", "2c"], ["Ah", "5h"]]
    boards = {
        "FLOP": ["Th", "4d", "9s"],
        "TURN": ["Th", "4d", "9s", "Qh"],
        "RIVER": ["Th", "4d", "9s", "Qh", "7h"],
    }
    hand_counter = 0

    for hero in hands:
        for phase_name, board in boards.items():
            for call_amt in [5, 15, 30, 50, 100]:
                hand_counter += 1
                # Facing bet
                out = sm.process_state(make_state(
                    hero=hero, board=board, facing=True, call_amt=call_amt,
                    pot=60, hand_id=f"exh_f_{hand_counter}"
                ))
                if out and out.action.upper() == "CHECK":
                    failures.append(f"facing=True {hero} {phase_name} call={call_amt}: got CHECK")

            # Not facing
            hand_counter += 1
            out = sm.process_state(make_state(
                hero=hero, board=board, facing=False, call_amt=0,
                pot=60, hand_id=f"exh_nf_{hand_counter}"
            ))
            if out and out.action.upper() == "CALL":
                failures.append(f"facing=False {hero} {phase_name}: got bare CALL")

    return failures


# ══════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        # Preflop
        ("SM: preflop premium RAISE", test_preflop_premium_raise),
        ("SM: preflop trash FOLD", test_preflop_trash_fold),
        ("SM: BB check no raise", test_preflop_bb_check_no_raise),
        ("SM: BB defend vs raise", test_preflop_bb_defend_vs_raise),
        ("SM: BB 3-bet premium", test_preflop_bb_3bet_premium),
        ("SM: facing raise tightens", test_preflop_facing_raise_tightens),
        ("SM: open raise sizing", test_preflop_raise_sizing_open),
        ("SM: 3-bet sizing", test_preflop_raise_sizing_3bet),
        # Postflop sanity
        ("SM: facing bet NEVER check", test_facing_bet_never_check),
        ("SM: not facing NEVER call", test_not_facing_bet_never_call),
        ("SM: all-in NEVER raise", test_allin_never_raise),
        # Postflop engine
        ("SM: engine BET format", test_postflop_engine_bet_action),
        ("SM: engine CALL format", test_postflop_engine_call_action),
        ("SM: engine RAISE format", test_postflop_engine_raise_action),
        # State transitions
        ("SM: no output on duplicate", test_no_output_when_nothing_changes),
        ("SM: output on facing change", test_output_on_facing_change),
        ("SM: output on board change", test_output_on_board_change),
        ("SM: output on new hand", test_output_on_new_hand),
        ("SM: waiting on no hero", test_waiting_clears_on_no_hero),
        ("SM: hand counter", test_hands_counted),
        # Equity adjustment
        ("SM: adj equity shown facing", test_equity_adjustment_shown_when_facing),
        ("SM: no adj equity not facing", test_no_equity_adjustment_when_not_facing),
        # Regressions
        ("SM: J3o BB CHECK not RAISE", test_regression_j3o_bb_check),
        ("SM: no stale overlay dupe", test_regression_stale_overlay_no_duplicate),
        ("SM: no CHECK facing bet", test_regression_check_facing_bet),
        ("SM: KK vs all-in no RAISE", test_regression_kk_vs_allin_no_raise),
        # Full sequences
        ("SM: full hand pre->river", test_full_hand_preflop_to_river),
        ("SM: two hands no bleed", test_two_hands_no_state_bleed),
        ("SM: fold then new hand", test_fold_then_new_hand),
        # Overlay format
        ("SM: green for action", test_overlay_green_for_action),
        ("SM: red for fold", test_overlay_red_for_fold),
        ("SM: blue for check", test_overlay_blue_for_check),
        ("SM: cards text format", test_cards_text_format),
        # Exhaustive
        ("SM: exhaustive preflop no crash", test_exhaustive_preflop_no_crash),
        ("SM: exhaustive postflop sanity", test_exhaustive_postflop_facing_sanity),
    ]

    print("=" * 60)
    print("  ADVISOR STATE MACHINE TEST SUITE")
    print("=" * 60)

    total = 0
    passed = 0
    all_failures = []

    for name, test_fn in tests:
        total += 1
        try:
            failures = test_fn()
            if not failures:
                print(f"  PASS  {name}")
                passed += 1
            else:
                print(f"  FAIL  {name}")
                for f in failures:
                    print(f"        - {f}")
                all_failures.extend(failures)
        except Exception as e:
            import traceback
            print(f"  ERROR {name}: {e}")
            traceback.print_exc()
            all_failures.append(f"{name}: {e}")

    print()
    print(f"  {passed}/{total} tests passed")
    if all_failures:
        print(f"  {len(all_failures)} failures")
    else:
        print("  ALL STATE MACHINE TESTS PASS")
    print("=" * 60)

    sys.exit(0 if not all_failures else 1)
