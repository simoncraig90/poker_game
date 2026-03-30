# Engine Phase 2 Plan

**Status: COMPLETE** — Session dispatch, reconstructState, SEAT_PLAYER/LEAVE_TABLE events. Conformance: 25/25.

Formalize the command/API boundary on top of the Phase 1 engine.

---

## Objective

Turn the engine into a callable service with:
1. A structured command surface (command in → result + events out)
2. State reconstruction from event log alone (no hidden state)
3. Conformance proof: replayed state === live engine state after every command

---

## What Phase 1 Has

`src/index.js` exposes `createGame()` which returns an object with direct method calls. This works but has gaps:

| Gap | Problem |
|-----|---------|
| No structured command/response | Methods throw on error instead of returning error objects |
| No command log | Can't replay the command sequence that produced the event log |
| No state reconstruction from log | EventLog is append-only but the engine can't be rebuilt from it |
| Seat changes not logged | `sitDown`/`leave` mutate state but emit no events |
| No session lifecycle | No clean start/stop/resume semantics |

## What Phase 2 Adds

| Feature | Deliverable |
|---------|------------|
| Command envelope | Every input goes through `dispatch(command) → result` |
| Structured results | `{ ok, events[], error?, state? }` instead of throws |
| Seat events | `SEAT_PLAYER` and `LEAVE_TABLE` events in the log |
| State snapshot from log | `reconstructState(eventLog) → TableState` |
| Session runner | Drives a table session with command dispatch + event log |
| Conformance test | Proves live state === reconstructed state at every step |

---

## Module Layout (additions to Phase 1)

```
src/
  engine/            (Phase 1, unchanged)
  api/
    commands.js      Command types + envelope schema
    dispatch.js      dispatch(session, command) → result
    session.js       Session runner (table + log + dispatch loop)
    reconstruct.js   Rebuild TableState from event log
  index.js           Updated: exports createSession instead of createGame
```

---

## Implementation Tasks

### Task 1: commands.js — Command Types

```javascript
const COMMANDS = {
  CREATE_TABLE:   "CREATE_TABLE",
  SEAT_PLAYER:    "SEAT_PLAYER",
  LEAVE_TABLE:    "LEAVE_TABLE",
  START_HAND:     "START_HAND",
  PLAYER_ACTION:  "PLAYER_ACTION",
  GET_STATE:      "GET_STATE",
  GET_EVENT_LOG:  "GET_EVENT_LOG",
};

// Command envelope:
// { type: COMMANDS.*, payload: {...}, ts: epoch }

// Result envelope:
// { ok: bool, events: Event[], error?: string, state?: TableState }
```

### Task 2: dispatch.js — Command Router

```javascript
function dispatch(session, command) → { ok, events, error?, state? }
```

Routes each command type to the engine, catches errors, returns structured results. Every successful command returns the events it produced. Every failed command returns `{ ok: false, error }` with zero events.

### Task 3: session.js — Session Runner

```javascript
class Session {
  constructor(config, options)
  dispatch(command) → result         // single entry point
  getState() → TableState            // current live state
  getEventLog() → Event[]            // full event history
  getHandEvents(handId) → Event[]    // events for one hand
}
```

Wraps table + orchestrator + event log. Manages session lifecycle. The session is the single owner of all mutable state.

### Task 4: reconstruct.js — State Reconstruction

```javascript
function reconstructState(events) → TableState
```

Replays an event log through a fresh reducer to produce the equivalent TableState. Uses only the data in the events — no external state.

This is the core of the conformance claim: if `reconstructState(session.getEventLog())` equals `session.getState()`, there is no hidden state.

### Task 5: Seat Events

Add two new event types to the log:

- `SEAT_PLAYER`: emitted when a player sits down
- `LEAVE_TABLE`: emitted when a player leaves

These close the gap where seat mutations were invisible in the event log.

### Task 6: Conformance Tests

Prove the three claims:
1. Event log fully reconstructs state
2. Replayed state matches live state
3. No hidden state outside the log

---

## Success Criteria

- [ ] Every engine mutation goes through `dispatch()`
- [ ] Every `dispatch()` returns structured `{ ok, events, error }`
- [ ] `sitDown` and `leave` emit events to the log
- [ ] `reconstructState(log)` matches `getState()` after every command
- [ ] All Phase 1 tests still pass through the new dispatch interface
- [ ] Event log from a full session replays through `replay-normalized-hand.js`
