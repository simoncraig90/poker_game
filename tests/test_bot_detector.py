"""
Test bot detector identifies suspicious behavior.

Includes a simulation of:
- A real human player (random timing, varied sizing)
- A bot (constant timing, identical sizing)
"""

import os, sys, random, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from bot_detector import BotDetector


def simulate_human(detector, name, n=100):
    """Generate human-like action stream."""
    random.seed(hash(name))
    for _ in range(n):
        # Human reaction time: log-normal, 2-15 seconds typical
        prompt_t = time.time()
        detector.mark_action_prompt(prompt_t)
        # Sleep simulated by adding to action time
        reaction_s = random.lognormvariate(1.2, 0.6)
        action_t = prompt_t + reaction_s

        action_type = random.choice(['fold', 'fold', 'fold', 'call', 'raise'])
        amount = 0
        if action_type == 'raise':
            # Humans use varied sizes
            amount = random.choice([10, 12, 14, 16, 20, 25, 30])
            detector.record_open_raise(name, amount)
        detector.record_action(name, action_type, amount, timestamp=action_t)


def simulate_bot(detector, name, n=100):
    """Generate bot-like action stream — constant timing, identical sizing."""
    random.seed(hash(name) + 1)
    for _ in range(n):
        prompt_t = time.time()
        detector.mark_action_prompt(prompt_t)
        # Bot reaction: 500ms ±50ms
        reaction_s = 0.5 + random.gauss(0, 0.05)
        action_t = prompt_t + reaction_s

        action_type = random.choice(['fold', 'fold', 'fold', 'call', 'raise'])
        amount = 0
        if action_type == 'raise':
            # Bot always raises 2.5x BB = 10 cents
            amount = 10
            detector.record_open_raise(name, amount)
        detector.record_action(name, action_type, amount, timestamp=action_t)


def test_human_low_bot_score():
    failures = []
    d = BotDetector()
    simulate_human(d, "RealPlayer", n=100)
    result = d.score_player("RealPlayer")
    if result is None:
        return ["No score generated for human"]
    score, signals = result
    if score > 0.4:
        failures.append(f"Human scored too high: {score:.2f}, signals={signals}")
    return failures


def test_bot_high_score():
    failures = []
    d = BotDetector()
    simulate_bot(d, "BotPlayer", n=100)
    result = d.score_player("BotPlayer")
    if result is None:
        return ["No score for bot"]
    score, signals = result
    print(f"\n  Bot score after 100 actions: {score:.2f}")
    for s in signals:
        print(f"    - {s}")
    if score < 0.4:
        failures.append(f"Bot scored too low: {score:.2f}, signals={signals}")
    return failures


def test_table_bot_density():
    """Mixed table: 1 bot + 5 humans → density should be small, not zero."""
    failures = []
    d = BotDetector()
    simulate_bot(d, "Bot1", n=100)
    for i in range(5):
        simulate_human(d, f"Human{i}", n=100)

    density = d.table_bot_density(["Bot1"] + [f"Human{i}" for i in range(5)])
    if density == 0:
        failures.append("Bot not detected at all")
    if density >= 0.5:
        failures.append(f"Density too high: {density:.2f}")
    return failures


def test_no_score_below_threshold():
    d = BotDetector()
    d.MIN_ACTIONS_FOR_SCORE = 100
    simulate_human(d, "Newbie", n=10)
    if d.score_player("Newbie") is not None:
        return ["Should return None below threshold"]
    return []


def test_persistence_schema():
    """DB schema is created when db is provided."""
    import tempfile, shutil
    from hand_db import HandDB

    failures = []
    tmp = tempfile.mkdtemp()
    try:
        db = HandDB(db_path=os.path.join(tmp, "test.db"))
        d = BotDetector(db=db)
        simulate_bot(d, "Bot1", n=60)
        d.flush()
        # Verify table exists
        rows = db.conn.execute("SELECT name, total_actions FROM bot_stats").fetchall()
        if not rows:
            failures.append("No rows persisted")
        elif rows[0][0] != "Bot1":
            failures.append(f"Wrong row: {rows[0]}")
        db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


if __name__ == "__main__":
    tests = [
        ("Human low bot score", test_human_low_bot_score),
        ("Bot high score", test_bot_high_score),
        ("Table bot density", test_table_bot_density),
        ("No score below threshold", test_no_score_below_threshold),
        ("Persistence schema", test_persistence_schema),
    ]
    print("=" * 60)
    print("  BOT DETECTOR TESTS")
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
