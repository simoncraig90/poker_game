# Session Storage Layout

Physical directory structure and file formats for persistent sessions.

---

## Directory Structure

```
data/
  sessions/
    session-1711745000000/
      meta.json
      events.jsonl
    session-1711745100000/
      meta.json
      events.jsonl
    session-1711745200000/
      meta.json
      events.jsonl
```

### Path Convention

| Component | Format | Example |
|-----------|--------|---------|
| Data root | `data/` | Relative to project root |
| Sessions dir | `data/sessions/` | Contains all session directories |
| Session dir | `data/sessions/{sessionId}/` | One directory per session |
| Event log | `data/sessions/{sessionId}/events.jsonl` | Append-only JSONL |
| Metadata | `data/sessions/{sessionId}/meta.json` | Overwritten on status change |
| Session ID | `session-{Date.now()}` | Monotonic, unique per process start |

### .gitignore

`data/` should be in `.gitignore`. Session data is local, per-machine, and potentially large.

---

## File: events.jsonl

Append-only. One JSON object per line. Schema matches the normalized event model exactly.

```jsonl
{"sessionId":"session-1711745000000","handId":null,"seq":0,"type":"TABLE_SNAPSHOT",...}
{"sessionId":"session-1711745000000","handId":null,"seq":1,"type":"SEAT_PLAYER",...}
{"sessionId":"session-1711745000000","handId":"1","seq":0,"type":"HAND_START",...}
{"sessionId":"session-1711745000000","handId":"1","seq":1,"type":"BLIND_POST",...}
...
```

### Size Estimates

| Session Length | Hands | Estimated Events | File Size |
|---------------|-------|-----------------|-----------|
| 15 minutes | 20 | ~250 | ~50 KB |
| 1 hour | 80 | ~1000 | ~200 KB |
| 4 hours | 300 | ~4000 | ~800 KB |

Small enough to load entirely into memory on recovery.

---

## File: meta.json

Written once on creation. Updated on:
- Session archive (status → "complete")
- Periodic hands-played update (every 10 hands)
- Clean server shutdown

```json
{
  "sessionId": "session-1711745000000",
  "config": {
    "tableId": "table-1",
    "tableName": "Poker Lab",
    "maxSeats": 6,
    "sb": 5,
    "bb": 10,
    "minBuyIn": 400,
    "maxBuyIn": 1000
  },
  "createdAt": "2026-03-30T12:00:00.000Z",
  "status": "active",
  "handsPlayed": 15,
  "lastEventAt": "2026-03-30T12:45:00.000Z"
}
```

### Status Values

| Status | Meaning | Mutable? |
|--------|---------|----------|
| `active` | Session is live, accepting events | Yes |
| `complete` | Session is archived, read-only | No |

Only one session may be `active` at a time per server instance.

---

## Operations

### List Sessions

Read all `data/sessions/*/meta.json`. Return array of metadata objects sorted by createdAt descending.

### Load Session

1. Read `meta.json` for config and sessionId
2. Read `events.jsonl` line by line, parse JSON
3. Pass events to `reconstructState()`
4. Return rebuilt Session object

### Create Session

1. Generate sessionId
2. `mkdir data/sessions/{sessionId}`
3. Write `meta.json` with status="active"
4. Create empty `events.jsonl`
5. Initialize Session with config

### Archive Session

1. Update `meta.json`: status="complete", final handsPlayed
2. Session is no longer writable
3. A new active session can be created

### Delete Session (future)

Not in Phase 6 scope. Sessions accumulate. Manual deletion via filesystem.

---

## Concurrency

Single server process, single active session. No concurrent writes to the same events.jsonl. appendFileSync is atomic for small writes on the same process.

Multi-process or multi-server is out of scope.
