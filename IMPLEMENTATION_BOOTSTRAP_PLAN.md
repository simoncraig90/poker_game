# Implementation Bootstrap Plan

## Objective

Build a functional NL Hold'em cash-game backend that handles the common path — the mainline hand lifecycle from seat to settlement — using the validated normalized event model as the authoritative specification.

This is not a full poker platform. It is the deterministic game-state engine that processes actions and produces events identical in structure to those already proven by the replay consumer.

---

## Assumptions

1. The `LIVE_TABLE_REDUCER_SPEC.md` is the implementation contract. Every state transition defined there is implementable now on the common path.
2. The normalized event schema is the wire format between the game engine and any consumer (UI, logger, replay).
3. Stack accounting is closed (validated via replay). The engine must maintain INV-1 through INV-8.
4. Rake exists but is deferred. The engine can apply 0% rake initially and add a configurable rake function later.
5. The engine does not need to parse PokerStars wire protocol. It produces its own events in the normalized schema.
6. Hand evaluation (who wins at showdown) is a separate module. The common-path engine does not need it because all common-path hands end with everyone folding to one remaining player.

---

## What Is Safe to Implement Now

These capabilities are fully specified and replay-validated:

| Capability | Spec Reference | Replay Status |
|-----------|---------------|---------------|
| Table creation with config | Reducer 3.1 | Validated |
| Seat management (occupy, empty) | SeatState shape | Validated via HAND_START |
| Hand start (deal button, reset state) | Reducer 3.2 | Validated |
| Blind posting (SB, BB) | Reducer 3.3 | Validated |
| Hole card dealing (per-seat, hidden) | Reducer 3.4 | Validated (hero path) |
| Voluntary actions (FOLD, CALL, BET, RAISE) | Reducer 3.5 | Validated |
| Inferred fold (skip/timeout) | Reducer 3.5 (inferred) | Validated |
| BET_RETURN (uncalled bet excess) | Reducer 3.6 | Validated |
| Street transitions (FLOP, TURN, RIVER) | Reducer 3.7 | Validated |
| Pot tracking (action-based, not server-push) | Reducer 3.8 | Validated |
| No-showdown settlement (last player wins) | Reducer 3.9–3.12 | Validated |
| Hand end with closed accounting | Reducer 3.12, INV-5 | Validated |
| Event emission (all 13 normalized types) | Schema 2.1–2.13 | Validated |

---

## What Is Explicitly Deferred

| Capability | Gap | Reason |
|-----------|-----|--------|
| Showdown hand evaluation | GAP-1 | No showdown data captured. Needs hand evaluator + card reveal events. |
| CHECK action (first-to-act, no bet) | GAP-2 | Unobserved in wire protocol. Believed to be `roundId=11, amount=0` but untested. |
| Side pot calculation | GAP-3 | Multi-way all-in not captured. POT_AWARD with multiple potIndex untested. |
| Player join/leave mid-table | GAP-4 | Join observed but not as discrete event. Leave unobserved. |
| Ante / straddle / missed blind | GAP-5 | Not applicable to standard 6-max ring. |
| Disconnect / timeout UX | GAP-6 | Auto-fold is indistinguishable from voluntary fold. Cosmetic only. |
| Rake engine | — | Validated as observable but not as a configurable rule. Defer to post-MVP. |
| Multi-table support | — | Single table first. |
| Persistence / database | — | Event log to file first. DB schema comes later. |
| Client protocol / WebSocket server | — | Build the state engine first. Wire protocol is a separate layer. |

---

## Phased Work Order

### Phase 1: Game State Engine (the reducer)

Build the deterministic state machine from `LIVE_TABLE_REDUCER_SPEC.md`.

```
Input:  Command (player action request)
Output: Event  (normalized event, emitted to event log)
State:  TableState (mutated in place per reducer rules)
```

Deliverables:
- `src/engine/table.js` — TableState, SeatState, HandState
- `src/engine/reducer.js` — event handlers for all 13 event types
- `src/engine/actions.js` — action validation (is this action legal right now?)
- `src/engine/betting.js` — min-raise, call amount, pot calculations
- `src/engine/dealer.js` — button rotation, blind assignment, deal sequence
- Tests: replay the 3 validated hands through the engine and assert identical event output

### Phase 2: Hand Orchestrator

Build the hand lifecycle driver that sequences events in the correct order.

```
1. HAND_START
2. BLIND_POST × 2
3. Deal hole cards (HERO_CARDS per seat, hidden)
4. Preflop action loop
5. Collect / BET_RETURN
6. DEAL_COMMUNITY (FLOP) → action loop → collect/return
7. DEAL_COMMUNITY (TURN) → action loop → collect/return
8. DEAL_COMMUNITY (RIVER) → action loop → collect/return
9. Settlement: POT_AWARD → HAND_SUMMARY → HAND_RESULT → HAND_END
```

The orchestrator calls the reducer for each step and emits events. It decides whose turn it is, when the street ends, and when to settle.

Deliverables:
- `src/engine/orchestrator.js` — hand lifecycle state machine
- `src/engine/action-loop.js` — betting round management (whose turn, is round over?)
- Tests: full hand simulation with scripted inputs

### Phase 3: Event Log + Replay Verification

Wire the engine to produce `normalized-hand-events.jsonl` and verify it replays identically through the existing `replay-normalized-hand.js`.

Deliverables:
- `src/engine/event-log.js` — append-only JSONL writer
- Regression test: run engine, pipe output to replay consumer, assert Stack check PASS

### Phase 4: Command Interface

Expose a synchronous API for external consumers (CLI, future WebSocket server).

```javascript
table.sitDown(seat, playerName, buyIn)
table.postBlinds()
table.act(seat, action, amount?)  // FOLD, CHECK, CALL, BET, RAISE
table.getState()                   // returns current TableState
table.getEventsForHand(handId)     // returns normalized events
```

Deliverables:
- `src/api/table-api.js`
- CLI driver for manual play testing

---

## Success Criteria

### Phase 1 Complete: ✓
- [x] All 8 reducer invariants (INV-1 through INV-8) are enforced in code
- [x] The 3 validated hands can be replayed through the engine with identical event output

### Phase 2 Complete: ✓
- [x] A full hand can be played from start to settlement via scripted actions
- [x] No-showdown settlement produces correct POT_AWARD, HAND_SUMMARY, HAND_RESULT

### Phase 3 Complete: ✓
- [x] Engine output passes `replay-normalized-hand.js` stack check for all test hands
- [x] Event log is append-only JSONL matching the normalized schema

### Phase 4 Complete: ✓
- [x] Two humans can play a hand via CLI (or scripted inputs)
- [x] The event log from a played hand replays correctly

### Overall Bootstrap Complete: ✓
- [x] A 6-max table can run continuous hands (deal, play, settle, repeat)
- [x] Every hand's event log passes replay verification
- [x] No showdown required (all hands end by fold)

### Post-Bootstrap (Phases 5-7) Also Complete: ✓
- [x] Phase 5: Play loop hardening, keyboard shortcuts, hand history, E2E test (38/38)
- [x] Phase 6: Persistence, recovery, session continuity (52/52 recovery tests)
- [x] Phase 7: Session browser, recovery UX, archive flow (22/22)
