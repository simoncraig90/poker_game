"""
Tests using REAL captured WS data from tonight's live session.
Replays exact messages and verifies the full advisor produces correct recommendations.
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from unibet_ws import UnibetWSReader
from preflop_chart import preflop_advice
from strategy.postflop_engine import PostflopEngine


# ══════════════════════════════════════════════════════════════════════
# Real captured data from 2026-04-06 session
# ══════════════════════════════════════════════════════════════════════

# Format from CDP capture:
# Hero cards: {"payLoad": {"p": ["handid", 1, 0, "cardstr", "desc", null], "hid": "handid"}}
# State: {"payLoad": {"c": [players, states, stacks, bets, pot, null, null, board, ...], "hid": "handid"}}

REAL_PLAYERS = "Grizzly12|Skurj_uni41|Enotsdrib|Quiwee|Aziz21|m0r0t"

def make_msg(payload_dict):
    return f'<body>{json.dumps({"payLoad": payload_dict})}</body>'


class AdvisorRecorder:
    """Simulates the advisor callback, records what it would recommend."""
    def __init__(self):
        self.engine = PostflopEngine()
        self.recommendations = []

    def on_state(self, state):
        hero = state["hero_cards"]
        board = state["board_cards"]
        phase = state["phase"]
        facing = state["facing_bet"]
        call_amt = state["call_amount"]
        pot = state["pot"]
        pos = state.get("position", "MP")
        stack = state.get("hero_stack", 1000)

        if len(hero) < 2:
            return

        rec = {"hero": hero, "board": board, "phase": phase, "facing": facing,
               "call_amount": call_amt, "pot": pot, "position": pos, "stack": stack}

        if phase == "PREFLOP":
            pf = preflop_advice(hero[0], hero[1], pos, facing_raise=facing)
            rec["action"] = pf["action"]
            rec["source"] = "chart"
        else:
            result = self.engine.get_action(hero, board, pos, facing, call_amt, pot, stack, phase, bb=4)
            if result:
                rec["action"] = result["action"]
                rec["source"] = result.get("source", "engine")
            else:
                rec["action"] = "CHECK"
                rec["source"] = "default"

        self.recommendations.append(rec)


def replay_hand(messages, wait=0.4):
    """Replay a sequence of WS messages, return advisor recommendations."""
    reader = UnibetWSReader()
    advisor = AdvisorRecorder()
    reader.on_state_change(advisor.on_state)

    for msg in messages:
        reader._parse_message(make_msg(msg))
        time.sleep(wait)

    return advisor.recommendations


# ══════════════════════════════════════════════════════════════════════
# Test: Hand 1 — Ah Tc, flop Jh 2s 9c, turn Qh, river 7h
# Hero folded preflop. Board should still be detected.
# ══════════════════════════════════════════════════════════════════════

def test_real_hand_ahtc():
    """Real hand: Ah Tc from captured data. Verify board detection through all streets."""
    msgs = [
        # Hero cards
        {"p": ["cg8e2029", 1, 0, "ahtc", "High card, Ace", None], "hid": "cg8e2029"},
        # State with flop
        {"c": [REAL_PLAYERS, [3,1,3,3,1,3], [224,393,483,400,160,400],
               [0,8,0,0,8,0], [[9,1]], None, None, "jh2s9c", 5, 26, 0,0,0,2,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "cg8e2029"},
        # Turn
        {"c": [REAL_PLAYERS, [3,1,3,3,1,3], [224,393,483,400,160,400],
               [0,0,0,0,0,0], [[25,1]], None, None, "jh2s9cqh", 5, 27, 0,0,0,3,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "cg8e2029"},
        # River
        {"c": [REAL_PLAYERS, [3,1,3,3,1,3], [224,393,483,400,160,400],
               [0,0,0,0,0,0], [[25,1]], None, None, "jh2s9cqh7h", 5, 28, 0,0,0,3,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "cg8e2029"},
    ]

    recs = replay_hand(msgs)
    failures = []

    # Should have recommendations
    if not recs:
        return ["No recommendations generated"]

    # Check board was detected at each street
    flop_recs = [r for r in recs if r["phase"] == "FLOP"]
    turn_recs = [r for r in recs if r["phase"] == "TURN"]
    river_recs = [r for r in recs if r["phase"] == "RIVER"]

    if not flop_recs:
        failures.append("No FLOP recommendation generated")
    elif flop_recs[0]["board"] != ["Jh", "2s", "9c"]:
        failures.append(f"Flop board: expected Jh 2s 9c, got {flop_recs[0]['board']}")

    if not turn_recs:
        failures.append("No TURN recommendation generated")
    elif len(turn_recs[0]["board"]) != 4:
        failures.append(f"Turn board: expected 4 cards, got {len(turn_recs[0]['board'])}")

    if not river_recs:
        failures.append("No RIVER recommendation generated")
    elif len(river_recs[0]["board"]) != 5:
        failures.append(f"River board: expected 5 cards, got {len(river_recs[0]['board'])}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# Test: Hand 2 — 6c 5h, folded preflop
# ══════════════════════════════════════════════════════════════════════

def test_real_hand_6c5h_fold():
    """Real hand: 6c 5h. Chart should say FOLD from most positions."""
    msgs = [
        {"p": ["cg8e2030", 1, 0, "6c5h", "High card, Six", None], "hid": "cg8e2030"},
        {"c": [REAL_PLAYERS, [1,1,1,1,1,1], [224,391,479,400,185,400],
               [0,2,4,0,0,0], [], None, None, None, 0, 1, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "cg8e2030"},
    ]

    recs = replay_hand(msgs)
    failures = []

    pf_recs = [r for r in recs if r["phase"] == "PREFLOP"]
    if not pf_recs:
        failures.append("No preflop recommendation")
    else:
        # 65o from non-BB position without raise should be FOLD
        r = pf_recs[0]
        if r["position"] not in ("BB",) and r["action"] not in ("FOLD",):
            failures.append(f"65o {r['position']}: expected FOLD, got {r['action']}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# Test: Facing bet on flop — must never show CHECK
# ══════════════════════════════════════════════════════════════════════

def test_real_facing_bet_flop():
    """Simulate opponent betting on flop. Advisor must show CALL/FOLD/RAISE, never CHECK."""
    msgs = [
        {"p": ["hand_fb1", 1, 0, "ksqh", "High card, King", None], "hid": "hand_fb1"},
        # Flop: Ah 7c 3d, no bet yet
        {"c": [REAL_PLAYERS, [1,1,3,3,3,3], [1000,1000,1000,1000,1000,1000],
               [0,0,0,0,0,0], [[20,1]], None, None, "ah7c3d", 5, 1, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "hand_fb1"},
        # Opponent bets 15
        {"c": [REAL_PLAYERS, [1,1,3,3,3,3], [985,1000,1000,1000,1000,1000],
               [15,0,0,0,0,0], [[35,1]], None, None, "ah7c3d", 5, 2, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "hand_fb1"},
    ]

    recs = replay_hand(msgs)
    failures = []

    # Find recommendation after opponent bet
    facing_recs = [r for r in recs if r["facing"] and r["call_amount"] > 0]
    for r in facing_recs:
        if r["action"] == "CHECK":
            failures.append(f"Facing bet of {r['call_amount']} on {r['phase']}: got CHECK")

    if not facing_recs:
        failures.append("No recommendations while facing a bet")

    return failures


# ══════════════════════════════════════════════════════════════════════
# Test: Facing bet resets between streets
# ══════════════════════════════════════════════════════════════════════

def test_real_facing_bet_reset():
    """After preflop raise, flop should start with facing_bet=False."""
    msgs = [
        {"p": ["hand_fbr", 1, 0, "ahkh", "High card, Ace", None], "hid": "hand_fbr"},
        # Preflop: villain raises to 12
        {"c": [REAL_PLAYERS, [1,1,1,1,1,1], [988,1000,1000,1000,998,996],
               [12,0,0,0,2,4], [], None, None, None, 0, 1, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "hand_fbr"},
        # Flop: Td 5c 2h, no bets
        {"c": [REAL_PLAYERS, [1,1,3,3,3,3], [988,988,1000,1000,998,996],
               [0,0,0,0,0,0], [[24,1]], None, None, "td5c2h", 5, 5, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "hand_fbr"},
    ]

    recs = replay_hand(msgs)
    failures = []

    flop_recs = [r for r in recs if r["phase"] == "FLOP"]
    for r in flop_recs:
        if r["facing"]:
            failures.append(f"Flop with no bets: facing_bet should be False, got True")
        if r["action"] == "CALL":
            failures.append(f"Flop with no bets: action should not be CALL, got {r['action']}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# Test: All-in scenario from tonight (KK vs all-in)
# ══════════════════════════════════════════════════════════════════════

def test_real_kk_allin():
    """KK on 9h6h2h facing all-in of 752. Must CALL not RAISE."""
    msgs = [
        # Hero cards
        {"p": ["hand_kk", 1, 0, "khkd", "Pair of Kings", None], "hid": "hand_kk"},
        # Preflop state first (to set hero_seat from player names)
        {"c": [REAL_PLAYERS, [1,1,1,1,1,1], [500,487,1000,1000,1000,1000],
               [0,0,4,0,2,4], [], None, None, None, 0, 1, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "hand_kk"},
        # Flop: 9h 6h 2h, opponent all-in for 752
        {"c": [REAL_PLAYERS, [1,1,3,3,3,3], [0,487,1000,1000,1000,1000],
               [752,0,0,0,0,0], [[834,1]], None, None, "9h6h2h", 5, 5, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "hand_kk"},
    ]

    recs = replay_hand(msgs)
    failures = []

    flop_recs = [r for r in recs if r["phase"] == "FLOP" and r["facing"]]
    for r in flop_recs:
        if "RAISE" in r["action"].upper() or "BET" in r["action"].upper():
            failures.append(f"KK vs all-in: got {r['action']}, should be CALL or FOLD")

    if not flop_recs:
        failures.append("No recommendation when facing all-in")

    return failures


# ══════════════════════════════════════════════════════════════════════
# Test: Multiple hands in sequence (hand changes detected)
# ══════════════════════════════════════════════════════════════════════

def test_real_hand_sequence():
    """Two hands in sequence. Second hand should not carry state from first."""
    msgs = [
        # Hand 1: 7h 2c, fold
        {"p": ["h1", 1, 0, "7h2c", "High card, Seven", None], "hid": "h1"},
        {"c": [REAL_PLAYERS, [1,1,1,1,1,1], [1000]*6,
               [0,0,4,0,2,4], [], None, None, None, 0, 1, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "h1"},
        # Hero folds
        {"c": [REAL_PLAYERS, [1,3,1,1,1,1], [1000]*6,
               [0,0,4,0,2,4], [], None, None, None, 0, 2, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "h1"},
        # Hand 2: Ah As
        {"p": ["h2", 1, 0, "ahas", "Pair of Aces", None], "hid": "h2"},
        {"c": [REAL_PLAYERS, [1,1,1,1,1,1], [1000]*6,
               [0,0,4,0,2,4], [], None, None, None, 0, 1, 0,0,0,0,
               [609,835,910,1040,1012,581], 4, [0]*6, 2], "hid": "h2"},
    ]

    recs = replay_hand(msgs)
    failures = []

    # AA hand should get RAISE
    aa_recs = [r for r in recs if r["hero"] == ["Ah", "As"]]
    if not aa_recs:
        failures.append("No recommendation for AA hand")
    else:
        if aa_recs[0]["action"] != "RAISE":
            failures.append(f"AA preflop: expected RAISE, got {aa_recs[0]['action']}")
        # Should not carry board from hand 1
        if aa_recs[0]["board"]:
            failures.append(f"AA hand has stale board: {aa_recs[0]['board']}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# Test: Verify every logged hand from tonight gets valid action
# ══════════════════════════════════════════════════════════════════════

def test_replay_session_logs():
    """Replay every hand from tonight's session logs and verify valid actions."""
    import glob
    failures = []
    hands_checked = 0

    # Only validate sessions after the fixes were applied (after 20:32)
    session_files = sorted(glob.glob("vision/data/session_20260406_*.jsonl"))
    # Filter to sessions after fixes
    session_files = [f for f in session_files if "2032" <= os.path.basename(f).split("_")[1][:4] or
                     "2033" <= os.path.basename(f).split("_")[1][:4]]
    if not session_files:
        session_files = sorted(glob.glob("vision/data/session_20260406_*.jsonl"))[-2:]  # last 2
    for f in session_files:
        for line in open(f):
            try:
                h = json.loads(line)
            except:
                continue

            hero = h.get("hero", [])
            pos = h.get("position", "MP")
            if len(hero) < 2:
                continue

            for s in h.get("streets", []):
                phase = s.get("phase", "")
                facing = s.get("facing_bet", False)
                call_amt = s.get("call_amount", 0)
                rec = s.get("rec_action", "")

                if not rec:
                    continue

                hands_checked += 1

                # Validate: no CHECK when facing bet
                if facing and call_amt > 0 and "CHECK" in rec.upper() and "FOLD" not in rec.upper():
                    failures.append(f"{' '.join(hero)} {pos} {phase}: CHECK facing bet of {call_amt}")

                # Validate: no bare CALL when not facing bet
                # "CHECK / CALL" is a valid composite action (check, call if bet)
                if not facing and call_amt == 0 and rec.upper().startswith("CALL"):
                    failures.append(f"{' '.join(hero)} {pos} {phase}: CALL with no bet")

    if hands_checked == 0:
        failures.append("No hands found in session logs to validate")

    return failures


# ══════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("Real: Ah Tc full board detection", test_real_hand_ahtc),
        ("Real: 6c 5h fold", test_real_hand_6c5h_fold),
        ("Real: facing bet on flop", test_real_facing_bet_flop),
        ("Real: facing bet resets between streets", test_real_facing_bet_reset),
        ("Real: KK vs all-in", test_real_kk_allin),
        ("Real: hand sequence (no stale state)", test_real_hand_sequence),
        ("Real: validate all session log entries", test_replay_session_logs),
    ]

    print("=" * 60)
    print("  REAL DATA TEST SUITE")
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
    else:
        print("  ALL REAL DATA TESTS PASS")
    print("=" * 60)

    sys.exit(0 if not all_failures else 1)
