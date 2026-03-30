# State Reducer Gap List

Remaining blockers before the live table reducer can be treated as production-grade for a clone backend. Each gap includes severity, what evidence is needed, and estimated capture effort.

---

## Critical (Blocks Core Gameplay)

### GAP-1: Showdown Card Reveal + Hand Evaluation

**What's missing**: No showdown hand has been captured. The reducer cannot:
- Reveal opponent hole cards at showdown
- Display winning hand rank ("Two Pair, Aces and Kings")
- Parse or validate hand evaluation results
- Handle split pots from tied hands

**What we expect**: HAND_SUMMARY fields `handRank` and `winCards` populate. HAND_RESULT text changes. Opponent `holeCards` may appear in PLAYER_STATE.

**Evidence needed**: Capture 3+ hands that reach showdown (ideally including one split pot).

**Capture effort**: 15–30 minutes at a loose play-money table. Limp and call to river.

**Blocked**: Hand evaluation engine cannot be validated. UI cannot show "what beat what." Split pot handling is unspecified.

---

### GAP-2: Genuine CHECK Action

**What's missing**: No player has been observed checking. The `roundId=11, amount=0 → CHECK` path is implemented from theory, not evidence.

**Risk**: If the server sends checks differently (e.g., a separate opcode, or `roundId=11` with different semantics), the reducer will misclassify them.

**Evidence needed**: Capture 2+ hands where a player checks on a post-flop street. Verify the decoded event structure.

**Capture effort**: Same session as GAP-1 (checking happens naturally at showdown tables).

**Blocked**: Cannot confirm that the action classifier handles all voluntary action types. The reducer's CHECK path is untested code.

---

## High (Blocks Multi-Way All-In)

### GAP-3: Side Pot Allocation

**What's missing**: Multi-entry POT_UPDATE observed (3 entries) but semantics unclear. `potIndex` in POT_AWARD is not mapped to "main" vs "side" pots. Multiple POT_AWARD events per hand not observed.

**Risk**: A short-stack all-in with continued betting will produce side pots. The reducer will credit the wrong amounts to the wrong seats if POT_AWARD events are misinterpreted.

**Evidence needed**: Capture a hand where:
1. Player A goes all-in short
2. Players B and C continue betting
3. Side pot forms
4. Two separate POT_AWARD events fire (one per pot)

**Capture effort**: 30–60 minutes. Requires a specific game situation — may need to force it by playing short-stacked.

**Blocked**: Multi-way all-in is a core cash-game scenario. Cannot ship without this.

---

## Medium (Blocks Table Lifecycle)

### GAP-4: Player Join / Leave / Sit-Out Events (Partially Resolved)

**Resolved**: The engine now emits `SEAT_PLAYER` and `LEAVE_TABLE` events (added in Phase 2). These are persisted in the event log and handled by `reconstructState()`. Basic join/leave is fully working.

**Still missing**: Sit-out / sit-in toggle, reconnection with state recovery, "wait for big blind" before being dealt in.

**Blocked**: Only the sit-out/sit-in edge cases. Core join/leave works.

---

## Low (Improves Robustness)

### GAP-5: Ante / Straddle / Missed Blind

**What's missing**: Only SB and BB blind types observed. The `roundId` values for antes, straddles, and missed blinds are unknown.

**Risk**: If a table has antes (e.g., turbo SnG format) or a player posts a missed blind, the reducer's blind detection will fail silently — the forced post will be misclassified as a voluntary call.

**Evidence needed**: Join a table that uses antes or wait for a missed-blind scenario.

**Capture effort**: 30+ minutes. May require specific table type.

**Blocked**: Only specialized table formats. Standard 6-max ring games do not have antes.

---

### GAP-6: Disconnect / Timeout Behavior

**What's missing**: No timeout or disconnect observed. Unknown whether the server sends a distinct event for auto-fold or time-bank activation.

**Risk**: Minimal. The reducer receives the resulting action regardless of cause. The gap is only relevant if timeout handling differs from voluntary fold (e.g., the server sends a CHECK instead of FOLD on timeout when no bet is facing).

**Evidence needed**: Let the action timer expire during a hand.

**Capture effort**: 5 minutes. Just don't act.

**Blocked**: Nothing critical. Cosmetic only (can't show "disconnected" indicator).

---

## Summary

| Gap | Severity | Effort | Blocks |
|-----|----------|--------|--------|
| GAP-1: Showdown | **Critical** | 15–30 min capture | Hand eval, card reveal, split pots |
| GAP-2: CHECK path | **Critical** | Same session as GAP-1 | Action classifier completeness |
| GAP-3: Side pots | **High** | 30–60 min capture | Multi-way all-in |
| GAP-4: Join/leave | **Low** (partially resolved) | — | Sit-out/sit-in only |
| GAP-5: Ante/straddle | **Low** | 30+ min, specific table | Specialized formats only |
| GAP-6: Timeout | **Low** | 5 min | Cosmetic only |

### Minimum Viable Capture Session

A single 45-minute session at a loose play-money table can close GAP-1, GAP-2, GAP-4, and GAP-6:
1. Play tight for 5 minutes (observe checks, timeouts)
2. Limp/call to showdown for 20 minutes (showdown + check evidence)
3. Sit out, sit in, leave, rejoin (join/leave evidence)
4. Play short-stacked hoping for all-in (side pot evidence, harder to guarantee)

GAP-3 (side pots) may require a dedicated session or luck.
