# Manual Recovery Demo Checklist

Step-by-step operator demo proving persistence and recovery work end-to-end in the browser.

---

## Prerequisites

- Server is NOT running
- `data/sessions/` directory is empty (or delete its contents for a clean start)

---

## Demo 1: Normal Session + Restart Recovery

### Part A: Play a Session

1. Start the server:
   ```
   node src/server/ws-server.js
   ```
   Console shows: `Created new session session-{id}`

2. Open browser: `http://localhost:9100`

3. Click 3 empty seats to add players (e.g., Alice/1000, Bob/800, Charlie/600)

4. Play 3 hands:
   - Click "Deal" (or press D)
   - Use Fold/Call buttons to play through each hand
   - Note the stacks after hand 3

5. Verify in Events tab: HAND_START, BLIND_POST, PLAYER_ACTION, etc. for each hand

6. Switch to History tab: should show 3 completed hands with winner/pot

7. **Record the current state**: who is seated, what are their stacks, how many hands played. Write these down.

### Part B: Kill the Server

8. Go to the server terminal and press Ctrl+C (or kill the process)

9. Observe: the browser shows "Disconnected" status and auto-reconnect attempts

### Part C: Restart and Verify Recovery

10. Start the server again:
    ```
    node src/server/ws-server.js
    ```
    Console should show: `Recovering session session-{id}...` then `Recovered: N events, 3 hands`

11. The browser auto-reconnects. Check:
    - [ ] Status shows "Connected"
    - [ ] Same 3 players are seated
    - [ ] Stacks match what you wrote down in step 7
    - [ ] Header shows correct hands played count
    - [ ] History tab shows the same 3 hands
    - [ ] Recovery banner appears briefly (if implemented)

12. **Play hand 4**: Deal and play normally. Verify it works and the hand appears in History.

**Pass criteria**: All player stacks, hand history, and table state are identical before and after restart.

---

## Demo 2: Mid-Hand Crash Recovery

### Part A: Start a Hand

1. Server should still be running from Demo 1 (or start it fresh with players seated)

2. Click "Deal" to start a new hand

3. One player acts (e.g., fold) but DON'T finish the hand — leave it mid-action

4. **Record**: Note which hand # is in progress and what the pre-hand stacks were (visible in the HAND_START event in the log)

### Part B: Force Crash

5. Go to the server terminal. Kill it hard:
   - Ctrl+C
   - Or: `taskkill /F /PID {pid}` on Windows

6. The hand was incomplete — HAND_START exists in the log but no HAND_END

### Part C: Restart and Verify Void

7. Restart the server:
   ```
   node src/server/ws-server.js
   ```
   Console should show:
   ```
   Recovering session session-{id}...
   Recovery: voided incomplete hand #{N}
   Recovered: M events, K hands
   ```

8. Browser reconnects. Check:
   - [ ] Status shows "Connected"
   - [ ] Players are seated with **pre-hand stacks** (the crash-hand stacks are reversed)
   - [ ] No hand is in progress (between-hands state)
   - [ ] History tab shows the voided hand marked differently (or one fewer hand than expected if void excluded it)
   - [ ] Events tab shows the void entry

9. **Play the next hand**: Deal and play normally. It should start clean.

10. Check final stacks: `sum(all stacks) == sum(original buy-ins)` (no chips lost or created)

**Pass criteria**: Mid-hand crash causes no chip loss. Stacks restore to pre-hand values. Play continues normally.

---

## Demo 3: Archive + New Session

### Part A: Archive Current Session

1. Switch to Sessions tab (if available) or use the Archive button

2. Send ARCHIVE_SESSION command (via button or console: `{"cmd":"ARCHIVE_SESSION"}`)

3. Observe:
   - Server console: `Archived old session. New session: session-{newId}`
   - Browser receives new welcome, table is empty
   - All previous players are gone (new session)

### Part B: Verify Archive

4. Switch to Sessions tab. Should show:
   - Previous session: status = "complete", hands played = N
   - New session: status = "active", hands played = 0

5. If the completed session is clickable: click it and verify hand history is viewable (read-only)

### Part C: New Session

6. Seat new players in the fresh session

7. Play 1-2 hands

8. Verify: hand numbers start from 1, stacks are fresh

**Pass criteria**: Old session is read-only and browsable. New session starts clean. No data from old session leaks into new one.

---

## Demo 4: Full Restart After Archive

1. Kill the server after completing Demo 3

2. Restart the server

3. Observe: server recovers the NEW active session (not the archived one)

4. Browser reconnects to the new session

**Pass criteria**: Archived sessions don't interfere with recovery. Only the active session is recovered.

---

## Checklist Summary

| # | Scenario | Key Check |
|---|----------|-----------|
| 1 | Normal restart | Stacks and history survive restart |
| 2 | Mid-hand crash | Incomplete hand voided, stacks restored, no chip leak |
| 3 | Archive + new | Old session read-only, new session clean |
| 4 | Post-archive restart | Recovers correct (active) session |

All four demos should be completable in under 15 minutes.
