#!/usr/bin/env python3
"""Pot-semantics validation script.

Cross-validates the session JSONL 'pot' field against other signals to
determine what it actually represents and whether it can be used for
pot-class classification.

Usage:
  python pot_semantics_audit.py ../vision/data/session_2026*.jsonl
"""

import json
import sys
from collections import Counter
from pathlib import Path


def load_hands(paths: list[str]) -> list[dict]:
    hands = []
    for p in paths:
        pp = Path(p)
        if not pp.exists():
            print(f"SKIP: {p}", file=sys.stderr)
            continue
        with open(pp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    hands.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return hands


def infer_bb(hand: dict) -> float:
    stack = hand.get("starting_stack", 0)
    if stack > 100000:
        return 10000  # CoinPoker
    return 10  # Unibet NL10


def analyze(hands: list[dict]):
    bb_val = 10  # default NL10

    # ── Section 1: Preflop pot field reliability ─────────────────────────
    print("=" * 72)
    print("POT SEMANTICS AUDIT")
    print("=" * 72)
    print(f"Total hands: {len(hands)}")
    print()

    # Hands with preflop street
    pf_hands = []
    for h in hands:
        bb_val = infer_bb(h)
        for s in h.get("streets", []):
            if s.get("phase", "").upper() == "PREFLOP":
                pf_hands.append({
                    "hand_id": h.get("hand_id"),
                    "pos": h.get("position", "?"),
                    "start": h.get("starting_stack", 0),
                    "pf_pot": s.get("pot", 0),
                    "pf_facing": s.get("facing_bet", False),
                    "pf_call": s.get("call_amount", 0),
                    "pf_stack": s.get("stack", 0),
                    "bb": bb_val,
                })
                break

    print(f"Hands with preflop street: {len(pf_hands)}")

    # Check: how many preflop pots are < blinds (1.5bb)?
    sub_blind = sum(1 for p in pf_hands if p["pf_pot"] / p["bb"] < 1.5)
    print(f"Preflop pot < 1.5bb (less than blinds): {sub_blind} "
          f"({100*sub_blind/len(pf_hands):.1f}%)")
    print("  >> If > 0, the 'pot' field does NOT represent total pot.")
    print()

    # Pot value distribution
    print("--- Preflop pot distribution (bb) ---")
    pot_bb_buckets = Counter()
    for p in pf_hands:
        pb = p["pf_pot"] / p["bb"]
        if pb < 1.0:
            pot_bb_buckets["< 1.0bb"] += 1
        elif pb < 1.5:
            pot_bb_buckets["1.0-1.5bb"] += 1
        elif pb < 3.0:
            pot_bb_buckets["1.5-3.0bb"] += 1
        elif pb < 5.0:
            pot_bb_buckets["3.0-5.0bb"] += 1
        elif pb < 10.0:
            pot_bb_buckets["5.0-10.0bb"] += 1
        else:
            pot_bb_buckets["10.0bb+"] += 1

    for b in ["< 1.0bb", "1.0-1.5bb", "1.5-3.0bb", "3.0-5.0bb",
              "5.0-10.0bb", "10.0bb+"]:
        c = pot_bb_buckets.get(b, 0)
        print(f"  {b:>12s}: {c:4d}  ({100*c/len(pf_hands):.1f}%)")

    # ── Section 2: Preflop-to-flop transition ────────────────────────────
    print()
    print("--- Preflop -> Flop transition ---")
    transitions = []
    for h in hands:
        bb_val = infer_bb(h)
        pf_s = fl_s = None
        for s in h.get("streets", []):
            phase = s.get("phase", "").upper()
            if phase == "PREFLOP":
                pf_s = s
            elif phase == "FLOP":
                fl_s = s
        if pf_s and fl_s:
            start = h.get("starting_stack", 0)
            fl_stack = fl_s.get("stack", start)
            hero_invest = max(0, start - fl_stack)
            transitions.append({
                "pos": h.get("position", "?"),
                "pf_pot_bb": pf_s.get("pot", 0) / bb_val,
                "pf_facing": pf_s.get("facing_bet", False),
                "pf_call_bb": pf_s.get("call_amount", 0) / bb_val,
                "fl_pot_bb": fl_s.get("pot", 0) / bb_val,
                "hero_invest_bb": hero_invest / bb_val,
            })

    print(f"Hands with preflop+flop: {len(transitions)}")
    if transitions:
        # Correlation: does flop pot correlate better with hero_invest?
        print()
        print("  Hero preflop investment vs flop pot:")
        for t in transitions[:20]:
            face = "Y" if t["pf_facing"] else "N"
            print(f"    pos={t['pos']:>4s}  pf_pot={t['pf_pot_bb']:5.1f}bb  "
                  f"face={face}  call={t['pf_call_bb']:4.1f}bb  "
                  f"invest={t['hero_invest_bb']:5.1f}bb  "
                  f"fl_pot={t['fl_pot_bb']:5.1f}bb")

    # ── Section 3: Signal reliability comparison ─────────────────────────
    print()
    print("--- Signal reliability for pot-class inference ---")
    print()

    # Signal A: preflop pot (current method)
    # Signal B: preflop facing_bet + call_amount
    # Signal C: hero preflop investment

    if transitions:
        # Classify using each signal and compare
        class_a = Counter()  # pot-based (old method)
        class_b = Counter()  # facing+call (new signal 1)
        class_c = Counter()  # investment (new signal 2)

        for t in transitions:
            # A: old pot-based
            if t["pf_pot_bb"] <= 3.0:
                class_a["limped"] += 1
            elif t["pf_pot_bb"] <= 10.0:
                class_a["SRP"] += 1
            elif t["pf_pot_bb"] <= 30.0:
                class_a["3bp"] += 1
            else:
                class_a["4bp+"] += 1

            # B: facing + call
            if t["pf_facing"] and t["pf_call_bb"] >= 4.0:
                class_b["3bp+"] += 1
            elif t["pf_facing"] and t["pf_call_bb"] >= 1.5:
                class_b["SRP"] += 1
            elif t["pf_facing"]:
                class_b["limp"] += 1
            else:
                class_b["open/check"] += 1

            # C: hero investment
            if t["hero_invest_bb"] >= 5.0:
                class_c["3bp+"] += 1
            elif t["hero_invest_bb"] >= 2.0:
                class_c["SRP"] += 1
            else:
                class_c["limp/walk"] += 1

        print("  Signal A (preflop pot, CURRENT):")
        for k, v in class_a.most_common():
            print(f"    {k:>12s}: {v:4d}  ({100*v/len(transitions):.1f}%)")

        print()
        print("  Signal B (facing_bet + call_amount):")
        for k, v in class_b.most_common():
            print(f"    {k:>12s}: {v:4d}  ({100*v/len(transitions):.1f}%)")

        print()
        print("  Signal C (hero preflop investment):")
        for k, v in class_c.most_common():
            print(f"    {k:>12s}: {v:4d}  ({100*v/len(transitions):.1f}%)")

    # ── Section 4: Verdict ───────────────────────────────────────────────
    print()
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)

    if sub_blind > 0:
        print("  [FAIL] pot field shows values below blinds (1.5bb).")
        print("         It does NOT represent total pot.")
        print("         DO NOT use preflop pot for pot-class inference.")
    else:
        print("  [PASS] pot field always >= blinds. May be usable.")

    print()
    print("  RECOMMENDED: Use hero preflop investment (Signal C) as")
    print("  primary classifier for postflop pot-class inference.")
    print("  Tag preflop records as 'preflop_unknown'.")
    print("=" * 72)


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} session_*.jsonl ...", file=sys.stderr)
        sys.exit(1)
    hands = load_hands(sys.argv[1:])
    if not hands:
        print("No hands loaded.", file=sys.stderr)
        sys.exit(1)
    analyze(hands)


if __name__ == "__main__":
    main()
