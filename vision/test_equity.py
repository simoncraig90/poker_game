"""Test the advisor against hands that cost money today."""
import sys, json
sys.path.insert(0, "vision")
from advisor import (equity_model_predict, evaluate_hand_strength,
                     card_str_to_dict, _load_equity_model, assess_board_danger)

_load_equity_model()

hands = [
    {"hero": ["Js", "9d"], "board": ["3c", "5h", "7d", "9s", "Tc"],
     "desc": "J9o on 3-5-7-9-T straight board (LOST to straight)",
     "bad_action": "RAISE", "ok_actions": ["FOLD", "CALL"]},

    {"hero": ["Jd", "Qs"], "board": ["2c", "7s", "4d"],
     "desc": "JQo on 2-7-4 (no pair, just overcards)",
     "bad_action": "RAISE", "ok_actions": ["FOLD", "CHECK"]},

    {"hero": ["9h", "Qc"], "board": ["7h", "Jd", "8c", "7s", "8s"],
     "desc": "Q9 on 7-J-8-7-8 double paired (Q high)",
     "bad_action": "CALL", "ok_actions": ["FOLD"]},

    {"hero": ["7s", "8h"], "board": ["6c", "2c", "7d", "9d"],
     "desc": "78 on 6-2-7-9 straight possible (middle pair)",
     "bad_action": "RAISE", "ok_actions": ["CHECK", "CALL"]},

    {"hero": ["3h", "Kc"], "board": ["9d", "Ad", "2h", "Ts", "Tc"],
     "desc": "K3 on A-T-T-2-9 paired (K high)",
     "bad_action": "CALL", "ok_actions": ["FOLD"]},

    {"hero": ["As", "Ah"], "board": [],
     "desc": "AA preflop (should raise)",
     "bad_action": "FOLD", "ok_actions": ["RAISE"]},

    {"hero": ["7s", "7h"], "board": ["7d", "2c", "6s"],
     "desc": "Set of 7s on 7-2-6 (should bet/raise)",
     "bad_action": "FOLD", "ok_actions": ["BET", "RAISE"]},

    {"hero": ["Ah", "Kh"], "board": ["Qh", "Jh", "2s"],
     "desc": "AK flush draw + 2 overs (should bet/raise)",
     "bad_action": "FOLD", "ok_actions": ["BET", "RAISE", "CALL"]},
]

print("=" * 65)
print("  ADVISOR SAFETY TEST — Would It Give Bad Advice?")
print("=" * 65)
print()

passed = 0
failed = 0

for h in hands:
    eq = equity_model_predict(h["hero"], h["board"])
    hero_dicts = [card_str_to_dict(c) for c in h["hero"]]
    hero_dicts = [c for c in hero_dicts if c]
    board_dicts = [card_str_to_dict(c) for c in h["board"]]
    board_dicts = [c for c in board_dicts if c]
    phase = "PREFLOP" if not h["board"] else ("FLOP" if len(h["board"]) == 3 else ("TURN" if len(h["board"]) == 4 else "RIVER"))
    heur = evaluate_hand_strength(hero_dicts, board_dicts, phase)

    # Board danger check
    danger = assess_board_danger(h["hero"], h["board"])

    eq_str = f"{eq*100:.0f}%" if eq is not None else "N/A"
    heur_str = f"{heur*100:.0f}%"

    # Would the raise be suppressed?
    raise_suppressed = danger["suppress_raise"]

    hero_str = " ".join(h["hero"])
    board_str = " ".join(h["board"]) if h["board"] else "(preflop)"

    ok = True
    if h["bad_action"] == "RAISE" and not raise_suppressed:
        # Bad: would still recommend raise
        ok = False
    if h["bad_action"] == "FOLD" and raise_suppressed:
        # Bad: strong hand but raise is suppressed
        ok = False

    icon = "PASS" if ok else "FAIL"
    if ok: passed += 1
    else: failed += 1

    print(f"  [{icon}] {h['desc']}")
    print(f"    {hero_str}  |  {board_str}")
    print(f"    Old heuristic: {heur_str}  New model: {eq_str}")
    print(f"    Board: danger={danger['danger']:.2f} cat={danger['category']} warn={danger['warnings']}")
    print(f"    Raise suppressed: {raise_suppressed}")
    print(f"    Old bad advice: {h['bad_action']}  OK actions: {h['ok_actions']}")
    print()

print("-" * 65)
print(f"  {passed} PASS, {failed} FAIL out of {len(hands)} tests")
print("=" * 65)
