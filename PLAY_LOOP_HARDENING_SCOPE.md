# Play Loop Hardening Scope

Exact changes to make repeated local play sessions stable and clear.

---

## Server Changes

### New Commands

| Command | Payload | Returns |
|---------|---------|---------|
| `GET_HAND_EVENTS` | `{ handId }` | `{ ok, events: [...events for that hand] }` |
| `GET_HAND_LIST` | — | `{ ok, hands: [{ handId, winner, pot, playerCount }] }` |

These use existing `session.getHandEvents(handId)` and `session.getEventLog()` — no engine changes needed, just command routing.

### Hand List Derivation

Scan the event log for HAND_SUMMARY events. Each one produces a hand list entry:
```json
{ "handId": "3", "winner": "Alice", "pot": 65, "playerCount": 3 }
```

---

## Client Changes

### Hand Result Banner

After HAND_RESULT arrives, show a brief summary overlay in the board area:
```
Alice wins $3.09
Takes down main pot.
```
Display for 3 seconds or until next HAND_START, whichever comes first. No modal — just text in the board-area center.

### Board/Bet Clearing

On HAND_END:
- Clear board cards (show 5 empty slots)
- Clear all seat bets
- Keep player names/stacks visible
- Show result banner

On HAND_START:
- Clear result banner
- Reset seat states (folded, allIn badges)

### Hand Number in Header

Show current hand ID: `Hand #3 | PREFLOP` or `Between hands` when idle.

### Error Toast

When a command returns `ok: false`, show a small red toast at the top of the table area:
```
Error: FOLD not legal. Legal: CHECK, BET
```
Auto-dismiss after 3 seconds. Implemented as a positioned div, not alert().

### Keyboard Shortcuts

| Key | Action | Condition |
|-----|--------|-----------|
| `f` | Fold | Legal |
| `c` | Call | Legal |
| `x` | Check | Legal |
| `d` or `Enter` | Deal next hand | No active hand, 2+ players |
| `1`-`9` | Set bet amount to N * BB | When bet/raise legal |

Only active when no input element is focused (don't intercept typing in bet input).

---

## What Does NOT Change

- Engine modules (src/engine/*) — untouched
- Session/dispatch/reconstruct (src/api/*) — only add new command routing
- Wire protocol format — unchanged, just new command types
- Event log format — unchanged
- Replay consumer — unchanged
