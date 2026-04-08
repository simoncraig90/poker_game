"""
Test collusion detector — defensive only.

Includes a fun scenario: two attacker bots play 200 hands trying to soft-play
each other and dump chips. The detector should flag them.
"""

import os
import sys
import random
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from collusion_detector import CollusionDetector
from hand_db import HandDB


def test_basic_pair_increment():
    """Hands together count increments correctly."""
    cd = CollusionDetector()
    cd.hand_started("h1", ["Hero", "Alice", "Bob"])
    cd.hand_started("h2", ["Hero", "Alice", "Bob"])
    cd.hand_started("h3", ["Hero", "Alice", "Bob"])

    p = cd._get_pair("Alice", "Bob")
    if p.hands_together != 3:
        return [f"Expected 3 hands together, got {p.hands_together}"]
    return []


def test_no_score_below_threshold():
    """Pairs need MIN_HANDS_FOR_SCORE before scoring."""
    cd = CollusionDetector()
    cd.MIN_HANDS_FOR_SCORE = 10
    for i in range(5):
        cd.hand_started(f"h{i}", ["A", "B"])

    score = cd.score_pair("A", "B")
    if score is not None:
        return [f"Should return None below threshold, got {score}"]
    return []


def test_friendly_pair_low_score():
    """Two players who play normally have low coordination score."""
    cd = CollusionDetector()
    random.seed(42)

    for i in range(60):
        players = ["Hero", "Alice", "Bob", "Charlie"]
        cd.hand_started(f"h{i}", players)
        # Random actions
        cd.record_action("Alice", "raise")
        if random.random() < 0.1:
            cd.record_action("Bob", "3bet")  # Normal 3-bet rate ~10%
            cd.record_action("Alice", "call")
        else:
            if random.random() < 0.5:
                cd.record_action("Bob", "fold")
            else:
                cd.record_action("Bob", "call")
        # Random showdowns
        if random.random() < 0.2:
            cd.record_showdown(["Alice", "Bob"])

    result = cd.score_pair("Alice", "Bob")
    if result is None:
        return ["No score for normal pair after 60 hands"]
    score, signals = result
    if score > 0.4:
        return [f"Normal pair scored too high: {score:.2f} {signals}"]
    return []


def test_colluding_pair_high_score():
    """Two attacker bots that soft-play each other should score high."""
    cd = CollusionDetector()
    cd.MIN_HANDS_FOR_SCORE = 30

    # Simulate 100 hands where Alice and Bob never 3-bet each other
    # and rarely go to showdown together
    for i in range(100):
        players = ["Hero", "Alice", "Bob", "Charlie"]
        cd.hand_started(f"h{i}", players)
        # Alice raises
        cd.record_action("Alice", "raise")
        # Bob ALWAYS folds to Alice (collusion: never 3-bet, never call to fight)
        cd.record_action("Bob", "fold")
        # Bob raises in some hands
        if i % 3 == 0:
            cd.hand_started(f"h{i}_b", players)  # new hand
            cd.record_action("Bob", "raise")
            cd.record_action("Alice", "fold")  # Alice always folds to Bob
        # Almost never showdown together
        # (default = no showdown call)

    result = cd.score_pair("Alice", "Bob")
    if result is None:
        return ["No score for colluding pair"]
    score, signals = result
    if score < 0.5:
        return [f"Colluding pair scored too low: {score:.2f} {signals}"]
    return []


def test_chip_dumping_detected():
    """Chip flow consistently one direction should add to score."""
    cd = CollusionDetector()
    cd.MIN_HANDS_FOR_SCORE = 30

    # 60 hands where chips flow Alice → Bob (Alice keeps losing to Bob)
    for i in range(60):
        cd.hand_started(f"h{i}", ["Hero", "Alice", "Bob"])
        cd.record_action("Alice", "raise")
        cd.record_action("Bob", "call")
        # Bob wins ~10 cents from Alice every hand
        cd.record_chip_flow("Bob", ["Alice"], 10)

    result = cd.score_pair("Alice", "Bob")
    if result is None:
        return ["No score"]
    score, signals = result
    has_chip_signal = any("chip flow" in s for s in signals)
    if not has_chip_signal:
        return [f"Chip dumping not detected: {signals}"]
    return []


def test_table_score():
    """get_table_score returns max pair score and details."""
    cd = CollusionDetector()
    cd.MIN_HANDS_FOR_SCORE = 30

    # Make Alice/Bob colluders at this table
    for i in range(60):
        cd.hand_started(f"h{i}", ["Hero", "Alice", "Bob", "Charlie"])
        cd.record_action("Alice", "raise")
        cd.record_action("Bob", "fold")  # always folds to Alice
        cd.record_action("Charlie", "fold")

    max_score, details = cd.get_table_score(["Hero", "Alice", "Bob", "Charlie"])
    if max_score == 0:
        return ["Should detect Alice/Bob coordination"]
    if not details:
        return ["No details returned"]
    return []


def test_persistence():
    """Detector loads/saves to DB across sessions."""
    failures = []
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.db")

    try:
        # Session 1: track some pair stats
        db1 = HandDB(db_path=db_path)
        cd1 = CollusionDetector(db=db1)
        for i in range(40):
            cd1.hand_started(f"h{i}", ["A", "B"])
            cd1.record_action("A", "raise")
            cd1.record_action("B", "fold")
        cd1.flush()
        db1.close()

        # Session 2: load and check stats persisted
        db2 = HandDB(db_path=db_path)
        cd2 = CollusionDetector(db=db2)
        p = cd2.pairs.get(("A", "B"))
        if not p:
            failures.append("Pair not loaded from DB")
        elif p.hands_together < 40:
            failures.append(f"Expected 40+ hands together, got {p.hands_together}")
        elif p.a_raised_b_folded < 40:
            failures.append(f"Expected 40+ B folds to A, got {p.a_raised_b_folded}")
        db2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def test_attacker_bots_simulation():
    """
    Fun scenario: two attacker bots vs the strategy engine.
    Run 200 hands and verify the detector flags them.
    """
    failures = []
    cd = CollusionDetector()
    cd.MIN_HANDS_FOR_SCORE = 30

    # Strategy:
    # - Attacker A and B never raise each other
    # - When facing the engine (Hero), they coordinate squeezes
    # - They never showdown against each other
    # - A occasionally dumps chips to B

    random.seed(123)
    for i in range(200):
        cd.hand_started(f"h{i}", ["Hero", "AttackerA", "AttackerB", "Random1", "Random2"])

        # Action sequence varies
        roll = random.random()
        if roll < 0.3:
            # A raises preflop, Hero calls, B 3-bets to squeeze, Hero folds
            cd.record_action("AttackerA", "raise")
            cd.record_action("Hero", "call")
            cd.record_action("AttackerB", "3bet")  # squeeze!
            cd.record_action("AttackerA", "fold")  # A folds to B's 3-bet (soft play)
            cd.record_action("Hero", "fold")
        elif roll < 0.6:
            # B raises, A folds (always)
            cd.record_action("AttackerB", "raise")
            cd.record_action("AttackerA", "fold")
            cd.record_action("Hero", "fold")
        elif roll < 0.85:
            # A raises, B folds (always)
            cd.record_action("AttackerA", "raise")
            cd.record_action("AttackerB", "fold")
            cd.record_action("Hero", "fold")
        else:
            # Chip dumping: B "loses" to A
            cd.record_action("AttackerB", "raise")
            cd.record_action("AttackerA", "call")
            cd.record_chip_flow("AttackerA", ["AttackerB"], 50)

        # Almost never go to showdown together
        if random.random() < 0.01:
            cd.record_showdown(["AttackerA", "AttackerB"])

    # Check if attackers were flagged
    result = cd.score_pair("AttackerA", "AttackerB")
    if result is None:
        return ["No score generated for attacker pair"]

    score, signals = result
    print(f"\n  Attacker pair score after 200 hands: {score:.2f}")
    for s in signals:
        print(f"    - {s}")

    if score < 0.5:
        failures.append(f"Attacker score too low: {score:.2f}")

    # Should be marked as suspect
    if not cd.is_suspect("AttackerA", "AttackerB"):
        failures.append("Attackers should be marked as suspect")

    # Random pair should NOT be flagged
    rand_result = cd.score_pair("Random1", "Random2")
    if rand_result is not None and rand_result[0] > 0.4:
        failures.append(f"Random pair flagged with {rand_result[0]:.2f}")

    return failures


if __name__ == "__main__":
    tests = [
        ("Basic pair increment", test_basic_pair_increment),
        ("No score below threshold", test_no_score_below_threshold),
        ("Friendly pair low score", test_friendly_pair_low_score),
        ("Colluding pair high score", test_colluding_pair_high_score),
        ("Chip dumping detected", test_chip_dumping_detected),
        ("Table score", test_table_score),
        ("Persistence", test_persistence),
        ("Attacker bots simulation", test_attacker_bots_simulation),
    ]

    print("=" * 60)
    print("  COLLUSION DETECTOR TESTS")
    print("=" * 60)

    total = 0
    passed = 0
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
    if all_failures:
        print(f"  {len(all_failures)} failures")
    print("=" * 60)
    sys.exit(0 if not all_failures else 1)
