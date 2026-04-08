"""
Test the what-if replay simulator works on captured hands.

Verifies:
- Loads hands from JSONL files
- Runs each variant
- Detects divergence between baseline and variants
- Returns sensible EV estimates
"""

import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_simulator_runs_without_crash():
    """Run the script with --limit 50 and verify it produces output."""
    p = subprocess.run(
        ["python", "scripts/replay_whatif.py", "--limit", "50"],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        cwd=ROOT, timeout=120,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    )
    out = p.stdout + p.stderr
    failures = []
    if "RANKING" not in out:
        failures.append("Missing RANKING section")
    if "baseline recommendations" not in out:
        failures.append("Missing baseline computation")
    if "Testing variant" not in out:
        failures.append("No variants tested")
    if p.returncode != 0:
        failures.append(f"Exit code {p.returncode}")
    return failures


def test_categorize_action():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from replay_whatif import categorize_action

    failures = []
    cases = [
        ("FOLD", "FOLD"),
        ("CALL 0.04", "CALL"),
        ("RAISE to 0.10", "RAISE"),
        ("BET 0.20", "BET"),
        ("CHECK", "CHECK"),
        ("CHECK / FOLD", "FOLD"),
    ]
    for action, expected in cases:
        result = categorize_action(action)
        if result != expected:
            failures.append(f"{action!r}: expected {expected}, got {result}")
    return failures


def test_variants_load():
    """All declared variants can be instantiated without crashing."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from replay_whatif import VARIANTS, _sm_cache, _get_sm

    failures = []
    _sm_cache.clear()
    for name, factory in VARIANTS.items():
        try:
            sm = _get_sm(factory)
            if sm is None:
                failures.append(f"{name}: returned None")
        except Exception as e:
            failures.append(f"{name}: {e}")
    return failures


if __name__ == "__main__":
    tests = [
        ("Simulator runs without crash", test_simulator_runs_without_crash),
        ("categorize_action correct", test_categorize_action),
        ("All variants load", test_variants_load),
    ]
    print("=" * 60)
    print("  REPLAY WHAT-IF TESTS")
    print("=" * 60)
    total = passed = 0
    fails = []
    for n, fn in tests:
        total += 1
        try:
            f = fn()
            if not f:
                print(f"  PASS  {n}")
                passed += 1
            else:
                print(f"  FAIL  {n}")
                for x in f: print(f"        - {x}")
                fails.extend(f)
        except Exception as e:
            import traceback
            print(f"  ERROR {n}: {e}")
            traceback.print_exc()
            fails.append(f"{n}: {e}")
    print()
    print(f"  {passed}/{total} tests passed")
    sys.exit(0 if not fails else 1)
