# Live Table Reducer Spec

Real-time backend state contract for a PokerStars-like NL Hold'em cash-game table, derived from the replay-validated normalized event model.

**Status**: Validated against 8 hands (1 session). Stack accounting and active-player tracking are correct. Showdown, side-pot, and player join/leave paths are specified but unvalidated.

---

## 1. State Shape

### 1.1 Table State (Canonical Backend)

```
TableState {
  // Identity
  tableId:       string          // "6R.{digits}.{hex}!"
  tableName:     string          // "Margo II"

  // Config (immutable per table)
  gameType:      int             // 2 = NL Hold'em
  maxSeats:      int             // 6
  sb:            int             // cents
  bb:            int             // cents
  minBuyIn:      int             // cents
  maxBuyIn:      int             // cents

  // Seats (indexed 0 to maxSeats-1)
  seats:         Map<int, SeatState>

  // Hand lifecycle
  hand:          HandState | null     // null between hands

  // Metadata
  handsPlayed:   int             // incrementing counter
}
```

### 1.2 Seat State

```
SeatState {
  seat:          int             // 0–5
  status:        EMPTY | OCCUPIED | SITTING_OUT

  // Player (present when status != EMPTY)
  player: {
    name:        string
    country:     string          // ISO 3166-1 alpha-2
    avatarId:    int | null
  } | null

  // Chips
  stack:         int             // cents, current stack behind the line

  // Per-hand state (reset at HAND_START)
  inHand:        bool            // participating in current hand
  folded:        bool            // true after FOLD action
  allIn:         bool            // true when stack reaches 0 via action
  bet:           int             // current-street bet amount (cents)
  totalInvested: int             // cumulative chips put in across all streets
  holeCards:     Card[2] | null  // only visible to the card holder (or at showdown)
}
```

### 1.3 Hand State

```
HandState {
  handId:        string          // globally unique numeric string
  button:        int             // dealer seat index
  sbSeat:        int | null      // set on first BLIND_POST with blindType=SB
  bbSeat:        int | null      // set on first BLIND_POST with blindType=BB

  // Street
  phase:         PREFLOP | FLOP | TURN | RIVER | SHOWDOWN | SETTLING | COMPLETE
  board:         Card[]          // 0, 3, 4, or 5 cards

  // Pot
  pot:           int             // total committed chips minus returns (cents)
  rake:          int             // platform fee, derived at settlement

  // Action tracking
  actions:       Action[]        // ordered list of all actions this hand
  actionSeat:    int | null      // whose turn it is (null during deal/settle)

  // Settlement (populated during SETTLING phase)
  winners:       Award[]
  resultText:    ResultEntry[]
  showdown:      bool
  handRank:      string | null
  winCards:      string | null
}
```

### 1.4 Supporting Types

```
Card {
  rank:          int             // 2–14 (14=A)
  suit:          int             // 1=c, 2=d, 3=h, 4=s
  display:       string          // "Ah", "Tc", "2d", etc.
}

Action {
  seat:          int
  type:          BLIND_SB | BLIND_BB | FOLD | CHECK | CALL | BET | RAISE
  amount:        int             // total bet this round (cents)
  delta:         int             // chips added by this action (cents)
  street:        string          // which street this occurred on
  inferred:      bool            // true if derived from ROUND_TRANSITION, not explicit ACTION
}

Award {
  seat:          int
  amount:        int             // cents awarded from pot
  potIndex:      int             // 0=main, 1+=side pots
}

ResultEntry {
  seat:          int
  won:           bool
  amount:        int
  text:          string          // "Takes down main pot.", "Loses main pot and mucks cards.", etc.
}
```

---

## 2. UI-Derived Convenience State

These values are computable from canonical state. They exist to simplify rendering, not to carry authoritative data. A reducer MAY maintain them for performance, but they MUST NOT be the source of truth.

```
UIDerived {
  // Active player count (non-folded, non-empty, inHand)
  activePlayers:       int

  // Per-seat display strings
  seatLabel[seat]:     string       // "Bandifull ($20.05)" or "Empty"

  // Pot display
  potDisplay:          string       // "$3.09" (net of rake)
  totalPotDisplay:     string       // "$3.25" (gross, including rake)

  // Street label
  streetLabel:         string       // "PREFLOP", "FLOP [9h 2h 4s]", etc.

  // Board display
  boardDisplay:        string[]     // ["9h", "2h", "4s", "6h", "3d"]

  // Hero cards display
  heroCardsDisplay:    string[] | null  // ["4c", "9c"] or null

  // Action-to-act indicator
  isHeroTurn:          bool

  // Timer (not in normalized events yet)
  actionTimerMs:       int | null
}
```

---

## 3. Reducer Event Handlers

Each normalized event maps to a deterministic state transition. Events are applied in sequence order. No event may be skipped or reordered.

### 3.1 TABLE_SNAPSHOT

**Precondition**: `table.hand == null` (or first event ever).

**Transition**:
1. Set all table config fields (tableId, tableName, gameType, maxSeats, sb, bb, minBuyIn, maxBuyIn).
2. For each seat in `event.seats`: set SeatState (status, player, stack, bet, inHand flags).
3. Set `table.hand = null` (snapshot is between-hands state).

**Invariant**: After snapshot, `sum(seat.stack)` for all occupied seats equals the total chips at the table.

---

### 3.2 HAND_START

**Precondition**: `table.hand == null` or `table.hand.phase == COMPLETE`.

**Transition**:
1. Create new `HandState` with `handId`, `button` from event.
2. Set `hand.phase = PREFLOP`, `hand.board = []`, `hand.pot = 0`, `hand.actions = []`.
3. For each seat in `event.players`:
   - Set `seat.stack` from event (authoritative starting stack).
   - Set `seat.inHand = true`, `seat.folded = false`, `seat.allIn = false`.
   - Set `seat.bet = 0`, `seat.totalInvested = 0`, `seat.holeCards = null`.
4. Increment `table.handsPlayed`.

**Invariant**: All occupied, sitting-in seats have `inHand = true`.

---

### 3.3 BLIND_POST

**Precondition**: `hand.phase == PREFLOP`.

**Transition**:
1. `seat.stack -= event.amount`
2. `seat.bet += event.amount`
3. `seat.totalInvested += event.amount`
4. `hand.pot += event.amount`
5. Append to `hand.actions`: `{ type: BLIND_SB|BLIND_BB, seat, amount, delta: event.amount, street: "PREFLOP", inferred: false }`
6. If `event.blindType == "SB"`: set `hand.sbSeat = event.seat`
7. If `event.blindType == "BB"`: set `hand.bbSeat = event.seat`

**Invariant**: After both blinds, `hand.pot == sb + bb`. `seat.stack >= 0` for both blind posters.

---

### 3.4 HERO_CARDS

**Precondition**: `hand.phase == PREFLOP`.

**Transition**:
1. Set `seats[heroSeat].holeCards = event.cards` (hero seat determined by context — the seat belonging to the observing player).

**Deduplication**: If `holeCards` is already set to the same cards, no-op.

**Visibility rule**: Only the hero's own seat gets `holeCards` populated. All other seats remain `null` until showdown (see Gap SG1).

---

### 3.5 PLAYER_ACTION

**Precondition**: `hand.phase` is PREFLOP, FLOP, TURN, or RIVER. `!seat.folded`.

**Transition by action type**:

| Action | Stack | Bet | TotalInvested | Pot | Folded | AllIn |
|--------|-------|-----|---------------|-----|--------|-------|
| FOLD | — | — | — | — | `true` | — |
| CHECK | — | — | — | — | — | — |
| CALL | `-delta` | `+delta` | `+delta` | `+delta` | — | if stack=0 |
| BET | `-delta` | `+delta` | `+delta` | `+delta` | — | if stack=0 |
| RAISE | `-delta` | `+delta` | `+delta` | `+delta` | — | if stack=0 |

For all action types:
1. Apply the mutation above.
2. Append to `hand.actions`.
3. If `seat.stack == 0` after mutation: set `seat.allIn = true`.

**Inferred actions**: When `event.inferred == true`, the action was derived from a ROUND_TRANSITION with no explicit server ACTION. The reducer applies it identically but flags it. Inferred FOLDs are idempotent (`folded = true` is safe to set multiple times for the same seat across streets).

**Invariant**: `seat.stack >= 0` after every action. `delta >= 0` for all non-FOLD actions (negative deltas are filtered as collect sweeps before reaching normalized events).

---

### 3.6 BET_RETURN

**Precondition**: `hand` is active.

**Transition**:
1. `seat.stack += event.amount`
2. `seat.totalInvested -= event.amount`
3. `hand.pot -= event.amount`

**When it fires**: After betting round closes and one player's bet exceeds all callers. The uncalled excess is returned before the next street or settlement.

**Invariant**: `event.amount <= seat.bet` (can't return more than was bet). After return, `hand.pot >= 0`.

---

### 3.7 DEAL_COMMUNITY

**Precondition**: `hand.phase` is PREFLOP, FLOP, or TURN (advancing to next street).

**Transition**:
1. Append `event.newCards` to `hand.board`.
2. Advance `hand.phase`:
   - 3 new cards → `FLOP`
   - 1 new card from FLOP → `TURN`
   - 1 new card from TURN → `RIVER`
3. Reset all seats: `seat.bet = 0` (new street, bets reset).

**Invariant**: `hand.board.length` is 0 (preflop), 3 (flop), 4 (turn), or 5 (river). Never any other count.

---

### 3.8 POT_UPDATE

**Precondition**: `hand` is active.

**Transition**: Informational only. The reducer MAY store the server's pot view for UI display but MUST NOT use it to override the computed `hand.pot`.

The canonical pot is derived from action math: `sum(blinds + positive deltas) - sum(bet_returns)`. The POT_UPDATE from the server represents a post-rake display value.

---

### 3.9 POT_AWARD

**Precondition**: `hand.phase` is PREFLOP, FLOP, TURN, or RIVER (hand ending).

**Transition**:
1. Set `hand.phase = SETTLING`.
2. For each award: `seats[award.seat].stack += award.amount`.
3. Store awards in `hand.winners`.

**Invariant**: `sum(awards) + rake == hand.pot`. The reducer computes `hand.rake = hand.pot - sum(awards)`.

---

### 3.10 HAND_SUMMARY

**Precondition**: `hand.phase == SETTLING`.

**Transition**:
1. Set `hand.showdown = event.showdown`.
2. Set `hand.handRank = event.handRank` (null if no showdown).
3. Set `hand.winCards = event.winCards` (null if no showdown).
4. Cross-validate: `event.totalPot == sum(hand.winners[].amount)`.

---

### 3.11 HAND_RESULT

**Precondition**: `hand.phase == SETTLING`.

**Transition**:
1. Store `event.results` in `hand.resultText`.
2. For each result where `result.won == false` and `result.text` contains "mucks": confirm `seats[result.seat].folded == true` (cross-validation).

---

### 3.12 HAND_END

**Precondition**: `hand.phase == SETTLING`.

**Transition**:
1. Set `hand.phase = COMPLETE`.
2. Persist final stacks to seat state (they carry to next hand).
3. Clear per-hand state: `seat.inHand = false`, `seat.bet = 0`, `seat.holeCards = null`.
4. Hand object remains on table state until next HAND_START replaces it.

**Invariant**: `sum(seat.stack for all occupied seats) + hand.rake == sum(seat.startStack for all occupied seats)`. This is the **closed accounting invariant** — money is conserved minus rake.

---

## 4. Reducer Invariants

These MUST hold after every event application. Violation indicates a reducer bug or corrupt event stream.

### INV-1: Stack Non-Negativity

```
∀ seat: seat.stack >= 0
```

A player's stack can reach 0 (all-in) but never go negative.

### INV-2: Pot Non-Negativity

```
hand.pot >= 0
```

The pot may temporarily decrease (via BET_RETURN) but must never go negative.

### INV-3: Active Player Count Consistency

```
activePlayers = count(seat where seat.inHand && !seat.folded && status == OCCUPIED)
```

After HAND_START: `activePlayers >= 2` (need at least 2 to play).
After any FOLD: `activePlayers >= 1`.
When `activePlayers == 1` and no pending actions: hand should proceed to settlement.

### INV-4: Street Ordering

```
phase transitions: PREFLOP → FLOP → TURN → RIVER → SETTLING → COMPLETE
```

No street may be skipped (a hand that ends preflop goes directly from PREFLOP to SETTLING). No street may be revisited. Board card counts must match phase:

| Phase | board.length |
|-------|-------------|
| PREFLOP | 0 |
| FLOP | 3 |
| TURN | 4 |
| RIVER | 5 |
| SETTLING/COMPLETE | 0, 3, 4, or 5 |

### INV-5: Hand-End Completeness

A hand reaching COMPLETE MUST have:
1. At least one `POT_AWARD` event processed.
2. Exactly one `HAND_SUMMARY` event processed.
3. `hand.rake >= 0`.
4. Closed accounting: `sum(startStacks) == sum(endStacks) + hand.rake`.

### INV-6: Bet Reset on Street Change

On every `DEAL_COMMUNITY` event: `∀ seat: seat.bet = 0`.

Bets are per-street. Street transitions reset them. `totalInvested` is cumulative and never resets within a hand.

### INV-7: Blind Completeness

Before any voluntary PLAYER_ACTION in PREFLOP, exactly two BLIND_POST events MUST have been processed (one SB, one BB). `hand.sbSeat` and `hand.bbSeat` MUST be set.

### INV-8: Fold Idempotence

Setting `seat.folded = true` when it is already `true` is a no-op. Inferred folds may fire multiple times for the same seat across streets (once per ROUND_TRANSITION skip). The reducer MUST tolerate this without error.

---

## 5. Stack/Bet/Pot Transition Rules

### 5.1 Per-Action Chip Flow

```
BLIND_POST:   seat.stack -= amount;  seat.bet += amount;  seat.totalInvested += amount;  hand.pot += amount
CALL:         seat.stack -= delta;   seat.bet += delta;   seat.totalInvested += delta;   hand.pot += delta
BET:          seat.stack -= delta;   seat.bet += delta;   seat.totalInvested += delta;   hand.pot += delta
RAISE:        seat.stack -= delta;   seat.bet += delta;   seat.totalInvested += delta;   hand.pot += delta
FOLD:         (no chip movement)
CHECK:        (no chip movement)
BET_RETURN:   seat.stack += amount;  seat.totalInvested -= amount;  hand.pot -= amount
POT_AWARD:    seat.stack += amount
```

### 5.2 Closed Accounting Identity

At HAND_END, for every hand:

```
sum(seat.totalInvested for all seats) = sum(award.amount for all awards) + hand.rake
```

Equivalently:
```
sum(seat.startStack) = sum(seat.stack) + hand.rake
```

This has been validated against 3 hands with rake of 0c, 4c, and 16c.

### 5.3 Street Boundary Bet Reset

When `DEAL_COMMUNITY` fires:
1. All `seat.bet` values are reset to 0.
2. `seat.totalInvested` is NOT reset (cumulative).
3. `hand.pot` is NOT reset (cumulative).

---

## 6. Board and Card Visibility Rules

### 6.1 Board Cards

| Phase | Board State | Visible To |
|-------|------------|------------|
| PREFLOP | `[]` | — |
| FLOP | `[c1, c2, c3]` | All players |
| TURN | `[c1, c2, c3, c4]` | All players |
| RIVER | `[c1, c2, c3, c4, c5]` | All players |

Board cards are always public once dealt.

### 6.2 Hole Cards

| Context | Visible To |
|---------|-----------|
| Pre-deal | Nobody (`null`) |
| After HERO_CARDS | Hero only |
| During hand | Each player sees only their own |
| At showdown | Players who show (see Gap SG1) |
| After fold | Only the folder knows; mucked cards are never revealed |

### 6.3 Card Encoding

```
rank: 2–14  (2–10 numeric, 11=J, 12=Q, 13=K, 14=A)
suit: 1=c (clubs), 2=d (diamonds), 3=h (hearts), 4=s (spades)
display: rank_char + suit_char  (e.g., "Ah", "Tc", "2d")
```

---

## 7. BET_RETURN Handling

### 7.1 When It Occurs

A BET_RETURN fires when:
- A player bets or raises
- All remaining opponents fold (or no one calls)
- The uncalled excess of the bet/raise is returned

### 7.2 Detection (From Wire Protocol)

Negative-delta ACTION events (opcode 0x77) are buffered. On flush:
- **Single event**: full uncalled return → emit BET_RETURN
- **Batch (2+ seats)**: collect sweep → skip
- **`amount > 0` in batch**: partial return → emit BET_RETURN for that entry

### 7.3 Reducer Application

```
seat.stack += returnAmount
seat.totalInvested -= returnAmount
hand.pot -= returnAmount
```

The return happens between the last action and the next DEAL_COMMUNITY or POT_AWARD. It does NOT change `seat.bet` (the bet was already made; the return is a settlement adjustment).

---

## 8. Hand Result and Settlement

### 8.1 Settlement Sequence

```
1. Last action / all fold
2. BET_RETURN (if applicable)
3. POT_AWARD → phase = SETTLING, stacks credited
4. HAND_SUMMARY → showdown flag, hand rank
5. HAND_RESULT → per-player text descriptions
6. HAND_END → phase = COMPLETE
```

### 8.2 Rake Calculation

```
hand.rake = hand.pot - sum(award.amount for all awards)
```

Rake is computed at settlement, not deducted incrementally. It represents the gap between what went in (from actions) and what came out (from awards).

### 8.3 No-Showdown Settlement

All 8 validated hands followed this pattern:
- `showdown = false`
- One player wins entire pot
- All others: "Loses main pot and mucks cards."
- `handRank = null`, `winCards = null`

### 8.4 Showdown Settlement (Unvalidated)

Expected behavior when two or more players reach showdown:
- `showdown = true`
- `handRank` populated with winning hand description
- `winCards` populated with winning card display
- HAND_RESULT text includes "Shows [hand]" for players who reveal
- Opponent `holeCards` populated in seat state (see Gap SG1)

---

## 9. Inferred vs. Verified Event Provenance

Every event in the normalized stream carries a `_source` object:

### 9.1 Verified Events

```json
{
  "_source": { "frameIdx": 139, "opcode": "0x77", "ts": 886379.405 }
}
```

These come directly from decoded wire protocol frames. The reducer treats them as authoritative.

### 9.2 Inferred Events

```json
{
  "_source": { "frameIdx": 172, "opcode": "0x72", "ts": 886416.392, "inferred": true }
}
```

These are derived from ROUND_TRANSITION events where the server skipped a seat. The reducer applies them identically but:
- Sets `action.inferred = true` in the action log
- MAY display them differently in UI (e.g., dimmed, italicized)
- MUST NOT use them for timing calculations (the `ts` is the ROUND_TRANSITION timestamp, not the actual fold time)

### 9.3 Provenance in Action Log

The `hand.actions[]` array preserves provenance:

```json
{ "type": "FOLD", "seat": 3, "inferred": true, "street": "PREFLOP" }
{ "type": "RAISE", "seat": 0, "inferred": false, "street": "PREFLOP", "amount": 50, "delta": 50 }
```

---

## 10. Open Gaps

### SG1: Showdown Card Reveal

**Status**: Unvalidated. No showdown hands captured.

**Impact**: The reducer cannot populate opponent `holeCards` at showdown. The `HAND_RESULT` text may contain revealed card information (e.g., "Shows Ah Kd"), but parsing it is fragile.

**Required evidence**: Capture a hand that reaches showdown. Check for:
- New normalized event type for card reveal
- PLAYER_STATE updates with opponent `holeCards` populated
- HAND_SUMMARY.handRank and winCards fields populated

**Reducer handling**: Until validated, `holeCards` for non-hero seats remains `null` even at showdown.

### SG2: Genuine CHECK Path

**Status**: Unvalidated. No checked actions observed.

**Impact**: The schema specifies that checks come via `roundId=11, amount=0` — but this is theoretical. If the server handles checks differently, the reducer may misclassify them.

**Required evidence**: Capture a hand where a player checks on a post-flop street (no bet facing them).

**Reducer handling**: The `roundId=11, amount=0 → CHECK` path is implemented but untested. The fallback `amount=0 → FOLD` classification should not fire for genuine checks because they should have `roundId=11`, not `roundId=10`.

### SG3: Side Pot Allocation

**Status**: Partially observed. Multi-entry POT_UPDATE seen (3 entries) but meaning unclear.

**Impact**: Multi-way all-in scenarios where side pots form may produce multiple POT_AWARD events with different `potIndex` values. The reducer stores `potIndex` but does not yet map it to "main pot" vs "side pot N."

**Required evidence**: Capture a hand with a short-stack all-in and continued betting between deeper stacks.

**Reducer handling**: Multiple POT_AWARD events are applied sequentially. Each credits the winning seat's stack. The `potIndex` is stored but not interpreted.

### SG4: Player Join/Leave

**Status**: Join observed (Dante63s appeared mid-session). Leave not observed.

**Impact**: The reducer receives HAND_START with a player roster and uses it as truth. Between-hand seat changes (joins, leaves, sit-out/sit-in) are visible in PLAYER_STATE events but not emitted as discrete normalized events.

**Reducer handling**: The reducer trusts HAND_START for the roster at hand start. Seat status changes between hands are absorbed silently. This is correct for hand-level state but insufficient for a persistent lobby/table view.

### SG5: Ante / Straddle / Missed Blind

**Status**: Not observed.

**Impact**: Some cash games have antes, straddles, or "missed blind" postings. The blind detection logic (`roundId=3 → SB, roundId=4 → BB`) does not account for additional forced bets.

**Reducer handling**: Not implemented. If encountered, these would likely appear as additional BLIND_POST events with unknown `blindType`.

### SG6: Disconnect / Timeout

**Status**: Not observed.

**Impact**: When a player disconnects or times out, the server may auto-fold or use a time bank. The reducer receives the resulting action (fold) but cannot distinguish intentional fold from timeout fold.

**Reducer handling**: Timeout folds are indistinguishable from intentional folds in the event stream. This is acceptable — the reducer cares about the action, not the intent.
