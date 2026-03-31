# Phase 8 Slice 5: Event Contract + Recovery + Client Plan

---

## A. Event Contract Clarification

### Settlement Event Sequence

```
SHOWDOWN_REVEAL   →  POT_AWARD (×N)  →  HAND_SUMMARY  →  HAND_RESULT (×N)  →  HAND_END
```

For fold-out (no showdown):
```
POT_AWARD (×1)  →  HAND_SUMMARY  →  HAND_RESULT (×1)  →  HAND_END
```

### Are POT_AWARD and HAND_RESULT Both Necessary?

**Yes. They serve distinct purposes.**

| Event | Purpose | Consumers | State Mutation |
|-------|---------|-----------|----------------|
| `POT_AWARD` | **Authoritative chip transfer.** Says who gets how many chips from which pot. This is the event that `reconstructState` uses to update stacks. | Reconstruct, accounting verifier | Yes — adds chips to winner stacks |
| `HAND_RESULT` | **Human-readable outcome summary.** Describes each eligible player's win/loss for a pot, including descriptive text. Does NOT move chips. | Client display, history viewer, replay | No — informational only |

**Why both**: `POT_AWARD` is the machine-readable source of truth for accounting. `HAND_RESULT` is the human-readable narrative. Merging them would either make the accounting event bloated with display text, or make the display event carry settlement responsibility. The current split keeps reconstruction simple (only `POT_AWARD` mutates state) and display flexible (`HAND_RESULT` can carry whatever text the client needs).

### Full Event Definitions

**SHOWDOWN_REVEAL** — Emitted once per showdown hand, immediately before settlement.
```javascript
{
  type: "SHOWDOWN_REVEAL",
  handId, sessionId,
  reveals: [{
    seat: number,
    player: string,
    cards: ["As", "Kh"],        // hole cards (display format)
    handName: "Pair of Aces",   // evaluated hand name
    bestFive: ["As", "Ah", "Kd", "9s", "7h"],  // best 5-card hand
  }]
}
```
- Only non-folded players appear in reveals
- No state mutation — informational for display and replay
- Reconstruct handler: no-op

**POT_AWARD** — Emitted once per pot (main + each side pot). Authoritative chip transfer.
```javascript
{
  type: "POT_AWARD",
  handId, sessionId,
  potIndex: number,   // 0 = main pot, 1+ = side pots
  awards: [{
    seat: number,
    player: string,
    amount: number,   // chips awarded from this pot
  }]
}
```
- Multiple POT_AWARD events per hand when side pots exist
- `reconstructState` applies each award to seat stacks
- Sum of all awards across all pots must equal sum of all player investments

**HAND_SUMMARY** — Emitted once per hand. Overall winner summary.
```javascript
{
  type: "HAND_SUMMARY",
  handId, sessionId,
  winSeat: number,          // primary winner (main pot winner)
  winPlayer: string,
  showdown: boolean,        // true if showdown occurred
  totalPot: number,         // total chips distributed
  handRank: string | null,  // e.g. "Pair of Aces" (null for fold-outs)
  winCards: string[] | null, // best 5 cards (null for fold-outs)
  board: string[] | null,
}
```
- No state mutation — informational
- `winSeat` is the main pot winner; side pot winners appear only in HAND_RESULT
- `handRank` and `winCards` are populated for showdown, null for fold-out

**HAND_RESULT** — Emitted once per pot. Per-player outcome narrative.
```javascript
{
  type: "HAND_RESULT",
  handId, sessionId,
  potIndex: number,
  results: [{
    seat: number,
    player: string,
    won: boolean,
    amount: number,
    text: string,   // e.g. "Wins main pot with Pair of Aces."
  }]
}
```
- No state mutation — informational
- One HAND_RESULT per HAND_AWARD (same potIndex)
- Only eligible players for that pot appear in results

---

## B. Recovery Tests — Implementation Summary

### Test File: `test/showdown-recovery.test.js` — 40 checks

| Test | Crash Point | What It Proves |
|------|-------------|----------------|
| T1 | After SHOWDOWN_REVEAL, before POT_AWARD | Stacks restored to pre-hand. Card reveals don't corrupt state. |
| T2 | After first POT_AWARD in multi-pot hand | Partial pot awards are discarded. All stacks restored to pre-hand. |
| T3 | After HAND_SUMMARY, before HAND_END | Even with all POT_AWARDs applied, stacks restored to pre-hand. |
| T4 | No crash (completed showdown) | Clean recovery preserves final stacks. No void emitted. |
| T5 | After SHOWDOWN_REVEAL + recovery + new hand | Reconstruct matches live state after recovery + continued play. |
| T6 | After first POT_AWARD (3-player multi-pot) | Partial settlement corruption explicitly checked and prevented. |

### Recovery Mechanism

The existing void logic in `Session.load()` handles all showdown crash points correctly:

1. Detects incomplete hand: HAND_START without matching HAND_END
2. Restores stacks from HAND_START.players (authoritative pre-hand stacks)
3. Emits void HAND_END with `void: true`
4. Decrements handsPlayed

**Key insight**: The void doesn't need to understand the settlement event types. It ignores everything between HAND_START and the missing HAND_END, and restores from the snapshot in HAND_START. This means partial SHOWDOWN_REVEAL, partial POT_AWARD, partial HAND_SUMMARY — none of them matter. The void always resets to pre-hand state.

### Reconstruct Fix

During this work, a bug was found and fixed in `reconstructState`: void HAND_END events now properly restore stacks from the matching HAND_START and decrement handsPlayed. Previously, reconstruct counted voided hands in handsPlayed, causing a mismatch with live state after recovery.

---

## C. Remaining Engine Trust Risks

**None identified.** The engine is now complete through showdown + side pots + recovery.

| Risk Area | Status | Evidence |
|-----------|--------|----------|
| Hand evaluation correctness | Covered | 113 evaluator tests |
| Side-pot accounting | Covered | 134 pot tests |
| Settlement assembly | Covered | 106 showdown tests |
| Orchestrator integration | Covered | 47 integration tests |
| Recovery after showdown crash | Covered | 40 recovery tests |
| Reconstruct consistency | Covered | Checked in T5, T7 integration + T5 recovery |
| Fold-out regression | Covered | T4 integration + all Phase 1-7 suites green |
| Accounting closure | Covered | Every test verifies sum(stacks) == sum(buy-ins) |

The engine has no known untested paths. All 608 checks pass across 13 suites.

---

## D. Client Catch-Up Plan (Slice 6)

The engine is ahead of the client. The browser currently:
- Does not render SHOWDOWN_REVEAL events
- Does not display opponent hole cards at showdown
- Does not handle multiple HAND_RESULT events (expects one)
- Does not show hand rank in result banners
- Does not format showdown events in the archived-hand timeline

### Slice 6A: SHOWDOWN_REVEAL Rendering

**File**: `client/table.js`

1. On receiving SHOWDOWN_REVEAL event:
   - For each reveal in the event, update the seat's displayed cards
   - Show hole cards face-up in the seat UI
   - Display hand name below the cards (e.g. "Pair of Aces")
2. Cards remain visible until HAND_END clears them

### Slice 6B: Multi-Pot Result Display

**File**: `client/table.js`

1. Accumulate HAND_RESULT events by potIndex
2. Show result banner per pot: "Main pot: Alice wins 300 with Pair of Aces"
3. For side pots: "Side pot 1: Bob wins 400 with King-high"
4. Auto-dismiss after 5-8 seconds per pot (sequential display)

### Slice 6C: HAND_SUMMARY Enhancement

**File**: `client/table.js`

1. Use `handRank` and `winCards` from HAND_SUMMARY for richer display
2. Show hand rank in the History tab for showdown hands
3. Distinguish showdown wins vs fold-outs visually

### Slice 6D: Archived-Hand Timeline

**File**: `client/table.js`

1. In `formatTimeline()`, handle SHOWDOWN_REVEAL:
   - Display each player's revealed hand with name
2. Handle multiple POT_AWARD/HAND_RESULT entries:
   - Show per-pot awards in sequence

### Implementation Order

6A → 6B → 6C → 6D

Rationale: Card reveal (6A) is the most visible change and validates the data pipeline. Multi-pot display (6B) is needed for correctness. Summary and timeline (6C, 6D) are polish.

### Scope Note

Slice 6 is display-only work. It cannot break engine correctness. All client changes are rendering logic that consumes existing events. No new events, no protocol changes, no server changes.
