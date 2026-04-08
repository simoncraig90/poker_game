# Multi-Venue Workflow

How to add new poker venues to the advisor + auto-player system, based on lessons from the Unibet integration (2026-04-06/07).

## Background

The Unibet integration was painful because:
- Strategy worked but clicks were intermittently broken
- Each "fix" was tested live, costing real money
- Clicks were never verified to actually reach the canvas
- Position calibration was guessed, not measured

This document defines a workflow that prevents these failures.

---

## Core architecture

### Universal layer (venue-agnostic)
```
vision/advisor_state_machine.py   — strategy (preflop chart + postflop engine)
vision/humanizer.py                — timing, mouse paths, session, play variation
vision/strategy/postflop_engine.py — flop CFR + turn/river rules
```

### Per-venue layer
```
vision/<venue>_ws.py        — WebSocket/protocol reader → universal state dict
vision/<venue>_clicker.py   — button positions + click method
client/<venue>-replica.html — test fixture matching real client layout
tests/test_<venue>_*.py     — venue-specific tests
```

### Universal state dict (target output of every WS reader)
```python
{
    "hero_cards": ["Ah", "Kh"],
    "board_cards": ["Td", "9c", "2s"],
    "hand_id": "h_2379",
    "facing_bet": True,
    "call_amount": 12,
    "pot": 50,
    "num_opponents": 5,
    "position": "BTN",
    "hero_stack": 1000,
    "phase": "FLOP",
    "bets": [0, 12, 0, 0, 4, 0],
    "players": ["v1", "Hero", ...],
    "hero_seat": 1,
    "hero_turn": True,  # CRITICAL: only True when buttons visible
}
```

---

## Per-venue workflow

### Step 1: Capture & analyze

1. Sit at the venue's lowest-stake table
2. Take screenshots of EVERY UI state:
   - Not your turn (FOLD TO ANY BET visible)
   - Your turn with FOLD/CHECK
   - Your turn with FOLD/CALL/RAISE
   - Your turn with FOLD/BET
   - Slider/preset row
   - Bet input field
   - PLAY button (sit back in)
3. Capture the WS protocol with `cdp-ws-bridge.js` style listener
4. Document the UI position percentages from screenshots

### Step 2: Build the replica

Create `client/<venue>-replica.html`:
- Match button positions to real client (use percentages of viewport)
- Each button has a click handler that logs to a verification panel
- Verification panel tracks: total clicks, button hit rate, last action
- Include slider presets, bet input, +/- buttons
- Use same canvas element pattern (e.g., `id="kenobiCanvas"` for Unibet/Relax)

### Step 3: Build the WS reader

Create `vision/<venue>_ws.py`:
- Parse the venue's protocol into the universal state dict
- **`hero_turn` MUST be `True` ONLY when action buttons are visible** — this is the most critical field
- Test against captured WS messages

### Step 4: Calibrate button positions

Use a screenshot analysis script:
```python
from PIL import Image
import numpy as np
img = Image.open("screenshot.png")
arr = np.array(img)
# Find buttons by color: red (FOLD), green (CALL/RAISE), yellow (CALL)
# Compute centers as % of render widget client area
```

Store positions as **% of render widget client size**, not absolute pixels:
```python
BUTTON_PCT = {
    "FOLD":  (0.397, 0.943),
    "CHECK": (0.498, 0.943),
    "CALL":  (0.498, 0.943),
    "RAISE": (0.600, 0.936),
    "BET":   (0.600, 0.936),
}
```

For elements that may shift with viewport changes, use **absolute pixel offsets from edges**:
```python
BTN_Y_FROM_BOTTOM = {"FOLD": 74, "CHECK": 74, "CALL": 74, "RAISE": 82}
```

### Step 5: Build the clicker

Try click methods in this order:

1. **Chrome extension** (TIER 1, recommended)
   - Runs inside browser context
   - Has access to `chrome.debugger` API
   - Can dispatch trusted events via `Input.dispatchMouseEvent`
   - Doesn't need window focus
   - **Build this first for any browser-based venue**

2. **CDP via Node** (works for HTML, unreliable for canvas)
   - `Input.dispatchMouseEvent` on the iframe target
   - Requires `Page.bringToFront()` for canvas-based clients
   - Works for some venues, fails for Emscripten clients

3. **SendInput** (last resort)
   - Win32 hardware-level input
   - Requires window to be foreground
   - Moves cursor visibly
   - Use `SetForegroundWindow` + Alt-key trick to bypass focus lock
   - Tab activation via `http://localhost:9222/json/activate/<tab_id>`

### Step 6: Click verification (REQUIRED for every venue)

After EVERY click, verify state changed:

```python
# Snapshot state before
pre_state = latest_ws_state.copy()
pre_hero_bet = pre_state["bets"]
pre_facing = pre_state["facing_bet"]
pre_pot = pre_state["pot"]

click_button(action)

# Wait up to 2.5s for ANY change
import time
start = time.time()
while time.time() - start < 2.5:
    cur = latest_ws_state
    if (cur["bets"] != pre_hero_bet or
        cur["facing_bet"] != pre_facing or
        cur["pot"] != pre_pot):
        return SUCCESS
    time.sleep(0.1)

# Retry up to 2 more times if no state change
return FAILED  # Log this — never silently fail
```

### Step 7: Pre-flight tests (run BEFORE every live session)

1. **`test_replay_session.py`** — replay all captured hands through state machine, flag bad recommendations
2. **`test_<venue>_clicks.py`** — run the bot's click code against the replica HTML, verify every click registers
3. **`test_stealth.py`** — verify timing/mouse/session distributions look human
4. **All 87+ universal tests** — strategy regression

### Step 8: Live verification at micro-stakes

- Play 50 hands at lowest stake available at the venue
- Track: % of hands where bot acted correctly without manual intervention
- **Required: ≥95% before promoting to higher stakes**
- Save all session JSONLs for replay testing

### Step 9: Promote to real stakes

Only after step 8 passes for 200+ hands.

---

## Lessons from Unibet (avoid these mistakes)

### Don't
- ❌ Test clicks live with real money
- ❌ Use absolute screen pixels (windows move, DPI changes)
- ❌ Click slider presets for "All-in" (catastrophic if misfires)
- ❌ Use keyboard typing for bet amounts (suspicious)
- ❌ Cancel pending actions on every WS state update (causes missed clicks)
- ❌ Trust "click sent" as "click registered" — always verify via WS state
- ❌ Calibrate button positions from a single screenshot (window may differ)
- ❌ Set DPI awareness without testing — it changes coordinate systems

### Do
- ✓ Build replica HTML first, test all click code there
- ✓ Use render-widget-relative coordinates (not whole-window)
- ✓ Verify clicks via WS state change (universal verification)
- ✓ Log every click attempt with target coordinates
- ✓ Save diagnostic screenshots of click target areas
- ✓ Detect failed clicks and retry with backoff
- ✓ Only fire clicks when WS confirms `hero_turn=True`
- ✓ Min think time ≥1.5s to look human; max ≤6s for action timer safety

---

## Stealth requirements (universal)

All clicks must look human across these dimensions:
- **Timing**: log-normal think time per action type (folds faster, river decisions slower)
- **Mouse**: Bezier path with jitter (if cursor visible)
- **Sizing**: ±15% variance on bet amounts
- **Mistakes**: ~2% intentional suboptimal plays
- **Session**: 45-180 min sessions with 2-15 min breaks
- **Tilt**: post-loss looser play simulation

These are venue-agnostic and live in `humanizer.py`.

---

## Detection avoidance (per venue)

Each venue has its own detection profile. Document for each:
- Client-side anti-cheat (PokerStars yes, Unibet no, CoinPoker no)
- Behavioral analysis sophistication (PokerStars > GG > others)
- Mouse pattern analysis (most have it)
- Timing distribution analysis (most have it)
- Bot-friendly stakes (micro-stakes safer than mid-stakes everywhere)

---

## Adding the next venue (checklist)

- [ ] Capture 100+ screenshots of all UI states
- [ ] Capture 1+ hour of WS protocol traffic
- [ ] Build `<venue>-replica.html` matching layout
- [ ] Build `<venue>_ws.py` outputting universal state
- [ ] Calibrate button positions (% of render widget)
- [ ] Build `<venue>_clicker.py` (try Chrome extension first)
- [ ] Add WS state verification after each click
- [ ] Write `tests/test_<venue>_clicks.py` against replica
- [ ] Run all universal tests + venue tests, all must pass
- [ ] Play 50 hands at lowest stake, target ≥95% bot autonomy
- [ ] Play 200 hands at micro stakes, validate bb/100 trend
- [ ] Promote to target stakes

---

## File naming conventions

```
client/<venue>-replica.html        — test fixture
vision/<venue>_ws.py               — protocol reader
vision/<venue>_clicker.py          — click adapter
vision/<venue>_extension/          — Chrome extension (if needed)
tests/test_<venue>_clicks.py       — click verification
tests/test_<venue>_replay.py       — hand replay
docs/<venue>-integration.md        — venue-specific notes
```

---

## Priority venues for expansion

Based on tonight's lessons (clicking difficulty + detection risk):

1. **CoinPoker** — HUDs allowed, no client anti-cheat, lowest risk. Should be next.
2. **Ignition/Bovada** — DOM-based, easier clicking, anonymous tables. Second.
3. **TigerGaming/BetOnline** — Binary protocol, harder. Third.
4. **PokerStars** — Active anti-cheat, highest risk, defer indefinitely.
5. **GGPoker** — Active ML detection, defer until proven elsewhere.

For each: build the replica BEFORE writing the WS reader. Test clicks first.
