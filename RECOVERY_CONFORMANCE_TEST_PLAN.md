# Recovery Conformance Test Plan

Tests proving that persistence and recovery introduce no hidden state, no data loss, and no accounting corruption.

---

## Core Claim

After recovery from disk, the session state is identical to what it was before the server stopped. The event log is the complete record. Nothing is lost.

---

## Test 1: Clean Recovery

### Setup
1. Create session, seat 3 players, play 5 hands (fold-out), all settle normally
2. Record final state: stacks, handsPlayed, button position
3. Read events.jsonl from disk

### Recovery
4. Create a new Session using `Session.load(sessionDir)`
5. Get state from recovered session

### Assertions
- Recovered stacks match pre-shutdown stacks per seat
- Recovered handsPlayed matches
- Recovered button position matches
- Recovered sessionId matches
- `reconstructState(events from disk)` matches recovered getState()
- Can start a new hand after recovery (hand 6 works)
- Stack accounting still holds across recovery boundary

---

## Test 2: Mid-Hand Recovery

### Setup
1. Create session, seat players, start hand
2. Post blinds, deal cards, one player acts (FOLD)
3. Kill the session (don't finish the hand — no HAND_END)
4. events.jsonl has HAND_START but no HAND_END

### Recovery
5. Load session from disk
6. Detect incomplete hand

### Assertions
- Recovery appends a void HAND_END event
- Player stacks are restored to pre-hand values (from HAND_START.players)
- No chips lost or created (stack sum matches initial buy-ins minus previous hand results)
- handsPlayed does NOT increment for the voided hand
- A new hand can be started after recovery
- The new hand's HAND_START shows correct stacks

---

## Test 3: Empty Session Recovery

### Setup
1. Create session, seat 1 player (not enough for a hand), write to disk

### Recovery
2. Load session from disk

### Assertions
- Session loads without error
- 1 player seated, correct stack
- Cannot start hand (need 2 players)
- No events beyond TABLE_SNAPSHOT + SEAT_PLAYER

---

## Test 4: Archived Session Read-Only

### Setup
1. Create session, play 3 hands, archive it (status="complete")

### Assertions
- meta.json status is "complete"
- Attempting to dispatch START_HAND returns error
- Attempting to dispatch SEAT_PLAYER returns error
- GET_STATE works (read-only access)
- GET_HAND_LIST works
- GET_HAND_EVENTS works
- events.jsonl is not modified after archive

---

## Test 5: Session List

### Setup
1. Create 3 sessions: 2 completed, 1 active
2. Each has different handsCounts

### Assertions
- GET_SESSION_LIST returns 3 entries
- Each entry has sessionId, status, handsPlayed, createdAt
- Sorted by createdAt descending
- Active session is identifiable by status="active"

---

## Test 6: Event Log Integrity Across Recovery

### Setup
1. Create session, play 10 hands
2. Record event count and last event
3. Recover session

### Assertions
- Event count on disk matches event count in memory before shutdown
- Event count after recovery matches (no events lost, no events duplicated)
- Last event on disk matches last event in memory
- Running the full event log through replay-normalized-hand.js produces Stack check PASS for every hand

---

## Test 7: Conformance Across Recovery Boundary

The strongest test: prove that no hidden state exists by comparing live state before and after recovery.

### Setup
1. Create session, seat 3 players
2. Play 5 hands with varied actions (fold, call, raise, multi-street)
3. After each hand: record `getState()`
4. After hand 5: save the live state as `preShutdownState`
5. Read events.jsonl from disk

### Recovery
6. Load session from a new process
7. Get state as `postRecoveryState`

### Assertions
- `preShutdownState.seats[*].stack === postRecoveryState.seats[*].stack` for all seats
- `preShutdownState.handsPlayed === postRecoveryState.handsPlayed`
- `preShutdownState.button === postRecoveryState.button`
- `reconstructState(events from disk) === postRecoveryState` (Phase 2 conformance applied to recovery)
- Can play hand 6 normally after recovery, accounting still passes

---

## Recovery Invariants to Verify

| ID | Invariant | Test |
|----|-----------|------|
| RI-1 | Idempotent: loading same log twice → same state | Test 1, load twice |
| RI-2 | No hidden state: reconstructState(disk) === getState() | Tests 1, 6, 7 |
| RI-3 | Complete log: event count on disk === event count in memory | Test 6 |
| RI-4 | Append-only: file never shrinks, events never reordered | Test 6 (compare files) |
| RI-5 | Mid-hand void: partial hand → voided, stacks restored | Test 2 |

---

## Implementation

Single test file: `test/recovery.test.js`

Uses Session directly (no WS server needed for recovery tests). The recovery path is pure: read files → reconstruct state → resume.

WS server recovery is an integration test: start server with existing session dir, connect client, verify state. Can be a separate `test/server-recovery.test.js` or folded into the E2E test.
