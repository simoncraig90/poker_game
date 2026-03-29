# Decoder Validation Plan

How to systematically validate and extend the CDP decoder against real gameplay.

---

## 1. Cross-Session Testing

### 1.1 Capture More Sessions

Run additional capture sessions with deliberate variation:

| Session Goal | What to Do | What It Tests |
|-------------|-----------|---------------|
| **Multi-street hands** | Play tight, see flops/turns/rivers | DEAL_COMMUNITY sequencing, street card counts |
| **Showdown** | Call down to showdown | G2: showdown text, hand rank, revealed cards |
| **All-in** | Shove preflop or on flop | Side pots, all-in display, POT_UPDATE multi-entry |
| **Hero folds** | Fold preflop often | 0x7a (hero action) semantics, ACTION_PROMPT |
| **Hero raises** | 3-bet and 4-bet | Raise classification, delta vs. amount patterns |
| **Player join/leave** | Sit out, leave, rejoin | Join/leave opcodes, seat status transitions |
| **Multi-table** | Open 2 tables simultaneously | CDP captures both — verify tableId routing |
| **Long session (30+ min)** | Play extended session | Heartbeat stability, reconnect handling, memory |

### 1.2 Automated Regression

After each session, run the decoder and diff the output:

```bash
# Capture
node scripts/cdp-capture.js
# (play, then Ctrl+C)

# Decode
node scripts/decode-session.js captures/<new-session>

# Quick checks
wc -l captures/<new-session>/decoded-events.jsonl
grep "HAND_START\|HAND_END" captures/<new-session>/decoded-events.jsonl | wc -l
# Should be equal counts (paired)

grep "DECODE_ERROR\|ERR_" captures/<new-session>/decoded-events.jsonl
# Should be empty

grep "RAW_" captures/<new-session>/decoded-events.jsonl | head
# Unknown opcodes — investigate if new
```

### 1.3 Stack Accounting Validator

Write a post-hoc validator that, for each hand:

1. Records each player's starting stack (from PLAYER_STATE at hand start).
2. Sums all blinds and voluntary actions (positive delta only).
3. Verifies `sum of all bets = totalPot` from HAND_SUMMARY.
4. Verifies `winner's ending stack = starting stack + pot won`.
5. Verifies all non-winners: `ending stack = starting stack - amount lost`.

This catches misclassified actions, missed events, and off-by-one cent errors.

```
For each hand:
  Assert: sum(BLIND_POST amounts) + sum(PLAYER_ACTION positive deltas) = HAND_SUMMARY.totalPot
  Assert: POT_AWARD.amount = HAND_SUMMARY.totalPot
  Assert: winner's stack delta = POT_AWARD.amount - their total investment
```

### 1.4 Round-Trip Consistency

For each hand, verify:
- Every `HAND_START` has exactly one `HAND_SUMMARY` and one `HAND_END`.
- Every `BLIND_POST` (roundId 3, 4) has a matching ACTION with positive delta.
- Board card count at hand end: 0 (no flop), 3 (flop only), 4 (turn), or 5 (river).
- Hero cards appear exactly once per hand (after dedup).

---

## 2. Showdown Validation (Gap G2)

### 2.1 What Evidence Is Needed

| Element | Where to Look | Expected Change |
|---------|--------------|-----------------|
| `showdown` flag | HAND_SUMMARY (0x34 F5[3]) | `"true"` instead of `"false"` |
| Hand rank text | HAND_SUMMARY (0x34 F5[5]) | Non-empty (e.g., `"Two Pair, Aces and Kings"`) |
| Winning cards text | HAND_SUMMARY (0x34 F5[6]) | Non-empty (e.g., `"Ah Kd Qs 9c 4s"`) |
| Result text variation | HAND_RESULT (0x7d F3[n].F5) | New strings like `"Shows [hand]."`, `"Wins with [hand]."` |
| Opponent hole cards | PLAYER_STATE (0x6c F3.F8) | Card structs with `F1=0` and real `suit`/`rank` values |
| Showdown card reveal | 0x5a or 0x8b or new opcode? | Unknown — this is the key question |

### 2.2 How to Trigger

1. Join a play-money table (low stakes, loose players).
2. Limp or call every hand to see a river.
3. If facing a bet on the river, call it.
4. Repeat until at least one hand reaches showdown.

### 2.3 Decoder Changes Needed

After capturing showdown evidence:

1. Check if `HAND_SUMMARY.handRank` and `HAND_SUMMARY.winCards` populate automatically.
2. Check if HAND_RESULT text changes.
3. Look for new opcodes or new fields in existing opcodes around the showdown point.
4. Check PLAYER_STATE for opponent cards becoming visible.
5. Check 0x5a/0x8b for showdown card reveals.
6. Update `decodeCard()` if new card formats appear.
7. Add `SHOWDOWN_REVEAL` normalized event if a new opcode is found.

---

## 3. Side Pot Validation (Gap G4)

### 3.1 What Evidence Is Needed

| Element | Where to Look | Expected Change |
|---------|--------------|-----------------|
| Multiple POT_UPDATE entries | 0x78 F2 | 2+ entries with distinct amounts |
| Multiple POT_AWARD calls | 0x7b | Separate awards per pot index |
| Multiple HAND_RESULT blocks | 0x7d | Separate per pot, with `"side pot"` text |
| All-in detection | ACTION or PLAYER_STATE | Stack reaches 0, or new opcode signals all-in |

### 3.2 How to Trigger

1. Play at a table with varying stack sizes.
2. Get into a multi-way pot.
3. Go all-in with a short stack when other players can continue betting.
4. Or: wait for another player to shove short and observe the side pot creation.

### 3.3 Decoder Changes Needed

1. Track `POT_UPDATE` entry count — when > 1, label as main pot + side pot(s).
2. Map `POT_AWARD.potIndex` to pot labels.
3. Look for an all-in signal in ACTION (possibly `amount = stack` or `options` is empty after).
4. Check if `HAND_RESULT` text includes `"side pot"` or `"main pot"` distinctions per pot.

---

## 4. Player Join/Leave/Reconnect Validation (Gap G5)

### 4.1 What Evidence Is Needed

| Element | Where to Look | Expected Change |
|---------|--------------|-----------------|
| New player appears | PLAYER_STATE (0x6c) | New name at previously empty seat |
| Player departs | PLAYER_STATE (0x6c) | Status changes to 0 or name disappears |
| Sit-out / sit-in | PLAYER_STATE flags | `sittingOut` toggles |
| Reconnect after disconnect | 0x6a re-sent? | Full table snapshot? Or incremental? |
| Hero leaves | 0x65 (sent) | Client-initiated leave |
| Table close | 0x6b (recv) | Server closes table |

### 4.2 How to Trigger

1. **Join**: Open a second tab to the same table, watch for new player events.
2. **Sit out**: Click "Sit Out" in the client mid-session.
3. **Leave**: Click "Leave Table" and observe the 0x65 message.
4. **Reconnect**: Kill the CDP capture, navigate away, come back — does the client get a fresh 0x6a snapshot?
5. **Other player leaves**: Watch for status=0 transitions in PLAYER_STATE when other players leave.

### 4.3 Decoder Changes Needed

1. Emit `PLAYER_JOIN` event when a seat transitions from `status=0` to `status=2`.
2. Emit `PLAYER_LEAVE` event when the reverse happens.
3. Detect and emit `SIT_OUT` / `SIT_IN` from PLAYER_STATE flag changes.
4. Handle mid-hand reconnect (if 0x6a re-fires, reset state).

---

## 5. Inferred Fold Validation (Gap G1)

### 5.1 What Evidence Is Needed

Confirm that every `ROUND_TRANSITION` with `roundId=10` and `betToCall > 0` that has no following ACTION for that seat is a fold.

Confirm that `roundId=10` with `betToCall = 0` and no following ACTION is a check (i.e., the player's turn was skipped because they were already all-in or folded earlier).

### 5.2 How to Validate

```
For each ROUND_TRANSITION with roundId=10:
  1. Record the seat and betToCall.
  2. Check if an ACTION with matching seat follows before the next ROUND_TRANSITION.
  3. If no ACTION: classify as inferred fold (betToCall > 0) or inferred pass (betToCall = 0).
  4. Cross-reference with HAND_RESULT text: does the seat show "Loses main pot and mucks cards"?
  5. Cross-reference with PLAYER_STATE: does hasCards change to false?
```

### 5.3 Decoder Changes Needed

1. Track `ROUND_TRANSITION` events with `roundId=10`.
2. If no ACTION follows for that seat before the next RT, emit `INFERRED_FOLD` (if `betToCall > 0`) or `INFERRED_CHECK` (if `betToCall = 0`).
3. Validate against HAND_RESULT — every inferred fold should appear as "Loses main pot and mucks cards."

---

## 6. Validation Checklist per Session

Run this after each capture + decode:

```
[ ] Decode completes without errors
[ ] Event count > 0
[ ] HAND_START count = HAND_END count
[ ] No DECODE_ERROR or ERR_ events
[ ] No new RAW_ event types (or: investigate new opcodes)
[ ] Stack accounting balances for every hand
[ ] Board card count is 0, 3, 4, or 5 for each hand
[ ] Hero cards appear exactly once per hand dealt in
[ ] All BLIND_POST amounts match SB/BB from TABLE_SNAPSHOT
[ ] Winner's stack change matches POT_AWARD amount minus their bets
[ ] (If showdown observed) showdown flag = true, handRank non-empty
[ ] (If side pot observed) multiple POT_AWARD events with distinct potIndex
[ ] (If player join/leave) PLAYER_STATE transitions are coherent
```

---

## 7. Priority Order

1. **Showdown** — highest priority gap. Blocks hand-rank validation and card reveal decoding.
2. **Inferred folds** — needed for complete action sequences. Can be validated from existing data.
3. **Stack accounting** — pure post-hoc validation. Can be built immediately.
4. **Side pots** — needs specific game scenario. Lower priority until all-in hands are captured.
5. **Join/leave** — lowest priority for hand reconstruction. Cosmetic for timeline.
