# End-to-End Browser Test Plan

Automated test proving the full browser play path works: server start, client connect, multi-hand session, hand history access, accounting closure.

---

## Approach

Use the existing WS test infrastructure (Node `ws` package as client) to simulate what the browser does. No actual browser automation — the browser client and the test client use the same WebSocket protocol.

This is a "headless browser" test: it exercises the same command/response/broadcast path the real browser uses, without rendering.

---

## Test: Full Session

### Setup
1. Start WS server on random port
2. Connect test client, receive welcome

### Phase 1: Seat Players
1. SEAT_PLAYER seat 0, "Alice", 1000
2. SEAT_PLAYER seat 1, "Bob", 800
3. SEAT_PLAYER seat 3, "Charlie", 600
4. GET_STATE: verify 3 players seated

### Phase 2: Play 5 Consecutive Hands
For each hand:
1. START_HAND → verify HAND_START in response
2. GET_STATE → record starting stacks, action seat
3. Loop: fold each action seat until hand settles
4. Verify HAND_END in final response events
5. GET_STATE → record ending stacks
6. Assert: sum(ending stacks) == sum(starting stacks) (rake=0)

### Phase 3: Play 1 Multi-Street Hand
1. START_HAND
2. First player: RAISE to 30
3. Second player: CALL
4. Third player: FOLD
5. Verify DEAL_COMMUNITY(FLOP) in response events
6. GET_STATE: verify board has 3 cards, phase is FLOP
7. First active player: BET 20
8. Other player: FOLD
9. Verify settlement events (POT_AWARD, HAND_SUMMARY, HAND_RESULT, HAND_END)
10. Verify winner stack increased by pot amount

### Phase 4: Hand History
1. GET_HAND_LIST → verify 6 completed hands
2. For each hand: GET_HAND_EVENTS → verify event count > 0
3. Verify each hand has HAND_START and HAND_END
4. Verify each HAND_SUMMARY has showdown=false (all no-showdown)

### Phase 5: Event Log Completeness
1. GET_EVENT_LOG → full log
2. Verify: TABLE_SNAPSHOT count = 1
3. Verify: HAND_START count = HAND_END count = 6
4. Verify: SEAT_PLAYER count = 3
5. Reconstruct state from event log
6. Compare against GET_STATE
7. Assert match (conformance)

### Teardown
Close client, stop server.

---

## Assertions

| ID | Assertion | Source |
|----|-----------|--------|
| E1 | Welcome message received on connect | welcome.welcome === true |
| E2 | 3 players seated correctly | state.seats[0,1,3].status === OCCUPIED |
| E3 | 6 hands complete | state.handsPlayed === 6 |
| E4 | Stack accounting per hand | sum(stacks) constant within each hand |
| E5 | Multi-street hand reaches flop | board.length === 3 after deal |
| E6 | Settlement events present | POT_AWARD + HAND_END in events |
| E7 | Hand list has 6 entries | hands.length === 6 |
| E8 | Each hand's events are non-empty | events.length > 0 for each |
| E9 | HAND_START/END paired | counts match |
| E10 | Event log reconstructs to live state | conformance match |

---

## Implementation

Single test file: `test/e2e-session.test.js`

Uses the same `sendCmd()` and `connect()` helpers from `ws-conformance.test.js`. Runs in ~2 seconds (no real I/O beyond localhost WS).

---

## What This Does NOT Test

- Actual browser rendering (DOM assertions)
- Visual correctness of card display
- Keyboard shortcuts
- Error toast display
- History tab UI
- Multiple concurrent browser clients (Phase 3 WS test already covers broadcast)
