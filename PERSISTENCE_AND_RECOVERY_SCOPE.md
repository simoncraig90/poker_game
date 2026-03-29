# Persistence and Recovery Scope

What gets persisted, when, and how recovery works.

---

## Persistence Model

The event log is the **only persistent artifact**. Table state is derived from the log. This is already proven by `reconstructState()` and the Phase 2 conformance test (25/25).

### What Gets Written to Disk

| Artifact | When Written | Mutability |
|----------|-------------|------------|
| `events.jsonl` | appendFileSync on every event | Append-only (never truncated) |
| `meta.json` | On session create; on status change | Overwritten on status change only |

### What Does NOT Get Written

| Data | Why Not |
|------|---------|
| Table state snapshot | Derived from events — writing it would create a second source of truth |
| BettingRound state | Transient, reconstructable from hand events (or voided on restart) |
| WS client connections | Ephemeral, clients reconnect |
| Command log | Debug-only, not needed for correctness |

---

## Session Lifecycle

```
CREATE → ACTIVE → COMPLETE (archived)
            ↑
            └── RECOVERY (from disk on restart)
```

### Create
1. Generate sessionId: `session-{timestamp}`
2. Create directory: `data/sessions/{sessionId}/`
3. Write `meta.json` with config + status="active"
4. Create `events.jsonl` (empty)
5. Emit TABLE_SNAPSHOT as first event

### Active
- Events appended to events.jsonl as they occur
- meta.json updated on handsPlayed milestone (every 10 hands, or on explicit save)

### Recovery
1. Read meta.json → get config, sessionId
2. Read events.jsonl → parse all events
3. Call reconstructState(events) → get table state
4. Check for incomplete hand (HAND_START without HAND_END)
5. If incomplete: append HAND_END(void=true), reset per-hand state
6. Resume normal operation — new events append to same file

### Complete (Archive)
1. Set meta.json status="complete"
2. No more events can be appended
3. Session is read-only — viewable in history, replayable

---

## meta.json Schema

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

status is one of: `"active"`, `"complete"`.

---

## EventLog Changes

### Before (Phase 1-5)

```javascript
constructor(filePath) {
  fs.writeFileSync(filePath, "");  // TRUNCATES on every construction
}
```

### After (Phase 6)

```javascript
constructor(filePath, loadExisting = false) {
  if (loadExisting && fs.existsSync(filePath)) {
    // Load existing events
    const content = fs.readFileSync(filePath, "utf8");
    this.events = content.trim().split("\n").filter(Boolean).map(JSON.parse);
  } else {
    // New session — create empty file
    fs.writeFileSync(filePath, "");
    this.events = [];
  }
}
```

The `loadExisting` flag is only true during recovery. Normal creation still starts fresh.

---

## Mid-Hand Recovery

If events.jsonl ends with a HAND_START but no HAND_END, the hand is incomplete. Possible causes: server crash, kill -9, power loss.

### Strategy: Void the Incomplete Hand

On recovery detection:
1. Identify the last HAND_START without a matching HAND_END
2. Find all players who were inHand at that point
3. Reconstruct their stacks to pre-hand values (from HAND_START.players)
4. Append a synthetic HAND_END event with `void: true`
5. Log: "Recovered: voided incomplete hand #{handId}"

This loses the partial hand but preserves accounting integrity. No chips are created or destroyed.

### Why Not Resume Mid-Hand

Resuming requires rebuilding BettingRound state (who has acted, current bet level, whose turn). This is possible but adds complexity for a rare edge case. Voiding is simple, safe, and auditable.

---

## Server Startup Flow

```
1. Check data/sessions/ for any session with status="active"
2. If found:
     a. Load events.jsonl
     b. Reconstruct state
     c. Handle mid-hand if needed
     d. Log: "Recovered session {id}: {N} events, {M} hands"
3. If not found:
     a. Create new session directory
     b. Initialize fresh
     c. Log: "Created new session {id}"
4. Start HTTP + WS server
5. Accept connections (welcome sends recovered state)
```

---

## Client Reconnection

No client-side changes needed. The existing flow already handles this:
1. Client connects (or auto-reconnects after disconnect)
2. Server sends welcome with current state
3. Client renders state
4. Play continues

The client does not know or care whether the server was restarted. The welcome message is identical whether the session is fresh or recovered.
