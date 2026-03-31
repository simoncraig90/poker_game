# Phase 8 Plan: Showdown + Side-Pot Closure

**Status: COMPLETE** — 15 suites / 692 checks green. See PHASE8_CLOSURE.md.

**Motivation**: The operator trust checkpoint documents exist and the recommended next branch is Option A (showdown). The checkpoint manual demos have not yet been executed — that gate remains open. Regardless, the game loop is incomplete: hands can only be won when all opponents fold. Without showdown and side pots, this is a folding simulator, not poker. Showdown implementation can proceed in parallel with checkpoint execution since it adds new capability without modifying existing verified paths.

---

## What Exists Today

| Component | State | Key File |
|-----------|-------|----------|
| Hand evaluation | **Missing** | — |
| Showdown sequencing | **Stub** — throws GAP-1 at `orchestrator.js:249` and `:294` | `src/engine/orchestrator.js` |
| Side pots | **Missing** — single `hand.pot` integer | `src/engine/orchestrator.js` |
| Settlement | Fold-only via `settleNoShowdown()` | `src/engine/settle.js` |
| POT_AWARD event | Exists, single pot | `src/engine/events.js` |
| HAND_SUMMARY event | Exists, `handRank: null`, `winCards: null` | `src/engine/events.js` |
| HAND_RESULT event | Exists, `potIndex: 0` only | `src/engine/events.js` |
| Reconstruct | Handles POT_AWARD with awards array | `src/api/reconstruct.js` |
| Client | Shows winner banner, no card reveal | `client/table.js` |

---

## What Must Change

### 1. Hand Evaluator (`src/engine/evaluate.js` — new file)

**Purpose**: Given 7 cards (2 hole + 5 board), find the best 5-card poker hand and return a comparable rank.

**Interface**:
```javascript
/**
 * @param {Array<{rank, suit}>} cards - exactly 7 cards
 * @returns {{ handRank: number, handName: string, bestFive: Card[], kickers: number[] }}
 *
 * handRank: integer, higher is better. Ties possible.
 *   9 = Royal Flush
 *   8 = Straight Flush
 *   7 = Four of a Kind
 *   6 = Full House
 *   5 = Flush
 *   4 = Straight
 *   3 = Three of a Kind
 *   2 = Two Pair
 *   1 = One Pair
 *   0 = High Card
 *
 * For tie-breaking: compare handRank, then kickers array element-by-element.
 */
function evaluateHand(cards) { ... }

/**
 * Compare two evaluated hands. Returns -1, 0, or 1.
 */
function compareHands(a, b) { ... }

/**
 * From N evaluated hands, return indices of winner(s). Ties return multiple indices.
 */
function findWinners(evaluatedHands) { ... }
```

**Algorithm**: Enumerate all C(7,5)=21 combinations of 5 cards. For each, classify hand type and rank. Keep the best. This is brute-force but correct and fast enough for 6-max (126 combos worst case for 6-way showdown).

**Hand classification rules** (standard poker):
- Royal Flush: A-K-Q-J-T same suit
- Straight Flush: 5 consecutive same suit (A-2-3-4-5 is lowest)
- Four of a Kind: 4 same rank + 1 kicker
- Full House: 3 same rank + 2 same rank (compare trips first, then pair)
- Flush: 5 same suit, not straight (compare high to low)
- Straight: 5 consecutive, not same suit (A-2-3-4-5 is lowest, A-high is highest)
- Three of a Kind: 3 same rank + 2 kickers
- Two Pair: 2+2+1 (compare high pair, low pair, kicker)
- One Pair: 2+3 kickers (compare pair rank, then kickers)
- High Card: 5 kickers (compare high to low)

**Tie-breaking**: Encode each hand as a comparable array: `[handType, ...kickers]` where kickers are rank values sorted per hand-type rules. Compare arrays element-by-element. Equal arrays = split pot.

**Edge cases**:
- Ace-low straight (A-2-3-4-5): ace counts as 1, hand ranks below 6-high straight
- Ace-high straight (A-K-Q-J-T): ace counts as 14
- Board plays: if the 5 best cards are all community cards, all remaining players tie
- Kicker relevance: with 3-of-a-kind using 2 board + 1 hole, kickers from hole matter

### 2. Side-Pot Calculation (`src/engine/pots.js` — new file)

**Purpose**: Given N players with varying `totalInvested` amounts, compute main pot and side pots with eligible player lists.

**Interface**:
```javascript
/**
 * @param {Array<{seat: number, totalInvested: number, folded: boolean}>} players
 * @returns {Array<{amount: number, eligible: number[]}>}
 *   Ordered: main pot first, then side pots in creation order.
 *   eligible: seat indices who can win this pot (non-folded only).
 */
function calculatePots(players) { ... }
```

**Algorithm**:
1. Collect all unique `totalInvested` values from non-folded players, sort ascending. These are the pot tier boundaries.
2. For each tier boundary `t`:
   - Count how many players (including folded) invested at least `t`
   - Pot amount for this tier = `(t - previousTier) * countOfPlayersInvestedAtLeastT`
   - Eligible = non-folded players who invested at least `t`
3. Sum of all pot tiers must equal sum of all `totalInvested` values (accounting check).

**Folded players contribute to pots but are not eligible to win.**

**Example — 3-way all-in**:
- Alice: invested 100 (all-in), not folded
- Bob: invested 300 (all-in), not folded
- Charlie: invested 500, not folded

Pots:
| Pot | Tier | Calculation | Amount | Eligible |
|-----|------|-------------|--------|----------|
| Main | 0–100 | 100 × 3 | 300 | Alice, Bob, Charlie |
| Side 1 | 100–300 | 200 × 2 | 400 | Bob, Charlie |
| Side 2 | 300–500 | 200 × 1 | 200 | Charlie (uncontested, auto-return) |

**Edge cases**:
- **Uncontested side pot**: Only 1 eligible player → auto-return, no showdown needed
- **Folded player with partial investment**: Contributes to pot tiers up to their invested amount, but never eligible
- **All same invested amount**: Single pot, all eligible
- **Odd chip**: When splitting a pot evenly among N winners and `amount % N !== 0`, the remainder chip(s) go to the first winner(s) clockwise from the button. This matters for accounting closure.

### 3. Showdown Settlement (`src/engine/settle.js` — extend)

**Add `settleShowdown()` alongside existing `settleNoShowdown()`.**

```javascript
/**
 * @param {string} sessionId
 * @param {string} handId
 * @param {object} table
 * @param {object} hand
 * @param {Array<{seat, rank, eligible}>} evaluatedPlayers - only non-folded players
 * @returns {Event[]} - POT_AWARD(s), HAND_SUMMARY, HAND_RESULT(s), HAND_END
 */
function settleShowdown(sessionId, handId, table, hand, evaluatedPlayers) { ... }
```

**Logic**:
1. Call `calculatePots()` with all in-hand players (folded and non-folded, with `totalInvested`).
2. For each pot:
   a. Filter `evaluatedPlayers` to those in `pot.eligible`.
   b. Call `findWinners()` on eligible evaluated hands.
   c. Split pot amount among winners. Apply odd-chip rule (first clockwise from button).
   d. Emit `POT_AWARD` with potIndex and awards array.
   e. Apply winnings to `seat.stack`.
3. Emit `HAND_SUMMARY` — populate `handRank` and `winCards` for the overall winner (best hand across all pots).
4. Emit `HAND_RESULT` — one per pot, showing each player's outcome.
5. Emit `HAND_END`.
6. Accounting check: `sum(awards across all pots) == sum(totalInvested across all players)`.

### 4. Orchestrator Changes (`src/engine/orchestrator.js`)

**Replace GAP-1 throws with showdown flow.**

#### `_nextStreet()` — RIVER case:
```javascript
case PHASE.RIVER:
  hand.phase = PHASE.SHOWDOWN;
  hand.actionSeat = null;
  this._showdown();
  return; // was: throw new Error("SHOWDOWN not implemented")
```

#### `_runOutBoard()` — after dealing all streets:
```javascript
// Replace throw with:
hand.phase = PHASE.SHOWDOWN;
hand.actionSeat = null;
this._showdown();
```

#### New `_showdown()` method:
```javascript
_showdown() {
  const hand = this.table.hand;
  const active = this._getActivePlayers(); // non-folded

  // Evaluate each active player's hand
  const evaluated = active.map(seat => ({
    seat: seat.seat,
    ...evaluateHand([...seat.holeCards, ...hand.board.map(parseCard)])
  }));

  hand.phase = PHASE.SETTLING;
  hand.showdown = true;

  // Settle with side pots
  const allInHand = Object.values(this.table.seats).filter(s => s.inHand);
  const settleEvents = settleShowdown(
    this.sessionId, hand.handId, this.table, hand, evaluated, allInHand
  );
  for (const e of settleEvents) {
    this.emit(e);
  }

  hand.phase = PHASE.COMPLETE;
  hand.rake = 0;

  // Clear per-hand state
  for (const seat of Object.values(this.table.seats)) {
    if (seat.inHand) resetHandState(seat);
  }

  // Accounting check
  const check = checkAccountingClosure(this.table, this.startStacks, hand.rake);
  if (!check.passed) {
    console.error("ACCOUNTING VIOLATION:", check.violations);
  }
}
```

### 5. Event Changes (`src/engine/events.js`)

**Minimal changes** — the event schema already supports showdown:

| Event | Change |
|-------|--------|
| `HAND_SUMMARY` | Populate `handRank` (string, e.g. "Full House, Aces over Kings") and `winCards` (array of 5 display strings) |
| `POT_AWARD` | Already supports `potIndex` and `awards[]` — no change needed |
| `HAND_RESULT` | Already supports `potIndex` and per-player results — emit one per pot |
| New: `SHOWDOWN_REVEAL` | **Add** — reveals each player's hole cards and evaluated hand at showdown |

**New event factory**:
```javascript
function showdownReveal(sessionId, handId, reveals) {
  // reveals: [{ seat, player, cards: ["As","Kh"], handRank: "Full House...", bestFive: [...] }]
  return { ...base(sessionId, handId, EVENT.SHOWDOWN_REVEAL), reveals };
}
```

**Add to types.js**: `SHOWDOWN_REVEAL: "SHOWDOWN_REVEAL"` in EVENT enum.

### 6. Reconstruct Changes (`src/api/reconstruct.js`)

Add handler for `SHOWDOWN_REVEAL`:
```javascript
case "SHOWDOWN_REVEAL":
  // Informational for replay — no state mutation needed
  // (POT_AWARD handles the stack changes)
  break;
```

Multiple `POT_AWARD` events already work — the existing handler applies each award to the seat stack. No change needed.

Multiple `HAND_RESULT` events: already handled as informational. No change.

### 7. Server Protocol Changes (`src/server/ws-server.js`, `src/server/protocol.js`)

**None required.** The server broadcasts all events from the engine. New event types (SHOWDOWN_REVEAL) will flow through the existing broadcast path. GET_STATE already returns `hand.showdown`, `hand.board`, etc.

### 8. Client Changes (`client/table.js`)

| Feature | Change |
|---------|--------|
| SHOWDOWN_REVEAL rendering | Show opponent hole cards on the table when showdown occurs |
| Multiple POT_AWARD banners | Show "Main pot: Alice wins 300" then "Side pot: Bob wins 400" |
| Hand rank display | Show winning hand name in result banner |
| History tab | Show hand rank and cards for showdown hands |
| Event log | Render SHOWDOWN_REVEAL events |

**Card reveal in seats**: When SHOWDOWN_REVEAL event arrives, update each revealed seat's displayed cards. Cards remain visible until HAND_END clears them.

---

## Edge Cases — Explicit List

| # | Case | Rule | Where Tested |
|---|------|------|-------------|
| E1 | 2-player showdown, clear winner | Best hand wins full pot | T1 |
| E2 | 2-player showdown, split pot (tied hands) | Each gets pot/2 | T2 |
| E3 | 3-way all-in, different stacks | Main pot + 1 side pot, each awarded to best eligible hand | T3 |
| E4 | 3-way all-in, 2 players tie for main pot | Main pot split 2 ways, side pot to best eligible | T4 |
| E5 | All-in with fold — 3 players, 1 folds, 2 all-in | Folded player contributes to pot but can't win | T5 |
| E6 | Uncontested side pot (1 eligible player) | Auto-return, no showdown needed for that pot | T6 |
| E7 | Odd chip on split (pot=101, 2 winners) | First clockwise from button gets extra chip | T7 |
| E8 | Board plays (5 community cards are the best hand) | All active players tie | T8 |
| E9 | Ace-low straight (A-2-3-4-5) vs higher straight | A-low loses to any higher straight | T9 |
| E10 | Multi-way all-in, 4+ players, 3 different stack sizes | Multiple side pots, each resolved independently | T10 |
| E11 | All-in for less than BB preflop | Short stack creates main pot, play continues in side pot | T11 |
| E12 | Run-out board (all-in preflop, board dealt automatically) | Showdown after auto-deal, no betting rounds between | T12 |
| E13 | Showdown after river betting (not all-in) | Normal showdown with card reveal | T13 |

---

## State Transitions

### Current (incomplete):
```
PREFLOP → FLOP → TURN → RIVER → ERROR (GAP-1)
                                   ↘ (fold-out) → SETTLING → COMPLETE
```

### After Phase 8:
```
PREFLOP → FLOP → TURN → RIVER → SHOWDOWN → SETTLING → COMPLETE
                                   ↗ (all-in run-out)
         (fold-out at any street) → SETTLING → COMPLETE
```

Event sequence for showdown hand:
```
HAND_START
BLIND_POST (×2)
HERO_CARDS (×N)
PLAYER_ACTION (×...)
DEAL_COMMUNITY (FLOP)
PLAYER_ACTION (×...)
DEAL_COMMUNITY (TURN)
PLAYER_ACTION (×...)
DEAL_COMMUNITY (RIVER)
PLAYER_ACTION (×...)
[BET_RETURN if applicable]
SHOWDOWN_REVEAL          ← NEW
POT_AWARD (×K, one per pot)  ← may be multiple now
HAND_SUMMARY             ← handRank + winCards populated
HAND_RESULT (×K, one per pot) ← may be multiple now
HAND_END
```

---

## Test Plan

### Unit Tests: Hand Evaluator (`test/evaluate.test.js`)

| Test | Input | Expected |
|------|-------|----------|
| T-E1 | Royal flush (7 cards) | handRank=9, correct best 5 |
| T-E2 | Straight flush | handRank=8 |
| T-E3 | Four of a kind | handRank=7 |
| T-E4 | Full house | handRank=6 |
| T-E5 | Flush | handRank=5 |
| T-E6 | Straight | handRank=4 |
| T-E7 | Three of a kind | handRank=3 |
| T-E8 | Two pair | handRank=2 |
| T-E9 | One pair | handRank=1 |
| T-E10 | High card | handRank=0 |
| T-E11 | Ace-low straight | handRank=4, kickers=[5] (5-high) |
| T-E12 | Ace-high straight | handRank=4, kickers=[14] (A-high) |
| T-E13 | Compare: flush vs straight | flush wins |
| T-E14 | Compare: pair of aces vs pair of kings | aces wins |
| T-E15 | Compare: same two pair, different kicker | better kicker wins |
| T-E16 | Compare: identical hands (board plays) | tie (returns 0) |
| T-E17 | Full house vs full house (different trips) | higher trips wins |
| T-E18 | Flush vs flush (kicker comparison) | higher kicker wins |

### Unit Tests: Side Pots (`test/pots.test.js`)

| Test | Setup | Expected Pots |
|------|-------|---------------|
| T-P1 | 2 players, equal stacks, no fold | 1 pot, both eligible |
| T-P2 | 3 players, one short all-in | Main (3-way) + Side (2-way) |
| T-P3 | 3 players, one folds, two contest | 1 pot, folder contributes but ineligible |
| T-P4 | 4 players, 3 different all-in amounts | Main + 2 side pots |
| T-P5 | Uncontested side pot | Side pot returned to sole eligible player |
| T-P6 | All equal investment | 1 pot |
| T-P7 | Accounting: sum(pots) == sum(invested) | Always true |

### Integration Tests: Showdown Hands (`test/showdown.test.js`)

Using deterministic RNG to control card deals:

| Test | Scenario | Checks |
|------|----------|--------|
| T1 | 2-player, play to river, clear winner | Correct hand wins, stacks correct, accounting closed |
| T2 | 2-player, identical best-5 (board plays) | Pot split evenly |
| T3 | 3-player, 1 short all-in preflop | 2 pots, correct awards per eligibility |
| T4 | 3-player all-in, 2 tie for main | Main split, side to best |
| T5 | 3-player, 1 folds preflop, 2 showdown | Folder contributes, 2-way showdown |
| T6 | Uncontested side pot | Returned without showdown |
| T7 | Odd chip split (pot=101, 2 winners) | Button-relative gets extra chip, accounting closed |
| T8 | Board plays (5 community > all hole cards) | All remaining split |
| T9 | Ace-low vs ace-high straight | Ace-high wins |
| T10 | 4-player, 3 stacks, multi-side-pot | 3 pots awarded correctly |
| T11 | Short stack BB all-in for less | Main pot capped, side pot continues |
| T12 | All-in preflop run-out | Board auto-dealt, showdown resolves |
| T13 | Normal river showdown (not all-in) | Betting + reveal + award |

### Conformance Tests: Event Log (`test/showdown-conformance.test.js`)

| Test | What It Proves |
|------|----------------|
| TC1 | `reconstructState(events)` matches live state after showdown hand |
| TC2 | `reconstructState(events)` matches after multi-pot showdown |
| TC3 | SHOWDOWN_REVEAL event contains correct cards for all active players |
| TC4 | Multiple POT_AWARD events reconstruct correctly |
| TC5 | Accounting closure holds across showdown (startStacks == endStacks) |

### Recovery Tests: Showdown + Persistence

| Test | What It Proves |
|------|----------------|
| TR1 | Server restart after completed showdown hand: stacks correct |
| TR2 | Mid-showdown crash (board dealt, before settlement): hand voided, stacks restored |

---

## Implementation Order (Smallest Safe First Slice)

### Slice 1: Hand Evaluator (isolated, no integration)

**Files**: `src/engine/evaluate.js` (new), `test/evaluate.test.js` (new)

**What**: Pure function. 7 cards in, hand rank + comparison out. No dependencies on the rest of the engine. Fully testable in isolation.

**Done when**: All T-E1 through T-E18 pass.

**Why first**: Everything downstream depends on correct hand evaluation. Must be bulletproof before integrating.

### Slice 2: Side-Pot Calculator (isolated, no integration)

**Files**: `src/engine/pots.js` (new), `test/pots.test.js` (new)

**What**: Pure function. Player investment data in, pot structure out. No engine coupling.

**Done when**: All T-P1 through T-P7 pass. Accounting invariant proven.

**Why second**: Settlement needs pots. Independent of evaluator, so could be developed in parallel with Slice 1.

### Slice 3: Showdown Settlement + Orchestrator Integration

**Files**: `src/engine/settle.js` (extend), `src/engine/orchestrator.js` (modify), `src/engine/events.js` (add SHOWDOWN_REVEAL), `src/engine/types.js` (add event type)

**What**: Wire evaluator + pots into the orchestrator. Replace GAP-1 throws. Add `settleShowdown()`. Add SHOWDOWN_REVEAL event.

**Done when**: T1–T13 integration tests pass. Accounting closure verified on every test.

### Slice 4: Conformance + Reconstruct

**Files**: `src/api/reconstruct.js` (extend), `test/showdown-conformance.test.js` (new)

**What**: Ensure event log faithfully represents showdown outcomes. Reconstruct handles new event types. No hidden state.

**Done when**: TC1–TC5 pass.

### Slice 5: Recovery

**Files**: `test/showdown-recovery.test.js` (new), possibly `src/api/session.js` (if recovery needs showdown-aware void logic)

**What**: Verify persistence + recovery with showdown hands. Mid-showdown crash should void like any other incomplete hand.

**Done when**: TR1–TR2 pass.

### Slice 6: Client Rendering

**Files**: `client/table.js`, `client/index.html` (if layout changes needed)

**What**: Show opponent cards at showdown. Display hand ranks. Handle multiple pot awards in result banner. Update history tab.

**Done when**: Manual demo shows cards revealed at showdown, correct winner displayed, multiple pots shown.

---

## What This Phase Does NOT Include

- Rake calculation (tracked as 0, no change)
- Muck/show choice (all hands revealed at showdown — standard for research/study)
- Showdown order (first-to-show rules) — all revealed simultaneously
- Ante (blinds only)
- Straddle
- Forced all-in disconnection
- Timer / shot clock

These are either unnecessary for correctness or belong in later phases.

---

## Accounting Invariant (Restated)

After every hand, including showdown:

```
sum(startStacks) == sum(endStacks) + rake
```

Where `rake = 0` currently. This is checked by `checkAccountingClosure()` after every hand. If it ever fails, the hand is broken.

For side pots specifically:
```
sum(all pot amounts) == sum(all totalInvested)
sum(all awards across all pots) == sum(all pot amounts)
```

Both checked in `settleShowdown()` before emitting events. Assertion failure = bug, not edge case.
