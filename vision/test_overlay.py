"""
Stress test the advisor overlay with simulated hands.

Cycles through a set of test hands, displaying each in the overlay
for a few seconds. No live screen capture — pure simulation.

Tests:
  - Preflop chart accuracy across all positions
  - Postflop equity + board danger display
  - Card display rendering
  - Overlay doesn't crash on edge cases

Usage:
  python vision/test_overlay.py              # run all tests with overlay
  python vision/test_overlay.py --fast       # 1s per hand
  python vision/test_overlay.py --headless   # no overlay, just verify logic
"""

import argparse
import sys
import time

sys.path.insert(0, "vision")
from advisor import (
    equity_model_predict, evaluate_hand_strength, card_str_to_dict,
    assess_board_danger, _load_equity_model, phase_from_board_count,
    strength_to_bucket, card_display,
)
from preflop_chart import preflop_advice

# ── Test hands ─────────────────────────────────────────────────────────

TEST_HANDS = [
    # Preflop — by position
    {"hero": ["As", "Ah"], "board": [], "pos": "EP", "facing": False,
     "expect_action": "RAISE", "desc": "AA from EP — always raise"},
    {"hero": ["Ks", "Qs"], "board": [], "pos": "MP", "facing": False,
     "expect_action": "RAISE", "desc": "KQs from MP — open raise"},
    {"hero": ["Qc", "Tc"], "board": [], "pos": "BTN", "facing": False,
     "expect_action": "RAISE", "desc": "QTs from BTN — open raise"},
    {"hero": ["Qd", "Tc"], "board": [], "pos": "MP", "facing": False,
     "expect_action": "FOLD", "desc": "QTo from MP — fold (not in MP range)"},
    {"hero": ["8s", "7s"], "board": [], "pos": "BTN", "facing": False,
     "expect_action": "RAISE", "desc": "87s from BTN — open raise"},
    {"hero": ["8s", "7s"], "board": [], "pos": "EP", "facing": False,
     "expect_action": "FOLD", "desc": "87s from EP — fold"},
    {"hero": ["7d", "2c"], "board": [], "pos": "BTN", "facing": False,
     "expect_action": "FOLD", "desc": "72o from BTN — fold (worst hand)"},
    {"hero": ["As", "5s"], "board": [], "pos": "CO", "facing": False,
     "expect_action": "RAISE", "desc": "A5s from CO — open raise"},
    {"hero": ["Kc", "Jc"], "board": [], "pos": "BTN", "facing": False,
     "expect_action": "RAISE", "desc": "KJs from BTN — open raise"},

    # Preflop — facing raises
    {"hero": ["As", "Ah"], "board": [], "pos": "MP", "facing": True,
     "expect_action": "RAISE", "desc": "AA facing raise — 3-bet"},
    {"hero": ["Jd", "Td"], "board": [], "pos": "CO", "facing": True,
     "expect_action": "CALL", "desc": "JTs facing raise — call"},
    {"hero": ["9h", "3c"], "board": [], "pos": "BTN", "facing": True,
     "expect_action": "FOLD", "desc": "93o facing raise — fold"},
    {"hero": ["Kd", "2h"], "board": [], "pos": "SB", "facing": True,
     "expect_action": "FOLD", "desc": "K2o from SB facing raise — fold"},

    # Postflop — strong hands
    {"hero": ["As", "Ah"], "board": ["Ks", "7d", "3c"], "pos": "BTN", "facing": False,
     "expect_eq_min": 0.70, "desc": "AA on K-7-3 — overpair, strong"},
    {"hero": ["7s", "7h"], "board": ["7d", "2c", "6s"], "pos": "BTN", "facing": False,
     "expect_eq_min": 0.70, "desc": "Set of 7s — very strong"},
    {"hero": ["Ah", "Kh"], "board": ["Qh", "Jh", "2s"], "pos": "CO", "facing": False,
     "expect_eq_min": 0.55, "desc": "AK flush draw + overs — strong draw"},

    # Postflop — dangerous boards (should show warnings)
    {"hero": ["Js", "9d"], "board": ["3c", "5h", "7d", "9s", "Tc"], "pos": "BTN", "facing": True,
     "expect_warnings": ["STRAIGHT_HEAVY"], "expect_suppress": True,
     "desc": "J9 on 3-5-7-9-T — pair but STRAIGHT_HEAVY"},
    {"hero": ["9h", "Qc"], "board": ["7h", "Jd", "8c", "7s", "8s"], "pos": "MP", "facing": True,
     "expect_warnings": ["DOUBLE_PAIRED"], "expect_suppress": True,
     "desc": "Q9 on 7-J-8-7-8 — double paired, no pair"},
    {"hero": ["Jd", "Qs"], "board": ["2c", "7s", "4d"], "pos": "CO", "facing": False,
     "expect_suppress": True,
     "desc": "JQ on 2-7-4 — no pair, suppress raise"},
    {"hero": ["3h", "Kc"], "board": ["9d", "Ad", "2h", "Ts", "Tc"], "pos": "MP", "facing": True,
     "expect_suppress": True,
     "desc": "K3 on A-T-T-2-9 — no pair, paired board"},

    # Postflop — weak hands
    {"hero": ["7d", "2c"], "board": ["As", "Kd", "Qh"], "pos": "BTN", "facing": True,
     "expect_eq_max": 0.30, "desc": "72o on A-K-Q — complete air"},

    # Edge cases
    {"hero": ["Ah", "Kh", "Qh"], "board": ["2c", "3d"], "pos": "BTN", "facing": False,
     "desc": "3 hero cards detected (YOLO bug) — should not crash"},
    {"hero": ["Ts"], "board": [], "pos": "BTN", "facing": False,
     "desc": "Only 1 hero card — should handle gracefully"},
    {"hero": [], "board": [], "pos": "BTN", "facing": False,
     "desc": "No cards — waiting state"},
]


def run_tests(use_overlay=True, delay=2.5):
    _load_equity_model()

    overlay = None
    if use_overlay:
        import tkinter as tk
        from advisor import OverlayWindow
        overlay = OverlayWindow()

    print("=" * 65)
    print("  ADVISOR STRESS TEST")
    print("=" * 65)
    print(f"  {len(TEST_HANDS)} test hands | overlay={'ON' if use_overlay else 'OFF'}")
    print()

    passed = 0
    failed = 0
    crashed = 0

    for i, h in enumerate(TEST_HANDS):
        hero = h["hero"]
        board = h.get("board", [])
        pos = h.get("pos", "BTN")
        facing = h.get("facing", False)
        desc = h.get("desc", "")

        try:
            # Get equity
            eq = None
            if len(hero) >= 2:
                eq = equity_model_predict(hero[:2], board[:5])
            if eq is None and len(hero) >= 2:
                hd = [card_str_to_dict(c) for c in hero[:2]]
                hd = [c for c in hd if c]
                bd = [card_str_to_dict(c) for c in board]
                bd = [c for c in bd if c]
                phase = phase_from_board_count(len(board))
                eq = evaluate_hand_strength(hd, bd, phase)
            if eq is None:
                eq = 0

            phase = phase_from_board_count(len(board))
            issues = []

            # Build info dict for overlay
            info = {"phase": phase, "equity": eq, "position": pos}

            if phase == "PREFLOP" and len(hero) >= 2:
                pf = preflop_advice(hero[0], hero[1], pos, facing_raise=facing)
                info["preflop"] = pf

                if "expect_action" in h and pf["action"] != h["expect_action"]:
                    issues.append(f"action={pf['action']} expected={h['expect_action']}")
            elif len(board) >= 3:
                danger = assess_board_danger(hero[:2] if len(hero) >= 2 else hero, board)
                info["danger"] = danger
                info["pot_odds"] = ""

                if "expect_warnings" in h:
                    for w in h["expect_warnings"]:
                        if w not in danger.get("warnings", []):
                            issues.append(f"missing warning {w}")

                if "expect_suppress" in h and h["expect_suppress"] != danger.get("suppress_raise", False):
                    issues.append(f"suppress={danger.get('suppress_raise')} expected={h['expect_suppress']}")

                if "expect_eq_min" in h and eq < h["expect_eq_min"]:
                    issues.append(f"eq={eq:.0%} < min {h['expect_eq_min']:.0%}")

                if "expect_eq_max" in h and eq > h["expect_eq_max"]:
                    issues.append(f"eq={eq:.0%} > max {h['expect_eq_max']:.0%}")

            # Update overlay
            if overlay and len(hero) >= 2:
                overlay.show_info(hero[:2], board[:5], info)
                overlay.update()

            # Report
            status = "PASS" if not issues else "FAIL"
            if issues:
                failed += 1
            else:
                passed += 1

            hero_str = " ".join(hero) if hero else "(none)"
            board_str = " ".join(board) if board else "(preflop)"
            eq_str = f"{eq:.0%}" if eq else "N/A"

            icon = "ok" if not issues else "FAIL"
            print(f"  [{icon}] {i+1:2d}. {desc}")
            if issues:
                for iss in issues:
                    print(f"        {iss}")

            if use_overlay:
                time.sleep(delay)

        except Exception as e:
            crashed += 1
            print(f"  [CRASH] {i+1:2d}. {desc}")
            print(f"          {type(e).__name__}: {e}")

    print()
    print("-" * 65)
    print(f"  {passed} PASS | {failed} FAIL | {crashed} CRASH | {len(TEST_HANDS)} total")
    print("=" * 65)

    if overlay:
        try:
            overlay.root.destroy()
        except Exception:
            pass

    return failed + crashed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="1s per hand")
    parser.add_argument("--headless", action="store_true", help="No overlay")
    args = parser.parse_args()

    delay = 1.0 if args.fast else 2.5
    ok = run_tests(use_overlay=not args.headless, delay=delay)
    sys.exit(0 if ok else 1)
