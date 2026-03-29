# Backend Common Path Scope

Exact boundary of what can be built immediately on the validated protocol baseline, without waiting for remaining capture gaps.

---

## In Scope: Common Path

### Table Setup

| Feature | Specification | Source |
|---------|--------------|--------|
| Create table with config | `tableId, tableName, gameType=2, maxSeats=6, sb, bb, minBuyIn, maxBuyIn` | Reducer 3.1 |
| Seat a player | Set `status=OCCUPIED`, assign name/stack/country | SeatState shape |
| Empty a seat | Set `status=EMPTY`, clear player | SeatState shape |
| Button rotation | Advance button clockwise each hand, skip empty seats | Reducer 3.2 |

### Hand Lifecycle

| Phase | Events Emitted | Validated |
|-------|---------------|-----------|
| Start | `HAND_START` with player roster + starting stacks | Yes |
| Blinds | `BLIND_POST(SB)`, `BLIND_POST(BB)` | Yes |
| Deal | `HERO_CARDS` per seat (one pair each, hidden from others) | Yes (hero path) |
| Preflop actions | `PLAYER_ACTION` (FOLD, CALL, RAISE) | Yes |
| Collect + return | `BET_RETURN` if uncalled excess | Yes |
| Flop | `DEAL_COMMUNITY(FLOP, 3 cards)` | Yes |
| Flop actions | `PLAYER_ACTION` (FOLD, CALL, BET, RAISE) | Yes |
| Collect + return | `BET_RETURN` if uncalled excess | Yes |
| Turn | `DEAL_COMMUNITY(TURN, 1 card)` | Yes |
| Turn actions | Same as flop | Yes |
| Collect + return | Same | Yes |
| River | `DEAL_COMMUNITY(RIVER, 1 card)` | Yes |
| River actions | Same | Yes |
| Collect + return | Same | Yes |
| Settlement | `POT_AWARD`, `HAND_SUMMARY`, `HAND_RESULT`, `HAND_END` | Yes (no-showdown) |

### Seat State Transitions

```
EMPTY → OCCUPIED            (player sits down, buys in)
OCCUPIED → inHand=true      (HAND_START, player has chips)
inHand → folded=true        (FOLD action)
inHand → allIn=true         (action depletes stack to 0)
inHand → inHand=false       (HAND_END)
OCCUPIED → EMPTY            (player leaves — deferred detail, basic path only)
```

### Buy-In

| Rule | Value |
|------|-------|
| Minimum | `table.minBuyIn` (400c observed) |
| Maximum | `table.maxBuyIn` (1000c observed) |
| When | Between hands only (when `seat.inHand == false`) |
| Top-up | Between hands, up to maxBuyIn |

### Blind Assignment

| Position | Rule |
|----------|------|
| Button | Rotates clockwise. Skips empty and sitting-out seats. |
| SB | First occupied seat clockwise from button. |
| BB | First occupied seat clockwise from SB. |
| Heads-up | Button posts SB, other posts BB. (Standard heads-up rule, not yet observed.) |

### Action Validation

The engine MUST reject illegal actions. Legal actions per context:

| Context | Legal Actions |
|---------|--------------|
| Facing no bet (first to act or checked to) | CHECK, BET(amount >= bb), FOLD |
| Facing a bet | FOLD, CALL, RAISE(amount >= last raise increment) |
| All-in (stack = 0) | No action (skip) |
| Already folded | No action (skip) |
| Not in hand | No action |

Minimum raise rule: `raise amount >= previous raise increment`. If a player cannot meet the minimum raise, they can only call or fold (or go all-in for less).

### Betting Round Completion

A betting round ends when:
1. All active (non-folded, non-all-in) players have acted at least once since the last bet/raise.
2. All active players have put in equal amounts (or are all-in for less).
3. Only one player remains (all others folded).

### Street Transitions

| Trigger | Transition | Board |
|---------|-----------|-------|
| Preflop round complete, 2+ players remain | → FLOP | Deal 3 cards |
| Flop round complete, 2+ players remain | → TURN | Deal 1 card |
| Turn round complete, 2+ players remain | → RIVER | Deal 1 card |
| River round complete, 2+ players remain | → SHOWDOWN | (deferred) |
| Any round, 1 player remains | → SETTLING | No new cards |

### Pot Tracking

```
hand.pot = sum(all BLIND_POST amounts)
         + sum(all PLAYER_ACTION positive deltas)
         - sum(all BET_RETURN amounts)
```

The engine computes pot from actions. It does NOT receive POT_UPDATE events — it *produces* them. POT_UPDATE events are optional informational emissions for UI consumers.

### BET_RETURN

When a betting round ends and one player's bet exceeds all callers:
1. Compute `uncalled = winner's bet - highest other bet`.
2. Emit `BET_RETURN(seat, uncalled)`.
3. Apply: `seat.stack += uncalled`, `seat.totalInvested -= uncalled`, `hand.pot -= uncalled`.

### No-Showdown Settlement

When only one player remains:
1. `POT_AWARD(seat=lastPlayer, amount=hand.pot)` — credits the pot.
2. `hand.rake = hand.pot - sum(awards)` — 0 for now (rake deferred).
3. `HAND_SUMMARY(winSeat, showdown=false, totalPot=hand.pot, handRank=null, winCards=null)`.
4. `HAND_RESULT` — one entry per seated player. Winner: `"Takes down main pot."` Others: `"Loses main pot and mucks cards."`
5. `HAND_END` — phase = COMPLETE, clear per-hand state.

### Event Emission

The engine emits all 13 normalized event types to an append-only event log:

```
TABLE_SNAPSHOT  (once, on table creation)
HAND_START      (once per hand)
BLIND_POST      (2 per hand)
HERO_CARDS      (1 per seated player per hand, private to each)
PLAYER_ACTION   (N per hand, includes inferred folds)
BET_RETURN      (0–4 per hand)
DEAL_COMMUNITY  (0–3 per hand)
POT_UPDATE      (optional, for UI)
POT_AWARD       (1+ per hand)
HAND_SUMMARY    (1 per hand)
HAND_RESULT     (1 per hand)
HAND_END        (1 per hand)
```

### Event Ingestion Boundary

The engine is a **producer** of normalized events, not a consumer. It does not read from the PokerStars capture pipeline. The capture pipeline was used to derive the specification; the engine implements that specification independently.

Future integration: a live capture could be replayed against the engine for regression testing (assert identical state transitions).

### Reducer / State Boundary

The reducer is synchronous and single-threaded. State transitions are deterministic — given the same sequence of commands, the engine produces the same sequence of events and the same final state.

No async I/O, no timers, no external dependencies inside the reducer. Timers (action clock, time bank) are external concerns that produce timeout commands fed into the reducer.

### Persistence / Event-Log Boundary

The event log is an append-only JSONL file. One file per table session, or one file per hand — configurable.

The event log is the **source of truth** for hand history. Table state can be reconstructed by replaying events from the log (already proven by `replay-normalized-hand.js`).

Schema:
```jsonl
{"sessionId":"...","handId":"...","seq":0,"type":"HAND_START",...,"_source":{...}}
{"sessionId":"...","handId":"...","seq":1,"type":"BLIND_POST",...,"_source":{...}}
...
```

The `_source` field is optional in engine-produced events (no wire protocol frame to reference). The engine MAY set `_source: { origin: "engine", ts: Date.now() }`.

---

## Out of Scope: Deferred

### Showdown Reveal Logic

The engine can deal all 5 board cards and track actions to the river. When 2+ players remain after the river betting round, the engine MUST NOT attempt to evaluate hands or reveal cards. Instead, it should:
- Set `hand.phase = SHOWDOWN`
- Halt and await the hand evaluator module (future work)

**Stub**: Return an error or placeholder award. Do not fake a winner.

### Side-Pot Resolution

When a player is all-in and other players continue betting, side pots form. The engine tracks `seat.allIn = true` and `seat.totalInvested` but does NOT split pots.

**Stub**: In the all-in case, the engine awards the entire pot to the last remaining player (if others fold) or halts at showdown (if multiple remain).

### Full Join/Leave Edge Handling

The common path handles:
- Player sits down (between hands)
- Player leaves (seat becomes EMPTY between hands)

NOT handled:
- Join queue / waiting list
- "Wait for big blind" before being dealt in
- Sit-out / sit-in toggle
- Reconnection with state recovery

### Timeout UX

The engine accepts actions synchronously. Timer management is external. When a timer fires, the caller sends a FOLD command. The engine does not know or care whether it was a timeout.

### Ante / Straddle Variants

Only SB + BB blind structure. No antes, no straddles, no missed blind postings.

### Rake

Rake is 0 for this phase. The accounting identity `sum(startStacks) = sum(endStacks) + rake` holds with `rake = 0`.

A future `rakeFunction(potSize, playerCount) → rakeCents` can be injected into settlement without changing the reducer.

### Chat / Emotes / Throwables

Not part of the game state engine.

### Multi-Table

One table per engine instance. Multi-table is a deployment concern, not an engine concern.
