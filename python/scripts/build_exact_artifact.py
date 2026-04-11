#!/usr/bin/env python3
"""Build solver strategy artifacts (strategy.bin + manifest.json).

Usage:
  # Build from a YAML strategy definition:
  python build_exact_artifact.py definition.yaml --output-dir artifacts/solver

  # Build the initial corpus of common spots:
  python build_exact_artifact.py --corpus --output-dir artifacts/solver
"""

import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

# ─── Wire constants (must match strategy.rs) ─────────────────────────────────

MAGIC = b"STRT"
FORMAT_VERSION = 1
N_HAND_BUCKETS = 12
HEADER_SIZE = 16

ACTION_KIND_WIRE = {
    "fold": 0, "check": 1, "call": 2, "bet_to": 3, "raise_to": 4, "jam": 5,
}

SIZE_ID_WIRE = {
    "none": 0, "open_std": 1, "open_large": 2,
    "threebet_ip_std": 3, "threebet_oop_std": 4, "threebet_bb_wide": 5,
    "fourbet_std": 6,
    "cbet_small": 7, "cbet_medium": 8, "cbet_large": 9, "cbet_overbet": 10,
    "raise_vs_small": 11, "raise_vs_large": 12,
    "protection_value_sm": 13,
}

HAND_BUCKETS = [
    "monster", "very_strong", "strong", "strong_two_pair", "weak_two_pair",
    "overpair", "top_pair_good_kicker", "top_pair_weak", "weak_pair",
    "strong_draw", "weak_draw", "air",
]


def build_binary(actions, matrix):
    """Build strategy.bin bytes from action list and probability matrix.

    actions: list of (kind_str, size_str) tuples
    matrix:  list of 12 lists, each with len(actions) floats
    """
    n = len(actions)
    assert len(matrix) == N_HAND_BUCKETS, f"need {N_HAND_BUCKETS} rows, got {len(matrix)}"
    for i, row in enumerate(matrix):
        assert len(row) == n, f"row {i}: expected {n} cols, got {len(row)}"

    buf = bytearray()
    # Header
    buf += MAGIC
    buf += struct.pack("<I", FORMAT_VERSION)
    buf += struct.pack("<H", n)
    buf += struct.pack("<H", N_HAND_BUCKETS)
    buf += b"\x00" * 4  # reserved

    # Action table
    for kind_str, size_str in actions:
        buf += struct.pack("BB", ACTION_KIND_WIRE[kind_str], SIZE_ID_WIRE[size_str])

    # Strategy matrix (f32 LE, row-major)
    for row in matrix:
        for p in row:
            buf += struct.pack("<f", p)

    return bytes(buf)


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_artifact(output_dir, artifact_key, actions, matrix, extra_manifest=None):
    """Write strategy.bin + strategy.manifest.json into output_dir/artifact_key/."""
    bin_data = build_binary(actions, matrix)
    checksum = sha256hex(bin_data)

    dest = Path(output_dir) / artifact_key
    dest.mkdir(parents=True, exist_ok=True)

    (dest / "strategy.bin").write_bytes(bin_data)

    manifest = {
        "artifact_type": "solver_strategy",
        "version": 1,
        "checksum_sha256": checksum,
        "file_size_bytes": len(bin_data),
        "menu_version": 1,
        "n_actions": len(actions),
        "n_hand_buckets": N_HAND_BUCKETS,
        "scenario_id": artifact_key,
    }
    if extra_manifest:
        manifest.update(extra_manifest)

    (dest / "strategy.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    return dest


# ─── YAML definition mode ────────────────────────────────────────────────────

def build_from_yaml(yaml_path, output_dir):
    """Build a single artifact from a YAML strategy definition."""
    import yaml
    with open(yaml_path) as f:
        defn = yaml.safe_load(f)

    artifact_key = defn["spot"]
    actions = [(a["kind"], a["size"]) for a in defn["actions"]]
    matrix = [defn["strategy"][bucket] for bucket in HAND_BUCKETS]

    dest = write_artifact(output_dir, artifact_key, actions, matrix)
    print(f"  wrote {dest}")


# ─── Corpus mode ──────────────────────────────────────────────────────────────

# Default postflop strategy: check/bet small/bet medium.
# Stronger hands bet more; weak hands check.
POSTFLOP_ACTIONS = [("check", "none"), ("bet_to", "cbet_small"), ("bet_to", "cbet_medium")]

# IP (BTN/CO) strategy: bets more often than OOP.
IP_MATRIX = [
    [0.00, 0.05, 0.95],  # Monster
    [0.00, 0.15, 0.85],  # VeryStrong
    [0.05, 0.65, 0.30],  # Strong
    [0.10, 0.60, 0.30],  # StrongTwoPair
    [0.25, 0.55, 0.20],  # WeakTwoPair
    [0.10, 0.50, 0.40],  # Overpair
    [0.20, 0.60, 0.20],  # TopPairGoodKicker
    [0.40, 0.50, 0.10],  # TopPairWeak
    [0.60, 0.30, 0.10],  # WeakPair
    [0.30, 0.50, 0.20],  # StrongDraw
    [0.65, 0.25, 0.10],  # WeakDraw
    [0.75, 0.15, 0.10],  # Air
]

# OOP (SB/BB) strategy: checks more.
OOP_MATRIX = [
    [0.05, 0.10, 0.85],  # Monster
    [0.10, 0.20, 0.70],  # VeryStrong
    [0.15, 0.60, 0.25],  # Strong
    [0.20, 0.55, 0.25],  # StrongTwoPair
    [0.40, 0.45, 0.15],  # WeakTwoPair
    [0.25, 0.45, 0.30],  # Overpair
    [0.35, 0.50, 0.15],  # TopPairGoodKicker
    [0.55, 0.35, 0.10],  # TopPairWeak
    [0.75, 0.20, 0.05],  # WeakPair
    [0.40, 0.45, 0.15],  # StrongDraw
    [0.75, 0.15, 0.10],  # WeakDraw
    [0.85, 0.10, 0.05],  # Air
]

# Preflop: fold/open/open_large
PREFLOP_ACTIONS = [("fold", "none"), ("bet_to", "open_std"), ("bet_to", "open_large")]

PREFLOP_OPEN_MATRIX = [
    # Rows irrelevant for preflop (bucket = raw hand class, not postflop eval)
    # But format requires 12 rows.  Use position-adjusted defaults.
    [0.00, 0.50, 0.50],  # Monster (AA/KK)
    [0.00, 0.80, 0.20],  # VeryStrong (QQ/JJ/AKs)
    [0.05, 0.85, 0.10],  # Strong (TT/99/AQs)
    [0.10, 0.80, 0.10],  # StrongTwoPair (88/77/AJs)
    [0.20, 0.70, 0.10],  # WeakTwoPair (66/55/KQs)
    [0.15, 0.75, 0.10],  # Overpair
    [0.30, 0.60, 0.10],  # TopPairGoodKicker (suited broadways)
    [0.50, 0.45, 0.05],  # TopPairWeak (suited connectors)
    [0.70, 0.25, 0.05],  # WeakPair (low pairs)
    [0.40, 0.55, 0.05],  # StrongDraw (suited aces)
    [0.75, 0.20, 0.05],  # WeakDraw
    [0.90, 0.08, 0.02],  # Air
]

# Board buckets for the initial corpus.
BOARD_BUCKETS = [0, 15, 30, 42, 55, 70, 85, 98]

CORPUS_SPOTS = [
    # (aggressor, hero, n_way, matrix_fn)
    ("btn", "bb", IP_MATRIX),
    ("co",  "bb", IP_MATRIX),
    ("sb",  "bb", OOP_MATRIX),
]


def build_corpus(output_dir):
    """Generate a small but meaningful artifact corpus for common spots."""
    count = 0

    # Postflop SRP spots.
    for agg, hero, matrix in CORPUS_SPOTS:
        for bb in BOARD_BUCKETS:
            key = f"srp/flop/{agg}_vs_{hero}_2way/s100/bb{bb}/norake/mv1"
            write_artifact(output_dir, key, POSTFLOP_ACTIONS, matrix)
            count += 1

    # Preflop spots (board = "preflop").
    for pos in ["btn", "co", "hj", "utg"]:
        key = f"srp/preflop/{pos}_vs_bb_2way/s100/preflop/norake/mv1"
        write_artifact(output_dir, key, PREFLOP_ACTIONS, PREFLOP_OPEN_MATRIX)
        count += 1

    print(f"Corpus: wrote {count} artifacts to {output_dir}")


# ─── Manifest mode ───────────────────────────────────────────────────────────

# Postflop acting order (lower = acts first = OOP).
# Must match runtime-advisor/src/classify.rs::postflop_order.
_POSTFLOP_ORDER = {"sb": 1, "bb": 2, "utg": 3, "hj": 4, "co": 5, "btn": 6}


def hero_is_ip(aggressor: str, hero: str) -> bool:
    """True if hero acts AFTER aggressor postflop (hero is in position).

    Examples:
      co_vs_btn  → hero btn(6) > agg co(5)  → hero IP  ✓
      btn_vs_bb  → hero bb(2)  < agg btn(6) → hero OOP ✓
      sb_vs_bb   → hero bb(2)  > agg sb(1)  → hero IP  ✓  (BB is IP vs SB)
      btn_vs_sb  → hero sb(1)  < agg btn(6) → hero OOP ✓

    Limped pots (aggressor="noagg"):
      No preflop aggressor. Use OOP as the safe default — in a 2-way
      limped pot, hero's opponent is unknown, and OOP strategy (more
      check-heavy) is the conservative choice.
    """
    if aggressor == "noagg":
        return False  # default to OOP for limped pots
    return _POSTFLOP_ORDER.get(hero, 0) > _POSTFLOP_ORDER.get(aggressor, 0)


def build_from_manifest(manifest_path, output_dir):
    """Build artifacts from a manifest JSONL file.

    Each line must have: artifact_key, aggressor, hero.
    IP/OOP matrix is selected from hero's relative position to aggressor.
    """
    count = 0
    skipped = 0
    with open(manifest_path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            key = entry["artifact_key"]
            agg = entry["aggressor"]
            hero = entry["hero"]

            if hero_is_ip(agg, hero):
                matrix = IP_MATRIX
                tag = "IP"
            else:
                matrix = OOP_MATRIX
                tag = "OOP"

            dest = write_artifact(output_dir, key, POSTFLOP_ACTIONS, matrix)
            count += 1
            print(f"  [{count:3d}] {tag:3s}  {key}")

    print(f"\nManifest: wrote {count} artifacts to {output_dir}")
    if skipped:
        print(f"  Skipped: {skipped}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build solver strategy artifacts")
    parser.add_argument("definition", nargs="?", help="YAML strategy definition file")
    parser.add_argument("--output-dir", required=True, help="Root output directory")
    parser.add_argument("--corpus", action="store_true", help="Build initial corpus of common spots")
    parser.add_argument("--from-manifest", metavar="MANIFEST_JSONL",
                        help="Build from a manifest JSONL (one artifact_key per line)")
    args = parser.parse_args()

    if args.corpus:
        build_corpus(args.output_dir)
    elif args.from_manifest:
        build_from_manifest(args.from_manifest, args.output_dir)
    elif args.definition:
        build_from_yaml(args.definition, args.output_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
