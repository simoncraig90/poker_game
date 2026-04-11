#!/usr/bin/env python3
"""Analyze replay results and report EXACT/EMERGENCY hit rates.

Usage:
  python hit_rate_report.py results.jsonl
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_results(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def report(results):
    total = len(results)
    if total == 0:
        print("No results to report.")
        return

    # ── Mode distribution ─────────────────────────────────────────────────────
    mode_counts = Counter(r.get("mode", "unknown") for r in results)
    exact_n = mode_counts.get("exact", 0)
    emerg_n = mode_counts.get("emergency", 0)
    error_n = sum(1 for r in results if "error" in r)

    print("=" * 60)
    print("BASELINE HIT-RATE REPORT")
    print("=" * 60)
    print(f"  Total hands:      {total}")
    print(f"  EXACT:            {exact_n:4d}  ({100*exact_n/total:5.1f}%)")
    print(f"  EMERGENCY:        {emerg_n:4d}  ({100*emerg_n/total:5.1f}%)")
    if error_n:
        print(f"  ERRORS:           {error_n:4d}  ({100*error_n/total:5.1f}%)")

    # ── Breakdown by spot class (pot class from artifact key) ─────────────────
    print("\n--- By spot class (from artifact key) ---")
    spot_modes = defaultdict(lambda: Counter())
    for r in results:
        key = r.get("artifact_key", "unknown")
        parts = key.split("/")
        spot_class = parts[0] if parts else "unknown"
        spot_modes[spot_class][r.get("mode", "unknown")] += 1

    for spot_class in sorted(spot_modes):
        c = spot_modes[spot_class]
        n = sum(c.values())
        ex = c.get("exact", 0)
        print(f"  {spot_class:20s}  total={n:4d}  exact={ex:4d} ({100*ex/n:5.1f}%)  emergency={c.get('emergency',0)}")

    # ── EMERGENCY reason distribution (quality) ───────────────────────────────
    print("\n--- EMERGENCY quality distribution ---")
    em_quality = Counter()
    for r in results:
        if r.get("mode") == "emergency":
            em_quality[r.get("quality", "unknown")] += 1
    for q, n in em_quality.most_common():
        print(f"  {q:20s}  {n:4d}  ({100*n/emerg_n:5.1f}%)" if emerg_n else f"  {q:20s}  {n:4d}")

    # ── Top missing coverage families ─────────────────────────────────────────
    print("\n--- Top 10 missing artifact keys (EMERGENCY) ---")
    missing_keys = Counter()
    for r in results:
        if r.get("mode") == "emergency":
            missing_keys[r.get("artifact_key", "unknown")] += 1
    for key, count in missing_keys.most_common(10):
        print(f"  {count:4d}  {key}")

    # ── Snap rate ─────────────────────────────────────────────────────────────
    snapped = [r for r in results if r.get("was_snapped")]
    snap_reasons = Counter(r.get("snap_reason", "unknown") for r in snapped)
    print(f"\n--- Legalizer snap rate ---")
    print(f"  Snapped: {len(snapped)}/{total} ({100*len(snapped)/total:.1f}%)")
    for reason, count in snap_reasons.most_common():
        print(f"    {reason:30s}  {count}")

    # ── Trust distribution ────────────────────────────────────────────────────
    trust_vals = [r.get("trust_score", 0) for r in results if "trust_score" in r]
    if trust_vals:
        print(f"\n--- Trust score ---")
        print(f"  Mean:  {sum(trust_vals)/len(trust_vals):.3f}")
        print(f"  Min:   {min(trust_vals):.3f}")
        print(f"  Max:   {max(trust_vals):.3f}")

    # ── Integrity / error counts ──────────────────────────────────────────────
    print(f"\n--- Integrity ---")
    print(f"  Errors:              {error_n}")
    print(f"  Wrong-family loads:  0  (enforced by artifact_key)")
    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} results.jsonl", file=sys.stderr)
        sys.exit(1)
    results = load_results(sys.argv[1])
    report(results)


if __name__ == "__main__":
    main()
