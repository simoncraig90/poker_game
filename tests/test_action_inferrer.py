"""Test WSActionInferrer infers actions from state diffs."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from action_inferrer import WSActionInferrer


def make_state(hand_id='h1', phase='PREFLOP', players=None, bets=None, seats=None):
    return {
        'hand_id': hand_id,
        'phase': phase,
        'players': players or ['Hero', 'Alice', 'Bob'],
        'bets': bets or [0, 0, 0],
        'seat_states': seats or [1, 1, 1],
    }


def test_detects_initial_raise():
    inf = WSActionInferrer()
    inf.update(make_state(bets=[0, 0, 0]))
    actions = inf.update(make_state(bets=[0, 12, 0]))
    if not any(a == ('Alice', 'raise', 12) for a in actions):
        return [f"Expected Alice raise 12, got {actions}"]
    return []


def test_detects_call():
    inf = WSActionInferrer()
    inf.update(make_state(bets=[0, 0, 0]))
    inf.update(make_state(bets=[0, 12, 0]))  # Alice raises
    actions = inf.update(make_state(bets=[0, 12, 12]))  # Bob calls
    if not any(a == ('Bob', 'call', 12) for a in actions):
        return [f"Expected Bob call 12, got {actions}"]
    return []


def test_detects_3bet():
    inf = WSActionInferrer()
    inf.update(make_state(bets=[0, 0, 0]))
    inf.update(make_state(bets=[0, 12, 0]))   # Alice raises
    actions = inf.update(make_state(bets=[0, 12, 36]))  # Bob 3-bets
    if not any(a[0] == 'Bob' and a[1] == '3bet' for a in actions):
        return [f"Expected Bob 3bet, got {actions}"]
    return []


def test_detects_fold():
    inf = WSActionInferrer()
    inf.update(make_state(bets=[0, 12, 0], seats=[1, 1, 1]))
    actions = inf.update(make_state(bets=[0, 12, 0], seats=[1, 1, 3]))  # Bob folds
    if not any(a == ('Bob', 'fold', 0) for a in actions):
        return [f"Expected Bob fold, got {actions}"]
    return []


def test_resets_on_new_hand():
    inf = WSActionInferrer()
    inf.update(make_state(hand_id='h1', bets=[0, 12, 0]))
    actions = inf.update(make_state(hand_id='h2', bets=[0, 0, 0]))
    if actions:
        return [f"Expected no actions on new hand, got {actions}"]
    if inf._current_hand_id != 'h2':
        return [f"Hand id should be h2, got {inf._current_hand_id}"]
    return []


def test_resets_on_new_street():
    inf = WSActionInferrer()
    inf.update(make_state(phase='PREFLOP', bets=[0, 12, 12]))
    actions = inf.update(make_state(phase='FLOP', bets=[0, 0, 0]))
    if actions:
        return [f"Expected no actions on new street, got {actions}"]
    return []


if __name__ == "__main__":
    tests = [
        ("detects initial raise", test_detects_initial_raise),
        ("detects call", test_detects_call),
        ("detects 3bet", test_detects_3bet),
        ("detects fold", test_detects_fold),
        ("resets on new hand", test_resets_on_new_hand),
        ("resets on new street", test_resets_on_new_street),
    ]
    print("=" * 60)
    print("  ACTION INFERRER TESTS")
    print("=" * 60)
    total = passed = 0
    fails = []
    for n, fn in tests:
        total += 1
        try:
            f = fn()
            if not f:
                print(f"  PASS  {n}")
                passed += 1
            else:
                print(f"  FAIL  {n}")
                for x in f: print(f"        - {x}")
                fails.extend(f)
        except Exception as e:
            import traceback
            print(f"  ERROR {n}: {e}")
            traceback.print_exc()
            fails.append(f"{n}: {e}")
    print()
    print(f"  {passed}/{total} tests passed")
    sys.exit(0 if not fails else 1)
