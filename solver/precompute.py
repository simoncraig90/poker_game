"""
Phase 1: Precompute flop solver solutions for all (scenario × cluster × SPR).

Runs the postflop-solver on each combination and stores results as compressed JSON.
Supports parallel execution and resume-on-interrupt.

Usage:
    python solver/precompute.py                    # all scenarios, all clusters, SPR bucket 3 only
    python solver/precompute.py --full             # all SPR buckets
    python solver/precompute.py --workers 4        # parallel workers
    python solver/precompute.py --scenario 0       # single scenario by index
    python solver/precompute.py --cluster-limit 10 # first 10 clusters only (for testing)
"""
import argparse
import json
import os
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Add solver dir to path
sys.path.insert(0, str(Path(__file__).parent))
from preflop_ranges import get_open_range, get_vs_open, range_to_solver_str, count_combos
from board_clusters import build_cluster_map, pick_representative, classify_flop
from solver_bridge import solve_spot

SOLUTIONS_DIR = Path(__file__).parent / "solutions"

# ============================================================
# Preflop scenarios — the most common HU postflop situations
# Each defines (description, oop_position, ip_position, oop_range_key, ip_range_key)
# ============================================================

def _build_scenarios():
    """Build the list of preflop scenarios with actual range strings."""
    scenarios = []

    # 1. BTN open, BB call (most common)
    btn_open = get_open_range("BTN")
    bb_vs_btn = get_vs_open("BB", "BTN")
    scenarios.append({
        "name": "BTN_open_BB_call",
        "desc": "BTN opens, BB calls",
        "oop_range": range_to_solver_str(bb_vs_btn["call"]),
        "ip_range": range_to_solver_str(btn_open),
        "oop_combos": count_combos(bb_vs_btn["call"]),
        "ip_combos": count_combos(btn_open),
    })

    # 2. CO open, BTN call
    co_open = get_open_range("CO")
    btn_vs_co = get_vs_open("BTN", "CO")
    scenarios.append({
        "name": "CO_open_BTN_call",
        "desc": "CO opens, BTN calls",
        "oop_range": range_to_solver_str(co_open),
        "ip_range": range_to_solver_str(btn_vs_co["call"]),
        "oop_combos": count_combos(co_open),
        "ip_combos": count_combos(btn_vs_co["call"]),
    })

    # 3. CO open, BB call
    bb_vs_co = get_vs_open("BB", "CO")
    scenarios.append({
        "name": "CO_open_BB_call",
        "desc": "CO opens, BB calls",
        "oop_range": range_to_solver_str(bb_vs_co["call"]),
        "ip_range": range_to_solver_str(co_open),
        "oop_combos": count_combos(bb_vs_co["call"]),
        "ip_combos": count_combos(co_open),
    })

    # 4. UTG open, BB call
    utg_open = get_open_range("UTG")
    bb_vs_utg = get_vs_open("BB", "UTG")
    scenarios.append({
        "name": "UTG_open_BB_call",
        "desc": "UTG opens, BB calls",
        "oop_range": range_to_solver_str(bb_vs_utg["call"]),
        "ip_range": range_to_solver_str(utg_open),
        "oop_combos": count_combos(bb_vs_utg["call"]),
        "ip_combos": count_combos(utg_open),
    })

    # 5. UTG open, CO call
    co_vs_utg = get_vs_open("CO", "UTG")
    scenarios.append({
        "name": "UTG_open_CO_call",
        "desc": "UTG opens, CO calls",
        "oop_range": range_to_solver_str(utg_open),
        "ip_range": range_to_solver_str(co_vs_utg["call"]),
        "oop_combos": count_combos(utg_open),
        "ip_combos": count_combos(co_vs_utg["call"]),
    })

    # 6. BTN open, SB 3bet, BTN call (3bet pot)
    sb_3bet_vs_btn = get_vs_open("SB", "BTN")
    # BTN call vs SB 3bet — approximate: remove the hands BTN would 4bet (AA,KK,AKs)
    # and the hands BTN would fold, keep the middle
    btn_call_3bet = {
        "QQ": 1.0, "JJ": 1.0, "TT": 1.0, "99": 0.5,
        "AKo": 0.5, "AQs": 1.0, "AJs": 1.0, "ATs": 0.5,
        "KQs": 1.0, "KJs": 0.5, "QJs": 1.0, "JTs": 1.0,
        "T9s": 1.0, "98s": 1.0, "87s": 1.0, "76s": 0.5, "65s": 0.5,
    }
    scenarios.append({
        "name": "SB_3bet_BTN_call",
        "desc": "BTN opens, SB 3bets, BTN calls (3bet pot)",
        "oop_range": range_to_solver_str(sb_3bet_vs_btn["3bet"]),
        "ip_range": range_to_solver_str(btn_call_3bet),
        "oop_combos": count_combos(sb_3bet_vs_btn["3bet"]),
        "ip_combos": count_combos(btn_call_3bet),
    })

    return scenarios


SCENARIOS = _build_scenarios()

# SPR buckets: (name, representative_pot, representative_stack)
SPR_BUCKETS = [
    ("spr_1",   400, 150),   # SPR ~0.4  (4bet pot / short)
    ("spr_2",   300, 700),   # SPR ~2.3  (3bet pot)
    ("spr_3",   200, 900),   # SPR ~4.5  (standard single raise, 100bb)
    ("spr_4",   120, 940),   # SPR ~7.8  (min-raise pot)
    ("spr_5",   120, 1380),  # SPR ~11.5 (deep, 150bb)
    ("spr_6",   100, 1900),  # SPR ~19   (very deep, 200bb)
]


def solve_one(scenario_idx: int, cluster_key: tuple, spr_name: str,
              pot: int, stack: int, board_str: str,
              iterations: int = 250) -> dict:
    """Solve a single (scenario, cluster, SPR) combination."""
    sc = SCENARIOS[scenario_idx]

    try:
        result = solve_spot(
            oop_range=sc["oop_range"],
            ip_range=sc["ip_range"],
            board=board_str,
            pot=pot,
            stack=stack,
            iterations=iterations,
            target_exploitability_pct=0.5,
            bet_sizes="33%, 75%, a",
            raise_sizes="2.5x",
        )
        return {
            "scenario": sc["name"],
            "cluster": str(cluster_key),
            "spr": spr_name,
            "board": board_str,
            "pot": pot,
            "stack": stack,
            "exploitability_pct": result.raw.get("exploitability_pct", 0),
            "oop_avg_eq": result.raw.get("oop_avg_eq", 0),
            "oop_avg_ev": result.raw.get("oop_avg_ev", 0),
            "ip_avg_eq": result.raw.get("ip_avg_eq", 0),
            "ip_avg_ev": result.raw.get("ip_avg_ev", 0),
            "nodes": result.raw.get("nodes", []),
            "error": None,
        }
    except Exception as e:
        return {
            "scenario": sc["name"],
            "cluster": str(cluster_key),
            "spr": spr_name,
            "board": board_str,
            "error": str(e),
        }


def output_path(scenario_name: str, cluster_key: tuple, spr_name: str) -> Path:
    cluster_str = "_".join(str(x).replace("/", "-") for x in cluster_key)
    d = SOLUTIONS_DIR / scenario_name
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{cluster_str}_{spr_name}.json.zlib"


def save_solution(data: dict, path: Path):
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    path.write_bytes(compressed)


def load_solution(path: Path) -> dict:
    compressed = path.read_bytes()
    raw = zlib.decompress(compressed)
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="All SPR buckets (default: bucket 3 only)")
    parser.add_argument("--workers", type=int, default=2, help="Parallel workers")
    parser.add_argument("--scenario", type=int, default=None, help="Single scenario index")
    parser.add_argument("--cluster-limit", type=int, default=None, help="Limit clusters (for testing)")
    parser.add_argument("--iterations", type=int, default=250, help="Solver iterations")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per solve (seconds)")
    args = parser.parse_args()

    clusters = build_cluster_map()
    cluster_keys = sorted(clusters.keys())
    if args.cluster_limit:
        cluster_keys = cluster_keys[:args.cluster_limit]

    spr_buckets = SPR_BUCKETS if args.full else [SPR_BUCKETS[2]]  # default: SPR 3-6 only

    scenario_indices = [args.scenario] if args.scenario is not None else list(range(len(SCENARIOS)))

    # Build job list, skip already-computed
    jobs = []
    skipped = 0
    for si in scenario_indices:
        sc = SCENARIOS[si]
        for ck in cluster_keys:
            rep = pick_representative(clusters[ck])
            board_str = " ".join(c for c in rep)
            for spr_name, pot, stack in spr_buckets:
                out = output_path(sc["name"], ck, spr_name)
                if out.exists():
                    skipped += 1
                    continue
                jobs.append((si, ck, spr_name, pot, stack, board_str, args.iterations))

    total = len(jobs) + skipped
    print(f"Scenarios: {len(scenario_indices)}, Clusters: {len(cluster_keys)}, SPR buckets: {len(spr_buckets)}")
    print(f"Total jobs: {total}, Already done: {skipped}, Remaining: {len(jobs)}")

    if not jobs:
        print("All solutions already computed!")
        return

    # Show scenario info
    for si in scenario_indices:
        sc = SCENARIOS[si]
        print(f"  [{si}] {sc['name']}: OOP {sc['oop_combos']:.0f} combos, IP {sc['ip_combos']:.0f} combos")

    print(f"\nStarting {len(jobs)} solves with {args.workers} workers...\n")

    done = 0
    errors = 0
    start = time.time()

    if args.workers <= 1:
        for job in jobs:
            si, ck, spr_name, pot, stack, board_str, iters = job
            sc = SCENARIOS[si]
            t0 = time.time()
            result = solve_one(si, ck, spr_name, pot, stack, board_str, iters)
            dt = time.time() - t0
            out = output_path(sc["name"], ck, spr_name)

            if result.get("error"):
                errors += 1
                print(f"  ERROR {sc['name']} {ck} {spr_name}: {result['error']}")
            else:
                save_solution(result, out)

            done += 1
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(jobs) - done) / rate if rate > 0 else 0
            print(f"  [{done}/{len(jobs)}] {sc['name']} {ck} {spr_name} — {dt:.1f}s — "
                  f"exploit={result.get('exploitability_pct', 'err')} — "
                  f"ETA {eta/60:.0f}min")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            future_to_job = {}
            for job in jobs:
                si, ck, spr_name, pot, stack, board_str, iters = job
                f = executor.submit(solve_one, si, ck, spr_name, pot, stack, board_str, iters)
                future_to_job[f] = job

            for future in as_completed(future_to_job):
                job = future_to_job[future]
                si, ck, spr_name, pot, stack, board_str, iters = job
                sc = SCENARIOS[si]
                out = output_path(sc["name"], ck, spr_name)

                try:
                    result = future.result(timeout=args.timeout)
                    if result.get("error"):
                        errors += 1
                        print(f"  ERROR {sc['name']} {ck} {spr_name}: {result['error']}")
                    else:
                        save_solution(result, out)
                except Exception as e:
                    errors += 1
                    result = {"error": str(e)}
                    print(f"  EXCEPTION {sc['name']} {ck} {spr_name}: {e}")

                done += 1
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(jobs) - done) / rate if rate > 0 else 0
                print(f"  [{done}/{len(jobs)}] {sc['name']} {str(ck)[:30]} {spr_name} — "
                      f"exploit={result.get('exploitability_pct', '?')} — "
                      f"ETA {eta/60:.0f}min")

    elapsed = time.time() - start
    print(f"\nDone: {done} solves in {elapsed/60:.1f} min ({errors} errors)")
    print(f"Solutions saved to {SOLUTIONS_DIR}")


if __name__ == "__main__":
    main()
