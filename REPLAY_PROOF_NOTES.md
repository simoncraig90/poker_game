# Replay Proof Notes

Results from replaying normalized hand events through `scripts/replay-normalized-hand.js`.

**Verdict** (updated): Both gaps (F1, F2) have been resolved. The normalized event stream is now sufficient for both narrative reconstruction AND correct stack accounting. See REPLAY_FIX_VALIDATION.md for before/after proof.

> Original verdict (pre-fix): "not yet sufficient for correct stack accounting due to two identified gaps." This was fixed by adding BET_RETURN events and changing inferred CHECK to FOLD.

---

## 1. What Works

| Capability | Status | Evidence |
|-----------|--------|---------|
| Hand identity & table setup | **PASS** | handId, tableName, blinds, button all correct |
| Player roster & starting stacks | **PASS** | All players from HAND_START.players match session |
| Blind posting (SB/BB) | **PASS** | Correct seats, correct amounts, correct stack deductions |
| Hero hole cards | **PASS** | Dealt once, deduplicated, correct values |
| Voluntary actions (call/raise/bet) | **PASS** | Action type, amount, delta all present and usable |
| Street transitions + board cards | **PASS** | FLOP (3 cards), TURN (+1), RIVER (+1) correct |
| Pot award | **PASS** | Winner seat, award amount match session |
| Hand result text | **PASS** | Human-readable per-player outcome text |
| Hand lifecycle | **PASS** | HAND_START → ... → HAND_END ordering correct |
| Inferred event marking | **PASS** | All inferred events carry `inferred: true` + reason |
| Source traceability | **PASS** | Every event has `_source.frameIdx` back to raw frame |

---

## 2. What Fails

### F1: Stack Accounting — Uncalled Bet Return Missing

**Severity**: High — breaks deterministic stack reconstruction.

**Observed in**: All hands where the winning bet was uncalled (all 8 hands in this session, since all were no-showdown).

**Example** (hand #260272208552):
```
BoriSuizo starts at 1000c
  raises to 40c         → stack: 960c  (per replay)
  everyone folds
  wins 25c (POT_AWARD)  → stack: 985c  (per replay)

Actual end stack: 1015c
Missing: 30c (uncalled portion of 40c raise, returned)
```

**Root cause**: The emitter filters all negative-delta ACTION events (the "collect sweep" rule). But one category of negative-delta events carries real information: **uncalled bet returns** where `amount > 0, delta < 0`. These return the excess portion of a bet that nobody called.

**Fix options**:
1. Emit a `BET_RETURN` normalized event for `amount > 0, delta < 0` ACTIONs.
2. Or: derive the return as `totalInvested - (potAwarded - netProfit)` post-hoc.
3. Or: use the final PLAYER_STATE stack as the authoritative end stack.

**Correct accounting formula**:
```
winner's end stack = startStack - totalInvested + potAwarded + uncalledReturn
non-winner's end stack = startStack - totalInvested
```

Without `uncalledReturn`, the winner's stack is always too low by the uncalled amount.

---

### F2: Inferred CHECK vs FOLD Confusion

**Severity**: Medium — narrative is wrong, but hand outcome is unaffected.

**Observed in**: Every hand. `ROUND_TRANSITION` with `roundId=10, betToCall=0` is emitted as an inferred CHECK, but in context these players actually **folded**.

**Example** (hand #260272208552):
```
  BoriSuizo raises to 40c
  Skurj_poker checks {inferred}    ← actually folded
  Blurrr99 checks {inferred}       ← actually folded
  Tragladit987 checks {inferred}   ← actually folded
  Bandifull checks {inferred}      ← actually folded

  (5 players remaining)            ← should be 1
```

**Evidence**: HAND_RESULT says these players "Loses main pot and mucks cards" — they clearly folded, not checked.

**Root cause**: The `betToCall` field in `ROUND_TRANSITION` reads 0 for these seats. This likely means they were already skipped/folded by the server, not that they faced a 0 bet. The emitter uses `betToCall > 0 → FOLD, betToCall = 0 → CHECK`, which is wrong in this context.

**Fix options**:
1. Post-hoc correction: cross-reference inferred CHECKs against HAND_RESULT text. Any "mucks cards" player who was inferred as CHECK should be reclassified as FOLD.
2. Or: use the `totalPot` / action amounts to determine if a CHECK is possible in context. If the pot has unmatched bets and a player "checks," it's actually a fold.
3. Or: compare the player's current bet to the current bet-to-call from the most recent raise ACTION. If they owe money and "checked," they folded.

---

### F3: Pot Math — Summary vs Actions Mismatch

**Severity**: Low — cosmetic, but confusing for validation.

**Observed in**: All hands.

**Explanation**: `HAND_SUMMARY.totalPot` is the **contested pot** (amount awarded to winner), not the sum of all bets. When a bet goes uncalled, the excess is returned. The sum of all positive-delta actions exceeds the awarded pot by the returned amount.

| Hand | Sum of actions | Pot awarded | Difference | Explanation |
|------|---------------|-------------|------------|-------------|
| 260272188638 | 505c | 309c | 196c | Multiple streets, uncalled bet returns |
| 260272208552 | 55c | 25c | 30c | 40c raise, only 10c called, 30c returned |
| 260272235570 | 100c | 66c | 34c | Multiple streets, uncalled bet returns |

This is not a bug — it's an accounting identity:
```
totalPot = sum(all bets) - sum(uncalled returns)
```

The replay should derive the correct pot from POT_AWARD, not from action summation.

---

## 3. Replay State Model Assessment

The replay maintains this state:

| State Field | Source | Accuracy |
|-------------|--------|----------|
| `seats[].name` | HAND_START | ✓ Correct |
| `seats[].startStack` | HAND_START | ✓ Correct |
| `seats[].stack` | Computed from actions | ✗ Missing uncalled returns (F1) |
| `seats[].bet` | Computed from actions | ✓ Correct per-street |
| `seats[].totalInvested` | Computed from actions | ✓ Correct |
| `seats[].folded` | From FOLD actions | ✗ Inferred CHECKs should be FOLDs (F2) |
| `heroCards` | HERO_CARDS | ✓ Correct |
| `board` | DEAL_COMMUNITY | ✓ Correct |
| `pot` | Computed from actions | ✗ Over-counts by uncalled returns (F1) |
| `winners` | POT_AWARD | ✓ Correct |
| `totalPot` | HAND_SUMMARY | ✓ Correct (contested amount) |
| `phase` | Street transitions | ✓ Correct |
| `actions[]` | BLIND_POST + PLAYER_ACTION | ✓ Correct ordering and amounts |

---

## 4. Schema Weaknesses Exposed

### W1: No BET_RETURN Event

The schema defines collect sweeps as "not a normalized event" and filters all negative-delta ACTIONs. This loses uncalled bet returns, which are needed for stack reconstruction. The schema should add a `BET_RETURN` event type:

```
BET_RETURN:
  seat: int
  amount: int (cents returned)
  _source: { frameIdx, opcode: "0x77", inferred: false }
```

### W2: Inferred Fold/Check Needs Context

The `roundId=10, betToCall=0` heuristic is insufficient. The schema's Gap G1 acknowledges this but underestimates its impact — it's not just missing folds, it produces incorrect CHECKs that poison the "players remaining" count and make the hand narrative misleading.

### W3: HAND_START Lacks Seat Positions Relative to Blinds

HAND_START has `button` but not `sbSeat` or `bbSeat`. These can be derived from the first two BLIND_POST events, but having them in HAND_START would make setup deterministic without consuming later events.

### W4: POT_UPDATE Granularity

51 POT_UPDATE events for 8 hands is very noisy (6+ per hand). Most are incremental display updates. A single `POT_STATE` at each street boundary would be more useful for replay. Current POT_UPDATEs are not wrong, just noisy.

---

## 5. Hands Replayed

| Hand ID | Events | Actions | Inferred | Board | Balance |
|---------|--------|---------|----------|-------|---------|
| 260272188638 | 30 | 9 | 4 | 9h 2h 4s 6h 3d | FAIL (F1) |
| 260272208552 | 19 | 5 | 4 | (none) | FAIL (F1) |
| 260272235570 | 25 | 7 | 4 | 4h 5s 7s Ts Ad | FAIL (F1) |

All failures are due to F1 (missing uncalled bet returns). The hand narrative (who did what, who won) is correct in all cases.

---

## 6. Recommended Next Steps

1. ~~**Add BET_RETURN event**~~ — **DONE** (batch detection: single negative-delta = return, batch = collect sweep).
2. ~~**Post-hoc fold correction**~~ — **DONE** (roundId=10 always emits FOLD, never CHECK).
3. ~~**Re-run replay**~~ — **DONE** (all 3 hands pass Stack check after fixes).
4. **Capture showdown hands** — validate the showdown path end-to-end. (Still open — GAP-1)
