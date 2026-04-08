"""
Humanization layer for automated poker play.

Every aspect of bot input must be statistically indistinguishable from
a real human player. Detection systems look at:

1. TIMING — decision speed distribution per action type
2. MOUSE — path shape, speed, jitter, idle movement
3. SESSION — length, breaks, schedule, table count
4. PLAY — occasional mistakes, variable sizing, tilt simulation

All distributions are derived from empirical human data at micro-stakes.
"""

import random
import math
import time
import threading


# ═══════════════════════════════════════════════════════════════════════
# 1. TIMING — log-normal distributions per decision type
# ═══════════════════════════════════════════════════════════════════════
#
# Human timing is log-normal: most decisions are fast, with a long tail
# of occasional tanks. The parameters differ by decision complexity.
#
# mu/sigma are for log(seconds). Sampled via exp(N(mu, sigma)).
# Empirical ranges from micro-stakes hand history databases.

TIMING_PROFILES = {
    # Preflop: fast for obvious folds, slower for marginal hands
    "preflop_fold":      {"mu": 0.4,  "sigma": 0.5, "min": 0.8,  "max": 6.0},
    "preflop_call":      {"mu": 0.8,  "sigma": 0.5, "min": 1.0,  "max": 8.0},
    "preflop_raise":     {"mu": 0.9,  "sigma": 0.5, "min": 1.2,  "max": 10.0},
    "preflop_check":     {"mu": 0.3,  "sigma": 0.4, "min": 0.5,  "max": 4.0},

    # Postflop: generally slower, more complex decisions
    "postflop_fold":     {"mu": 0.7,  "sigma": 0.6, "min": 0.8,  "max": 12.0},
    "postflop_call":     {"mu": 1.1,  "sigma": 0.6, "min": 1.2,  "max": 15.0},
    "postflop_raise":    {"mu": 1.3,  "sigma": 0.6, "min": 1.5,  "max": 20.0},
    "postflop_bet":      {"mu": 1.2,  "sigma": 0.6, "min": 1.2,  "max": 18.0},
    "postflop_check":    {"mu": 0.6,  "sigma": 0.5, "min": 0.6,  "max": 8.0},

    # River: biggest decisions, longest tanks
    "river_fold":        {"mu": 0.9,  "sigma": 0.7, "min": 1.0,  "max": 15.0},
    "river_call":        {"mu": 1.4,  "sigma": 0.7, "min": 1.5,  "max": 25.0},
    "river_raise":       {"mu": 1.5,  "sigma": 0.7, "min": 2.0,  "max": 25.0},
    "river_bet":         {"mu": 1.3,  "sigma": 0.7, "min": 1.5,  "max": 20.0},
    "river_check":       {"mu": 0.7,  "sigma": 0.5, "min": 0.7,  "max": 10.0},
}


def get_think_time(phase, action):
    """
    Sample a human-like think time for a given phase and action.

    Returns seconds to wait before acting.
    """
    action_key = action.split()[0].lower()  # "RAISE to 0.36" -> "raise"
    if action_key in ("check", "bet"):
        pass
    elif action_key in ("call", "fold", "raise"):
        pass
    else:
        action_key = "check"

    phase_key = phase.lower()
    if phase_key == "river":
        key = f"river_{action_key}"
    elif phase_key == "preflop":
        key = f"preflop_{action_key}"
    else:
        key = f"postflop_{action_key}"

    profile = TIMING_PROFILES.get(key, TIMING_PROFILES["postflop_check"])

    # Log-normal sample
    t = math.exp(random.gauss(profile["mu"], profile["sigma"]))
    t = max(profile["min"], min(profile["max"], t))

    # Occasional long tank (3% chance, adds 5-20 seconds)
    if random.random() < 0.03:
        t += random.uniform(5.0, 20.0)

    # Occasional instant snap (2% for folds only — humans snap-fold trash)
    if action_key == "fold" and random.random() < 0.02:
        t = random.uniform(0.3, 0.6)

    # Add micro-noise to break up round numbers and floor clustering
    # Real human timers have continuous distributions, not discrete
    t += random.uniform(-0.037, 0.037)
    # Dither the last digit to avoid x.x0 clustering
    t += random.choice([-0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07])
    # Soften the floor — vary minimum by ±30%
    floor = profile["min"] * random.uniform(0.7, 1.3)
    t = max(floor, t)

    return t


# ═══════════════════════════════════════════════════════════════════════
# 2. MOUSE — Bezier curves with jitter and idle movement
# ═══════════════════════════════════════════════════════════════════════

def _bezier_point(t, p0, p1, p2, p3):
    """Cubic Bezier interpolation."""
    u = 1 - t
    return (
        u**3 * p0[0] + 3*u**2*t * p1[0] + 3*u*t**2 * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3*u**2*t * p1[1] + 3*u*t**2 * p2[1] + t**3 * p3[1],
    )


def human_mouse_path(start, end, steps=None):
    """
    Generate a human-like mouse path from start to end.

    Returns list of (x, y) points with:
    - Curved Bezier path (not straight line)
    - Sub-pixel jitter
    - Variable speed (fast middle, slow endpoints)
    - Slight overshoot at destination
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.sqrt(dx**2 + dy**2)

    if steps is None:
        steps = max(8, int(dist / 8))  # ~8px per step

    # Control points for cubic Bezier — randomized curvature
    # Humans don't move in straight lines; they arc slightly
    perp_x, perp_y = -dy, dx  # perpendicular direction
    curve_amount = random.uniform(-0.3, 0.3)  # how much to curve

    cp1 = (
        start[0] + dx * 0.3 + perp_x * curve_amount * random.uniform(0.5, 1.5),
        start[1] + dy * 0.3 + perp_y * curve_amount * random.uniform(0.5, 1.5),
    )
    cp2 = (
        start[0] + dx * 0.7 + perp_x * curve_amount * random.uniform(0.3, 1.0),
        start[1] + dy * 0.7 + perp_y * curve_amount * random.uniform(0.3, 1.0),
    )

    # Slight overshoot: target past the endpoint, then correct
    overshoot = random.uniform(0, 0.04) if dist > 50 else 0
    actual_end = (
        end[0] + dx * overshoot,
        end[1] + dy * overshoot,
    )

    path = []
    for i in range(steps):
        t = i / max(steps - 1, 1)

        # Variable speed: ease-in, fast middle, ease-out
        # Using smoothstep for natural acceleration
        t_smooth = t * t * (3 - 2 * t)

        x, y = _bezier_point(t_smooth, start, cp1, cp2, actual_end)

        # Sub-pixel jitter (humans have hand tremor)
        jitter = max(0.5, dist * 0.005)
        x += random.gauss(0, jitter)
        y += random.gauss(0, jitter)

        path.append((round(x), round(y)))

    # Correction step if we overshot
    if overshoot > 0:
        correction_steps = random.randint(2, 4)
        for i in range(correction_steps):
            t = (i + 1) / correction_steps
            cx = actual_end[0] + (end[0] - actual_end[0]) * t + random.gauss(0, 0.5)
            cy = actual_end[1] + (end[1] - actual_end[1]) * t + random.gauss(0, 0.5)
            path.append((round(cx), round(cy)))

    # Final click position: not exact center, gaussian around target
    # Wider spread to avoid duplicate pixel positions
    final_x = end[0] + random.gauss(0, 5)
    final_y = end[1] + random.gauss(0, 4)
    path.append((round(final_x), round(final_y)))

    return path


def mouse_move_duration(distance):
    """Human-like mouse movement duration based on Fitts's law."""
    # Fitts's law: T = a + b * log2(1 + D/W)
    # a=0.1, b=0.15, W~20 (button width)
    if distance < 5:
        return random.uniform(0.02, 0.05)
    t = 0.1 + 0.15 * math.log2(1 + distance / 20)
    t *= random.uniform(0.8, 1.3)  # variance
    return max(0.05, min(0.8, t))


def inter_move_delay():
    """Delay between mouse move events. Humans don't move at constant speed."""
    return random.uniform(0.005, 0.025)


# ═══════════════════════════════════════════════════════════════════════
# 3. IDLE MOVEMENT — humans fidget between actions
# ═══════════════════════════════════════════════════════════════════════

def generate_idle_movements(center, radius=100, count=None):
    """
    Generate random idle mouse movements near a center point.
    Humans move the mouse around while waiting for their turn.

    Returns list of (x, y) points to move through slowly.
    """
    if count is None:
        count = random.randint(2, 8)

    points = []
    x, y = center
    for _ in range(count):
        # Small random drift
        x += random.gauss(0, radius * 0.3)
        y += random.gauss(0, radius * 0.2)
        # Keep near center
        x = center[0] + max(-radius, min(radius, x - center[0]))
        y = center[1] + max(-radius, min(radius, y - center[1]))
        points.append((round(x), round(y)))

    return points


# ═══════════════════════════════════════════════════════════════════════
# 4. SESSION MANAGEMENT — breaks, schedule, session length
# ═══════════════════════════════════════════════════════════════════════

class SessionManager:
    """
    Manages session timing to look human:
    - Variable session length (45-180 min)
    - Random breaks every 20-60 min
    - Break duration 2-15 min
    - Occasional long break (bathroom/food)
    - Session end randomization
    """

    def __init__(self):
        self.session_start = time.time()
        self.session_length = random.uniform(45, 180) * 60  # seconds
        self.next_break = time.time() + random.uniform(20, 60) * 60
        self.break_duration = 0
        self.on_break = False
        self.hands_since_break = 0
        self.total_hands = 0

    def should_take_break(self):
        """Check if it's time for a break."""
        now = time.time()

        # Scheduled break time
        if now >= self.next_break and not self.on_break:
            return True

        # Hands-based break (every 30-80 hands)
        if self.hands_since_break > random.randint(30, 80):
            return True

        return False

    def start_break(self):
        """Start a break. Returns duration in seconds."""
        self.on_break = True
        self.hands_since_break = 0

        # 70% short break (2-5 min), 20% medium (5-10 min), 10% long (10-15 min)
        r = random.random()
        if r < 0.70:
            self.break_duration = random.uniform(2, 5) * 60
        elif r < 0.90:
            self.break_duration = random.uniform(5, 10) * 60
        else:
            self.break_duration = random.uniform(10, 15) * 60

        return self.break_duration

    def end_break(self):
        """End current break, schedule next one."""
        self.on_break = False
        self.next_break = time.time() + random.uniform(20, 60) * 60

    def should_end_session(self):
        """Check if session should end."""
        elapsed = time.time() - self.session_start
        return elapsed >= self.session_length

    def record_hand(self):
        """Record a hand played."""
        self.total_hands += 1
        self.hands_since_break += 1


# ═══════════════════════════════════════════════════════════════════════
# 5. PLAY VARIATION — deliberate imperfections
# ═══════════════════════════════════════════════════════════════════════

class PlayVariation:
    """
    Introduces controlled imperfections to avoid pattern detection:
    - Occasional suboptimal plays (1-3%)
    - Bet sizing variation (±10-20%)
    - Tilt simulation after bad beats
    - Frequency mixing (don't always do the same thing)
    """

    def __init__(self, mistake_rate=0.02):
        self.mistake_rate = mistake_rate
        self.recent_results = []  # last N hand results for tilt sim
        self._tilt_level = 0.0    # 0 = calm, 1 = full tilt

    def maybe_modify_action(self, action, equity, phase, facing):
        """
        Possibly modify an action to look more human.
        Returns (modified_action, was_modified).
        """
        # Never modify with very strong or very weak hands — too costly
        if equity > 0.85 or equity < 0.15:
            return action, False

        # Tilt: after bad beats, play slightly looser — but only with decent equity
        if self._tilt_level > 0.5 and random.random() < self._tilt_level * 0.1:
            if "FOLD" in action.upper() and equity > 0.50:
                # Tilt call — "I'm not folding this time" (only with decent hand)
                return action.replace("FOLD", "CALL"), True

        # Random mistake
        if random.random() < self.mistake_rate:
            return self._make_mistake(action, equity, phase, facing)

        return action, False

    def _make_mistake(self, action, equity, phase, facing):
        """Generate a plausible human mistake."""
        action_upper = action.upper()

        # Mistake types weighted by plausibility
        if "FOLD" in action_upper and equity > 0.35:
            # Accidental call (clicked wrong button)
            return "CALL", True
        elif "CHECK" in action_upper and equity > 0.50:
            # Missed a bet (happens when distracted)
            return "CHECK", False  # keeping check is also a "mistake"
        elif "RAISE" in action_upper and phase == "PREFLOP":
            # Slightly off sizing (fat-fingered the slider)
            return action, False

        return action, False

    def vary_bet_size(self, amount_cents):
        """
        Add human-like variance to bet sizing.
        Humans don't always bet exactly 66% pot — they round, they misclick.
        """
        if amount_cents <= 0:
            return amount_cents

        # ±15% variance
        factor = random.gauss(1.0, 0.08)
        factor = max(0.85, min(1.15, factor))
        varied = int(amount_cents * factor)

        # Humans round to nice numbers
        varied = self._round_human(varied)

        return max(2, varied)  # minimum 1BB at 2NL

    def _round_human(self, cents):
        """Round to amounts humans typically use."""
        if cents < 10:
            return cents  # small amounts, exact
        elif cents < 50:
            return round(cents / 2) * 2  # round to nearest 2 cents
        elif cents < 200:
            return round(cents / 5) * 5  # round to nearest 5 cents
        else:
            return round(cents / 10) * 10  # round to nearest 10 cents

    def record_result(self, profit_bb):
        """Record hand result for tilt simulation."""
        self.recent_results.append(profit_bb)
        if len(self.recent_results) > 20:
            self.recent_results.pop(0)

        # Update tilt level based on recent results
        if len(self.recent_results) >= 5:
            recent = self.recent_results[-5:]
            avg = sum(recent) / len(recent)
            if avg < -5:  # losing 5+ bb/hand over last 5 hands
                self._tilt_level = min(1.0, self._tilt_level + 0.2)
            elif avg > 0:
                self._tilt_level = max(0.0, self._tilt_level - 0.1)

    @property
    def tilt_level(self):
        return self._tilt_level


# ═══════════════════════════════════════════════════════════════════════
# 6. INPUT INJECTION — use SendInput for hardware-level events
# ═══════════════════════════════════════════════════════════════════════

import ctypes
from ctypes import wintypes

# Windows SendInput structures
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]


def _send_input(*inputs):
    """Send hardware-level input events via Win32 SendInput."""
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _make_mouse_input(x, y, flags):
    """Create a mouse INPUT structure. Coordinates are absolute (0-65535)."""
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp._input.mi.dx = x
    inp._input.mi.dy = y
    inp._input.mi.dwFlags = flags
    inp._input.mi.time = 0
    inp._input.mi.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
    return inp


def _screen_to_absolute(x, y):
    """Convert screen pixel coordinates to absolute (0-65535) for SendInput."""
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    ax = int(x * 65535 / sw)
    ay = int(y * 65535 / sh)
    return ax, ay


def move_mouse(x, y):
    """Move mouse to screen coordinates using SendInput (hardware-level)."""
    ax, ay = _screen_to_absolute(x, y)
    inp = _make_mouse_input(ax, ay, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
    _send_input(inp)


def _focus_chrome():
    """Bring Chrome window to foreground before clicking."""
    hwnd = ctypes.windll.user32.FindWindowW(None, None)
    # Find Chrome by enumerating windows
    import ctypes.wintypes as wt
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    chrome_hwnd = [None]
    def enum_cb(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if 'unibet' in title.lower() or 'relax poker' in title.lower() or 'chrome' in title.lower():
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                chrome_hwnd[0] = hwnd
                return False  # stop enumerating
        return True
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    if chrome_hwnd[0]:
        ctypes.windll.user32.SetForegroundWindow(chrome_hwnd[0])
        time.sleep(0.05)


def click_mouse(x, y):
    """Click at screen coordinates. Focus Chrome, move cursor, then click."""
    _focus_chrome()
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(random.uniform(0.02, 0.06))
    # mouse_event with no ABSOLUTE flag clicks at current cursor position
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(random.uniform(0.04, 0.12))  # humans hold click 40-120ms
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


def human_click(start_pos, target_pos):
    """
    Full human-like click sequence:
    1. Generate Bezier path from current position to target
    2. Move along path with variable speed
    3. Click with hardware-level SendInput
    """
    path = human_mouse_path(start_pos, target_pos)
    dist = math.sqrt((target_pos[0] - start_pos[0])**2 +
                     (target_pos[1] - start_pos[1])**2)
    total_duration = mouse_move_duration(dist)
    step_delay = total_duration / max(len(path), 1)

    for point in path[:-1]:
        move_mouse(point[0], point[1])
        # Variable speed along path
        time.sleep(step_delay * random.uniform(0.5, 1.5))

    # Click at final position
    final = path[-1]
    click_mouse(final[0], final[1])


def get_cursor_pos():
    """Get current cursor position."""
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)
