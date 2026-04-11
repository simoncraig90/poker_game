#!/usr/bin/env python3
"""Run replay hands through the EXACT + EMERGENCY baseline and collect results.

Usage:
  # Run synthetic test hands:
  python baseline_replay_runner.py --synthetic --output results.jsonl

  # Run from a hand-records JSONL file:
  python baseline_replay_runner.py --input hands.jsonl --output results.jsonl

Output: one JSON object per line with mode, action, latency, diagnostics.
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Add project root to path for imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "python"))

from advisor_service.mode_router import ModeRouter

# ─── Synthetic hand generator ─────────────────────────────────────────────────

# Representative hands covering major spot types.
SYNTHETIC_HANDS = [
    # SRP BTN vs BB flop — various hand strengths
    {
        "desc": "SRP BTN-vs-BB flop: TPGK",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 6,
        "street": "flop", "effective_stack_bb": 97.5, "n_players_in_hand": 2,
        "action_history": ["4:BET_TO:250", "6:CALL"],
        "board_bucket": 42, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["As", "Kd"], "board": ["Ah", "7c", "2s"],
        "facing_bet": False, "pot": 550.0, "big_blind": 100.0,
        "hero_committed": 250.0, "hero_start_stack": 10000.0, "hero_stack": 9750.0,
        "legal_actions": [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 100, "max": 9750},
        ],
    },
    {
        "desc": "SRP BTN-vs-BB flop: air",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 6,
        "street": "flop", "effective_stack_bb": 97.5, "n_players_in_hand": 2,
        "action_history": ["4:BET_TO:250", "6:CALL"],
        "board_bucket": 42, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["7h", "2d"], "board": ["Ks", "Qc", "Js"],
        "facing_bet": False, "pot": 550.0, "big_blind": 100.0,
        "hero_committed": 250.0, "hero_start_stack": 10000.0, "hero_stack": 9750.0,
        "legal_actions": [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 100, "max": 9750},
        ],
    },
    {
        "desc": "SRP CO-vs-BB flop: overpair",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 6,
        "street": "flop", "effective_stack_bb": 100.0, "n_players_in_hand": 2,
        "action_history": ["3:BET_TO:250", "6:CALL"],
        "board_bucket": 30, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["Qs", "Qd"], "board": ["Tc", "7h", "2s"],
        "facing_bet": False, "pot": 550.0, "big_blind": 100.0,
        "hero_committed": 250.0, "hero_start_stack": 10000.0, "hero_stack": 9750.0,
        "legal_actions": [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 100, "max": 9750},
        ],
    },
    # Facing a bet — fold/call scenario
    {
        "desc": "SRP facing bet: weak hand",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 6,
        "street": "flop", "effective_stack_bb": 97.5, "n_players_in_hand": 2,
        "action_history": ["4:BET_TO:250", "6:CALL"],
        "board_bucket": 42, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["9s", "8d"], "board": ["Ah", "Kc", "2s"],
        "facing_bet": True, "pot": 900.0, "big_blind": 100.0,
        "hero_committed": 250.0, "hero_start_stack": 10000.0, "hero_stack": 9750.0,
        "legal_actions": [
            {"kind": "fold", "min": 0, "max": 0},
            {"kind": "call", "min": 350, "max": 350},
            {"kind": "raise_to", "min": 700, "max": 9750},
        ],
    },
    # 3-bet pot — no artifact expected → EMERGENCY
    {
        "desc": "3BP flop: strong hand, no artifact",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 4,
        "street": "flop", "effective_stack_bb": 95.0, "n_players_in_hand": 2,
        "action_history": ["4:BET_TO:250", "6:RAISE_TO:800", "4:CALL"],
        "board_bucket": 10, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["As", "Ah"], "board": ["Kh", "7c", "2s"],
        "facing_bet": False, "pot": 1650.0, "big_blind": 100.0,
        "hero_committed": 800.0, "hero_start_stack": 10000.0, "hero_stack": 9200.0,
        "legal_actions": [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 200, "max": 9200},
        ],
    },
    # Multiway — EMERGENCY expected
    {
        "desc": "SRP 3-way: draws",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 6,
        "street": "flop", "effective_stack_bb": 97.5, "n_players_in_hand": 3,
        "action_history": ["4:BET_TO:250", "5:CALL", "6:CALL"],
        "board_bucket": 55, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["Th", "9h"], "board": ["Jh", "8c", "2s"],
        "facing_bet": False, "pot": 800.0, "big_blind": 100.0,
        "hero_committed": 250.0, "hero_start_stack": 10000.0, "hero_stack": 9750.0,
        "legal_actions": [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 100, "max": 9750},
        ],
    },
    # Board bucket not in corpus → cache miss even for SRP HU
    {
        "desc": "SRP BTN-vs-BB, uncovered board bucket",
        "active_seats": [1, 2, 3, 4, 5, 6], "button_seat": 4, "hero_seat": 6,
        "street": "flop", "effective_stack_bb": 97.5, "n_players_in_hand": 2,
        "action_history": ["4:BET_TO:250", "6:CALL"],
        "board_bucket": 99, "rake_profile": "norake", "menu_version": 1,
        "hole_cards": ["Kh", "Qh"], "board": ["Jh", "Ts", "2c"],
        "facing_bet": False, "pot": 550.0, "big_blind": 100.0,
        "hero_committed": 250.0, "hero_start_stack": 10000.0, "hero_stack": 9750.0,
        "legal_actions": [
            {"kind": "check", "min": 0, "max": 0},
            {"kind": "bet_to", "min": 100, "max": 9750},
        ],
    },
]


def run_replay(router: ModeRouter, hands: list, output_path: str):
    """Run hands through the router and write results JSONL."""
    results = []
    with open(output_path, "w") as f:
        for i, hand in enumerate(hands):
            desc = hand.pop("desc", f"hand_{i}")
            resp = router.recommend(hand)
            resp["hand_index"] = i
            resp["hand_desc"] = desc
            f.write(json.dumps(resp) + "\n")
            results.append(resp)
            print(f"  [{i+1}/{len(hands)}] {desc}: mode={resp.get('mode')} "
                  f"action={resp.get('action_kind')} latency={resp.get('latency_us')}us")

    print(f"\nWrote {len(results)} results to {output_path}")
    return results


def run_replay_from_replay_jsonl(router: ModeRouter, replay_path: str, output_path: str):
    """Run from a full replay JSONL (converter output with metadata).

    Extracts the 'request' field from each record, runs it through the
    router, and writes results.  Output records are index-aligned with the
    replay JSONL so hit_rate_report_v2.py can correlate them.
    """
    results = []
    skipped = 0
    with open(replay_path) as rf, open(output_path, "w") as wf:
        for i, line in enumerate(rf):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            req = rec.get("request")
            if req is None:
                skipped += 1
                continue
            desc = req.get("street", "?")
            hand = dict(req)  # shallow copy
            resp = router.recommend(hand)
            resp["hand_index"] = i
            resp["hand_desc"] = f"replay_{i}_{desc}"
            wf.write(json.dumps(resp) + "\n")
            results.append(resp)
            if (i + 1) % 200 == 0:
                print(f"  [{i+1}] ...")

    print(f"\nWrote {len(results)} results to {output_path}")
    if skipped:
        print(f"  Skipped {skipped} records (null request)")
    return results


def main():
    parser = argparse.ArgumentParser(description="Baseline replay runner")
    parser.add_argument("--synthetic", action="store_true", help="Use built-in synthetic hands")
    parser.add_argument("--input", help="JSONL file with bare request dicts")
    parser.add_argument("--replay", help="Full replay JSONL (from session_to_replay.py)")
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--artifact-root", default=str(_PROJECT_ROOT / "artifacts" / "solver"))
    parser.add_argument("--action-menu", default=str(_PROJECT_ROOT / "configs" / "action_menu_v1.yaml"))
    parser.add_argument("--prior-bin", default=str(_PROJECT_ROOT / "artifacts" / "emergency" / "emergency_range_prior.bin"))
    parser.add_argument("--prior-manifest", default=str(_PROJECT_ROOT / "artifacts" / "emergency" / "emergency_range_prior.manifest.json"))
    parser.add_argument("--preflop-charts", default=str(_PROJECT_ROOT / "configs" / "preflop_charts.json"))
    args = parser.parse_args()

    def _make_router():
        return ModeRouter(
            artifact_root=args.artifact_root,
            action_menu=args.action_menu,
            prior_bin=args.prior_bin,
            prior_manifest=args.prior_manifest,
            preflop_charts=args.preflop_charts,
        )

    if args.synthetic:
        hands = [dict(h) for h in SYNTHETIC_HANDS]  # shallow copy
    elif args.input:
        with open(args.input) as f:
            hands = [json.loads(line) for line in f if line.strip()]
    elif args.replay:
        # Full replay JSONL mode — handled separately to preserve alignment
        with _make_router() as router:
            run_replay_from_replay_jsonl(router, args.replay, args.output)
            stats = router.stats()
            print(f"\nRouter stats: {json.dumps(stats, indent=2)}")
        return
    else:
        print("error: specify --synthetic, --input FILE, or --replay FILE",
              file=sys.stderr)
        sys.exit(1)

    with _make_router() as router:
        run_replay(router, hands, args.output)
        stats = router.stats()
        print(f"\nRouter stats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
