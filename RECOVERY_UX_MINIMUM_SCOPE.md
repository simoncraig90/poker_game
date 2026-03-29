# Recovery UX Minimum Scope

How recovery is surfaced to the human operator in the browser.

---

## Recovery Indicators

### Welcome Message Enhancement

The server's welcome message gains two fields:

```json
{
  "welcome": true,
  "sessionId": "session-...",
  "state": {...},
  "eventCount": 42,
  "recovered": true,
  "voidedHands": ["3"]
}
```

- `recovered`: true if the server loaded this session from disk on startup (vs. created fresh)
- `voidedHands`: array of handIds that were voided during recovery (empty if no mid-hand crash)

### Recovery Banner

When the client receives a welcome with `recovered: true`, show a brief info banner in the table area:

```
Recovered session session-1711745000000 (42 events, 5 hands)
```

Style: blue/teal background, auto-dismiss after 5 seconds. Positioned same as error toast but different color.

If `voidedHands.length > 0`, the banner also shows:

```
Recovered session session-... | Hand #3 voided (mid-hand crash recovery)
```

### Voided Hand in History

In the History tab's hand list, voided hands appear with a distinct marker:

```
Hand #1  Alice wins 10c
Hand #2  Bob wins $3.09
Hand #3  [VOIDED - mid-hand recovery]     ← red/muted text
Hand #4  Charlie wins 25c
```

Voided hands are clickable. Their detail view shows whatever events were logged before the crash plus the synthetic void HAND_END:

```
Hand #3 [VOIDED]
Stacks: Alice $20 | Bob $8 | Charlie $10

Alice posts SB 5c
Bob posts BB 10c
[HAND VOIDED - server recovered from mid-hand crash]
[Stacks restored to pre-hand values]
```

### Event Log Entry

On recovery, the event log panel shows:

```
RECOVERY  Session recovered from disk (42 events)
```

And if a hand was voided:

```
HAND_END  Hand #3 voided (mid-hand recovery)
```

---

## Session Info in Header

The header already shows table info. Add session context:

**Before**: `Poker Lab | 5c/10c | Hand #4 | PREFLOP | Played: 3`

**After**: `Poker Lab | 5c/10c | Hand #4 | PREFLOP | Played: 3 | session-...`

When recovered, briefly append `[recovered]`:

`Poker Lab | 5c/10c | Between hands | Played: 3 | session-... [recovered]`

This fades after 10 seconds.

---

## What This Does NOT Do

- Does not auto-reconnect to a different session if multiple exist
- Does not allow choosing which session to recover (server recovers the active one)
- Does not show server-side log output in the browser
- Does not replay the recovery process visually (state appears instantly, as in a normal welcome)

---

## Implementation Checklist

### Server (ws-server.js)

- [ ] Track `wasRecovered` boolean on startup
- [ ] Scan event log for voided hands (events with `void: true`)
- [ ] Include `recovered` and `voidedHands` in formatWelcome()

### Client (table.js)

- [ ] On welcome: check `recovered` flag, show recovery banner if true
- [ ] On welcome: store `voidedHands` array
- [ ] In hand list rendering: mark voided hands
- [ ] In hand detail rendering: show void message for voided hands
- [ ] In event log: add RECOVERY entry on recovered welcome

### Protocol (protocol.js)

- [ ] Update formatWelcome() signature to include recovered + voidedHands
