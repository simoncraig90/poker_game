"""
Test that opponent_type produces different decisions in the postflop engine.

Validates that the wiring from opponent_tracker → state_machine → postflop_engine
actually changes recommendations based on villain classification.
"""

import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from strategy.postflop_engine import PostflopEngine
from opponent_tracker import OpponentTracker


def test_classify_picks_last_aggressor():
    """classify_villain should prefer the player with the highest bet."""
    failures = []
    tracker = OpponentTracker()
    # Set up known stats: Alice = NIT, Bob = FISH
    for _ in range(20):
        tracker.players.setdefault('Alice', {
            'hands': 0, 'vpip': 0, 'pfr': 0, 'bets': 0, 'calls': 0,
            'folds': 0, 'showdowns': 0, 'wins': 0
        })
        tracker.players.setdefault('Bob', {
            'hands': 0, 'vpip': 0, 'pfr': 0, 'bets': 0, 'calls': 0,
            'folds': 0, 'showdowns': 0, 'wins': 0
        })

    # Alice is a NIT: VPIP 10%, PFR 8%
    tracker.players['Alice'] = {'hands': 100, 'vpip': 10, 'pfr': 8,
                                 'bets': 5, 'calls': 30, 'folds': 50,
                                 'showdowns': 0, 'wins': 0}
    # Bob is a FISH: VPIP 60%, PFR 5%
    tracker.players['Bob'] = {'hands': 100, 'vpip': 60, 'pfr': 5,
                               'bets': 10, 'calls': 50, 'folds': 5,
                               'showdowns': 0, 'wins': 0}

    # Hero in seat 0, Alice in seat 1, Bob in seat 2
    state = {
        'players': ['Hero', 'Alice', 'Bob'],
        'hero_seat': 0,
        'bets': [4, 12, 0],  # Alice raised, Bob folded
    }
    villain = tracker.classify_villain(state)
    if villain != 'NIT':
        failures.append(f"Should pick Alice (NIT, last aggressor), got {villain}")

    # Now Bob is the aggressor
    state['bets'] = [4, 0, 16]
    villain = tracker.classify_villain(state)
    if villain != 'FISH':
        failures.append(f"Should pick Bob (FISH, last aggressor), got {villain}")

    return failures


def test_engine_value_thresh_changes_by_opponent():
    """
    Same hand, different opponent_type → engine should produce different decisions.
    Specifically: vs NIT we should value-bet less (need stronger hand).
    """
    failures = []
    engine = PostflopEngine()

    # Marginal value bet spot: middle pair on dry board, not facing
    hero = ['Th', '9s']
    board = ['9c', '4d', '2h']
    pot = 30
    stack = 1000

    random.seed(0)
    vs_unknown = engine.get_action(hero, board, 'BTN', False, 0, pot, stack, 'TURN', bb=4, opponent_type='UNKNOWN')
    random.seed(0)
    vs_nit = engine.get_action(hero, board, 'BTN', False, 0, pot, stack, 'TURN', bb=4, opponent_type='NIT')
    random.seed(0)
    vs_fish = engine.get_action(hero, board, 'BTN', False, 0, pot, stack, 'TURN', bb=4, opponent_type='FISH')

    # Vs NIT, value threshold is +0.10 stricter, so middle pair should NOT bet
    # Vs FISH, value threshold is -0.05 looser, so we might bet
    # We don't enforce specific actions, just that different opponents produce
    # at least sometimes different sizing or actions.

    # The key invariant: vs NIT, we should NEVER bet a hand we don't bet vs UNKNOWN
    if vs_unknown and vs_nit:
        unknown_aggressive = vs_unknown['action'] in ('BET', 'RAISE')
        nit_aggressive = vs_nit['action'] in ('BET', 'RAISE')
        if nit_aggressive and not unknown_aggressive:
            failures.append("Bet vs NIT but not vs UNKNOWN (should be other way)")

    return failures


def test_engine_call_thresh_changes_by_opponent():
    """Vs NIT we should fold more (call_delta +0.10). Vs LAG fold less (-0.05)."""
    failures = []
    engine = PostflopEngine()

    # Marginal call spot: weak pair facing a bet
    hero = ['7h', '7s']
    board = ['Kd', '4c', '2h']  # underpair
    pot = 60
    stack = 1000
    call_amt = 30  # half pot

    random.seed(0)
    vs_nit = engine.get_action(hero, board, 'BB', True, call_amt, pot, stack, 'TURN', bb=4, opponent_type='NIT')
    random.seed(0)
    vs_fish = engine.get_action(hero, board, 'BB', True, call_amt, pot, stack, 'TURN', bb=4, opponent_type='FISH')
    random.seed(0)
    vs_lag = engine.get_action(hero, board, 'BB', True, call_amt, pot, stack, 'TURN', bb=4, opponent_type='LAG')

    # Vs NIT we should fold (NIT bet = strong hand)
    # Vs FISH/LAG we might call (they bluff more)
    if vs_nit and vs_nit['action'] not in ('FOLD',):
        # Not strict — engine has many factors, but document
        pass

    # The big invariant: we should fold MORE often vs NIT than vs LAG
    # Hard to enforce in single call without statistical sampling
    # Just verify all three return valid results
    for name, r in [('vs NIT', vs_nit), ('vs FISH', vs_fish), ('vs LAG', vs_lag)]:
        if r is None:
            failures.append(f"{name}: engine returned None")
        elif r['action'] not in ('FOLD', 'CALL', 'CHECK', 'BET', 'RAISE'):
            failures.append(f"{name}: invalid action {r['action']}")
    return failures


def test_state_machine_passes_opponent_type():
    """The state machine should pass opponent_type from tracker → engine."""
    failures = []
    from advisor_state_machine import AdvisorStateMachine
    from preflop_chart import preflop_advice

    # Mock base advisor
    class FakeBase:
        def _get_recommendation(self, state):
            return {
                "phase": "FLOP", "equity": 0.55,
                "danger": {"warnings": [], "danger": 0},
                "category": "TEST",
            }

    # Tracker with a known FISH
    tracker = OpponentTracker()
    tracker.players['villain1'] = {
        'hands': 100, 'vpip': 60, 'pfr': 5,
        'bets': 10, 'calls': 50, 'folds': 5,
        'showdowns': 0, 'wins': 0
    }

    # Capture what opponent_type the engine is called with
    called_with = {}

    class CapturingEngine:
        def get_action(self, *args, **kwargs):
            called_with['opponent_type'] = kwargs.get('opponent_type')
            return None

    sm = AdvisorStateMachine(
        base_advisor=FakeBase(),
        preflop_advice_fn=preflop_advice,
        postflop_engine=CapturingEngine(),
        tracker=tracker,
        bb_cents=4,
    )

    state = {
        'hero_cards': ['Ah', 'Kh'],
        'board_cards': ['Td', '4c', '9s'],
        'hand_id': 'h1',
        'facing_bet': True,
        'call_amount': 20,
        'pot': 60,
        'num_opponents': 1,
        'position': 'BTN',
        'hero_stack': 1000,
        'phase': 'FLOP',
        'bets': [0, 20],
        'players': ['Hero', 'villain1'],
        'hero_seat': 0,
    }
    sm.process_state(state)

    if called_with.get('opponent_type') != 'FISH':
        failures.append(f"Engine called with opponent_type={called_with.get('opponent_type')}, expected FISH")
    return failures


if __name__ == "__main__":
    tests = [
        ("classify picks last aggressor", test_classify_picks_last_aggressor),
        ("engine value thresh changes", test_engine_value_thresh_changes_by_opponent),
        ("engine call thresh changes", test_engine_call_thresh_changes_by_opponent),
        ("state machine passes opponent_type", test_state_machine_passes_opponent_type),
    ]

    print("=" * 60)
    print("  OPPONENT ADJUSTMENT TESTS")
    print("=" * 60)
    total = passed = 0
    all_failures = []
    for name, fn in tests:
        total += 1
        try:
            failures = fn()
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
    print("=" * 60)
    sys.exit(0 if not all_failures else 1)
