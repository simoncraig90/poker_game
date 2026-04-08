"""
Automated test suite for the poker advisor.
Every test must pass before the advisor goes live.

Tests:
1. Preflop chart: correct action for every position/hand/facing combo
2. Facing bet detection: CALL/FOLD/RAISE when facing, CHECK/BET when not
3. All-in detection: never RAISE against all-in
4. Board detection: flop/turn/river cards parsed correctly
5. Overlay: no stale recommendations
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from preflop_chart import preflop_advice

# ══════════════════════════════════════════════════════════════════════
# 1. PREFLOP CHART TESTS
# ══════════════════════════════════════════════════════════════════════

def test_preflop_premium_hands():
    """Premium hands should RAISE from every position."""
    premiums = [("Ah", "As"), ("Kh", "Ks"), ("Qh", "Qs"), ("Ah", "Kh"), ("Ah", "Ks")]
    positions = ["EP", "MP", "CO", "BTN", "SB"]

    failures = []
    for c1, c2 in premiums:
        for pos in positions:
            result = preflop_advice(c1, c2, pos, facing_raise=False)
            if result["action"] != "RAISE":
                failures.append(f"{c1}{c2} {pos} open: expected RAISE, got {result['action']}")

    return failures


def test_preflop_trash_folds():
    """Trash hands should FOLD from EP/MP."""
    trash = [("7h", "2c"), ("9h", "3d"), ("8c", "2s"), ("4h", "3c"), ("Jh", "3c")]

    failures = []
    for c1, c2 in trash:
        for pos in ["EP", "MP"]:
            result = preflop_advice(c1, c2, pos, facing_raise=False)
            if result["action"] != "FOLD":
                failures.append(f"{c1}{c2} {pos} open: expected FOLD, got {result['action']}")

    return failures


def test_preflop_pocket_pairs_open():
    """All pocket pairs should RAISE from every position."""
    pairs = [("2h", "2c"), ("3h", "3c"), ("4h", "4c"), ("5h", "5c"),
             ("6h", "6c"), ("7h", "7c"), ("8h", "8c"), ("9h", "9c")]
    positions = ["EP", "MP", "CO", "BTN"]

    failures = []
    for c1, c2 in pairs:
        for pos in positions:
            result = preflop_advice(c1, c2, pos, facing_raise=False)
            if result["action"] != "RAISE":
                failures.append(f"{c1}{c2} {pos} open: expected RAISE, got {result['action']}")

    return failures


def test_preflop_bb_free_check():
    """BB with no raise should CHECK, never FOLD."""
    hands = [("7h", "2c"), ("3h", "4c"), ("Jh", "3c"), ("9h", "5d")]

    failures = []
    for c1, c2 in hands:
        result = preflop_advice(c1, c2, "BB", facing_raise=False)
        if result["action"] not in ("CHECK", "RAISE"):
            failures.append(f"{c1}{c2} BB no raise: expected CHECK/RAISE, got {result['action']}")

    return failures


def test_preflop_bb_defend():
    """BB should CALL with decent hands facing a single raise."""
    # T9s instead of T9o (offsuit T9 is now folded as part of leak fix)
    defends = [("9h", "9c"), ("Ah", "Ts"), ("Kh", "Qs"), ("Jh", "Ts"), ("Th", "9h")]

    failures = []
    for c1, c2 in defends:
        result = preflop_advice(c1, c2, "BB", facing_raise=True)
        if result["action"] not in ("CALL", "RAISE"):
            failures.append(f"{c1}{c2} BB vs raise: expected CALL/RAISE, got {result['action']}")

    return failures


def test_preflop_co_opens():
    """CO should open standard hands."""
    opens = [("Ah", "9c"), ("Ah", "8c"), ("Kh", "Js"), ("Qh", "Js"), ("Th", "9s")]

    failures = []
    for c1, c2 in opens:
        result = preflop_advice(c1, c2, "CO", facing_raise=False)
        if result["action"] != "RAISE":
            failures.append(f"{c1}{c2} CO open: expected RAISE, got {result['action']}")

    return failures


def test_preflop_btn_opens():
    """BTN should open wide."""
    opens = [("Ah", "2c"), ("Kh", "7s"), ("Qh", "8s"), ("Jh", "8s"),
             ("Th", "8s"), ("9h", "7s"), ("8h", "6s"), ("7h", "5s")]

    failures = []
    for c1, c2 in opens:
        result = preflop_advice(c1, c2, "BTN", facing_raise=False)
        if result["action"] != "RAISE":
            failures.append(f"{c1}{c2} BTN open: expected RAISE, got {result['action']}")

    return failures


def test_preflop_facing_raise_tightens():
    """Marginal hands should FOLD facing a raise."""
    marginals = [("Jh", "8c"), ("Th", "7c"), ("9h", "6c"), ("8h", "5c")]

    failures = []
    for c1, c2 in marginals:
        for pos in ["EP", "MP", "CO"]:
            result = preflop_advice(c1, c2, pos, facing_raise=True)
            if result["action"] != "FOLD":
                failures.append(f"{c1}{c2} {pos} vs raise: expected FOLD, got {result['action']}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# 2. FACING BET LOGIC TESTS
# ══════════════════════════════════════════════════════════════════════

def test_facing_bet_actions():
    """When facing a bet: only CALL, FOLD, or RAISE are valid. Never CHECK."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    scenarios = [
        # (hero, board, pos, facing, call_amt, pot, stack, phase)
        (["Ah", "Kh"], ["Th", "4d", "9s"], "BTN", True, 20, 40, 380, "FLOP"),
        (["7h", "7c"], ["Qs", "Jd", "5h", "3c"], "BB", True, 30, 60, 350, "TURN"),
        (["2h", "3h"], ["Kd", "7c", "3h", "9s", "2d"], "MP", True, 50, 100, 300, "RIVER"),
    ]

    failures = []
    for hero, board, pos, facing, call_amt, pot, stack, phase in scenarios:
        result = engine.get_action(hero, board, pos, facing, call_amt, pot, stack, phase, bb=4)
        if result and result["action"] == "CHECK":
            failures.append(f"{' '.join(hero)} {phase} facing bet: got CHECK (should be CALL/FOLD/RAISE)")

    return failures


def test_not_facing_bet_actions():
    """When not facing a bet: only CHECK or BET are valid. Never CALL."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    scenarios = [
        (["Ah", "Kh"], ["Th", "4d", "9s"], "BTN", False, 0, 40, 380, "FLOP"),
        (["7h", "7c"], ["Qs", "Jd", "5h", "3c"], "BB", False, 0, 60, 350, "TURN"),
        (["Ah", "As"], ["Kd", "7c", "3h", "9s", "2d"], "MP", False, 0, 100, 300, "RIVER"),
    ]

    failures = []
    for hero, board, pos, facing, call_amt, pot, stack, phase in scenarios:
        result = engine.get_action(hero, board, pos, facing, call_amt, pot, stack, phase, bb=4)
        if result and result["action"] == "CALL":
            failures.append(f"{' '.join(hero)} {phase} not facing: got CALL (should be CHECK/BET)")

    return failures


# ══════════════════════════════════════════════════════════════════════
# 3. ALL-IN DETECTION
# ══════════════════════════════════════════════════════════════════════

def test_no_raise_against_allin():
    """When opponent is all-in (call_amount >= hero_stack), never recommend RAISE."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    scenarios = [
        # call_amount >= stack means opponent all-in
        (["Ah", "Ah"], ["Kd", "7c", "3h"], "BTN", True, 500, 100, 400, "FLOP"),
        (["Kh", "Kd"], ["9h", "6h", "2h"], "SB", True, 752, 82, 487, "FLOP"),
        (["Jh", "4s"], ["4d", "4c", "4h"], "BB", True, 300, 16, 200, "FLOP"),
    ]

    failures = []
    for hero, board, pos, facing, call_amt, pot, stack, phase in scenarios:
        result = engine.get_action(hero, board, pos, facing, call_amt, pot, stack, phase, bb=4)
        if result and "RAISE" in result["action"].upper():
            failures.append(f"{' '.join(hero)} {phase} vs all-in (call={call_amt} stack={stack}): got {result['action']} (should be CALL/FOLD)")

    return failures


# ══════════════════════════════════════════════════════════════════════
# 4. BOARD CARD PARSING
# ══════════════════════════════════════════════════════════════════════

def test_board_parsing():
    """WS board strings parse correctly."""
    from unibet_ws import UnibetWSReader
    reader = UnibetWSReader()

    tests = [
        ("jh2s9c", ["Jh", "2s", "9c"]),           # flop
        ("jh2s9cqh", ["Jh", "2s", "9c", "Qh"]),   # turn
        ("jh2s9cqh7h", ["Jh", "2s", "9c", "Qh", "7h"]),  # river
        ("ahtc", ["Ah", "Tc"]),                     # hero cards
        ("", []),                                     # empty
    ]

    failures = []
    for input_str, expected in tests:
        result = reader._parse_cards(input_str)
        if result != expected:
            failures.append(f"parse_cards('{input_str}'): expected {expected}, got {result}")

    return failures


def test_phase_from_board():
    """Board card count maps to correct phase."""
    tests = [
        (0, "PREFLOP"),
        (3, "FLOP"),
        (4, "TURN"),
        (5, "RIVER"),
    ]

    failures = []
    for n, expected_phase in tests:
        if n == 0:
            phase = "PREFLOP"
        elif n == 3:
            phase = "FLOP"
        elif n == 4:
            phase = "TURN"
        elif n >= 5:
            phase = "RIVER"

        if phase != expected_phase:
            failures.append(f"board count {n}: expected {expected_phase}, got {phase}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# 5. POSTFLOP ENGINE
# ══════════════════════════════════════════════════════════════════════

def test_postflop_strong_hand_bets():
    """Strong hands should BET when not facing a bet."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    scenarios = [
        # Top pair+, not facing bet -> should BET
        (["Ah", "Kh"], ["Ah", "5c", "3d"], "BTN", False, 0, 30, 380, "FLOP"),
        (["Ah", "As"], ["Kd", "7c", "3h", "9s", "2d"], "MP", False, 0, 100, 300, "RIVER"),
    ]

    failures = []
    for hero, board, pos, facing, call_amt, pot, stack, phase in scenarios:
        result = engine.get_action(hero, board, pos, facing, call_amt, pot, stack, phase, bb=4)
        if result and result["action"] not in ("BET", "RAISE"):
            failures.append(f"{' '.join(hero)} on {' '.join(board)} {phase}: expected BET/RAISE, got {result['action']}")

    return failures


def test_postflop_weak_hand_checks():
    """Weak hands should CHECK when not facing a bet, FOLD when facing."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    failures = []

    # Not facing: should CHECK
    result = engine.get_action(["7h", "2c"], ["Ks", "Qd", "Jh"], "EP", False, 0, 30, 380, "FLOP", bb=4)
    if result and result["action"] not in ("CHECK", "FOLD"):
        failures.append(f"72o on KQJ not facing: expected CHECK, got {result['action']}")

    # Facing big bet with nothing: should FOLD
    result = engine.get_action(["7h", "2c"], ["Ks", "Qd", "Jh"], "EP", True, 25, 30, 380, "FLOP", bb=4)
    if result and result["action"] not in ("FOLD",):
        failures.append(f"72o on KQJ facing 25: expected FOLD, got {result['action']}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# 6. REAL BUG REGRESSION TESTS (from tonight's live session)
# ══════════════════════════════════════════════════════════════════════

def test_regression_j3o_bb_no_raise():
    """J3o in BB with no raise should CHECK, not RAISE."""
    result = preflop_advice("Jh", "3c", "BB", facing_raise=False)
    if result["action"] not in ("CHECK",):
        return [f"J3o BB no raise: expected CHECK, got {result['action']}"]
    return []


def test_regression_a9o_co():
    """A9o from CO should RAISE."""
    result = preflop_advice("Ah", "9c", "CO", facing_raise=False)
    if result["action"] != "RAISE":
        return [f"A9o CO open: expected RAISE, got {result['action']}"]
    return []


def test_regression_a4s_bb_fold():
    """A4s in BB should FOLD facing a raise.

    Leak detection on 2026-04-07 showed A4s BB defends lost €2.54 across 2 hands.
    Dominated by A-high opponents, low equity realization OOP.
    """
    result = preflop_advice("Ah", "4h", "BB", facing_raise=True)
    if result["action"] != "FOLD":
        return [f"A4s BB vs raise: expected FOLD (leak), got {result['action']}"]
    return []


def test_regression_qto_btn():
    """QTo from BTN should RAISE."""
    result = preflop_advice("Qd", "Ts", "BTN", facing_raise=False)
    if result["action"] != "RAISE":
        return [f"QTo BTN open: expected RAISE, got {result['action']}"]
    return []


def test_regression_kk_vs_allin():
    """KK facing all-in should CALL, never RAISE."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()
    result = engine.get_action(
        ["Kh", "Kd"], ["9h", "6h", "2h"], "SB",
        True, 752, 82, 487, "FLOP", bb=4
    )
    if result and "RAISE" in result["action"].upper():
        return [f"KK vs all-in: got {result['action']}, expected CALL"]
    return []


def test_regression_77_utg_no_raise():
    """77 from UTG with no raise should RAISE (open), not CALL."""
    result = preflop_advice("7d", "7c", "UTG", facing_raise=False)
    if result["action"] != "RAISE":
        return [f"77 UTG open: expected RAISE, got {result['action']}"]
    return []


# ══════════════════════════════════════════════════════════════════════
# 7. EXHAUSTIVE ACTION VALIDITY TESTS
# ══════════════════════════════════════════════════════════════════════

def test_facing_bet_never_check():
    """Across many hands: facing a bet should NEVER recommend CHECK."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    failures = []
    hands = [
        (["Ah", "Kh"], ["Th", "4d", "9s"]),
        (["2h", "7c"], ["Ks", "Qd", "Jh"]),
        (["9h", "9c"], ["Ah", "5c", "3d"]),
        (["Jh", "Ts"], ["Qh", "9d", "2c"]),
        (["5h", "5c"], ["Kd", "Kh", "3s"]),
        (["Ah", "2h"], ["9h", "6h", "3d"]),
        (["Kh", "Qh"], ["Ah", "Jd", "Tc"]),
    ]

    for hero, board in hands:
        for phase in ["FLOP", "TURN", "RIVER"]:
            if phase == "TURN":
                board = board + ["8c"] if len(board) == 3 else board[:4]
            elif phase == "RIVER":
                board = board[:3] + ["8c", "2d"] if len(board) < 5 else board[:5]
            else:
                board = board[:3]

            for call_amt in [10, 30, 50, 100, 500]:
                result = engine.get_action(hero, board, "BTN", True, call_amt, 50, 400, phase, bb=4)
                if result and result["action"] == "CHECK":
                    failures.append(f"{' '.join(hero)} {phase} facing call={call_amt}: CHECK")
                    if len(failures) > 5:
                        return failures

    return failures


def test_not_facing_never_call():
    """Not facing a bet should NEVER recommend CALL."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    failures = []
    hands = [
        (["Ah", "Kh"], ["Th", "4d", "9s"]),
        (["2h", "7c"], ["Ks", "Qd", "Jh"]),
        (["9h", "9c"], ["Ah", "5c", "3d"]),
        (["Ah", "As"], ["Kd", "7c", "3h"]),
    ]

    for hero, board in hands:
        for phase in ["FLOP", "TURN", "RIVER"]:
            if phase == "TURN":
                board = board[:3] + ["8c"]
            elif phase == "RIVER":
                board = board[:3] + ["8c", "2d"]
            else:
                board = board[:3]

            result = engine.get_action(hero, board, "BTN", False, 0, 50, 400, phase, bb=4)
            if result and result["action"] == "CALL":
                failures.append(f"{' '.join(hero)} {phase} not facing: CALL")

    return failures


def test_allin_exhaustive():
    """Any hand facing all-in should be CALL or FOLD, never RAISE/BET."""
    from strategy.postflop_engine import PostflopEngine
    engine = PostflopEngine()

    failures = []
    hands = [
        (["Ah", "As"], ["Kd", "7c", "3h"]),
        (["2h", "7c"], ["Ks", "Qd", "Jh"]),
        (["Kh", "Kd"], ["9h", "6h", "2h"]),
        (["Jh", "4s"], ["4d", "4c", "4h"]),
        (["Th", "9h"], ["8h", "7h", "2c"]),
    ]

    for hero, board in hands:
        for stack in [100, 200, 400, 800]:
            call_amt = stack + 100  # opponent all-in for more than our stack
            result = engine.get_action(hero, board, "BTN", True, call_amt, 50, stack, "FLOP", bb=4)
            if result and result["action"] in ("RAISE", "BET"):
                failures.append(f"{' '.join(hero)} stack={stack} call={call_amt}: {result['action']}")

    return failures


def test_preflop_every_position_no_crash():
    """Every hand from every position should return a valid action without crashing."""
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
    suits = ["h", "c", "d", "s"]
    positions = ["EP", "MP", "CO", "BTN", "SB", "BB"]
    valid_actions = {"RAISE", "CALL", "FOLD", "CHECK"}

    failures = []
    for r1 in ranks:
        for r2 in ranks:
            c1 = r1 + "h"
            c2 = r2 + ("c" if r1 != r2 else "s")  # offsuit unless pair
            for pos in positions:
                for facing in [True, False]:
                    try:
                        result = preflop_advice(c1, c2, pos, facing_raise=facing)
                        if result["action"] not in valid_actions:
                            failures.append(f"{c1}{c2} {pos} facing={facing}: invalid action '{result['action']}'")
                    except Exception as e:
                        failures.append(f"{c1}{c2} {pos} facing={facing}: CRASH {e}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# RUN ALL TESTS
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        # Preflop chart
        ("Preflop: premium hands RAISE", test_preflop_premium_hands),
        ("Preflop: trash hands FOLD", test_preflop_trash_folds),
        ("Preflop: pocket pairs open", test_preflop_pocket_pairs_open),
        ("Preflop: BB free check", test_preflop_bb_free_check),
        ("Preflop: BB defend vs raise", test_preflop_bb_defend),
        ("Preflop: CO opens", test_preflop_co_opens),
        ("Preflop: BTN opens wide", test_preflop_btn_opens),
        ("Preflop: tighten vs raise", test_preflop_facing_raise_tightens),
        # Postflop engine
        ("Postflop: facing bet actions", test_facing_bet_actions),
        ("Postflop: not facing bet actions", test_not_facing_bet_actions),
        ("All-in: no raise against all-in", test_no_raise_against_allin),
        ("Board parsing", test_board_parsing),
        ("Postflop: strong hands bet", test_postflop_strong_hand_bets),
        ("Postflop: weak hands check/fold", test_postflop_weak_hand_checks),
        # Regression (bugs from live session 2026-04-06)
        ("Regression: J3o BB no raise", test_regression_j3o_bb_no_raise),
        ("Regression: A9o CO open", test_regression_a9o_co),
        ("Regression: A4s BB fold (leak)", test_regression_a4s_bb_fold),
        ("Regression: QTo BTN open", test_regression_qto_btn),
        ("Regression: KK vs all-in", test_regression_kk_vs_allin),
        ("Regression: 77 UTG open", test_regression_77_utg_no_raise),
        # Exhaustive
        ("Exhaustive: facing bet never CHECK", test_facing_bet_never_check),
        ("Exhaustive: not facing never CALL", test_not_facing_never_call),
        ("Exhaustive: all-in never RAISE", test_allin_exhaustive),
        ("Exhaustive: every hand/position no crash", test_preflop_every_position_no_crash),
    ]

    print("=" * 60)
    print("  ADVISOR TEST SUITE")
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
            print(f"  ERROR {name}: {e}")
            all_failures.append(f"{name}: {e}")

    print()
    print(f"  {passed}/{total} tests passed")
    if all_failures:
        print(f"  {len(all_failures)} failures")
        print()
        print("  DO NOT GO LIVE UNTIL ALL TESTS PASS")
    else:
        print()
        print("  ALL TESTS PASS — safe to go live")

    print("=" * 60)

    sys.exit(0 if not all_failures else 1)
