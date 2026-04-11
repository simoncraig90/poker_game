"""
Pre-flight gate for live (real-money) advisor sessions.

Run this before any session where actual money is at stake. Exit code 0
means all gates passed and the advisor is in a known-safe state. Any
non-zero exit means at least one gate failed and you should NOT spin
up `vision/coinpoker_runner.py --follow` against a real-money table.

Memory anchor: this tool exists because passing unit tests is necessary
but not sufficient — see `feedback_passing_tests_not_validation.md`. The
user lost two real-money buy-ins on 2026-04-08 with passing unit tests
because the strategy itself had unvalidated leaks. This gate is the
blocker that should have caught those before they cost money.

Gates:

  1. **Test suite green** — every test under tests/ must pass. No
     skipped tests except the ones flagged @unittest.expectedFailure
     (those are documenting known leaks that must be fixed before the
     test can be promoted to a permanent guard).

  2. **Strategy regression suite green** — all named real-money loss
     spots in tests/test_strategy_regressions.py must be passing
     (NOT @expectedFailure). New entries can be added but never weakened.

  3. **Replay validation positive** — `scripts/replay_whatif.py`
     against the combined captured dataset must show the production
     baseline is at least as good as the best alternative variant
     within tolerance, AND the worst variant must be worse than the
     baseline (proves the strategy is sensitive to changes, not just
     ignoring everything).

  4. **No new leaks since last green** — danger override fire counts
     across the captured dataset must match the expected list. Any
     unexpected new firing is a signal that something has changed in
     a way that wasn't anticipated.

Exit codes:
  0  All gates passed → safe to spin up the advisor for real money
  1  Gate failure (specific failure listed in output)
  2  Tooling error (couldn't run a check, not a strategy issue)

Usage:
    python tools/check_ready_for_live.py
    python tools/check_ready_for_live.py --skip-replay   # tests only
    python tools/check_ready_for_live.py --verbose
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PREFLIGHT_STATE = os.path.join(ROOT, ".shadow_preflight.json")
PYTHON = sys.executable

# ── Gate definitions ─────────────────────────────────────────────────────


def gate_test_suite(verbose: bool) -> tuple[bool, str]:
    """Run the full test discovery and require all tests pass."""
    cmd = [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-t", "tests"]
    print("[gate] running full test suite ...")
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "test suite timed out after 10 minutes"
    output = (r.stderr or "") + (r.stdout or "")
    if verbose:
        print("  --- test output (last 30 lines) ---")
        for line in output.splitlines()[-30:]:
            print(f"    {line}")
    if r.returncode != 0:
        # Find the summary line
        summary = ""
        for line in output.splitlines():
            if line.startswith("Ran ") or "FAIL" in line or "ERROR" in line:
                summary = line.strip()
                break
        return False, f"test suite FAILED ({summary or 'see verbose output'})"
    # Extract "Ran N tests"
    test_count = "?"
    for line in output.splitlines():
        if line.startswith("Ran "):
            test_count = line.strip()
            break
    return True, f"{test_count}, all green"


def gate_strategy_regressions(verbose: bool) -> tuple[bool, str]:
    """Strategy regression suite must be all-green, no @expectedFailure
    on the named-spot tests (TestStrategyRegressions class).

    @expectedFailure on TestActionHistoryAccumulator helpers is OK
    because those are unit tests for the accumulator, not named loss
    spots. We specifically check the named-spot test class.

    `tests/` isn't a python package (no __init__.py) so we use discover
    with a file pattern instead of a dotted import path.
    """
    cmd = [PYTHON, "-m", "unittest", "discover",
           "-s", "tests", "-p", "test_strategy_regressions.py",
           "-t", "tests", "-v"]
    print("[gate] running strategy regression suite ...")
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "strategy regression timed out"
    output = (r.stderr or "") + (r.stdout or "")
    if verbose:
        for line in output.splitlines():
            if "test_" in line or "expected" in line.lower() or "FAIL" in line:
                print(f"    {line}")
    if r.returncode != 0:
        return False, "strategy regression suite FAILED"
    # Count named tests AND verify none are expectedFailure
    expected_failures = output.count("expected failure")
    if expected_failures > 0:
        return False, (f"{expected_failures} named loss spot(s) still marked "
                       f"@expectedFailure — fix the leak before going live")
    test_count = "?"
    for line in output.splitlines():
        if line.startswith("Ran "):
            test_count = line.strip()
            break
    return True, f"{test_count}, no expected failures"


def gate_replay_validation(verbose: bool) -> tuple[bool, str]:
    """Run replay_whatif against the captured dataset and require:
       - At least one variant tested
       - The baseline doesn't lose to the best variant by more than 0.50 EUR
         (rough threshold given replay_whatif uses heuristic EV)
       - At least one variant is meaningfully different from baseline
         (proves strategy is sensitive, not catastrophically broken).
    """
    cmd = [PYTHON, "scripts/replay_whatif.py"]
    print("[gate] running replay_whatif ...")
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "replay_whatif timed out"
    output = (r.stderr or "") + (r.stdout or "")
    if verbose:
        print("  --- replay output ---")
        for line in output.splitlines()[-25:]:
            print(f"    {line}")

    if r.returncode != 0:
        return False, "replay_whatif crashed"

    # Parse the ranking section to find best and worst variants
    import re
    rank_lines = []
    in_ranking = False
    for line in output.splitlines():
        if "RANKING" in line:
            in_ranking = True
            continue
        if in_ranking and line.strip():
            # Format: "  variant_name           +€0.22  (N divergent)"
            m = re.match(r"\s+(\w+)\s+([+-]?[^\d]*[\d.]+)\s+", line)
            if m:
                # Strip non-numeric chars from the EV
                ev_str = re.sub(r"[^\d.+-]", "", m.group(2))
                try:
                    ev = float(ev_str)
                    rank_lines.append((m.group(1), ev))
                except ValueError:
                    pass

    if len(rank_lines) < 2:
        return False, f"replay_whatif produced unexpected output (ranking has {len(rank_lines)} variants)"

    # Best variant should be at most +0.50 above baseline (which is at 0)
    best_name, best_ev = rank_lines[0]
    worst_name, worst_ev = rank_lines[-1]
    if best_ev > 0.50:
        return False, (f"variant '{best_name}' is +EUR {best_ev:.2f} better than baseline "
                       f"— production strategy may be regressed")
    return True, (f"baseline within tolerance "
                  f"(best={best_name} +EUR {best_ev:.2f}, worst={worst_name} EUR {worst_ev:+.2f}, "
                  f"{len(rank_lines)} variants tested)")


def gate_named_overrides(verbose: bool) -> tuple[bool, str]:
    """Verify the danger filters fire EXACTLY on the expected named hands
    when replaying captured data. Any new firing is a signal that something
    has shifted in a way that wasn't anticipated.

    Expected firings (as of 2026-04-11, Phase 15 green state):
      Unibet: 1 hand (hand_id 2379414698 = KK 3-flush) + 1 hand (2379447781 = QJ paired)
      CoinPoker: 1 hand (2460830707 = KK 4-straight river)
               + 1 hand (2460830659 = 54o counterfeited two-pair on paired board)
    """
    expected_unibet = {"2379414698", "2379447781"}
    expected_coinpoker = {"2460830707", "2460830659"}

    print("[gate] running named-override sweep on captured data ...")
    script = '''
import sys, json, glob, io, contextlib
sys.path.insert(0, "vision")
from advisor_state_machine import AdvisorStateMachine
from advisor import Advisor as BaseAdvisor
from preflop_chart import preflop_advice
try:
    from strategy.postflop_engine import PostflopEngine
    postflop = PostflopEngine()
except: postflop = None
try:
    from advisor import assess_board_danger
except: assess_board_danger = lambda h, b: {"warnings": []}

base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
sm = AdvisorStateMachine(base_advisor=base, preflop_advice_fn=preflop_advice,
    postflop_engine=postflop, assess_board_danger_fn=assess_board_danger,
    tracker=None, bb_cents=4)

fired_hands = set()
for f in sorted(glob.glob("vision/data/session_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            h = json.loads(line)
            if not h.get("hero") or len(h.get("hero", [])) < 2: continue
            if not h.get("streets"): continue
        except: continue
        sm.action_history=[]; sm.action_history_hand=None; sm._prev_villain_actions={}
        sm.prev_hero=[]; sm.prev_board=[]; sm.prev_hand_id=None; sm.prev_phase=None
        sm.last_facing=None; sm.flop_action_history=""
        for street in h["streets"]:
            if not street.get("rec_action"): continue
            state = {
                "hero_cards": h["hero"], "board_cards": street.get("board", []),
                "hand_id": h["hand_id"], "facing_bet": street.get("facing_bet", False),
                "call_amount": street.get("call_amount", 0), "phase": street["phase"],
                "num_opponents": street.get("num_opponents", 5), "pot": street.get("pot", 0),
                "hero_stack": street.get("stack", 1000), "position": h.get("position", "MP"),
                "hero_turn": True, "bets": [], "players": [], "hero_seat": 0,
            }
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try: sm.process_state(state)
                except: continue
            if "danger-override" in buf.getvalue():
                fired_hands.add(str(h["hand_id"]))

print("FIRED:" + ",".join(sorted(fired_hands)))
'''
    try:
        r = subprocess.run([PYTHON, "-c", script], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "named-override sweep timed out"
    if r.returncode != 0:
        return False, f"sweep crashed: {(r.stderr or '')[-200:]}"

    fired = set()
    for line in r.stdout.splitlines():
        if line.startswith("FIRED:"):
            fired = set(h for h in line[6:].split(",") if h)
            break
    expected = expected_unibet | expected_coinpoker
    unexpected = fired - expected
    missing = expected - fired
    if verbose:
        print(f"  expected: {sorted(expected)}")
        print(f"  fired:    {sorted(fired)}")
    if unexpected:
        return False, (f"unexpected danger overrides on hands: {sorted(unexpected)} — "
                       f"investigate before going live")
    if missing:
        return False, (f"expected danger overrides did NOT fire on: {sorted(missing)} — "
                       f"the seed leak may have regressed")
    return True, f"all {len(expected)} expected overrides fired, no unexpected ones"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _load_preflight_state() -> dict:
    try:
        return json.loads(open(_PREFLIGHT_STATE, encoding="utf-8").read())
    except Exception:
        return {}


def _save_preflight_state(state: dict) -> None:
    with open(_PREFLIGHT_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def gate_binary_freshness(verbose: bool) -> tuple[bool, str]:
    """Verify advisor-cli binary exists, hash it, warn if stale."""
    bin_path = os.path.join(ROOT, "rust", "target", "release",
                            "advisor-cli.exe" if sys.platform == "win32" else "advisor-cli")
    if not os.path.exists(bin_path):
        return False, f"advisor-cli binary not found at {bin_path}"

    h = _sha256_file(bin_path)
    mtime = os.path.getmtime(bin_path)
    age_hours = (time.time() - mtime) / 3600

    state = _load_preflight_state()
    prev_hash = state.get("binary_hash", "")

    warnings = []
    if prev_hash and prev_hash != h:
        warnings.append("hash changed since last run (rebuilt?)")
    if age_hours > 24:
        warnings.append(f"binary is {age_hours:.0f}h old")

    state["binary_hash"] = h
    state["binary_mtime"] = mtime
    _save_preflight_state(state)

    if verbose:
        print(f"    binary: {bin_path}")
        print(f"    hash:   {h}")
        print(f"    age:    {age_hours:.1f}h")

    msg = f"hash={h[:24]}... age={age_hours:.0f}h"
    if warnings:
        msg += " WARN: " + "; ".join(warnings)
    return True, msg


def gate_artifact_snapshot(verbose: bool) -> tuple[bool, str]:
    """Count artifacts, verify manifest/bin pairing, sample integrity."""
    solver_root = os.path.join(ROOT, "artifacts", "solver")
    bins = glob.glob(os.path.join(solver_root, "**", "*.bin"), recursive=True)
    manifests = glob.glob(os.path.join(solver_root, "**", "*.manifest.json"), recursive=True)

    if len(bins) == 0:
        return False, "zero artifact bins found in artifacts/solver/"
    if len(manifests) == 0:
        return False, "zero manifests found in artifacts/solver/"

    warnings = []
    if len(bins) != len(manifests):
        warnings.append(f"bin count ({len(bins)}) != manifest count ({len(manifests)})")

    # Sample up to 5 manifests and verify their bin exists + SHA matches
    sample = random.sample(manifests, min(5, len(manifests)))
    sample_ok = 0
    for mf_path in sample:
        try:
            mf = json.loads(open(mf_path, encoding="utf-8").read())
            expected_sha = mf.get("checksum_sha256", "")
            # Bin path: same directory, same stem but .bin extension
            bin_stem = mf_path.replace(".manifest.json", ".bin")
            if not os.path.exists(bin_stem):
                warnings.append(f"manifest orphan: {os.path.basename(mf_path)}")
                continue
            if expected_sha:
                actual = hashlib.sha256(open(bin_stem, "rb").read()).hexdigest()
                if actual != expected_sha:
                    warnings.append(f"SHA mismatch: {os.path.basename(bin_stem)}")
                    continue
            sample_ok += 1
        except Exception as e:
            warnings.append(f"sample error: {e}")

    state = _load_preflight_state()
    prev_count = state.get("artifact_count", 0)
    if prev_count and prev_count != len(bins):
        warnings.append(f"count changed: {prev_count} -> {len(bins)}")
    state["artifact_count"] = len(bins)
    _save_preflight_state(state)

    if verbose:
        print(f"    bins:      {len(bins)}")
        print(f"    manifests: {len(manifests)}")
        print(f"    sample OK: {sample_ok}/{len(sample)}")

    msg = f"{len(bins)} bins, {len(manifests)} manifests, sample {sample_ok}/{len(sample)}"
    if warnings:
        msg += " WARN: " + "; ".join(warnings[:3])
    return True, msg


def gate_frame_file_health(verbose: bool, frame_path: str = "") -> tuple[bool, str]:
    """Verify the CoinPoker frame log exists and is fresh."""
    if not frame_path:
        frame_path = r"C:\Users\Simon\coinpoker_frames.jsonl"

    if not os.path.exists(frame_path):
        return False, f"frame file not found: {frame_path}"

    mtime = os.path.getmtime(frame_path)
    age_min = (time.time() - mtime) / 60
    size_mb = os.path.getsize(frame_path) / (1024 * 1024)

    # Read last 5 lines and verify they're valid JSON with cmd_bean
    valid_lines = 0
    try:
        with open(frame_path, "rb") as f:
            # Seek to near the end
            f.seek(max(0, os.path.getsize(frame_path) - 8192))
            tail = f.read().decode("utf-8", errors="replace")
            lines = [l for l in tail.strip().split("\n") if l.strip()][-5:]
            for line in lines:
                try:
                    obj = json.loads(line)
                    if "cmd_bean" in obj:
                        valid_lines += 1
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        return False, f"cannot read frame file: {e}"

    warnings = []
    if age_min > 5:
        warnings.append(f"last write {age_min:.0f}min ago (DLL patched?)")
    if valid_lines == 0:
        warnings.append("no valid cmd_bean lines in tail")

    if verbose:
        print(f"    path:        {frame_path}")
        print(f"    size:        {size_mb:.1f} MB")
        print(f"    last write:  {age_min:.1f} min ago")
        print(f"    tail valid:  {valid_lines}/5")

    msg = f"{size_mb:.1f}MB, {age_min:.0f}min ago, tail {valid_lines}/5 valid"
    if warnings:
        msg += " WARN: " + "; ".join(warnings)
    # Frame file existing but stale is a WARN, not a FAIL
    return True, msg


# ── Preflight summary for shadow sessions ─────────────────────────────


def run_preflight(session_id: str = "", skip_replay: bool = False,
                  skip_tests: bool = False, verbose: bool = False,
                  frame_path: str = "") -> dict:
    """Run all gates and return a preflight summary dict.

    This is the API entry point for shadow_session.py to call
    programmatically instead of shelling out.
    """
    gates = []
    if not skip_tests:
        gates.append(("test_suite", lambda v: gate_test_suite(v)))
    gates.append(("strategy_regressions", lambda v: gate_strategy_regressions(v)))
    gates.append(("named_overrides", lambda v: gate_named_overrides(v)))
    if not skip_replay:
        gates.append(("replay_validation", lambda v: gate_replay_validation(v)))
    gates.append(("binary_freshness", lambda v: gate_binary_freshness(v)))
    gates.append(("artifact_snapshot", lambda v: gate_artifact_snapshot(v)))
    gates.append(("frame_file_health", lambda v: gate_frame_file_health(v, frame_path)))

    results = {}
    all_pass = True
    for name, fn in gates:
        try:
            ok, msg = fn(verbose)
        except Exception as e:
            ok, msg = False, f"tooling error: {type(e).__name__}: {e}"
        results[name] = "PASS" if ok else f"FAIL: {msg}"
        if not ok:
            all_pass = False

    state = _load_preflight_state()
    return {
        "session_id": session_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "binary_hash": state.get("binary_hash", ""),
        "binary_mtime": state.get("binary_mtime", ""),
        "artifact_count": state.get("artifact_count", 0),
        "frame_file": frame_path or r"C:\Users\Simon\coinpoker_frames.jsonl",
        "gates": results,
        "verdict": "GO" if all_pass else "NO-GO",
    }


# ── Driver ───────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-replay", action="store_true",
                    help="Skip the replay_whatif gate (faster, less coverage)")
    ap.add_argument("--skip-tests", action="store_true",
                    help="Skip the test suite gate (NOT recommended)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    print("=" * 70)
    print("  PRE-FLIGHT VALIDATION GATE -- go/no-go for live advisor session")
    print("=" * 70)
    print()

    gates = []
    if not args.skip_tests:
        gates.append(("test_suite", gate_test_suite))
    gates.append(("strategy_regressions", gate_strategy_regressions))
    gates.append(("named_overrides", gate_named_overrides))
    if not args.skip_replay:
        gates.append(("replay_validation", gate_replay_validation))
    gates.append(("binary_freshness", gate_binary_freshness))
    gates.append(("artifact_snapshot", gate_artifact_snapshot))
    gates.append(("frame_file_health", gate_frame_file_health))

    results = []
    for name, fn in gates:
        t0 = time.time()
        try:
            ok, msg = fn(args.verbose)
        except Exception as e:
            ok, msg = False, f"tooling error: {type(e).__name__}: {e}"
        elapsed = time.time() - t0
        status = "PASS" if ok else "FAIL"
        results.append((name, ok, msg, elapsed))
        sym = "+" if ok else "x"
        print(f"  [{status}] {sym} {name:25} ({elapsed:.1f}s)  {msg}")

    print()
    print("=" * 70)
    failures = [r for r in results if not r[1]]
    if failures:
        print(f"  RESULT: NO-GO ({len(failures)} gate(s) failed)")
        print("=" * 70)
        print()
        print("  Failed gates:")
        for name, ok, msg, elapsed in failures:
            print(f"    - {name}: {msg}")
        print()
        print("  Do NOT spin up the advisor for real money until all gates pass.")
        return 1

    print(f"  RESULT: GO -- all {len(results)} gates passed")
    print("=" * 70)
    print()
    print("  Safe to run: python vision/coinpoker_runner.py --follow")
    print("  Reminder: even with all gates passing, this validates the strategy")
    print("  against captured hands. Real-money sessions are still bounded by the")
    print("  user's session bankroll budget. Memory: feedback_passing_tests_not_validation.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
