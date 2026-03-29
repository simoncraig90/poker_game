# Session Browser Scope

What the Sessions tab shows and how it works.

---

## UI Layout

Third tab in the right panel:

```
[Events] [History] [Sessions]
```

### Session List View (default)

```
Sessions
─────────────────────────────
● session-1711745100000     ← green dot = active
  Poker Lab | 12 hands
  Started: 2026-03-30 12:00

○ session-1711745000000     ← grey dot = complete
  Poker Lab | 47 hands
  Started: 2026-03-30 11:30

[Archive & New Session]
```

Each entry shows:
- Status indicator: `●` green (active) or `○` grey (complete)
- Session ID (truncated if needed)
- Table name from config
- Hands played count
- Created timestamp (human-readable)

### Interactions

| Action | Behavior |
|--------|----------|
| Click active session | No-op (already viewing it) |
| Click completed session | Fetch GET_HAND_LIST for that session, show read-only history |
| Click "Archive & New" | Send ARCHIVE_SESSION, receive new welcome, re-render |

### Completed Session Detail

When a completed session is clicked, replace the session list with:

```
← Back to sessions

session-1711745000000 (complete)
Poker Lab | 47 hands | 2026-03-30 11:30

Hand #1  Alice wins 10c
Hand #2  Bob wins $3.09
Hand #3  [VOIDED]
Hand #4  Charlie wins 25c
...
```

This reuses the existing hand list rendering from the History tab. Voided hands show `[VOIDED]` instead of a winner.

Clicking a hand within a completed session shows the timeline detail (same as History tab detail view).

---

## Server Support

### Existing Commands Used

- `GET_SESSION_LIST` → returns all sessions with metadata
- `GET_HAND_LIST` → returns hands for the current active session
- `ARCHIVE_SESSION` → archives current, creates new

### New: GET_SESSION_HANDS

Need to fetch hand list for a SPECIFIC session (not just the active one). Two options:

**Option A**: Load the session's events.jsonl on server, scan for HAND_SUMMARY events. This requires the server to read archived session files on demand.

**Option B**: Include hand summaries in the session list response (pre-computed from meta or scanned on list).

**Chosen: Option A** — add a payload to GET_HAND_LIST: `{ sessionId }`. When sessionId is provided and differs from the active session, load that session's events from disk and return its hand summaries. This keeps the command surface minimal.

---

## What This Is NOT

- Not a session management dashboard (no delete, rename, export)
- Not a cross-session analytics view
- Not showing real-time stats (win rate, etc.)
- Not a lobby (no table selection, no multi-table)

---

## Implementation

### Client Changes

1. Add "Sessions" tab button in `#panel-tabs`
2. Add `#sessions-panel` div in panel content area
3. `loadSessionList()` → sends GET_SESSION_LIST, renders list
4. `loadSessionHands(sessionId)` → sends GET_HAND_LIST with sessionId, renders read-only hand list
5. `archiveSession()` → sends ARCHIVE_SESSION

### Server Changes

1. Update GET_HAND_LIST handler: when `payload.sessionId` is provided and differs from active session, load that session's events from disk
2. This is a small addition to `session.js` or handled in the WS server command router
