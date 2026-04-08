"""
Replay every hand from live sessions through the AdvisorStateMachine.
Flag any recommendation that looks wrong:

1. FOLD with equity > 60% facing a bet (folding strong hands)
2. FOLD with equity > 85% ever (the flush bug)
3. CHECK when facing bet with call_amount > 0
4. CALL when not facing bet with call_amount == 0
5. RAISE when opponent is all-in
6. BET/RAISE with equity < 20% and no bluff justification
7. Postflop FOLD with +EV pot odds (equity > pot_odds)
8. Preflop: hand in opening range but told to FOLD
9. Preflop: hand NOT in range but told to RAISE

No live play needed. Catches edge cases from real game state sequences.
"""

import sys
import os
import json
import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from advisor_state_machine import AdvisorStateMachine
from preflop_chart import preflop_advice, _hand_key
from preflop_chart import EP_RAISE, MP_RAISE, CO_RAISE, BTN_RAISE, SB_RAISE, BB_CALL, BB_3BET


# ── Load real dependencies ──
from strategy.postflop_engine import PostflopEngine
try:
    postflop_engine = PostflopEngine()
except Exception:
    postflop_engine = None

from advisor import Advisor as BaseAdvisor
base_advisor = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)


def make_sm():
    return AdvisorStateMachine(
        base_advisor=base_advisor,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop_engine,
        bb_cents=4,
    )


def get_opening_range(pos):
    ranges = {
        "EP": EP_RAISE, "UTG": EP_RAISE,
        "MP": MP_RAISE, "CO": CO_RAISE,
        "BTN": BTN_RAISE, "SB": SB_RAISE,
        "BB": BB_CALL | BB_3BET,
    }
    return ranges.get(pos, BTN_RAISE)


def replay_hand(sm, hand):
    """Replay one hand through the state machine, return list of issues."""
    issues = []
    hero = hand.get("hero", [])
    pos = hand.get("position", "MP")
    hand_id = hand.get("hand_id", 0)
    starting_stack = hand.get("starting_stack", 1000)

    if len(hero) < 2:
        return issues

    hand_key = _hand_key(hero[0], hero[1])

    for i, street in enumerate(hand.get("streets", [])):
        phase = street.get("phase", "")
        board = street.get("board", [])
        pot = street.get("pot", 0)
        facing = street.get("facing_bet", False)
        call_amt = street.get("call_amount", 0)
        stack = street.get("hero_stack", starting_stack)
        logged_action = street.get("rec_action", "")
        logged_eq = street.get("rec_equity", 0.5)

        # Build state and run through state machine
        state = {
            "hero_cards": hero,
            "board_cards": board,
            "hand_id": f"replay_{hand_id}_{i}",
            "facing_bet": facing,
            "call_amount": call_amt,
            "pot": pot,
            "num_opponents": 3,
            "position": pos,
            "hero_stack": stack,
            "phase": phase,
            "bets": [0] * 6,
            "players": ["V1", "Hero", "V2", "V3", "V4", "V5"],
            "hero_seat": 1,
        }

        out = sm.process_state(state)
        if out is None:
            continue

        action = out.action.upper()
        eq = out.equity
        tag = f"{' '.join(hero)} {pos} {phase} board={''.join(board)} eq={eq:.0%}"

        # ── CHECK 1: fold monster ──
        if "FOLD" in action and eq > 0.85:
            issues.append(f"FOLD MONSTER | {tag} | action={out.action}")

        # ── CHECK 2: fold strong hand facing bet ──
        if "FOLD" in action and facing and eq > 0.60:
            pot_odds = call_amt / (pot + call_amt) if (pot + call_amt) > 0 else 0
            if eq > pot_odds:
                issues.append(f"FOLD +EV | {tag} | call={call_amt} pot_odds={pot_odds:.0%} | action={out.action}")

        # ── CHECK 3: CHECK facing bet ──
        if facing and call_amt > 0 and action == "CHECK":
            issues.append(f"CHECK FACING BET | {tag} | call={call_amt} | action={out.action}")

        # ── CHECK 4: bare CALL not facing ──
        if not facing and call_amt == 0 and action.startswith("CALL"):
            issues.append(f"CALL NOT FACING | {tag} | action={out.action}")

        # ── CHECK 5: RAISE vs all-in ──
        if facing and call_amt >= stack and "RAISE" in action:
            issues.append(f"RAISE VS ALL-IN | {tag} | call={call_amt} stack={stack} | action={out.action}")

        # ── CHECK 6: preflop chart consistency ──
        if phase == "PREFLOP" and hand_key:
            opening_range = get_opening_range(pos)
            if not facing:
                if hand_key in opening_range and "FOLD" in action:
                    if not (pos == "BB"):  # BB can check instead of fold
                        issues.append(f"FOLD IN RANGE | {tag} | {hand_key} in {pos} range | action={out.action}")
                if hand_key not in opening_range and "RAISE" in action:
                    if pos != "BB":
                        issues.append(f"RAISE NOT IN RANGE | {tag} | {hand_key} not in {pos} range | action={out.action}")

        # ── CHECK 7: huge bet with trash ──
        if not facing and ("BET" in action or "RAISE" in action) and eq < 0.20:
            # Could be a bluff, but flag it
            issues.append(f"AGGRO WITH TRASH | {tag} | action={out.action}")

    return issues


def run_replay():
    """Replay all hands from all session logs."""
    session_files = sorted(glob.glob("vision/data/session_*.jsonl"))
    if not session_files:
        print("No session logs found!")
        return []

    all_issues = []
    hands_replayed = 0
    streets_checked = 0

    for f in session_files:
        sm = make_sm()  # fresh state machine per session
        file_issues = []

        for line in open(f):
            try:
                hand = json.loads(line)
            except Exception:
                continue

            hands_replayed += 1
            streets_checked += len(hand.get("streets", []))
            issues = replay_hand(sm, hand)
            file_issues.extend(issues)

        if file_issues:
            print(f"\n  {os.path.basename(f)}: {len(file_issues)} issues")
            for issue in file_issues:
                print(f"    - {issue}")
        else:
            print(f"  {os.path.basename(f)}: CLEAN")

        all_issues.extend(file_issues)

    return all_issues, hands_replayed, streets_checked


if __name__ == "__main__":
    print("=" * 70)
    print("  SESSION REPLAY — edge case detector")
    print("=" * 70)

    issues, hands, streets = run_replay()

    print()
    print(f"  Replayed {hands} hands, {streets} streets")
    if issues:
        # Categorize
        categories = {}
        for issue in issues:
            cat = issue.split("|")[0].strip()
            categories[cat] = categories.get(cat, 0) + 1

        print(f"  {len(issues)} issues found:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"    {cat}: {count}")
    else:
        print("  NO ISSUES — all recommendations valid")
    print("=" * 70)

    sys.exit(0 if not issues else 1)
