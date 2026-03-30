# Engine Phase 6 Plan

**Status: COMPLETE** — SessionStorage, Session.load, mid-hand void, recovery (52/52).

Persistence, recovery, and session continuity.

---

## Objective

Make the server resilient to restarts. When the process stops and restarts, the table session resumes from the event log on disk — same state, same hand in progress (if any), clients reconnect and continue playing.

---

## What Phase 5 Has

- EventLog writes append-only JSONL to a file path passed at construction
- reconstructState(events) rebuilds full table state from the log
- The file path is ephemeral — set by the caller, lost on restart
- EventLog truncates the file on construction (`fs.writeFileSync(filePath, "")`)
- Session constructor always creates a fresh table + fresh log
- No concept of loading an existing session from disk

## What Phase 6 Adds

| Capability | Mechanism |
|-----------|-----------|
| Stable session directory | `data/sessions/{sessionId}/` with events.jsonl + meta.json |
| Session metadata | meta.json: config, sessionId, createdAt, status (active/complete) |
| Load from disk | `Session.load(sessionDir)` → rebuild state from events.jsonl |
| EventLog append-only (no truncate) | Load existing events, append new ones |
| Server restart recovery | On start, scan for active session and resume |
| Session listing | List all sessions (active + completed) from disk |
| Session archival | Mark a session complete when table empties or manually |
| Reconnecting clients | Welcome message sends current state (already works) |

---

## What Remains In-Memory

| Data | Location | Reason |
|------|----------|--------|
| BettingRound state | orchestrator.round | Transient — rebuilt when hand resumes. If mid-hand on restart, the round must be reconstructed from action events. |
| Connected WS clients | ws-server clients Set | Ephemeral by nature |
| Command log | session.commandLog | Debug-only, not needed for recovery |

## What Must Be Persisted Immediately

| Data | File | Timing |
|------|------|--------|
| Every event | events.jsonl | appendFileSync on every emit (already done) |
| Session metadata | meta.json | Written on create, updated on status change |
| Table config | Inside TABLE_SNAPSHOT event | Already in events.jsonl |

---

## Implementation Tasks

### Task 1: Session storage directory layout

Create and manage `data/sessions/{sessionId}/`:
```
data/
  sessions/
    session-1711745000000/
      meta.json         { sessionId, config, createdAt, status, handsPlayed }
      events.jsonl      append-only event log (source of truth)
    session-1711745100000/
      meta.json
      events.jsonl
```

### Task 2: EventLog — load existing + append

Change EventLog constructor: if file exists with content, load events into memory (don't truncate). New events append to the end.

### Task 3: Session.load() — reconstruct from disk

Static method that reads meta.json + events.jsonl, calls reconstructState(), rebuilds the Session object with correct table state, handsPlayed, button position, and seated players.

### Task 4: Mid-hand recovery

If the event log ends mid-hand (HAND_START without HAND_END), the recovered session must:
- Detect the incomplete hand
- Void it (emit HAND_END with a void flag, reset state)
- Allow a new hand to start cleanly

Full mid-hand resumption (rebuilding BettingRound state) is deferred — too complex for the benefit. Voiding the partial hand is safe and simple.

### Task 5: Server startup with recovery

On start, the server:
1. Checks for an active session in `data/sessions/`
2. If found: loads it, resumes
3. If not found: creates a new session
4. Prints recovery status to console

### Task 6: Session listing and archival

- `GET_SESSION_LIST` command: returns all sessions with metadata
- `ARCHIVE_SESSION` command: marks current session complete, creates new one
- Sessions with status="complete" are read-only (no new events)

---

## Recovery Invariants

### RI-1: Idempotent Recovery
Loading the same event log twice produces identical state.

### RI-2: No Hidden State
`reconstructState(events on disk) === session.getState()` after recovery. The Phase 2 conformance test already proves this — Phase 6 applies it at startup.

### RI-3: Event Log is Complete
Every state-changing operation produces an event before the state change is visible. No mutation without a log entry.

### RI-4: Append-Only Integrity
The event log file is never truncated, rewritten, or edited. New events are appended. Archived sessions are read-only.

### RI-5: Graceful Mid-Hand Recovery
If the log ends mid-hand, the partial hand is voided on recovery. No corrupt state is carried forward.

---

## Success Criteria

- [x] Server creates session directory with meta.json + events.jsonl
- [x] Server restart recovers seated players, stacks, hand count
- [x] Clients reconnect after restart and see correct state
- [x] Mid-hand restart voids the partial hand cleanly
- [x] GET_SESSION_LIST returns all sessions from disk
- [x] Archived sessions are read-only
- [x] reconstructState(disk events) matches live state after recovery
- [x] All Phase 1-5 tests pass through the new storage layer
