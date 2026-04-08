"""
Stealth detection simulator.

Analyzes timing distributions, mouse paths, session patterns, and
play consistency — flags anything that looks non-human.

This is what a poker site's detection system would check.
Run this BEFORE going live with auto-play.
"""

import sys
import os
import math
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from humanizer import (
    get_think_time, human_mouse_path, mouse_move_duration,
    SessionManager, PlayVariation, generate_idle_movements,
)


# ══════════════════════════════════════════════════════════════════════
# TIMING ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def test_timing_distribution():
    """Think times must follow log-normal distribution, not uniform."""
    failures = []
    N = 2000

    for phase in ["PREFLOP", "FLOP", "TURN", "RIVER"]:
        for action in ["FOLD", "CALL", "RAISE", "CHECK"]:
            times = [get_think_time(phase, action) for _ in range(N)]

            mean = statistics.mean(times)
            stdev = statistics.stdev(times)
            median = statistics.median(times)

            # Log-normal: mean > median (right-skewed)
            if mean <= median:
                failures.append(f"{phase} {action}: not right-skewed (mean={mean:.2f} <= median={median:.2f})")

            # Coefficient of variation should be 0.3-1.5 (humans are variable)
            cv = stdev / mean if mean > 0 else 0
            if cv < 0.25:
                failures.append(f"{phase} {action}: too consistent CV={cv:.2f} (need >0.25)")
            if cv > 2.0:
                failures.append(f"{phase} {action}: too erratic CV={cv:.2f} (need <2.0)")

            # No suspiciously round numbers (constant delay floor)
            # ~20% is mathematically expected for any continuous distribution
            # Flag only if significantly above that (>28% = artificial clustering)
            rounded_count = sum(1 for t in times if abs(t - round(t, 1)) < 0.01)
            if rounded_count / N > 0.28:
                failures.append(f"{phase} {action}: too many round numbers ({rounded_count}/{N})")

            # Must have occasional long tanks (>8s)
            long_tanks = sum(1 for t in times if t > 8.0)
            if phase != "PREFLOP" and action not in ("FOLD", "CHECK"):
                if long_tanks < N * 0.01:
                    failures.append(f"{phase} {action}: no long tanks ({long_tanks}/{N}, need >1%)")

            # Must have some fast actions (<1.5s)
            fast = sum(1 for t in times if t < 1.5)
            if action == "FOLD" and fast < N * 0.05:
                failures.append(f"{phase} {action}: no snap-folds ({fast}/{N})")

    return failures


def test_timing_varies_by_decision():
    """Complex decisions should take longer than simple ones."""
    failures = []
    N = 1000

    fold_times = [get_think_time("FLOP", "FOLD") for _ in range(N)]
    raise_times = [get_think_time("FLOP", "RAISE to 0.50") for _ in range(N)]
    river_call = [get_think_time("RIVER", "CALL 1.50") for _ in range(N)]

    fold_mean = statistics.mean(fold_times)
    raise_mean = statistics.mean(raise_times)
    river_mean = statistics.mean(river_call)

    if raise_mean <= fold_mean * 1.1:
        failures.append(f"RAISE ({raise_mean:.2f}s) should take longer than FOLD ({fold_mean:.2f}s)")

    if river_mean <= raise_mean * 0.9:
        failures.append(f"River CALL ({river_mean:.2f}s) should be >= flop RAISE ({raise_mean:.2f}s)")

    return failures


def test_timing_no_constant_floor():
    """No evidence of a constant minimum delay (bot signature)."""
    failures = []
    N = 5000

    all_times = [get_think_time("FLOP", "CALL") for _ in range(N)]

    # Check for clustering at the minimum (would indicate a floor)
    sorted_times = sorted(all_times)
    bottom_5pct = sorted_times[:int(N * 0.05)]
    bottom_range = max(bottom_5pct) - min(bottom_5pct)

    if bottom_range < 0.1:
        failures.append(f"Bottom 5% too clustered: range={bottom_range:.3f}s (floor detected)")

    # Kolmogorov-Smirnov style check: no gaps in distribution
    # (uniform random + floor creates a gap)
    for i in range(1, len(sorted_times)):
        gap = sorted_times[i] - sorted_times[i-1]
        if gap > 3.0 and sorted_times[i] < 10:
            failures.append(f"Suspicious gap at {sorted_times[i-1]:.2f}-{sorted_times[i]:.2f}s")
            break

    return failures


# ══════════════════════════════════════════════════════════════════════
# MOUSE ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def test_mouse_paths_curved():
    """Mouse paths must not be straight lines."""
    failures = []
    N = 200

    straight_count = 0
    for _ in range(N):
        start = (100 + int(800 * (_%10)/10), 200 + int(400 * (_%7)/7))
        end = (600 + int(200 * (_%5)/5), 500 + int(100 * (_%3)/3))
        path = human_mouse_path(start, end)

        if len(path) < 3:
            continue

        # Measure max deviation from straight line
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        line_len = math.sqrt(dx**2 + dy**2)
        if line_len < 10:
            continue

        max_dev = 0
        for px, py in path[1:-1]:
            # Distance from point to line
            cross = abs((py - start[1]) * dx - (px - start[0]) * dy)
            dev = cross / line_len
            max_dev = max(max_dev, dev)

        if max_dev < 2:
            straight_count += 1

    if straight_count / N > 0.3:
        failures.append(f"Too many straight paths: {straight_count}/{N} (need <30%)")

    return failures


def test_mouse_no_pixel_perfect():
    """Click positions must not be the exact same point repeatedly."""
    failures = []
    N = 100
    target = (500, 400)

    endpoints = []
    for _ in range(N):
        path = human_mouse_path((200, 300), target)
        endpoints.append(path[-1])

    # Check variance in click positions
    x_vals = [p[0] for p in endpoints]
    y_vals = [p[1] for p in endpoints]

    x_std = statistics.stdev(x_vals) if len(x_vals) > 1 else 0
    y_std = statistics.stdev(y_vals) if len(y_vals) > 1 else 0

    if x_std < 1.0:
        failures.append(f"X click positions too precise: std={x_std:.2f} (need >1px)")
    if y_std < 1.0:
        failures.append(f"Y click positions too precise: std={y_std:.2f} (need >1px)")

    # No exact duplicates
    unique = len(set(endpoints))
    if unique < N * 0.8:
        failures.append(f"Too many duplicate click positions: {N - unique}/{N}")

    return failures


def test_mouse_speed_varies():
    """Mouse movement duration must vary with distance (Fitts's law)."""
    failures = []

    short_times = [mouse_move_duration(30) for _ in range(100)]
    long_times = [mouse_move_duration(500) for _ in range(100)]

    short_mean = statistics.mean(short_times)
    long_mean = statistics.mean(long_times)

    if long_mean <= short_mean * 1.3:
        failures.append(f"Long moves ({long_mean:.3f}s) should take longer than short ({short_mean:.3f}s)")

    # Duration should have variance
    short_cv = statistics.stdev(short_times) / short_mean
    if short_cv < 0.1:
        failures.append(f"Movement duration too consistent: CV={short_cv:.2f}")

    return failures


def test_idle_movements_exist():
    """Must generate idle mouse movements (humans fidget)."""
    failures = []

    movements = generate_idle_movements((500, 400), radius=100)
    if len(movements) < 2:
        failures.append(f"Too few idle movements: {len(movements)}")

    # Should stay near center
    for x, y in movements:
        dist = math.sqrt((x - 500)**2 + (y - 400)**2)
        if dist > 200:
            failures.append(f"Idle movement too far from center: ({x},{y}) dist={dist:.0f}")
            break

    return failures


# ══════════════════════════════════════════════════════════════════════
# SESSION ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def test_session_has_breaks():
    """Sessions must include breaks."""
    failures = []

    sm = SessionManager()
    breaks_taken = 0

    # Simulate 200 hands
    for i in range(200):
        sm.record_hand()
        if sm.should_take_break():
            dur = sm.start_break()
            breaks_taken += 1
            sm.end_break()

    if breaks_taken < 2:
        failures.append(f"Only {breaks_taken} breaks in 200 hands (need >=2)")

    return failures


def test_session_length_varies():
    """Session lengths must not be constant."""
    failures = []

    lengths = [SessionManager().session_length / 60 for _ in range(50)]
    stdev = statistics.stdev(lengths)

    if stdev < 10:
        failures.append(f"Session length too consistent: stdev={stdev:.1f} min")

    if min(lengths) < 30:
        failures.append(f"Session too short: {min(lengths):.0f} min")
    if max(lengths) > 200:
        failures.append(f"Session too long: {max(lengths):.0f} min")

    return failures


# ══════════════════════════════════════════════════════════════════════
# PLAY VARIATION ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def test_play_has_mistakes():
    """Must occasionally make suboptimal plays."""
    failures = []
    pv = PlayVariation(mistake_rate=0.02)
    N = 5000

    mistakes = 0
    for _ in range(N):
        _, was_modified = pv.maybe_modify_action("FOLD", 0.45, "FLOP", True)
        if was_modified:
            mistakes += 1

    # Should be ~1-4% mistakes
    rate = mistakes / N
    if rate < 0.005:
        failures.append(f"Too few mistakes: {rate:.1%} (need >0.5%)")
    if rate > 0.10:
        failures.append(f"Too many mistakes: {rate:.1%} (need <10%)")

    return failures


def test_bet_sizing_varies():
    """Bet sizing must not be exactly the same every time."""
    failures = []
    pv = PlayVariation()

    sizes = [pv.vary_bet_size(66) for _ in range(100)]
    unique = len(set(sizes))

    if unique < 3:
        failures.append(f"Bet sizing too consistent: only {unique} unique values for 66c bet")

    stdev = statistics.stdev(sizes) if len(sizes) > 1 else 0
    if stdev < 2:
        failures.append(f"Bet sizing stdev too low: {stdev:.1f}c")

    return failures


def test_tilt_simulation():
    """Bad beats should affect play (humans tilt)."""
    failures = []
    pv = PlayVariation(mistake_rate=0.02)

    # Record a series of bad beats
    for _ in range(10):
        pv.record_result(-15)  # losing 15bb/hand

    if pv.tilt_level < 0.3:
        failures.append(f"No tilt after 10 bad beats: level={pv.tilt_level:.2f}")

    # Recovery
    for _ in range(20):
        pv.record_result(2)

    if pv.tilt_level > 0.5:
        failures.append(f"Still tilting after recovery: level={pv.tilt_level:.2f}")

    return failures


def test_never_modify_monsters():
    """Never modify action with monster hands (>85% equity)."""
    failures = []
    pv = PlayVariation(mistake_rate=1.0)  # 100% mistake rate
    N = 1000

    for _ in range(N):
        action, modified = pv.maybe_modify_action("RAISE to 1.00", 0.95, "RIVER", True)
        if modified:
            failures.append("Modified action with 95% equity — must never happen")
            break

    return failures


# ══════════════════════════════════════════════════════════════════════
# COMBINED DETECTION SCORE
# ══════════════════════════════════════════════════════════════════════

def test_detection_score():
    """
    Simulate a detection system scoring a session.
    Combines all signals into a single bot probability.
    Must score < 0.3 (human-like).
    """
    failures = []
    score = 0.0
    N = 500

    # Timing consistency
    times = [get_think_time("FLOP", "CALL") for _ in range(N)]
    cv = statistics.stdev(times) / statistics.mean(times)
    if cv < 0.4:
        score += 0.3  # too consistent

    # Timing distribution shape (should be right-skewed)
    mean_t = statistics.mean(times)
    median_t = statistics.median(times)
    skew_ratio = mean_t / median_t if median_t > 0 else 1
    if skew_ratio < 1.05:
        score += 0.2  # not skewed enough

    # Mouse path straightness
    straight = 0
    for i in range(50):
        path = human_mouse_path((100, 300), (600, 500))
        dx = 500
        dy = 200
        line_len = math.sqrt(dx**2 + dy**2)
        max_dev = max(
            abs((p[1]-300)*dx - (p[0]-100)*dy) / line_len
            for p in path[1:-1]
        ) if len(path) > 2 else 0
        if max_dev < 2:
            straight += 1
    if straight / 50 > 0.3:
        score += 0.3

    # Click position variance
    endpoints = [human_mouse_path((200, 300), (500, 400))[-1] for _ in range(50)]
    x_std = statistics.stdev([p[0] for p in endpoints])
    if x_std < 1.5:
        score += 0.2

    if score >= 0.3:
        failures.append(f"Detection score {score:.2f} >= 0.3 threshold (would be flagged)")

    return failures


# ══════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        # Timing
        ("Stealth: timing distribution", test_timing_distribution),
        ("Stealth: timing varies by decision", test_timing_varies_by_decision),
        ("Stealth: no constant floor", test_timing_no_constant_floor),
        # Mouse
        ("Stealth: mouse paths curved", test_mouse_paths_curved),
        ("Stealth: no pixel-perfect clicks", test_mouse_no_pixel_perfect),
        ("Stealth: mouse speed varies", test_mouse_speed_varies),
        ("Stealth: idle movements exist", test_idle_movements_exist),
        # Session
        ("Stealth: session has breaks", test_session_has_breaks),
        ("Stealth: session length varies", test_session_length_varies),
        # Play variation
        ("Stealth: play has mistakes", test_play_has_mistakes),
        ("Stealth: bet sizing varies", test_bet_sizing_varies),
        ("Stealth: tilt simulation", test_tilt_simulation),
        ("Stealth: never modify monsters", test_never_modify_monsters),
        # Combined
        ("Stealth: detection score < 0.3", test_detection_score),
    ]

    print("=" * 60)
    print("  STEALTH DETECTION SIMULATOR")
    print("=" * 60)

    total = 0
    passed = 0
    all_failures = []

    for name, test_fn in tests:
        total += 1
        try:
            failures = test_fn()
            if not failures:
                print(f"  PASS  {name}")
                passed += 1
            else:
                print(f"  FAIL  {name}")
                for f in failures:
                    print(f"        - {f}")
                all_failures.extend(failures)
        except Exception as e:
            import traceback
            print(f"  ERROR {name}: {e}")
            traceback.print_exc()
            all_failures.append(f"{name}: {e}")

    print()
    print(f"  {passed}/{total} tests passed")
    if all_failures:
        print(f"  {len(all_failures)} detection flags")
    else:
        print("  STEALTH: UNDETECTABLE")
    print("=" * 60)

    sys.exit(0 if not all_failures else 1)
