# Normalized Hand Event Schema

Canonical event model for representing one observed PokerStars-style cash-game hand, derived from CDP-captured WebSocket frames decoded via Apache Thrift Binary Protocol.

**Status**: v1 — based on 1 session, 8 hands, all no-showdown. Showdown and side-pot paths are modeled but unvalidated.

---

## 1. Identifiers

| ID | Format | Source | Scope |
|----|--------|--------|-------|
| `sessionId` | `YYYYMMDD_HHMMSS` | Capture folder name | One capture run |
| `tableId` | `6R.{digits}.{hex}!` | Opcode 0x6a F1 | One ring-game table instance |
| `tableName` | Free text (e.g., `"Margo II"`) | Opcode 0x6a F2.F1 | Human label for table |
| `handId` | Numeric string (e.g., `"260272188638"`) | Opcode 0x6d(large) F2 | Globally unique hand |
| `prevHandId` | Numeric string | Opcode 0x6a F6 | Hand preceding snapshot |
| `seat` | Integer 0–5 | Multiple opcodes | Position at this table |
| `playerName` | UTF-8 string | Opcode 0x6a/0x6c F3.F1 | Display name (not globally unique) |

---

## 2. Normalized Event Types

Events are listed in the order they occur within one hand. Each event maps back to one or more raw opcodes.

### 2.1 TABLE_SNAPSHOT

Full table state. Emitted once when the observer joins mid-session, then never again (incremental updates via PLAYER_UPDATE thereafter).

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `tableId` | string | yes | 0x6a F1 |
| `tableName` | string | yes | 0x6a F2.F1 |
| `gameType` | int | yes | 0x6a F2.F2 (2 = NL Hold'em) |
| `maxSeats` | int | yes | 0x6a F2.F4 |
| `sb` | int (cents) | yes | 0x6a F2.F7.F1 |
| `bb` | int (cents) | yes | 0x6a F2.F7.F2 |
| `minBuyIn` | int (cents) | yes | 0x6a F2.F9 |
| `maxBuyIn` | int (cents) | yes | 0x6a F2.F10 |
| `seats[]` | Seat array | yes | 0x6a F4 |
| `handId` | string | yes | 0x6a F5 |
| `prevHandId` | string | yes | 0x6a F6 |
| `button` | int (seat) | yes | 0x6a F7 |
| `board` | Card[] or null | optional | 0x6a F11 |
| `features` | string[] | optional | 0x6a F20 |
| `_u9` (F9, unknown) | int | — | 0x6a F2.F9 value 145 observed |

**Seat struct:**

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `seat` | int (0–5) | yes | F4[n].F1 |
| `status` | int | yes | F4[n].F2 (0=empty, 2=occupied) |
| `player.name` | string | if occupied | F4[n].F3.F1 |
| `player.country` | string (ISO 2) | if occupied | F4[n].F3.F2 |
| `player.stack` | int (cents) | if occupied | F4[n].F3.F7 |
| `player.isActive` | bool | if occupied | F4[n].F3.F3 |
| `player.hasCards` | bool | if occupied | F4[n].F3.F4 |
| `player.sittingIn` | bool | if occupied | F4[n].F3.F5 |
| `player.sittingOut` | bool | if occupied | F4[n].F3.F6 |
| `player.holeCards` | Card[] or null | if hero | F4[n].F3.F8 |
| `player.avatarId` | int | optional | F4[n].F3.F10 |
| `player.roundBet` | int (cents) | optional | F4[n].F3.F17 |
| `bet` | int (cents) | yes | F4[n].F4 |

---

### 2.2 HAND_START

Marks the beginning of a new hand.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `handId` | string | yes | 0x6d(large) F2 |
| `tableId` | string | yes | 0x6d F1 |
| `button` | int (seat) | yes | 0x6d F5 |

**Source opcode**: 0x6d when payload > 16 bytes (small payloads are heartbeat acks).

---

### 2.3 PLAYER_STATE

Player stack/status update. Fires frequently — at hand start (reset bets), after each action (update stack), and at hand end.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `tableId` | string | yes | 0x6c F1 |
| `seat` | int | yes | 0x6c F2.F1 |
| `status` | int | yes | 0x6c F2.F2 |
| `player` | Player struct | if occupied | 0x6c F2.F3 |
| `bet` | int (cents) | yes | 0x6c F2.F4 |

---

### 2.4 BLIND_POST

Forced blind posting. Identified by `ROUND_TRANSITION.roundId` = 3 (SB) or 4 (BB) preceding the ACTION.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `seat` | int | yes | 0x77 F2 |
| `amount` | int (cents) | yes | 0x77 F3 |
| `blindType` | "SB" or "BB" | yes | Derived from 0x72 F3 (3=SB, 4=BB) |

**Source opcodes**: 0x72 (classification) + 0x77 (amount).

---

### 2.5 HERO_CARDS

Hero's hole cards. Delivered once per hand when hero is dealt in.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `cards` | Card[2] | yes | 0x8b F3 |

**Card struct**: `{visibility: 0=visible, suit: 1-4, rank: 2-14}` decoded to string `"Ah"`, `"Tc"`, etc.

- Suits: 1=c (clubs), 2=d (diamonds), 3=h (hearts), 4=s (spades)
- Ranks: 2–10 numeric, 11=J, 12=Q, 13=K, 14=A

**Note**: 0x8b may fire more than once per hand (observed 2x in some hands). Deduplicate by card string.

---

### 2.6 PLAYER_ACTION

A voluntary player action.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `seat` | int | yes | 0x77 F2 |
| `action` | enum | yes | Derived (see below) |
| `totalBet` | int (cents) | yes | 0x77 F3 (total bet amount this round) |
| `delta` | int (cents) | yes | 0x77 F4 (chips added this action) |
| `options` | int[] | optional | 0x77 F5 |

**Action derivation** (from preceding 0x72 `roundId` + 0x77 fields):

| Condition | Action |
|-----------|--------|
| `amount=0` | FOLD |
| `roundId=3` | POST_SB (→ emit as BLIND_POST instead) |
| `roundId=4` | POST_BB (→ emit as BLIND_POST instead) |
| `roundId=12` or `delta < amount` | CALL |
| `roundId=13` | BET |
| `roundId=15` or `options.length > 1` | RAISE |
| `roundId=11` and `amount=0` | CHECK |
| `roundId=11` and `amount>0` | BET |
| `roundId >= 5 and < 10` | CALL (preflop voluntary) |
| `delta < 0` | Not a player action — collect sweep / blind return → skip |

**Inferred folds**: When `roundId=10` fires for a seat but no ACTION follows, that player folded. The current decoder does not emit an explicit FOLD event for these — they appear only as absences. See [Gap G1](#g1-inferred-folds).

---

### 2.7 DEAL_COMMUNITY

Community cards dealt to the board.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `street` | "FLOP" / "TURN" / "RIVER" | yes | Derived from card count |
| `cards` | Card[] | yes | 0x71 F2 and/or 0x5a F2 |
| `boardSoFar` | Card[] | yes | Accumulated |

**Source opcodes**:
- 0x71 F2: primary source (3 cards = flop, 1 card = turn or river)
- 0x5a F2: sometimes carries visible cards (river, or when 0x71 F2 is empty)

**Card visibility**: Community cards arrive with `F1=255` (hidden perspective marker), decoded the same way as visible cards.

---

### 2.8 POT_UPDATE

Current pot state.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `pots[]` | array | yes | 0x78 F2 |
| `pots[n].amount` | int (cents) | yes | 0x78 F2[n].F1 |
| `pots[n].pending` | int (cents) | yes | 0x78 F2[n].F2 |

**Note**: Multiple entries appear when side pots form (observed as 3 entries in one hand). See [Gap G4](#g4-side-pot-structure).

---

### 2.9 POT_AWARD

Who wins how much from each pot.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `potIndex` | int | yes | 0x7b F2 |
| `awards[]` | array | yes | 0x7b F3 |
| `awards[n].seat` | int | yes | 0x7b F3[n].F1 |
| `awards[n].amount` | int (cents) | yes | 0x7b F3[n].F2 |

---

### 2.10 HAND_RESULT

Human-readable result text per player.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `potIndex` | int | yes | 0x7d F2 |
| `results[]` | array | yes | 0x7d F3 |
| `results[n].seat` | int | yes | 0x7d F3[n].F1 |
| `results[n].won` | bool | yes | 0x7d F3[n].F2 |
| `results[n].amount` | int (cents) | yes | 0x7d F3[n].F3 |
| `results[n].text` | string | yes | 0x7d F3[n].F5 |

**Observed text values**:
- `"Takes down main pot."`
- `"Loses main pot and mucks cards."`
- Expected but unobserved: `"Shows ..."`, `"Wins side pot ..."`, etc.

---

### 2.11 HAND_SUMMARY

Compact summary emitted after pot distribution.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `winSeat` | string (seat as string) | yes | 0x34 F5[0] |
| `showdown` | bool | yes | 0x34 F5[3] ("true"/"false") |
| `totalPot` | int (cents, as string) | yes | 0x34 F5[4] |
| `handRank` | string or null | optional | 0x34 F5[5] |
| `winCards` | string or null | optional | 0x34 F5[6] |

**Current state**: `showdown` has only been observed as `false`. Fields `handRank` and `winCards` have only been observed as empty strings. See [Gap G2](#g2-showdown-path).

---

### 2.12 HAND_END

Hand processing complete.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `tableId` | string | yes | 0x6e F1 |

**Source opcode**: 0x6e (HAND_BOUNDARY). Fires after HAND_RESULT.

---

## 3. Supporting Event Types (Non-Hand)

These events occur during a hand but are not part of the core hand narrative.

| Event | Source | Purpose |
|-------|--------|---------|
| `ROUND_TRANSITION` | 0x72 | Identifies active seat + round ID. Used to classify the next ACTION. Not emitted as a normalized event. |
| `ACTION_TIMER` | 0x73 | Countdown timer for active player (ms). |
| `PLAYER_TIMER` | 0x6f | Per-player action countdown (ms). |
| `TIME_BANK` | 0x74 | Extended timer activation. |
| `STACK_UPDATE` | 0x76 | Stack size change after chip movement. Redundant with PLAYER_STATE but more granular. |
| `SEAT_BETS` | 0x79 | Per-seat bet display array (6 values, one per seat). |
| `ROUND_BET_SUMMARY` | 0x7c | Per-player investment in the completed round. |
| `BET_ORDER` | 0x70 | Seat order for betting in current round. |
| `TABLE_CONFIG` | 0x48 | Bet increments and table settings. |
| `ACTION_PROMPT` | 0x8d | Available actions for hero (fold/call/raise + raise details). |
| `HEARTBEAT_PING` | 0x83 | Server keep-alive (incrementing seq). |
| `HEARTBEAT_ACK` | 0x6d(small) | Client keep-alive response. |

---

## 4. Event Ordering Constraints

Within one hand, events follow this strict sequence:

```
HAND_START
  │
  ├── PLAYER_STATE × N          (reset stacks and bets for all seated players)
  │
  ├── BLIND_POST (SB)           (roundId=3)
  ├── BLIND_POST (BB)           (roundId=4)
  │
  ├── HERO_CARDS                (0x8b, once, may repeat — deduplicate)
  │
  ├── PLAYER_ACTION × N         (preflop voluntary actions, roundId 5+/10/12/15)
  │   ├── [ROUND_TRANSITION preceding each action]
  │   └── [PLAYER_STATE + STACK_UPDATE + POT_UPDATE after each action]
  │
  ├── [Collect sweep: ACTION events with delta < 0 — SKIP]
  │
  ├── DEAL_COMMUNITY (FLOP)     (0x71 with 3 cards)
  │   ├── PLAYER_ACTION × N     (flop actions)
  │   ├── [Collect sweep]
  │   │
  │   ├── DEAL_COMMUNITY (TURN) (0x71 with 1 card)
  │   │   ├── PLAYER_ACTION × N
  │   │   ├── [Collect sweep]
  │   │   │
  │   │   └── DEAL_COMMUNITY (RIVER) (0x71 or 0x5a with 1 card)
  │   │       └── PLAYER_ACTION × N
  │   │
  │   └── (hand may end at any street if all but one fold)
  │
  ├── [Collect sweep: final]
  │
  ├── POT_AWARD                 (0x7b — who wins)
  ├── HAND_SUMMARY              (0x34 — winner, pot, showdown flag)
  ├── HAND_RESULT               (0x7d — human-readable text per player)
  │
  └── HAND_END                  (0x6e — boundary marker)
```

**Key ordering rules**:
1. `HAND_START` always precedes all other hand events.
2. `BLIND_POST` events always precede voluntary `PLAYER_ACTION` events.
3. `HERO_CARDS` arrives after blinds, before or during first voluntary action.
4. `DEAL_COMMUNITY` always follows a collect sweep (negative-delta ACTIONs).
5. `POT_AWARD` always precedes `HAND_SUMMARY` and `HAND_RESULT`.
6. `HAND_END` is always last.
7. `PLAYER_STATE` events interleave freely throughout — they are informational, not sequencing anchors.

---

## 5. Collect Sweep (Internal, Not a Normalized Event)

Between streets, the server emits a batch of `ACTION` frames (opcode 0x77) with `amount=0, delta<0` for every seated player. These move chips from the per-seat bet display into the collected pot. They are **not player actions** and must be filtered.

**Detection rule**: `delta < 0` on any ACTION event → collect sweep or blind return.

A separate pattern: `amount>0, delta<0` — this is an **uncalled blind/bet return** (excess returned to the player when no one calls).

---

## 6. Raw Opcode → Normalized Event Map

| Opcode | Decoded Type | Normalized Event(s) | Notes |
|--------|-------------|---------------------|-------|
| 0x6a | TABLE_SNAPSHOT | TABLE_SNAPSHOT | Once per session |
| 0x6d (large) | NEW_HAND | HAND_START | Payload > 16 bytes |
| 0x6d (small) | HEARTBEAT_ACK | — | Filtered |
| 0x83 | HEARTBEAT_PING | — | Filtered |
| 0x6c | PLAYER_UPDATE | PLAYER_STATE | Very frequent |
| 0x72 | ROUND_TRANSITION | — | Consumed internally for action classification |
| 0x77 | ACTION | BLIND_POST or PLAYER_ACTION or — (collect sweep) | Classification depends on 0x72 |
| 0x8b | HERO_CARDS | HERO_CARDS | Deduplicate |
| 0x71 | DEAL_BOARD | DEAL_COMMUNITY | Card array in F2 |
| 0x5a | DEAL_NOTIFY | DEAL_COMMUNITY (sometimes) | Visible cards in F2 |
| 0x78 | POT_UPDATE | POT_UPDATE | |
| 0x7b | POT_AWARD | POT_AWARD | |
| 0x7d | HAND_RESULT | HAND_RESULT | |
| 0x34 | HAND_SUMMARY | HAND_SUMMARY | |
| 0x6e | HAND_BOUNDARY | HAND_END | |
| 0x8d | ACTION_PROMPT | (optional: HERO_OPTIONS) | Not emitted in v1 |
| 0x76 | STACK_UPDATE | (absorbed into PLAYER_STATE) | |
| 0x6f | PLAYER_TIMER | — | Display only |
| 0x73 | ACTION_TIMER | — | Display only |
| 0x79 | SEAT_BETS | — | Display only |
| 0x7c | ROUND_BET_SUMMARY | — | Audit / verification |
| 0x70 | BET_ORDER | — | Audit / verification |
| 0x48 | TABLE_CONFIG | — | Reference |

---

## 7. Known Gaps

### G1: Inferred Folds

When `ROUND_TRANSITION` fires with `roundId=10` for a seat, that player folded (or checked). But no explicit `ACTION` event follows — the server simply skips them. The current decoder does not emit a FOLD event for these implicit folds.

**Impact**: The timeline shows raises and calls but skips folds. The hand narrative is incomplete — you cannot reconstruct the exact action order without cross-referencing ROUND_TRANSITION events.

**Evidence needed**: Compare ROUND_TRANSITION seat sequences with ACTION events to confirm that every `roundId=10` without a following ACTION is an implicit fold (vs. an implicit check). The `betToCall` field may distinguish: `betToCall > 0` → fold, `betToCall = 0` → check.

---

### G2: Showdown Path

All 8 observed hands ended without showdown (`HAND_SUMMARY.showdown = "false"`). The following fields have only been observed as empty:
- `HAND_SUMMARY.handRank` (F5[5])
- `HAND_SUMMARY.winCards` (F5[6])

**Expected showdown behavior** (unvalidated):
- `showdown = "true"` in HAND_SUMMARY
- `handRank` populated (e.g., `"Two Pair, Aces and Kings"`)
- `winCards` populated (e.g., `"Ah Kd"`)
- HAND_RESULT text changes from `"Takes down main pot."` to `"Shows [hand]"` or `"Wins main pot with [hand]."`
- Opponent hole cards may appear in PLAYER_STATE (0x6c) with `holeCards` populated (F3.F8 cards with visibility=0)

---

### G3: Action Prompt Semantics

`ACTION_PROMPT` (0x8d) tells the hero what actions are available. The action type IDs in F3[n].F1 are partially decoded:

| Observed F1 | Tentative meaning |
|-------------|-------------------|
| 4 | Check or Call (low amount context) |
| 5 | Call (with amount in F2) |
| 7 | Check |
| 10 | Fold |
| 12 | Raise (with min amount in F2) |
| 40 | Check / passive option |

`raiseDetails` (F4) struct:
| Field | Tentative | Observed values |
|-------|-----------|-----------------|
| F1 | Min raise amount | 15 |
| F2 | Max raise / pot raise | 20–90 |
| F3 | Remaining stack | 790–800 |
| F4 | Big blind size | 10 |
| F5 | Step / increment | 1 |

**Impact**: Cannot reliably distinguish hero's available options programmatically. Does not affect hand reconstruction (which uses actual actions, not prompts).

---

### G4: Side Pot Structure

One hand (260272188638) produced 3 entries in a `POT_UPDATE`:
```json
[{"amount":10,"pending":309}, {"amount":11,"pending":16}, {"amount":0,"pending":309}]
```

The meaning of the second and third entries is unclear. Likely:
- Entry 0: main pot
- Entry 1: side pot (for all-in cases)
- Entry 2: display total?

**Impact**: Side pot allocation and multi-way all-in scenarios are not decoded correctly. The `POT_AWARD` event (0x7b) only showed single-winner awards in this session.

---

### G5: Player Join/Leave Mid-Hand

Dante63s joined the table between hands 4 and 5. This was detected via `PLAYER_STATE` updates showing a new name at seat 5. The actual join mechanism (0xb2?, 0x60?) is not decoded.

Player departures are signaled by `PLAYER_STATE` with `status=0`, or by opcode 0x65 (CLIENT_LEAVE) for the hero.

**Impact**: Join/leave transitions are visible in state but not emitted as discrete normalized events.

---

### G6: Card Visibility Model

Community cards arrive via 0x71 with `F1=255` (hidden marker), but they are visible to all players. The "hidden" marker likely means "not one of your hole cards" or is a rendering hint.

Hero's hole cards arrive via 0x8b with `F1=0` (visible). Opponent hole cards in 0x6a/0x6c snapshots show `F1=0` with `rank=0, suit=0` meaning "face-down / unknown."

At showdown, opponent cards should change to `F1=0` with real rank/suit values — **unvalidated**.

---

## 8. Monetary Unit

All monetary values in the wire protocol are **integer cents**. The decoder preserves this.

Display formatting: `c$(value)` renders values < 100 as `"Nc"` and >= 100 as `"$N.NN"`.

Play-money tables use the same cent encoding as real-money tables.
