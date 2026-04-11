#!/usr/bin/env python3
"""Compute latency percentiles from replay results.

Usage:
  python latency_bench.py results.jsonl
"""

import json
import sys


def percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0
    idx = int(len(sorted_vals) * pct / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def report(results):
    exact_lat   = sorted(r["latency_us"] for r in results if r.get("mode") == "exact" and "latency_us" in r)
    emerg_lat   = sorted(r["latency_us"] for r in results if r.get("mode") == "emergency" and "latency_us" in r)
    all_lat     = sorted(r["latency_us"] for r in results if "latency_us" in r)

    def print_stats(label, vals):
        if not vals:
            print(f"  {label:12s}  (no data)")
            return
        p50 = percentile(vals, 50)
        p95 = percentile(vals, 95)
        p99 = percentile(vals, 99)
        mean = sum(vals) / len(vals)
        print(f"  {label:12s}  n={len(vals):5d}  mean={mean:8.0f}us  P50={p50:6d}us  P95={p95:6d}us  P99={p99:6d}us")

    print("=" * 70)
    print("LATENCY BENCHMARK")
    print("=" * 70)
    print_stats("EXACT", exact_lat)
    print_stats("EMERGENCY", emerg_lat)
    print_stats("ALL", all_lat)
    print("=" * 70)

    if all_lat:
        budget_ms = 50  # typical action clock budget
        over_budget = sum(1 for v in all_lat if v > budget_ms * 1000)
        print(f"\n  Over {budget_ms}ms budget: {over_budget}/{len(all_lat)} "
              f"({100*over_budget/len(all_lat):.1f}%)")


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} results.jsonl", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1]) as f:
        results = [json.loads(line) for line in f if line.strip()]
    report(results)


if __name__ == "__main__":
    main()
