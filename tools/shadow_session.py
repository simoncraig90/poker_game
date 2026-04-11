"""Shadow session orchestrator.

Runs: preflight checks → coinpoker_runner --shadow → post-session summary.

Usage:
    python tools/shadow_session.py                    # live follow mode
    python tools/shadow_session.py --replay           # replay mode (testing)
    python tools/shadow_session.py --skip-tests       # skip test suite gate
    python tools/shadow_session.py --skip-replay-gate # skip replay_whatif gate
    python tools/shadow_session.py --max-minutes 60   # session time limit

Exit codes:
    0  Clean session exit
    1  Preflight failed (NO-GO)
    2  Tooling error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "vision"))


def run_preflight(session_id: str, skip_tests: bool, skip_replay: bool,
                  verbose: bool, frame_path: str) -> dict:
    """Run all preflight gates and return a summary dict."""
    from check_ready_for_live import run_preflight as _run
    return _run(
        session_id=session_id,
        skip_tests=skip_tests,
        skip_replay=skip_replay,
        verbose=verbose,
        frame_path=frame_path,
    )


def write_preflight_report(report: dict, output_dir: str) -> str:
    """Write the preflight report to disk. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"preflight_{report['session_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return path


def run_session(session_id: str, replay: bool, frame_path: str,
                multi_table: bool, hero_id: int, max_minutes: int) -> int:
    """Run the coinpoker_runner with shadow mode. Returns exit code."""
    from coinpoker_runner import main as runner_main

    argv = ["--shadow", "--session-id", session_id]
    if replay:
        argv.append("--replay")
    else:
        argv.append("--follow")
    if multi_table:
        argv.append("--multi-table")
    argv.extend(["--hero-id", str(hero_id)])
    argv.extend(["--file", frame_path])

    if max_minutes > 0 and not replay:
        # Run with a time limit via threading timer
        import threading

        stop = threading.Event()

        def _timer():
            stop.wait(max_minutes * 60)
            if not stop.is_set():
                print(f"\n[shadow] session time limit reached ({max_minutes}min)")
                # Raise KeyboardInterrupt in the main thread
                import _thread
                _thread.interrupt_main()

        t = threading.Thread(target=_timer, daemon=True)
        t.start()
        try:
            rc = runner_main(argv)
        finally:
            stop.set()
        return rc

    return runner_main(argv)


def print_post_session_summary(session_id: str, data_dir: str) -> None:
    """Print a summary of the shadow session log."""
    log_path = os.path.join(data_dir, f"shadow_{session_id}.jsonl")
    if not os.path.exists(log_path):
        print(f"[shadow] no log found at {log_path}")
        return

    from collections import Counter
    meta = None
    n_decisions = 0
    n_anomalies = 0
    n_unsafe = 0
    n_warns = 0
    check_counts = Counter()

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("type", "")
            if t == "session_meta":
                meta = r
            elif t == "decision":
                n_decisions += 1
            elif t == "validation_anomaly":
                n_anomalies += 1
                if r.get("severity") == "unsafe":
                    n_unsafe += 1
                elif r.get("severity") == "warn":
                    n_warns += 1
                for c in r.get("checks_failed", []):
                    check_counts[c] += 1

    print()
    print("=" * 60)
    print("  POST-SESSION SUMMARY")
    print("=" * 60)
    if meta:
        print(f"  session_id:      {meta.get('session_id')}")
        print(f"  start:           {meta.get('start_ts', '')[:19]}")
        print(f"  end:             {meta.get('end_ts', '')[:19]}")
        print(f"  decisions:       {meta.get('total_decisions', 0)}")
        print(f"  hands:           {meta.get('total_hands', 0)}")
        print(f"  mode breakdown:  {meta.get('mode_counts', {})}")
        print(f"  mean trust:      {meta.get('mean_trust', 0):.3f}")
        print(f"  mean latency:    {meta.get('mean_latency_us', 0)} us")
        print(f"  focus:           {meta.get('focus_succeeded', 0)}/{meta.get('focus_requests', 0)}")
        print(f"  validation:      {n_warns} warns, {n_unsafe} unsafe (of {n_anomalies} total)")
    else:
        print(f"  decisions: {n_decisions}")
        print(f"  anomalies: {n_anomalies} ({n_warns} warn, {n_unsafe} unsafe)")

    if check_counts:
        print(f"  top checks:      {dict(check_counts.most_common(5))}")
    print(f"  log:             {log_path}")
    print("=" * 60)
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Shadow session orchestrator")
    p.add_argument("--replay", action="store_true",
                   help="Replay mode (read file once, no overlay)")
    p.add_argument("--multi-table", action="store_true",
                   help="Multi-table mode")
    p.add_argument("--hero-id", type=int, default=1571120)
    p.add_argument("--file", default=r"C:\Users\Simon\coinpoker_frames.jsonl")
    p.add_argument("--skip-tests", action="store_true",
                   help="Skip test suite preflight gate")
    p.add_argument("--skip-replay-gate", action="store_true",
                   help="Skip replay_whatif preflight gate")
    p.add_argument("--skip-preflight", action="store_true",
                   help="Skip all preflight gates (DANGEROUS)")
    p.add_argument("--max-minutes", type=int, default=0,
                   help="Session time limit in minutes (0 = unlimited)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    session_id = uuid.uuid4().hex[:12]
    data_dir = os.path.join(ROOT, "vision", "data")

    print("=" * 60)
    print(f"  SHADOW SESSION — {session_id}")
    print("=" * 60)
    print()

    # ── Preflight ────────────────────────────────────────────────────
    if not args.skip_preflight:
        print("[preflight] running gates ...")
        report = run_preflight(
            session_id=session_id,
            skip_tests=args.skip_tests,
            skip_replay=args.skip_replay_gate,
            verbose=args.verbose,
            frame_path=args.file,
        )
        pf_path = write_preflight_report(report, data_dir)
        print(f"[preflight] report: {pf_path}")

        verdict = report.get("verdict", "NO-GO")
        if verdict != "GO":
            print()
            print(f"  PREFLIGHT VERDICT: {verdict}")
            for gate, result in report.get("gates", {}).items():
                status = "PASS" if result == "PASS" else "FAIL"
                print(f"    [{status}] {gate:25} {result}")
            print()
            print("  Session aborted. Fix the failing gates and retry.")
            return 1

        print(f"[preflight] verdict: GO")
        for gate, result in report.get("gates", {}).items():
            print(f"  [PASS] {gate}")
        print()
    else:
        print("[preflight] SKIPPED (--skip-preflight)")
        print()

    # ── Session ──────────────────────────────────────────────────────
    try:
        rc = run_session(
            session_id=session_id,
            replay=args.replay,
            frame_path=args.file,
            multi_table=args.multi_table,
            hero_id=args.hero_id,
            max_minutes=args.max_minutes,
        )
    except KeyboardInterrupt:
        print("\n[shadow] session interrupted")
        rc = 0

    # ── Post-session ─────────────────────────────────────────────────
    print_post_session_summary(session_id, data_dir)

    # Binary hash stability check
    if not args.skip_preflight:
        import hashlib
        bin_path = os.path.join(ROOT, "rust", "target", "release",
                                "advisor-cli.exe" if sys.platform == "win32" else "advisor-cli")
        if os.path.exists(bin_path):
            h = hashlib.sha256(open(bin_path, "rb").read()).hexdigest()
            pre_hash = report.get("binary_hash", "").replace("sha256:", "")
            if pre_hash and h != pre_hash:
                print("  WARNING: binary hash changed during session!")
                print(f"    pre:  {pre_hash[:24]}...")
                print(f"    post: {h[:24]}...")
            else:
                print("  binary hash stable across session")

    return rc


if __name__ == "__main__":
    sys.exit(main())
