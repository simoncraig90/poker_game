"""Post-session shadow log review tool.

Parses a shadow session JSONL log, computes stats, and flags anomalies.
Designed for the human review gate in the shadow → supervised pilot path.

Usage:
    python tools/shadow_review.py vision/data/shadow_abc123.jsonl
    python tools/shadow_review.py vision/data/shadow_*.jsonl   # multi-session
    python tools/shadow_review.py --check-criteria vision/data/shadow_*.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import List


@dataclass
class SessionStats:
    session_id: str = ""
    log_path: str = ""
    total_decisions: int = 0
    total_hands: int = 0
    mode_counts: Counter = field(default_factory=Counter)
    trust_sum: float = 0.0
    latency_sum_us: int = 0
    validation_warns: int = 0
    validation_unsafe: int = 0
    focus_requests: int = 0
    focus_succeeded: int = 0
    check_counts: Counter = field(default_factory=Counter)
    # Per-decision details for review
    decisions: list = field(default_factory=list)
    anomalies: list = field(default_factory=list)

    @property
    def mean_trust(self) -> float:
        return self.trust_sum / max(self.total_decisions, 1)

    @property
    def mean_latency_us(self) -> int:
        return self.latency_sum_us // max(self.total_decisions, 1)

    @property
    def emergency_rate(self) -> float:
        em = self.mode_counts.get("emergency", 0)
        return em / max(self.total_decisions, 1)

    @property
    def warn_rate(self) -> float:
        return self.validation_warns / max(self.total_decisions, 1)

    @property
    def unsafe_rate(self) -> float:
        return self.validation_unsafe / max(self.total_decisions, 1)

    @property
    def focus_rate(self) -> float:
        return self.focus_succeeded / max(self.focus_requests, 1)


def parse_log(path: str) -> SessionStats:
    """Parse a single shadow session JSONL log into stats."""
    s = SessionStats(log_path=path)
    hand_ids = set()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = r.get("type", "")

            if t == "session_meta":
                s.session_id = r.get("session_id", "")

            elif t == "decision":
                s.total_decisions += 1
                s.mode_counts[r.get("mode", "unknown")] += 1
                s.trust_sum += r.get("trust_score", 0.0)
                s.latency_sum_us += r.get("latency_us", 0)
                hid = r.get("hand_id", "")
                if hid and hid not in hand_ids:
                    hand_ids.add(hid)
                    s.total_hands += 1
                s.decisions.append(r)

            elif t == "validation_anomaly":
                sev = r.get("severity", "")
                if sev == "warn":
                    s.validation_warns += 1
                elif sev == "unsafe":
                    s.validation_unsafe += 1
                for c in r.get("checks_failed", []):
                    s.check_counts[c] += 1
                s.anomalies.append(r)

            elif t == "focus_event":
                if r.get("requested"):
                    s.focus_requests += 1
                    if r.get("succeeded"):
                        s.focus_succeeded += 1

    return s


def print_session_report(s: SessionStats) -> None:
    """Print a human-readable session report."""
    print(f"\n{'=' * 60}")
    print(f"  SESSION: {s.session_id or '(unknown)'}")
    print(f"  LOG:     {s.log_path}")
    print(f"{'=' * 60}")
    print(f"  Decisions:       {s.total_decisions}")
    print(f"  Hands:           {s.total_hands}")
    print(f"  Mode breakdown:  {dict(s.mode_counts)}")
    print(f"  Emergency rate:  {s.emergency_rate:.1%}")
    print(f"  Mean trust:      {s.mean_trust:.3f}")
    print(f"  Mean latency:    {s.mean_latency_us} us")
    print(f"  Focus:           {s.focus_succeeded}/{s.focus_requests} "
          f"({s.focus_rate:.0%})")
    print(f"  Validation:      {s.validation_warns} warns, "
          f"{s.validation_unsafe} unsafe")
    print(f"  Warn rate:       {s.warn_rate:.1%}")
    if s.check_counts:
        print(f"  Top anomalies:   {dict(s.check_counts.most_common(5))}")

    # Flag suspicious decisions
    low_trust = [d for d in s.decisions if d.get("trust_score", 1.0) < 0.5]
    if low_trust:
        print(f"\n  LOW-TRUST DECISIONS ({len(low_trust)}):")
        for d in low_trust[:10]:
            print(f"    hand={d['hand_id']} {d['phase']:7} "
                  f"{' '.join(d.get('hero_cards', [])):<5} "
                  f"=> {d.get('action_kind', '?')} "
                  f"trust={d.get('trust_score', 0):.2f} "
                  f"mode={d.get('mode', '?')}")

    # Emergency decisions
    em = [d for d in s.decisions if d.get("mode") == "emergency"]
    if em:
        print(f"\n  EMERGENCY DECISIONS ({len(em)}):")
        for d in em[:10]:
            print(f"    hand={d['hand_id']} {d['phase']:7} "
                  f"{' '.join(d.get('hero_cards', [])):<5} "
                  f"board={' '.join(d.get('board_cards', []))} "
                  f"=> {d.get('action_kind', '?')}")

    print()


# ── Shadow-session success criteria ──────────────────────────────────────

CRITERIA = {
    "min_hands": 200,
    "max_unsafe_rate": 0.0,
    "max_warn_rate": 0.02,
    "max_emergency_rate": 0.05,
    "min_trust": 0.85,
    "max_latency_us": 50_000,
    "min_focus_rate": 0.95,
    "min_sessions": 2,
}


def check_criteria(sessions: List[SessionStats]) -> list[tuple[str, bool, str]]:
    """Check shadow-session success criteria across all sessions.

    Returns list of (criterion, passed, detail).
    """
    results = []

    total_hands = sum(s.total_hands for s in sessions)
    total_decisions = sum(s.total_decisions for s in sessions)

    results.append((
        f"min_hands >= {CRITERIA['min_hands']}",
        total_hands >= CRITERIA["min_hands"],
        f"{total_hands} hands across {len(sessions)} sessions",
    ))

    results.append((
        f"min_sessions >= {CRITERIA['min_sessions']}",
        len(sessions) >= CRITERIA["min_sessions"],
        f"{len(sessions)} sessions",
    ))

    # Unsafe/warn rates are measured against decisions only (not all
    # snapshots). A validation_anomaly with severity=unsafe means the
    # advisor was CALLED on a bad snapshot — hero_cards_missing unsafes
    # that never reach the advisor aren't counted here. We count only
    # decisions that were logged with validation_status != "ok".
    decisions_warn = sum(
        1 for s in sessions for d in s.decisions
        if d.get("validation_status") == "warn"
    )
    decisions_unsafe = sum(
        1 for s in sessions for d in s.decisions
        if d.get("validation_status") == "unsafe"
    )
    results.append((
        f"unsafe_rate == {CRITERIA['max_unsafe_rate']}",
        decisions_unsafe <= 0,
        f"{decisions_unsafe}/{total_decisions} decisions with unsafe state",
    ))

    warn_rate = decisions_warn / max(total_decisions, 1)
    results.append((
        f"warn_rate < {CRITERIA['max_warn_rate']:.0%}",
        warn_rate < CRITERIA["max_warn_rate"],
        f"{warn_rate:.1%} ({decisions_warn}/{total_decisions})",
    ))

    em_count = sum(s.mode_counts.get("emergency", 0) for s in sessions)
    em_rate = em_count / max(total_decisions, 1)
    results.append((
        f"emergency_rate < {CRITERIA['max_emergency_rate']:.0%}",
        em_rate < CRITERIA["max_emergency_rate"],
        f"{em_rate:.1%} ({em_count}/{total_decisions})",
    ))

    trust_sum = sum(s.trust_sum for s in sessions)
    mean_trust = trust_sum / max(total_decisions, 1)
    results.append((
        f"mean_trust >= {CRITERIA['min_trust']}",
        mean_trust >= CRITERIA["min_trust"],
        f"{mean_trust:.3f}",
    ))

    lat_sum = sum(s.latency_sum_us for s in sessions)
    mean_lat = lat_sum // max(total_decisions, 1)
    results.append((
        f"mean_latency < {CRITERIA['max_latency_us']}us",
        mean_lat < CRITERIA["max_latency_us"],
        f"{mean_lat} us",
    ))

    focus_req = sum(s.focus_requests for s in sessions)
    focus_ok = sum(s.focus_succeeded for s in sessions)
    focus_rate = focus_ok / max(focus_req, 1)
    results.append((
        f"focus_rate >= {CRITERIA['min_focus_rate']:.0%}",
        focus_rate >= CRITERIA["min_focus_rate"],
        f"{focus_rate:.0%} ({focus_ok}/{focus_req})",
    ))

    return results


def print_criteria_report(results: list[tuple[str, bool, str]]) -> None:
    """Print the criteria check results."""
    print(f"\n{'=' * 60}")
    print("  SHADOW-SESSION SUCCESS CRITERIA")
    print(f"{'=' * 60}")
    all_pass = True
    for criterion, passed, detail in results:
        sym = "PASS" if passed else "FAIL"
        mark = "+" if passed else "x"
        if not passed:
            all_pass = False
        print(f"  [{sym}] {mark} {criterion:40} {detail}")
    print(f"{'=' * 60}")
    if all_pass:
        print("  VERDICT: ALL CRITERIA MET — ready for supervised pilot")
    else:
        failed = sum(1 for _, p, _ in results if not p)
        print(f"  VERDICT: {failed} criteria not met — continue shadow sessions")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Shadow session review tool")
    p.add_argument("logs", nargs="+",
                   help="Shadow session JSONL log file(s) or glob pattern")
    p.add_argument("--check-criteria", action="store_true",
                   help="Check shadow-session success criteria across all logs")
    p.add_argument("--brief", action="store_true",
                   help="One-line summary per session")
    args = p.parse_args()

    # Expand glob patterns
    log_files = []
    for pattern in args.logs:
        expanded = glob.glob(pattern)
        if expanded:
            log_files.extend(expanded)
        elif os.path.exists(pattern):
            log_files.append(pattern)
        else:
            print(f"WARNING: no files matching {pattern}", file=sys.stderr)

    if not log_files:
        print("No log files found.", file=sys.stderr)
        return 2

    sessions = []
    for path in sorted(log_files):
        s = parse_log(path)
        sessions.append(s)
        if args.brief:
            print(f"  {s.session_id or os.path.basename(path):15} "
                  f"hands={s.total_hands:4} decisions={s.total_decisions:4} "
                  f"trust={s.mean_trust:.2f} em={s.emergency_rate:.0%} "
                  f"warns={s.validation_warns} unsafe={s.validation_unsafe}")
        else:
            print_session_report(s)

    if args.check_criteria or len(sessions) > 1:
        results = check_criteria(sessions)
        print_criteria_report(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
