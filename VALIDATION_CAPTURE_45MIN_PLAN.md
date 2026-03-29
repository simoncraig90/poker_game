# Validation Capture: 45-Minute Session Plan

One capture session to close as many remaining protocol gaps as possible.

---

## Session Goals

| Gap | Target | Priority |
|-----|--------|----------|
| GAP-1: Showdown | Capture 3+ hands reaching showdown | **Must** |
| GAP-2: CHECK path | Capture 2+ genuine check actions | **Must** |
| GAP-4: Join/leave | Capture sit-out → sit-in → leave → rejoin cycle | **Should** |
| GAP-6: Timeout | Let one action timer expire | **Should** |
| GAP-3: Side pots | Capture 1 all-in with side pot | **Try** (not guaranteed) |

---

## Pre-Session Setup (5 minutes)

### 1. Launch Chrome with debug port

```
scripts\launch-chrome-debug.bat
```

### 2. Navigate to PokerStars

Open https://www.pokerstars.uk in the debug Chrome instance. Log in.

### 3. Start CDP capture

In a second terminal:
```
cd C:\poker-research
node scripts/cdp-capture.js
```

Verify the capture terminal shows `Listening...` and logs initial page events.

### 4. Join a table

Pick a **play-money 6-max NL Hold'em** table at the lowest stakes (5c/10c or 1c/2c). Lowest stakes = loosest players = most showdowns.

Buy in for the **minimum** amount. A short stack increases the chance of all-in situations (GAP-3).

---

## During Play (35 minutes)

### Phase A: Showdown Hunting (Minutes 0–20)

**Goal**: Get to showdown. This is the most important gap.

**Strategy**: Limp or min-raise preflop. Call all bets postflop. Do NOT fold unless you literally cannot call (all-in).

**What to do**:
- Limp preflop (call the BB). Do not raise big — you want multiway pots.
- On flop/turn/river: call any bet. If checked to, **check** (this covers GAP-2).
- If you reach the river and there's a bet, call it. You want showdown.
- If checked to on the river, make a small bet (half pot) to induce a call.

**What NOT to do**:
- Do not fold postflop (you need showdowns).
- Do not raise big preflop (you'll fold everyone out).
- Do not bluff large amounts (opponents may fold, defeating the purpose).
- Do not play aggressively — you want opponents to stay in.

**Target**: 3+ showdowns in 20 minutes. At a loose micro-stakes table, this should happen naturally.

**CHECK coverage**: By checking when first to act postflop, you'll generate genuine CHECK events. This covers GAP-2 with no extra effort.

### Phase B: Join/Leave Lifecycle (Minutes 20–30)

**Goal**: Capture the sit-out / sit-in / leave / rejoin flow.

**Steps** (in order):
1. Click **Sit Out Next Hand** (or equivalent button). Wait for the hand to end.
2. Observe the capture log — note any new opcodes or PLAYER_STATE changes.
3. Wait 2–3 hands while sitting out.
4. Click **Sit In** (or **I'm Back**). Rejoin play.
5. Play 1–2 hands normally.
6. Click **Leave Table**. Watch for 0x65 or similar leave opcode.
7. Navigate back and **rejoin the same table** (or a similar one).
8. Buy in again and play 1–2 more hands.

**What this captures**:
- Sit-out: PLAYER_STATE flag changes (`sittingOut = true`)
- Sit-in: reverse flag change
- Leave: hero leave opcode (0x65?)
- Rejoin: fresh TABLE_SNAPSHOT (0x6a) or incremental sync
- Other player joins during sitting-out period (likely, at an active table)

### Phase C: Timeout + All-In Attempt (Minutes 30–40)

**Goal**: Capture a timeout and attempt an all-in scenario.

**Timeout (GAP-6)**:
1. When it's your turn to act, do nothing. Let the timer run out.
2. Watch the capture log for the resulting event (auto-fold? auto-check?).
3. Do this once only — you don't want to get kicked.

**All-in attempt (GAP-3)**:
1. With a short stack, push all-in preflop.
2. If someone calls and someone else also has chips, a side pot may form.
3. This is not guaranteed — you may just get called heads-up or everyone folds.
4. Try 2–3 times if the first attempt doesn't produce a side pot.

### Phase D: Final Clean Hands (Minutes 40–45)

**Goal**: Capture 2–3 clean normal hands to verify nothing broke.

Play normally. Fold bad hands, call with decent ones. This provides baseline comparison data.

---

## Session End

1. **Ctrl+C** in the capture terminal. Verify `session-meta.json` is written.
2. Note the session folder name (e.g., `captures/20260329_HHMMSS`).

---

## Artifacts to Save

| File | Purpose |
|------|---------|
| `websocket.jsonl` | All raw WebSocket frames |
| `requests.jsonl` | HTTP requests/responses |
| `session.log` | Human-readable chronological log |
| `timing.jsonl` | Request timing breakdowns |
| `ws-lifecycle.jsonl` | WS open/close events |
| `session-meta.json` | Start/stop times, stats |

---

## Post-Session Analysis (15–30 minutes)

Run the full pipeline on the new session:

```bash
# 1. Decode
node scripts/decode-session.js captures/<new-session>

# 2. Check for new opcodes
grep "RAW_\|ERR_\|DECODE_ERROR" captures/<new-session>/decoded-events.jsonl

# 3. Emit normalized events
node scripts/emit-normalized-events.js captures/<new-session>

# 4. Replay each hand
for f in captures/<new-session>/hands/hand-*.jsonl; do
  node scripts/replay-normalized-hand.js "$f"
done

# 5. Check results
grep "Stack check\|Balance:" captures/<new-session>/hands/replay-timeline-*.txt
```

### Gap-Specific Checks

**GAP-1 (showdown)**:
```bash
grep '"showdown":true' captures/<new-session>/normalized-hand-events.jsonl
grep 'handRank' captures/<new-session>/normalized-hand-events.jsonl | grep -v 'null'
```
If found: showdown is captured. Check `handRank`, `winCards`, and HAND_RESULT text for revealed cards. Check PLAYER_STATE/HERO_CARDS for opponent hole card visibility.

**GAP-2 (CHECK)**:
```bash
grep '"action":"CHECK"' captures/<new-session>/normalized-hand-events.jsonl | grep '"inferred":false'
```
If found: genuine CHECK observed and decoded. Verify it came from `roundId=11, amount=0`.

**GAP-3 (side pots)**:
```bash
grep '"potIndex":1' captures/<new-session>/normalized-hand-events.jsonl
```
If found: side pot award observed.

**GAP-4 (join/leave)**:
```bash
grep '0x65\|CLIENT_LEAVE\|TABLE_SNAPSHOT' captures/<new-session>/decoded-events.jsonl | head
```
Check for new TABLE_SNAPSHOT after rejoin. Check PLAYER_STATE transitions during sit-out.

**GAP-6 (timeout)**:
Manually check the decoded-events.jsonl around the timestamp where you let the timer expire. Look for any new opcode or action pattern.

---

## Success Criteria

| Gap | Criterion | Evidence |
|-----|-----------|---------|
| GAP-1 | `showdown: true` in at least 1 HAND_SUMMARY | grep output |
| GAP-2 | `action: "CHECK", inferred: false` in at least 1 PLAYER_ACTION | grep output |
| GAP-3 | `potIndex: 1` in at least 1 POT_AWARD | grep output (stretch) |
| GAP-4 | TABLE_SNAPSHOT or PLAYER_STATE change during sit-out/rejoin | decoded-events scan |
| GAP-6 | Action emitted after timer expiry (fold or check?) | decoded-events scan |

**Minimum success**: GAP-1 and GAP-2 closed. GAP-3 is stretch. GAP-4 and GAP-6 are low-effort bonuses.

---

## What NOT to Do

- Do not play at multiple tables simultaneously (complicates analysis).
- Do not use browser extensions that inject scripts (may interfere with CDP).
- Do not open DevTools in the debug Chrome (competes with CDP connection).
- Do not navigate away from the poker table during Phase A/B (breaks CDP target).
- Do not worry about winning or losing — you're capturing data, not playing optimally.
