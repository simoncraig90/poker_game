# Engine Phase 3 Plan

Build the thinnest external control surface on top of the Phase 2 Session API.

---

## Transport Recommendation: Local WebSocket Server

### Options Evaluated

| Option | Pros | Cons |
|--------|------|------|
| **CLI-only** | Zero deps, instant | No concurrent clients, no event push, dead-end for UI |
| **HTTP + polling** | Simple, stateless | No event streaming, polling latency, two-request pattern (act + poll state) |
| **WebSocket** | Bidirectional, event push, natural fit for poker | Slightly more setup, requires `ws` package |

### Decision: WebSocket

**Why**: Poker is inherently a push-based protocol. When one player acts, all others need to see the result immediately. HTTP polling would require every client to poll after every action. WebSocket gives us:

1. **Command in**: client sends JSON command → server dispatches → returns result
2. **Events out**: server pushes new events to all connected clients in real-time
3. **State on demand**: client can request state at any time
4. **Natural browser path**: browser WebSocket client is trivial to build later

The `ws` npm package is 0 dependencies, battle-tested, and works identically on Windows. One package added.

A CLI harness is also included (for scripted testing and solo play), but it calls the same Session dispatch boundary.

---

## Architecture

```
                    ┌──────────────┐
                    │  Session     │  (Phase 2, unchanged)
                    │  .dispatch() │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴───┐ ┌─────┴─────┐
        │ WS Server │ │  CLI  │ │  Tests    │
        │ (network) │ │(stdin)│ │ (direct)  │
        └───────────┘ └───────┘ └───────────┘
              │
        ┌─────┴─────┐
        │ WS Client │  (browser, later)
        └───────────┘
```

All three entry points call `session.dispatch(command)`. No game logic in the transport layer. The event log remains the single source of truth.

---

## Module Layout

```
src/
  api/              (Phase 2, unchanged)
  engine/           (Phase 1, unchanged)
  server/
    ws-server.js    WebSocket server: accept connections, route commands, push events
    protocol.js     Wire protocol: JSON message framing for WS
  cli/
    cli-runner.js   Interactive stdin-based table driver
  index.js          Updated exports
```

---

## Implementation Tasks

### Task 1: protocol.js — Wire Protocol

JSON message format over WebSocket:

**Client → Server (command)**:
```json
{ "id": "msg-1", "cmd": "PLAYER_ACTION", "payload": { "seat": 0, "action": "FOLD" } }
```

**Server → Client (response)**:
```json
{ "id": "msg-1", "ok": true, "events": [...], "error": null }
```

**Server → Client (broadcast)**:
```json
{ "broadcast": true, "events": [...] }
```

`id` is a client-chosen correlation ID. Responses echo it back. Broadcasts have no `id`.

### Task 2: ws-server.js — WebSocket Server

- Listens on a configurable port (default 9100)
- Manages one Session per server instance (single-table)
- On message: parse JSON → dispatch to session → send response to sender → broadcast events to all clients
- On connect: send current state as welcome message
- On disconnect: no-op (seat management is explicit)
- Event push: after every successful mutation, broadcast new events to all connected clients

### Task 3: cli-runner.js — CLI Harness

- Reads commands from stdin (one JSON per line, or shorthand)
- Dispatches to Session
- Prints results and events to stdout
- Supports shorthand: `fold`, `call`, `bet 20`, `raise 40`, `state`, `log`
- Useful for scripted test sessions and quick manual play

### Task 4: Conformance test for external clients

- Start WS server
- Connect a test client
- Send commands via WebSocket
- Verify responses match direct dispatch results
- Verify event broadcasts arrive
- Verify state from WS matches reconstructState(events)

---

## Event Streaming Plan

### Push Model

After every `dispatch()` that produces events:
1. Return events in the response to the commanding client
2. Broadcast the same events to all other connected clients
3. Events are the normalized schema — no transformation at the transport layer

### Per-Seat Visibility

For Phase 3, all events are sent to all clients (no seat-based filtering). Hole cards are visible to everyone (development mode). Seat-based visibility filtering is a future concern.

### Event Ordering

Events are emitted in the order they appear in the append-only log. The `seq` field within each hand provides ordering. Clients process events in arrival order.

### Reconnection

On connect, the server sends a `WELCOME` message containing:
- Current table state (from `GET_STATE`)
- Session ID
- Event count (so client knows if it missed events)

Full event replay: client can request `GET_EVENT_LOG` to catch up.

---

## Hand Replay Access Plan

### Via Command

```json
{ "cmd": "GET_EVENT_LOG" }
```

Returns all events for the session. Client can filter by `handId`.

### Via Direct File

The JSONL event log is written to disk by `EventLog`. External tools (replay-normalized-hand.js, analysis scripts) can read it directly.

### Future: Per-Hand Endpoint

A `GET_HAND_EVENTS` command (payload: `{ handId }`) can be added trivially — the Session already has `getHandEvents(handId)`. Not implementing in Phase 3 unless needed.

---

## Success Criteria

- [ ] WS server starts, accepts connections, routes commands
- [ ] Client sends command → receives structured response with events
- [ ] All connected clients receive broadcast events after every mutation
- [ ] CLI harness can play a full hand via stdin
- [ ] WS client conformance: state matches reconstructState(events)
- [ ] All Phase 1 and Phase 2 tests still pass
- [ ] Event log JSONL file is identical whether accessed via WS or direct
