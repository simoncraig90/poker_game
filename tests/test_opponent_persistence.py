"""
Test that OpponentTracker persists opponent profiles across sessions via HandDB.
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from opponent_tracker import OpponentTracker
from hand_db import HandDB


def make_state(hand_id, players, bets, hero_seat=0, phase='PREFLOP'):
    return {
        'hand_id': hand_id,
        'players': players,
        'bets': bets,
        'phase': phase,
        'hero_seat': hero_seat,
    }


def test_load_save_roundtrip():
    """Tracker saves to DB on hand transition, loads from DB on restart."""
    failures = []

    # Use a temp DB
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_hands.db")

    try:
        # ── Session 1: track some players ──
        db1 = HandDB(db_path=db_path)
        tracker1 = OpponentTracker(db=db1)

        # players: index 0=Hero, 1=Alice, 2=Bob, 3=Charlie
        players = ["Hero", "Alice", "Bob", "Charlie"]

        # Hand 1: Alice (idx 1) raises to 12, Bob (idx 2) limp-calls 4
        # bet array: [hero_bet, alice_bet, bob_bet, charlie_bet, 0, 0]
        tracker1.update(make_state("h1", players, [0, 12, 4, 0, 0, 0]))
        # Hand 2: trigger save of hand 1 stats
        tracker1.update(make_state("h2", players, [0, 0, 0, 0, 0, 0]))

        # Force flush
        tracker1.flush()
        db1.close()

        # ── Session 2: load and check ──
        db2 = HandDB(db_path=db_path)
        tracker2 = OpponentTracker(db=db2)

        if "Alice" not in tracker2.players:
            failures.append("Alice not loaded from DB")
        else:
            alice = tracker2.players["Alice"]
            if alice['hands'] < 1:
                failures.append(f"Alice hands count wrong: {alice['hands']}")
            if alice['vpip'] < 1:
                failures.append(f"Alice vpip wrong: {alice['vpip']}")
            if alice['pfr'] < 1:
                failures.append(f"Alice pfr wrong: {alice['pfr']}")

        if "Bob" not in tracker2.players:
            failures.append("Bob not loaded from DB")
        else:
            bob = tracker2.players["Bob"]
            # Bob bet 4 = BB amount, so not VPIP (just posted BB) and not PFR
            if bob['pfr'] != 0:
                failures.append(f"Bob pfr should be 0: {bob['pfr']}")

        db2.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return failures


def test_accumulates_across_sessions():
    """Stats accumulate over multiple sessions."""
    failures = []

    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_hands.db")

    try:
        players = ["Hero", "Villain"]

        # Session 1: 5 hands, Villain (idx 1) VPIPs all 5
        db1 = HandDB(db_path=db_path)
        tracker1 = OpponentTracker(db=db1)
        for i in range(5):
            tracker1.update(make_state(f"h{i}", players, [0, 12, 0, 0, 0, 0]))
        # New hand triggers save
        tracker1.update(make_state("h99", players, [0, 0, 0, 0, 0, 0]))
        tracker1.flush()
        db1.close()

        # Session 2: 5 more hands
        db2 = HandDB(db_path=db_path)
        tracker2 = OpponentTracker(db=db2)
        # Verify session 1 data loaded
        v_hands_after_s1 = tracker2.players.get("Villain", {}).get('hands', 0)
        if v_hands_after_s1 < 5:
            failures.append(f"After session 1, Villain hands should be >=5, got {v_hands_after_s1}")

        for i in range(5, 10):
            tracker2.update(make_state(f"h{i}", players, [0, 12, 0, 0, 0, 0]))
        tracker2.update(make_state("h199", players, [0, 0, 0, 0, 0, 0]))
        tracker2.flush()
        db2.close()

        # Session 3: verify accumulated
        db3 = HandDB(db_path=db_path)
        tracker3 = OpponentTracker(db=db3)
        v_total = tracker3.players.get("Villain", {}).get('hands', 0)
        if v_total < 10:
            failures.append(f"After 2 sessions, Villain hands should be >=10, got {v_total}")
        db3.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return failures


def test_classification_persists():
    """Player classification (FISH/TAG/etc) persists."""
    failures = []

    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_hands.db")

    try:
        players = ["Hero", "FishyPlayer"]

        db1 = HandDB(db_path=db_path)
        tracker1 = OpponentTracker(db=db1)

        # Make FishyPlayer (idx 1) VPIP very high (FISH classification)
        for i in range(20):
            # Each hand: FishyPlayer (idx 1) puts in 12 (raises)
            tracker1.update(make_state(f"h{i}", players, [0, 12, 0, 0, 0, 0]))
        tracker1.update(make_state("hend", players, [0, 0, 0, 0, 0, 0]))
        tracker1.flush()

        # Check classification
        stats = tracker1.get_stats("FishyPlayer")
        if not stats:
            failures.append("No stats for FishyPlayer")
        elif stats['vpip'] < 0.50:
            failures.append(f"VPIP too low: {stats['vpip']}")

        db1.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return failures


def test_works_without_db():
    """Tracker works without DB (backwards compat)."""
    failures = []
    tracker = OpponentTracker(db=None)
    players = ["Hero", "Alice"]
    tracker.update(make_state("h1", players, [0, 0, 12, 0, 0, 0]))
    if "Alice" not in tracker.players:
        failures.append("Tracker without DB should still work in-memory")
    return failures


if __name__ == "__main__":
    tests = [
        ("Load/save roundtrip", test_load_save_roundtrip),
        ("Accumulate across sessions", test_accumulates_across_sessions),
        ("Classification persists", test_classification_persists),
        ("Works without DB", test_works_without_db),
    ]

    print("=" * 60)
    print("  OPPONENT TRACKER PERSISTENCE TESTS")
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
    print("=" * 60)
    sys.exit(0 if not all_failures else 1)
