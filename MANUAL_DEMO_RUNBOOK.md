# Manual Demo Runbook

Operator-facing runbook for the trust checkpoint. Four demos, ~15 minutes total.

---

## Setup

1. Verify automated suites pass:
   ```
   npm test
   ```
   All 168 checks must be green. Do not proceed if any fail.

2. Clean slate:
   - Stop the server if running
   - Delete `data/sessions/` contents (or the whole directory)

3. Have a notepad open (text file or paper) to record evidence.

---

## Demo 1: Normal Session + Restart Recovery

**Goal**: Prove table state survives a clean server restart.

### Steps

| # | Action | What to observe |
|---|--------|-----------------|
| 1 | `node src/server/ws-server.js` | Console: `Created new session session-{id}` |
| 2 | Open `http://localhost:9100` | Table loads, status "Connected" |
| 3 | Click 3 empty seats. Add: Alice/1000, Bob/800, Charlie/600 | Players appear in seats |
| 4 | Deal and play 3 hands (Deal button or D key, then Fold/Call) | Hands complete normally |
| 5 | **RECORD**: player names, stacks, hands played count | Write these down |
| 6 | Check History tab: 3 completed hands visible | Winners and pots shown |
| 7 | Ctrl+C the server | Browser shows "Disconnected" |
| 8 | `node src/server/ws-server.js` | Console: `Recovering session...` then `Recovered: N events, 3 hands` |
| 9 | Browser auto-reconnects | Status: "Connected" |

### Verify

- [ ] Same 3 players seated
- [ ] Stacks match recorded values exactly
- [ ] Hands played count matches (3)
- [ ] History tab shows same 3 hands
- [ ] Recovery banner appears briefly
- [ ] Deal and play hand 4 -- completes normally

### Evidence to Record

- Screenshot or note: stacks before kill vs. stacks after recovery
- Screenshot: History tab after recovery showing 3 hands
- Note: hand 4 completed successfully (yes/no)

### Pass Criteria

All stacks, player positions, and hand history are identical before and after restart. Hand 4 plays normally.

### Fail Criteria

Any of: wrong stacks, missing player, missing hand history, hand 4 fails to deal.

---

## Demo 2: Mid-Hand Crash Recovery

**Goal**: Prove an incomplete hand is voided on recovery with no chip leak.

### Steps

| # | Action | What to observe |
|---|--------|-----------------|
| 1 | Server running with players seated (continue from Demo 1) | |
| 2 | Deal a new hand | Hand starts |
| 3 | One player acts (e.g., fold) but do NOT finish the hand | Hand in progress |
| 4 | **RECORD**: hand number, pre-hand stacks (from HAND_START event in Events tab) | Write these down |
| 5 | Ctrl+C the server (hard kill) | Incomplete hand in the log |
| 6 | `node src/server/ws-server.js` | Console: `Recovery: voided incomplete hand #N` |
| 7 | Browser auto-reconnects | |

### Verify

- [ ] Players seated with **pre-hand** stacks (not mid-hand stacks)
- [ ] No hand in progress (between-hands state)
- [ ] History tab shows voided hand marked distinctly
- [ ] Events tab shows void/recovery entry
- [ ] Deal next hand -- completes normally
- [ ] `sum(all stacks) == sum(original buy-ins)` -- no chips created or destroyed

### Evidence to Record

- Note: pre-hand stacks recorded vs. post-recovery stacks (must match)
- Screenshot: voided hand marker in History tab
- Calculation: total chips before vs. after

### Pass Criteria

Mid-hand crash causes zero chip leak. Stacks restore to pre-hand values. Next hand plays normally.

### Fail Criteria

Any of: stacks don't match pre-hand values, chip total changes, next hand fails, voided hand not marked.

---

## Demo 3: Archive + New Session

**Goal**: Prove session archival works and old sessions are browsable read-only.

### Steps

| # | Action | What to observe |
|---|--------|-----------------|
| 1 | Switch to Sessions tab | Current session listed as active (green dot) |
| 2 | Click "Archive & New Session" | Confirm dialog appears |
| 3 | Confirm | Console: `Archived old session. New session: session-{newId}` |
| 4 | Browser shows empty table, new session | No players seated |
| 5 | Sessions tab: old session shows "complete" (grey dot) | |
| 6 | Click the completed session | Hand list loads with hands from old session |
| 7 | Click a hand in the list | Hand detail/timeline loads |
| 8 | Seat 2 new players in the new session | Players appear |
| 9 | Deal and play 1 hand | Completes normally, hand #1 |

### Verify

- [ ] Old session listed as complete
- [ ] Old session's hand history is viewable (read-only)
- [ ] Voided hands (if any) still marked in archived session
- [ ] New session starts with hand #1
- [ ] No data from old session appears in new session's History tab
- [ ] New session works normally

### Evidence to Record

- Screenshot: Sessions tab showing both sessions (one active, one complete)
- Screenshot: browsing a hand from the archived session
- Note: new session hand numbering starts at 1

### Pass Criteria

Old session is read-only and browsable. New session is clean with no data leakage. Play works in the new session.

### Fail Criteria

Any of: old session not visible, old session data leaks into new session, can't browse archived hands, new session broken.

---

## Demo 4: Post-Archive Restart

**Goal**: Prove the server recovers only the active session after an archive.

### Steps

| # | Action | What to observe |
|---|--------|-----------------|
| 1 | Server running with new session from Demo 3 | |
| 2 | **RECORD**: new session's player names, stacks, hands played | |
| 3 | Ctrl+C the server | |
| 4 | `node src/server/ws-server.js` | Console recovers the NEW session, not the archived one |
| 5 | Browser reconnects | |

### Verify

- [ ] Recovered session is the new (active) session, not the archived one
- [ ] Players and stacks match recorded values
- [ ] Sessions tab still shows both sessions with correct statuses
- [ ] Archived session still browsable

### Evidence to Record

- Note: session ID recovered matches the active session ID
- Screenshot: Sessions tab after restart

### Pass Criteria

Only the active session is recovered. Archived sessions remain untouched and browsable.

### Fail Criteria

Wrong session recovered, or archived session corrupted/missing.

---

## After All Demos

Fill in the checkpoint summary in **OPERATOR_TRUST_CHECKPOINT.md**:

- [ ] All automated suites green
- [ ] Demo 1: PASS / FAIL
- [ ] Demo 2: PASS / FAIL
- [ ] Demo 3: PASS / FAIL
- [ ] Demo 4: PASS / FAIL

If all pass, proceed to **POST_DEMO_DECISION_FRAME.md** for the next development branch decision.

If any fail: fix the issue, re-run `npm test`, then re-run only the failed demo.
