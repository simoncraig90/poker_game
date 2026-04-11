#!/usr/bin/env python3
"""Coverage opportunity report: rank SRP HU flop spots by replay frequency.

Reads the replay JSONL (converter output) and produces:
  1. Position pair ranking (aggregated across boards/stacks)
  2. Stack bucket ranking (aggregated across pairs/boards)
  3. Board bucket ranking (aggregated across pairs/stacks)
  4. Full cross-tab: pair x stack x board, sorted by frequency
  5. Concrete artifact manifest for the top-N spots

Usage:
  python coverage_opportunity_report.py replay_full_v2.jsonl
  python coverage_opportunity_report.py replay_full_v2.jsonl --manifest 50
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

POSITION_SEATS = {1: "utg", 2: "hj", 3: "co", 4: "btn", 5: "sb", 6: "bb"}

# Current artifact coverage (update when new artifacts are built).
ARTIFACT_ROOT = _PROJECT_ROOT / "artifacts" / "solver"


def _stack_bucket(eff):
    if eff <= 50:
        return "s40"
    elif eff <= 80:
        return "s60"
    elif eff <= 125:
        return "s100"
    elif eff <= 175:
        return "s150"
    return "s200"


def _artifact_key(agg, hero, stack, bb_idx):
    return f"srp/flop/{agg}_vs_{hero}_2way/{stack}/bb{bb_idx}/norake/mv1"


def _artifact_exists(key):
    return (ARTIFACT_ROOT / key / "strategy.bin").exists()


def load_srp_flop_hu(replay_path):
    """Extract all SRP HU flop decisions from replay JSONL."""
    spots = []
    with open(replay_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rr = json.loads(line)
            req = rr.get("request") or {}
            meta = rr.get("inference_metadata", {})
            ah_method = meta.get("action_history", "")

            # Skip non-real data
            if ah_method in ("preflop_unknown", "no_preflop_street",
                             "empty_streets"):
                continue
            if ah_method.startswith("synthetic_"):
                continue

            # SRP only
            ah = req.get("action_history", [])
            n_agg = sum(1 for a in ah
                        if "BET_TO" in a or "RAISE_TO" in a)
            if n_agg != 1:
                continue

            # Flop, HU
            if req.get("street") != "flop":
                continue
            if req.get("n_players_in_hand", 0) != 2:
                continue

            hero_seat = req.get("hero_seat", 0)
            hero_pos = POSITION_SEATS.get(hero_seat, "?")

            agg_seat = None
            for a in ah:
                parts = a.split(":")
                if len(parts) >= 2 and parts[1] in ("BET_TO", "RAISE_TO"):
                    agg_seat = int(parts[0])
            agg_pos = POSITION_SEATS.get(agg_seat, "?") if agg_seat else "?"

            eff = req.get("effective_stack_bb", 0)
            sb = _stack_bucket(eff)
            bb_idx = req.get("board_bucket")

            pot_bb = (req.get("pot", 0) / req.get("big_blind", 10)
                      if req.get("big_blind", 10) > 0 else 0)

            spots.append({
                "agg": agg_pos,
                "hero": hero_pos,
                "stack": sb,
                "bb": bb_idx,
                "eff_bb": eff,
                "pot_bb": pot_bb,
            })
    return spots


def report(spots):
    total = len(spots)
    print("=" * 72)
    print("COVERAGE OPPORTUNITY REPORT — SRP HU FLOP")
    print("=" * 72)
    print(f"Total SRP HU flop decisions in replay: {total}")
    print()

    # ── 1. Position pair ranking ─────────────────────────────────────────
    print("--- 1. Position pair ranking ---")
    pairs = Counter(f"{s['agg']}_vs_{s['hero']}" for s in spots)
    for pair, c in pairs.most_common():
        has_any = any(
            _artifact_exists(_artifact_key(
                pair.split("_vs_")[0], pair.split("_vs_")[1], sb, bb))
            for sb in ("s40", "s60", "s100")
            for bb in range(99)
        )
        flag = "  [HAS ARTIFACTS]" if has_any else ""
        print(f"  {pair:>15s}: {c:3d}  ({100*c/total:.1f}%){flag}")

    # ── 2. Stack bucket ranking ──────────────────────────────────────────
    print()
    print("--- 2. Stack bucket ranking ---")
    stacks = Counter(s["stack"] for s in spots)
    for sb, c in stacks.most_common():
        print(f"  {sb:>6s}: {c:3d}  ({100*c/total:.1f}%)")

    # ── 3. Board bucket ranking ──────────────────────────────────────────
    print()
    print("--- 3. Board bucket ranking (top 30) ---")
    boards = Counter(s["bb"] for s in spots)
    cumulative = 0
    for bb, c in boards.most_common(30):
        cumulative += c
        print(f"  bb{bb:2d}: {c:3d}  (cumulative {100*cumulative/total:.0f}%)")
    print(f"  Unique board buckets: {len(boards)}")

    # ── 4. Full spot table (pair x stack x board) ────────────────────────
    print()
    print("--- 4. Full spot table (sorted by frequency) ---")
    full = Counter(
        (s["agg"], s["hero"], s["stack"], s["bb"]) for s in spots
    )
    print(f"  Unique spots: {len(full)}")
    print()
    print(f"  {'agg':>4s} {'hero':>4s} {'stack':>5s} {'bb':>4s}  "
          f"{'n':>3s}  {'key':40s}  {'exists':>6s}")
    for (agg, hero, stack, bb), c in full.most_common():
        key = _artifact_key(agg, hero, stack, bb)
        exists = "YES" if _artifact_exists(key) else "no"
        print(f"  {agg:>4s} {hero:>4s} {stack:>5s}  {bb:>3d}  "
              f"{c:3d}  {key:40s}  {exists:>6s}")

    # ── 5. Build priority ────────────────────────────────────────────────
    print()
    print("--- 5. Build priority (missing artifacts, by frequency) ---")
    missing = []
    for (agg, hero, stack, bb), c in full.most_common():
        key = _artifact_key(agg, hero, stack, bb)
        if not _artifact_exists(key):
            missing.append((key, c, agg, hero, stack, bb))

    cumulative = 0
    for key, c, agg, hero, stack, bb in missing[:40]:
        cumulative += c
        print(f"  {c:3d}  {key}  (cumulative {cumulative})")

    print()
    total_missing = sum(c for _, c, *_ in missing)
    print(f"  Total missing decisions: {total_missing} / {total}")
    print(f"  Total missing artifacts: {len(missing)}")
    if missing:
        # Group by position pair
        pair_missing = defaultdict(int)
        for _, c, agg, hero, *_ in missing:
            pair_missing[f"{agg}_vs_{hero}"] += c
        print()
        print("  Missing by position pair:")
        for pair, c in sorted(pair_missing.items(), key=lambda x: -x[1]):
            print(f"    {pair:>15s}: {c:3d} decisions")


def write_manifest(spots, output_path, max_artifacts):
    """Write a build manifest JSONL for the top-N missing artifacts."""
    full = Counter(
        (s["agg"], s["hero"], s["stack"], s["bb"]) for s in spots
    )

    manifest = []
    for (agg, hero, stack, bb), c in full.most_common():
        key = _artifact_key(agg, hero, stack, bb)
        if _artifact_exists(key):
            continue
        manifest.append({
            "artifact_key": key,
            "aggressor": agg,
            "hero": hero,
            "stack_bucket": stack,
            "board_bucket": bb,
            "replay_frequency": c,
        })
        if len(manifest) >= max_artifacts:
            break

    with open(output_path, "w") as f:
        for entry in manifest:
            f.write(json.dumps(entry) + "\n")

    print(f"\nWrote {len(manifest)} entries to {output_path}")
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Coverage opportunity report for SRP HU flop artifacts")
    parser.add_argument("replay", help="Replay JSONL (from session_to_replay.py)")
    parser.add_argument("--manifest", type=int, metavar="N",
                        help="Write top-N missing artifacts as build manifest")
    parser.add_argument("--manifest-output",
                        default="artifact_batch_manifest.jsonl",
                        help="Manifest output path")
    args = parser.parse_args()

    spots = load_srp_flop_hu(args.replay)
    if not spots:
        print("No SRP HU flop decisions found.", file=sys.stderr)
        sys.exit(1)

    report(spots)

    if args.manifest:
        write_manifest(spots, args.manifest_output, args.manifest)


if __name__ == "__main__":
    main()
