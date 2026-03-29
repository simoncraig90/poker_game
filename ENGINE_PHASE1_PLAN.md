# Engine Phase 1 Plan

Build the deterministic common-path game engine from the validated reducer spec.

---

## Objective

A working NL Hold'em cash-game state engine that:
1. Accepts commands (sit down, post blinds, fold/call/bet/raise)
2. Maintains deterministic state per LIVE_TABLE_REDUCER_SPEC.md
3. Emits normalized events to an append-only log
4. Produces event streams that replay correctly through the existing replay consumer

---

## Module Layout

```
src/
  engine/
    table.js          TableState constructor + seat management (sit/leave/buy-in)
    hand.js           HandState constructor + per-hand state reset
    deck.js           Shuffled 52-card deck, deal N cards
    dealer.js         Button rotation, blind seat assignment, deal sequence
    betting.js        Action validation, min-raise calc, round completion check
    round.js          Single betting round state machine (who acts, is round over)
    orchestrator.js   Hand lifecycle: blinds → deal → rounds → settle
    settle.js         BET_RETURN, POT_AWARD, HAND_SUMMARY, HAND_RESULT, HAND_END
    events.js         Event factory (builds normalized event objects)
    event-log.js      Append-only JSONL writer
    invariants.js     Runtime invariant checks (INV-1 through INV-8)
    types.js          Shared constants: phases, actions, suits, ranks

  index.js            Public API: createTable, sitDown, startHand, act, getState

test/
  replay-regression.test.js   Feed validated hands through engine, compare output
  table.test.js               Seat management, buy-in rules
  betting.test.js             Action validation, min-raise, round completion
  hand-lifecycle.test.js      Full hand from start to settle
  invariants.test.js          Invariant violation detection
  event-log.test.js           JSONL write + replay round-trip
```

---

## Implementation Order

Each task is a single deliverable. Tasks depend only on completed predecessors.

### Task 1: types.js — Constants and Enums

No dependencies.

```javascript
// Phases
PREFLOP, FLOP, TURN, RIVER, SHOWDOWN, SETTLING, COMPLETE

// Seat status
EMPTY, OCCUPIED, SITTING_OUT

// Action types
FOLD, CHECK, CALL, BET, RAISE, BLIND_SB, BLIND_BB

// Suits / Ranks
SUITS: { 1: 'c', 2: 'd', 3: 'h', 4: 's' }
RANKS: { 2:'2', ..., 10:'T', 11:'J', 12:'Q', 13:'K', 14:'A' }

// Event types
EVENT_TYPES: TABLE_SNAPSHOT, HAND_START, BLIND_POST, HERO_CARDS,
             PLAYER_ACTION, BET_RETURN, DEAL_COMMUNITY, POT_UPDATE,
             POT_AWARD, HAND_SUMMARY, HAND_RESULT, HAND_END
```

### Task 2: deck.js — Card Deck

Depends on: types.js

- `createDeck()` → shuffled array of 52 Card objects `{ rank, suit, display }`
- `dealCards(deck, n)` → removes and returns top N cards
- Fisher-Yates shuffle, seeded optionally for deterministic tests

### Task 3: events.js — Event Factory

Depends on: types.js

One factory function per event type. Each returns a plain object matching the normalized schema:

```javascript
createHandStart({ handId, tableId, tableName, button, sb, bb, players }) → event
createBlindPost({ seat, player, amount, blindType }) → event
createHeroCards({ seat, cards }) → event
createPlayerAction({ seat, player, action, totalBet, delta, street, inferred }) → event
createBetReturn({ seat, player, amount }) → event
createDealCommunity({ street, newCards, board }) → event
createPotAward({ potIndex, awards }) → event
createHandSummary({ winSeat, winPlayer, showdown, totalPot, board }) → event
createHandResult({ potIndex, results }) → event
createHandEnd({ tableId }) → event
```

Every event gets `sessionId`, `handId`, `seq` (auto-incrementing), `type`, and `_source: { origin: "engine", ts }`.

### Task 4: event-log.js — Append-Only Writer

Depends on: events.js

```javascript
class EventLog {
  constructor(filePath)
  append(event)         // JSON.stringify + newline + fs.appendFileSync
  getEvents()           // read back all events (for testing)
  getHandEvents(handId) // filter by handId
}
```

### Task 5: table.js — Table State

Depends on: types.js, events.js

```javascript
class Table {
  constructor({ tableId, tableName, maxSeats, sb, bb, minBuyIn, maxBuyIn })

  // Seat management
  sitDown(seatIndex, playerName, buyIn, country)  // → TABLE_SNAPSHOT or seat event
  leave(seatIndex)
  getOccupiedSeats()    // → SeatState[]
  getSeat(index)        // → SeatState

  // State access
  getState()            // → full TableState snapshot
}
```

Enforces: buy-in min/max, seat must be EMPTY, between-hands only.

### Task 6: hand.js — Hand State

Depends on: types.js

```javascript
class Hand {
  constructor({ handId, button, sbSeat, bbSeat })

  // State
  phase, board, pot, rake, actions, winners, resultText
  showdown, handRank, winCards

  // Helpers
  addAction(action)
  setPhase(phase)
  addBoardCards(cards)
}
```

Pure data container with helpers. No game logic.

### Task 7: dealer.js — Button + Blind Assignment

Depends on: table.js

```javascript
nextButton(currentButton, seats, maxSeats)     // → new button seat index
assignBlinds(button, seats, maxSeats)           // → { sbSeat, bbSeat }
```

Clockwise rotation, skip empty seats. Heads-up special case (button = SB).

### Task 8: betting.js — Action Validation

Depends on: types.js, table.js, hand.js

```javascript
validateAction(seat, action, amount, tableState, handState) → { valid, error? }
getLegalActions(seat, tableState, handState) → { canCheck, canCall, canBet, canRaise, callAmount, minRaise, maxRaise }
```

Implements the action validation table from BACKEND_COMMON_PATH_SCOPE.md.

### Task 9: round.js — Betting Round Manager

Depends on: betting.js, types.js

```javascript
class BettingRound {
  constructor(seats, phase, currentBet)

  getNextToAct()           // → seat index or null (round over)
  applyAction(seat, action, amount) // → mutates round state
  isComplete()             // → bool
  getUncalledReturn()      // → { seat, amount } or null
}
```

Tracks: who has acted, current bet level, last aggressor. Determines round completion per the 3 rules in the scope doc.

### Task 10: settle.js — Settlement

Depends on: types.js, events.js, hand.js

```javascript
settleNoShowdown(hand, seats, lastPlayerSeat) → Event[]
```

Produces: BET_RETURN (if applicable), POT_AWARD, HAND_SUMMARY, HAND_RESULT, HAND_END. Computes rake (0 for now).

### Task 11: invariants.js — Runtime Checks

Depends on: types.js, table.js, hand.js

```javascript
checkInvariants(tableState) → { passed: bool, violations: string[] }
```

Checks INV-1 through INV-8 from the reducer spec. Called after every state transition in debug mode.

### Task 12: orchestrator.js — Hand Lifecycle

Depends on: ALL of the above

```javascript
class HandOrchestrator {
  constructor(table, eventLog, deck?)

  startHand()                    // → HAND_START, BLIND_POST × 2, deal cards
  act(seat, action, amount?)     // → PLAYER_ACTION + state mutation
                                 //   may trigger street transition, BET_RETURN, settlement
  getActionSeat()                // → whose turn, or null
  isHandComplete()               // → bool
}
```

This is the top-level driver. It calls dealer, round, betting, settle internally. It emits events to the event log. External callers only see `startHand()` and `act()`.

### Task 13: index.js — Public API

Depends on: orchestrator.js, table.js, event-log.js

```javascript
function createTable(config) → { table, sitDown, leave, startHand, act, getState, getEvents }
```

Thin wrapper that wires together Table + HandOrchestrator + EventLog.

---

## State Reducer Entrypoints

The engine is command-driven. Each command maps to one or more events:

| Command | Events Produced | Reducer Section |
|---------|----------------|-----------------|
| `createTable(config)` | TABLE_SNAPSHOT | 3.1 |
| `sitDown(seat, name, buyIn)` | (state mutation, no event for now) | — |
| `leave(seat)` | (state mutation, no event for now) | — |
| `startHand()` | HAND_START, BLIND_POST ×2, HERO_CARDS ×N | 3.2, 3.3, 3.4 |
| `act(seat, FOLD)` | PLAYER_ACTION(FOLD) | 3.5 |
| `act(seat, CHECK)` | PLAYER_ACTION(CHECK) | 3.5 |
| `act(seat, CALL)` | PLAYER_ACTION(CALL) | 3.5 |
| `act(seat, BET, amount)` | PLAYER_ACTION(BET) | 3.5 |
| `act(seat, RAISE, amount)` | PLAYER_ACTION(RAISE) | 3.5 |
| (auto after round) | BET_RETURN | 3.6 |
| (auto after round) | DEAL_COMMUNITY | 3.7 |
| (auto on last fold) | POT_AWARD, HAND_SUMMARY, HAND_RESULT, HAND_END | 3.9–3.12 |

Events after `act()` may cascade: a fold can trigger BET_RETURN → settlement in a single call.

---

## Event Log Format

Append-only JSONL. One line per event.

```jsonl
{"sessionId":"engine-001","handId":"1","seq":0,"type":"HAND_START","tableId":"table-1","tableName":"Test Table","button":0,"sb":5,"bb":10,"players":{"0":{"name":"Alice","stack":1000,"country":"US"},"1":{"name":"Bob","stack":1000,"country":"GB"}},"_source":{"origin":"engine","ts":1711745000000}}
{"sessionId":"engine-001","handId":"1","seq":1,"type":"BLIND_POST","seat":0,"player":"Alice","amount":5,"blindType":"SB","_source":{"origin":"engine","ts":1711745000001}}
```

Properties:
- `sessionId`: set once per engine instance
- `handId`: incrementing string per hand
- `seq`: incrementing integer within each hand, resets to 0 at HAND_START
- `_source.origin`: always `"engine"` (distinguishes from captured events)
- `_source.ts`: wall-clock timestamp of event emission

The log is readable by `replay-normalized-hand.js` without modification.

---

## Test Plan

### T1: Replay Regression

Feed the 3 validated hands (260272188638, 260272208552, 260272235570) as scripted action sequences into the engine. Compare emitted events against the known-good normalized-hand-events.jsonl. Assert:
- Same event types in same order
- Same seat/player/amount values
- Stack check PASS from replay consumer

This is the primary correctness gate.

### T2: Action Validation

| Test Case | Input | Expected |
|-----------|-------|----------|
| Fold when not your turn | act(wrong_seat, FOLD) | Error: not your turn |
| Bet below BB | act(seat, BET, 5) when bb=10 | Error: below minimum |
| Raise below min-raise | act(seat, RAISE, 15) when min=20 | Error: below min-raise |
| Call when no bet | act(seat, CALL) when bet=0 | Error: nothing to call |
| Check when facing bet | act(seat, CHECK) when bet>0 | Error: must call/fold/raise |
| Valid fold | act(seat, FOLD) | PLAYER_ACTION(FOLD) |
| Valid call | act(seat, CALL) | PLAYER_ACTION(CALL, correct delta) |
| Valid raise | act(seat, RAISE, 40) when min=20 | PLAYER_ACTION(RAISE, delta=correct) |
| All-in call (short stack) | act(seat, CALL) when stack < callAmt | PLAYER_ACTION(CALL, delta=stack, allIn=true) |

### T3: Betting Round Completion

| Test Case | Expected |
|-----------|----------|
| All players check | Round complete, advance street |
| Bet → all fold | Round complete, settle (last player wins) |
| Bet → call → fold | Round continues until all acted |
| Raise → re-raise → call | Round complete when action returns to re-raiser |
| All fold to BB preflop | BB wins, settle |

### T4: Street Transitions

| Test Case | Expected |
|-----------|----------|
| Preflop complete, 2 remain | DEAL_COMMUNITY(FLOP, 3 cards), board.length=3 |
| Flop complete, 2 remain | DEAL_COMMUNITY(TURN, 1 card), board.length=4 |
| Turn complete, 2 remain | DEAL_COMMUNITY(RIVER, 1 card), board.length=5 |
| River complete, 2 remain | phase=SHOWDOWN, halt (deferred) |

### T5: Settlement

| Test Case | Expected |
|-----------|----------|
| All fold to one player preflop | POT_AWARD(winner, pot), HAND_SUMMARY(showdown=false) |
| Uncalled raise → settle | BET_RETURN(excess), then POT_AWARD |
| Pot accounting | sum(startStacks) == sum(endStacks) (rake=0) |

### T6: Invariants

After every `act()` call, run `checkInvariants()`. Assert no violations for:
- INV-1: stack >= 0
- INV-2: pot >= 0
- INV-3: activePlayers count matches
- INV-4: phase ordering
- INV-6: bet reset on street change
- INV-7: blinds before actions
- INV-8: fold idempotence

### T7: Event Log Round-Trip

1. Run a full hand through the engine
2. Write events to JSONL
3. Read JSONL back through `replay-normalized-hand.js`
4. Assert Stack check PASS
