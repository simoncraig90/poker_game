#!/usr/bin/env python3
"""Enhanced replay analysis report (v2).

Extends the original hit_rate_report.py with:
  - Pot class x street matrix
  - EMERGENCY decomposition by structural reason
  - Missing board bucket ranking
  - Stack bucket distribution
  - Pot-size-weighted EMERGENCY exposure
  - Inference method quality breakdown
  - Trust bucket distribution

Requires BOTH the replay JSONL (converter output) and the results JSONL
(replay runner output) to correlate inference metadata with routing outcome.

Usage:
  python hit_rate_report_v2.py replay_full_v2.jsonl results_v2.jsonl

  # Or results-only mode (no inference metadata, limited decomposition):
  python hit_rate_report_v2.py --results-only results_v2.jsonl
"""

import json
import os
import os.path
import sys
from collections import Counter, defaultdict


def load_jsonl(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _pct(n, total):
    return f"{100*n/total:5.1f}%" if total > 0 else "  n/a"


def _pot_class_from_ah(action_history):
    """Derive pot class from action_history (same logic as Rust classify_pot)."""
    n_agg = 0
    had_caller = False
    squeeze = False
    for entry in action_history:
        parts = entry.split(":")
        if len(parts) < 2:
            continue
        action = parts[1]
        if action in ("BET_TO", "RAISE_TO"):
            if n_agg >= 1 and had_caller:
                squeeze = True
            n_agg += 1
            had_caller = False
        elif action == "CALL":
            if n_agg >= 1:
                had_caller = True
    if squeeze:
        return "squeeze"
    return {0: "limped", 1: "srp", 2: "3bp"}.get(n_agg, "4bp+")


def _stack_bucket(eff_bb):
    if eff_bb <= 50:
        return "S40"
    elif eff_bb <= 80:
        return "S60"
    elif eff_bb <= 125:
        return "S100"
    elif eff_bb <= 175:
        return "S150"
    return "S200+"


# ── Known artifact board buckets (scanned from artifacts/solver/) ────────────
def _scan_board_buckets():
    """Scan the artifact tree for all board bucket indices that have artifacts."""
    buckets = set()
    solver_root = os.path.join(
        os.path.dirname(__file__), "..", "..", "artifacts", "solver")
    if not os.path.isdir(solver_root):
        return {0, 15, 30, 42, 55, 70, 85, 98}  # fallback
    for root, _dirs, files in os.walk(solver_root):
        if "strategy.bin" in files:
            # Extract board bucket from path component like "bb42"
            for part in root.replace("\\", "/").split("/"):
                if part.startswith("bb") and part[2:].isdigit():
                    buckets.add(int(part[2:]))
    return buckets if buckets else {0, 15, 30, 42, 55, 70, 85, 98}

KNOWN_BOARD_BUCKETS = _scan_board_buckets()


_SOLVER_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "artifacts", "solver"))

_POSITION_SEATS = {1: "utg", 2: "hj", 3: "co", 4: "btn", 5: "sb", 6: "bb"}


def _artifact_exists_for(pot_cls, street, agg, hero, n_players, sb, bb_idx):
    """Check if a specific artifact exists on disk."""
    if bb_idx is None:
        board = "preflop"
    else:
        board = f"bb{bb_idx}"
    key = (f"{pot_cls}/{street}/{agg}_vs_{hero}_{n_players}way"
           f"/{sb.lower()}/{board}/norake/mv1")
    return os.path.isfile(os.path.join(_SOLVER_ROOT, key, "strategy.bin"))


def _emergency_reason(req, meta):
    """Classify why a request went to EMERGENCY.

    Returns a short structural reason string.
    """
    ah_method = meta.get("action_history", "")
    ah = req.get("action_history", [])
    street = req.get("street", "")
    n_players = req.get("n_players_in_hand", 0)
    eff_bb = req.get("effective_stack_bb", 0)
    bb_idx = req.get("board_bucket")
    pot_class = _pot_class_from_ah(ah)
    sb = _stack_bucket(eff_bb)

    # Preflop unknown (pot class unknowable)
    if ah_method == "preflop_unknown":
        return "preflop_unknown"

    # No preflop street / empty
    if ah_method in ("no_preflop_street", "empty_streets"):
        return "no_preflop_data"

    # Non-SRP pot class
    if pot_class == "limped":
        return "limped_pot"
    if pot_class in ("3bp", "4bp+", "squeeze"):
        return f"{pot_class}_no_artifact"

    # SRP from here on
    if street == "preflop":
        return "srp_preflop"
    if street in ("turn", "river"):
        return f"srp_{street}_no_artifact"
    if n_players > 2:
        return "srp_multiway"

    # SRP flop HU — check actual artifact existence
    # Derive position pair from action_history
    agg_seat = None
    for a in ah:
        parts = a.split(":")
        if len(parts) >= 2 and parts[1] in ("BET_TO", "RAISE_TO"):
            try:
                agg_seat = int(parts[0])
            except ValueError:
                pass
    hero_seat = req.get("hero_seat", 0)
    agg_pos = _POSITION_SEATS.get(agg_seat, "btn")
    hero_pos = _POSITION_SEATS.get(hero_seat, "bb")

    if _artifact_exists_for("srp", street, agg_pos, hero_pos, n_players,
                            sb, bb_idx):
        return "srp_should_be_exact"

    # Diagnose which dimension is missing
    # Check if ANY artifact exists for this position pair at this stack
    has_any_board = any(
        _artifact_exists_for("srp", street, agg_pos, hero_pos, n_players,
                             sb, test_bb)
        for test_bb in range(99)
    )
    if not has_any_board:
        # No artifacts at all for this pair+stack — stack or pair miss
        has_any_stack = any(
            _artifact_exists_for("srp", street, agg_pos, hero_pos, n_players,
                                 test_sb, 0)
            for test_sb in ("S40", "S60", "S100", "S150", "S200+")
        )
        if not has_any_stack:
            return f"srp_position_pair_miss"
        return f"srp_stack_miss_{sb}"

    return "srp_board_bucket_miss"


def report_v2(replay_records, result_records):
    """Full v2 report using both replay metadata and router results."""
    total = len(result_records)
    if total == 0:
        print("No results to report.")
        return

    # Merge: result_records are indexed by position, replay_records have metadata
    # Both are in the same order (one per decision point).
    has_meta = len(replay_records) == len(result_records)

    # ── 1. Mode distribution ─────────────────────────────────────────────
    mode_counts = Counter(r.get("mode", "unknown") for r in result_records)
    exact_n = mode_counts.get("exact", 0)
    emerg_n = mode_counts.get("emergency", 0)
    preflop_n = mode_counts.get("preflop_chart", 0)
    error_n = sum(1 for r in result_records if "error" in r)

    print("=" * 72)
    print("BASELINE HIT-RATE REPORT (v2)")
    print("=" * 72)
    print(f"  Total decisions:  {total}")
    print(f"  PREFLOP_CHART:    {preflop_n:5d}  ({_pct(preflop_n, total)})")
    print(f"  EXACT:            {exact_n:5d}  ({_pct(exact_n, total)})")
    print(f"  EMERGENCY:        {emerg_n:5d}  ({_pct(emerg_n, total)})")
    guided_n = exact_n + preflop_n
    print(f"  Guided total:     {guided_n:5d}  ({_pct(guided_n, total)})")
    if error_n:
        print(f"  ERRORS:           {error_n:5d}  ({_pct(error_n, total)})")

    # ── 2. Pot class x street matrix ─────────────────────────────────────
    if has_meta:
        print()
        print("--- Pot class x street matrix ---")

        matrix = defaultdict(Counter)
        street_totals = Counter()
        for i, rr in enumerate(replay_records):
            req = rr.get("request") or {}
            meta = rr.get("inference_metadata", {})
            ah_method = meta.get("action_history", "")
            street = req.get("street", "unknown")
            ah = req.get("action_history", [])

            if ah_method == "preflop_unknown":
                pot_class = "preflop_unknown"
            elif ah_method in ("no_preflop_street", "empty_streets"):
                pot_class = "no_data"
            elif ah_method.startswith("synthetic_"):
                pot_class = "review_synth"
            else:
                pot_class = _pot_class_from_ah(ah)

            matrix[pot_class][street] += 1
            street_totals[street] += 1

        streets_order = ["preflop", "flop", "turn", "river"]
        classes_order = ["preflop_unknown", "limped", "srp", "3bp",
                         "4bp+", "squeeze", "review_synth", "no_data"]
        # Only show classes that appear
        classes_order = [c for c in classes_order if c in matrix]

        header = f"{'':>18s}"
        for s in streets_order:
            header += f" {s:>8s}"
        header += f" {'TOTAL':>8s}"
        print(header)

        for cls in classes_order:
            row = f"  {cls:>16s}"
            row_total = 0
            for s in streets_order:
                c = matrix[cls].get(s, 0)
                row_total += c
                row += f" {c:8d}" if c > 0 else f" {'':>8s}"
            row += f" {row_total:8d}"
            print(row)

        # Totals row
        row = f"  {'TOTAL':>16s}"
        grand = 0
        for s in streets_order:
            c = street_totals.get(s, 0)
            grand += c
            row += f" {c:8d}"
        row += f" {grand:8d}"
        print(row)

    # ── 3. EMERGENCY by structural reason ────────────────────────────────
    if has_meta:
        print()
        print("--- EMERGENCY by structural reason ---")

        reason_counts = Counter()
        reason_pot_exposure = defaultdict(float)
        for i, res in enumerate(result_records):
            if res.get("mode") != "emergency":
                continue
            rr = replay_records[i]
            req = rr.get("request") or {}
            meta = rr.get("inference_metadata", {})
            reason = _emergency_reason(req, meta)
            reason_counts[reason] += 1
            pot_bb = req.get("pot", 0) / req.get("big_blind", 10) if req.get("big_blind", 10) > 0 else 0
            reason_pot_exposure[reason] += pot_bb

        if emerg_n > 0:
            print(f"  {'Reason':>30s} {'Count':>6s} {'%':>7s} {'Pot(bb)':>9s}")
            for reason, count in reason_counts.most_common():
                exposure = reason_pot_exposure[reason]
                print(f"  {reason:>30s} {count:6d} {_pct(count, emerg_n)} {exposure:9.1f}")

    # ── 4. Missing SRP board buckets ─────────────────────────────────────
    if has_meta:
        print()
        print("--- Missing SRP flop board buckets (S100, 2-way) ---")

        missing_bb = Counter()
        for i, res in enumerate(result_records):
            if res.get("mode") != "emergency":
                continue
            rr = replay_records[i]
            req = rr.get("request") or {}
            meta = rr.get("inference_metadata", {})
            reason = _emergency_reason(req, meta)
            if reason == "srp_board_bucket_miss":
                bb_idx = req.get("board_bucket")
                if bb_idx is not None:
                    missing_bb[bb_idx] += 1

        if missing_bb:
            cumulative = 0
            for bb_idx, count in missing_bb.most_common():
                cumulative += count
                pct_of_missing = 100 * cumulative / sum(missing_bb.values())
                print(f"  bb{bb_idx:2d}: {count:3d} decisions  "
                      f"(cumulative {pct_of_missing:.0f}%)")
        else:
            print("  No SRP board bucket misses (or no SRP hands).")

    # ── 5. Stack bucket distribution ─────────────────────────────────────
    if has_meta:
        print()
        print("--- Stack bucket distribution ---")

        sb_dist = Counter()
        sb_exact = Counter()
        for i, rr in enumerate(replay_records):
            req = rr.get("request") or {}
            eff = req.get("effective_stack_bb", 0)
            sb = _stack_bucket(eff)
            sb_dist[sb] += 1
            if i < len(result_records) and result_records[i].get("mode") == "exact":
                sb_exact[sb] += 1

        for sb in ["S40", "S60", "S100", "S150", "S200+"]:
            n = sb_dist.get(sb, 0)
            ex = sb_exact.get(sb, 0)
            hit = f"{100*ex/n:.1f}%" if n > 0 else "n/a"
            print(f"  {sb:>6s}: {n:5d}  ({_pct(n, total)})  "
                  f"EXACT hit: {ex:4d} ({hit})")

    # ── 6. Snap reason breakdown ─────────────────────────────────────────
    print()
    print("--- Legalizer snap rate ---")
    snapped = [r for r in result_records if r.get("was_snapped")]
    print(f"  Snapped: {len(snapped)}/{total} ({_pct(len(snapped), total)})")
    snap_reasons = Counter(r.get("snap_reason", "unknown") for r in snapped)
    for reason, count in snap_reasons.most_common():
        print(f"    {reason:30s}  {count}")

    # ── 7. Trust bucket distribution ─────────────────────────────────────
    print()
    print("--- Trust score distribution ---")
    trust_vals = [r.get("trust_score", 0) for r in result_records
                  if "trust_score" in r]
    if trust_vals:
        print(f"  Mean:  {sum(trust_vals)/len(trust_vals):.3f}")
        print(f"  Min:   {min(trust_vals):.3f}")
        print(f"  Max:   {max(trust_vals):.3f}")
        buckets = Counter()
        for t in trust_vals:
            if t < 0.3:
                buckets["0.0-0.3"] += 1
            elif t < 0.5:
                buckets["0.3-0.5"] += 1
            elif t < 0.7:
                buckets["0.5-0.7"] += 1
            else:
                buckets["0.7-1.0"] += 1
        for b in ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-1.0"]:
            c = buckets.get(b, 0)
            print(f"    {b}: {c:5d}  ({_pct(c, len(trust_vals))})")

    # ── 8. Inference method quality ──────────────────────────────────────
    if has_meta:
        print()
        print("--- Inference method distribution ---")
        ah_methods = Counter()
        for rr in replay_records:
            meta = rr.get("inference_metadata", {})
            ah_methods[meta.get("action_history", "unknown")] += 1

        for method, count in ah_methods.most_common():
            print(f"  {method:40s} {count:5d}  ({_pct(count, total)})")

    # ── 9. Postflop-only summary ─────────────────────────────────────────
    if has_meta:
        print()
        print("--- Postflop-only summary (excluding preflop_unknown) ---")
        postflop_indices = [
            i for i, rr in enumerate(replay_records)
            if (rr.get("inference_metadata", {}).get("action_history", "")
                != "preflop_unknown")
            and rr.get("request", {}).get("street", "") != "preflop"
        ]
        pf_total = len(postflop_indices)
        pf_exact = sum(1 for i in postflop_indices
                       if result_records[i].get("mode") == "exact")
        pf_emerg = pf_total - pf_exact
        print(f"  Postflop decisions: {pf_total}")
        print(f"  EXACT:              {pf_exact:5d}  ({_pct(pf_exact, pf_total)})")
        print(f"  EMERGENCY:          {pf_emerg:5d}  ({_pct(pf_emerg, pf_total)})")

        # Pot class distribution on postflop only
        pf_pot_class = Counter()
        for i in postflop_indices:
            rr = replay_records[i]
            req = rr.get("request") or {}
            ah = req.get("action_history", [])
            meta = rr.get("inference_metadata", {})
            ah_method = meta.get("action_history", "")
            if ah_method.startswith("synthetic_"):
                pf_pot_class["review_synth"] += 1
            elif ah_method in ("no_preflop_street", "empty_streets"):
                pf_pot_class["no_data"] += 1
            else:
                pf_pot_class[_pot_class_from_ah(ah)] += 1

        print(f"  Pot class (postflop):")
        for cls, count in pf_pot_class.most_common():
            print(f"    {cls:>16s}: {count:5d}  ({_pct(count, pf_total)})")

    print()
    print("=" * 72)


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} replay.jsonl results.jsonl", file=sys.stderr)
        print(f"       {sys.argv[0]} --results-only results.jsonl", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--results-only":
        result_records = load_jsonl(sys.argv[2])
        report_v2([], result_records)
    elif len(sys.argv) >= 3:
        replay_records = load_jsonl(sys.argv[1])
        result_records = load_jsonl(sys.argv[2])

        if len(replay_records) != len(result_records):
            print(f"WARNING: replay ({len(replay_records)}) and results "
                  f"({len(result_records)}) have different counts.",
                  file=sys.stderr)
            # Truncate to shorter
            n = min(len(replay_records), len(result_records))
            replay_records = replay_records[:n]
            result_records = result_records[:n]

        report_v2(replay_records, result_records)
    else:
        print(f"usage: {sys.argv[0]} replay.jsonl results.jsonl", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
