# Phase 6 Implementation Notes

---

## What Changed

### src/engine/event-log.js
- Added `loadExisting` parameter to constructor
- When `loadExisting=true` and file exists: reads existing events into memory, appends new events to end
- When `loadExisting=false` (default): truncates file as before (backward compat)

### src/api/storage.js (new)
- `SessionStorage` class manages `data/sessions/{sessionId}/` directories
- Operations: create, load, list, findActive, updateMeta, archive
- meta.json tracks: sessionId, config, createdAt, status, handsPlayed, lastEventAt

### src/api/session.js
- Added `Session.load()` static method for recovery from disk
- Recovery rebuilds table state from `reconstructState(events)` then restores seats directly (bypasses buy-in validation since stacks may exceed maxBuyIn from winnings)
- Added mid-hand detection: scans for HAND_START without matching HAND_END
- Added mid-hand void: restores pre-hand stacks, appends synthetic HAND_END with `void: true`
- Added `status` field: "active" sessions accept all commands, "complete" sessions are read-only (GET_STATE/GET_HAND_LIST/GET_HAND_EVENTS only)

### src/api/commands.js
- Added GET_SESSION_LIST, ARCHIVE_SESSION command types

### src/server/ws-server.js
- Startup: checks `storage.findActive()` for existing active session, recovers if found, creates new if not
- Added `dataDir` config option for test isolation
- GET_SESSION_LIST command: returns all sessions from storage
- ARCHIVE_SESSION command: marks current session complete, creates new one, sends welcome to all clients
- Periodic meta.json updates (every 5 hands)
- Graceful shutdown updates meta.json

### .gitignore
- Added `data/` (session storage is local, per-machine)

### test/ws-conformance.test.js, test/e2e-session.test.js
- Updated to use isolated `dataDir` per test run (prevents cross-test interference from recovery)

---

## Recovery Semantics

### Clean Recovery
Events file loaded in full. `reconstructState()` rebuilds seat/stack/button/handsPlayed. Seats restored directly on table object (bypasses sitDown validation). New events append to same file.

### Mid-Hand Recovery
Detected by: last HAND_START index > last HAND_END index. Void strategy:
1. Restore all player stacks to their HAND_START.players values
2. Decrement handsPlayed (voided hand doesn't count)
3. Clear per-hand state (inHand, folded, allIn, bet, totalInvested, holeCards)
4. Append synthetic HAND_END with `void: true, voidReason: "mid-hand recovery"`
5. Next hand starts clean

### Archive
`storage.archive(sessionId)` sets meta.json status to "complete". Session.dispatch rejects write commands when status is "complete".

---

## Test Results

| Suite | Checks | Status |
|-------|--------|--------|
| Phase 1: hand-lifecycle | PASS | Accounting PASS |
| Phase 2: conformance | 25/25 | No hidden state |
| Phase 3: ws-conformance | 31/31 | External conformance |
| Phase 5: e2e-session | 38/38 | Full session |
| **Phase 6: recovery** | **52/52** | **All recovery invariants** |

### Recovery Tests Breakdown

| Test | Checks | What It Proves |
|------|--------|----------------|
| T1: Clean recovery | 11 | Stacks, button, handsPlayed survive restart; can continue playing |
| T2: Mid-hand recovery | 8 | Incomplete hand voided, stacks restored, no chip leak |
| T3: Empty session | 3 | Single-player session loads, blocks hand start |
| T4: Archive read-only | 6 | Complete sessions reject writes, allow reads |
| T5: Session list | 4 | Multiple sessions discoverable, active findable |
| T6: Event log integrity | 4 | Disk count = memory count, no loss/duplication |
| T7: Cross-recovery conformance | 16 | Pre-shutdown state === post-recovery state, can continue |

---

## Design Decisions

**No snapshot persistence**: The event log is the only persistent artifact. State is always derived. This is consistent with the Phase 2 conformance proof and avoids dual-source-of-truth bugs.

**Void over resume for mid-hand**: Resuming a hand mid-action requires rebuilding BettingRound state (who acted, current bet level, whose turn). This is complex and brittle for a rare edge case. Voiding loses one hand but guarantees accounting integrity.

**Bypass buy-in validation on recovery**: A player who won chips may have a stack exceeding maxBuyIn. Recovery sets stacks directly without going through sitDown() validation. The stack value comes from the event log, which is authoritative.

**Periodic meta.json updates**: Every 5 hands, not every hand. meta.json is convenience (for listing), not source of truth. Worst case on crash: handsPlayed in meta.json is up to 5 behind reality, but the event log has the correct count.
