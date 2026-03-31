# Post-Demo Decision Frame

Use this after the operator trust checkpoint passes. Choose the next development branch.

---

## Current State

After Phase 7, the system has:

- Full pre-flop through river deal engine (heads-up and multi-way)
- Fold, call, check, bet, raise, all-in actions
- Blind posting, pot collection, single-pot winner determination
- Event-sourced persistence with crash recovery and mid-hand void
- Session lifecycle: create, archive, browse, recover
- Browser client: live table, event log, hand history, session browser, recovery UX
- 168 automated checks across 6 test suites, all green

The system does NOT yet have:

- Showdown (card reveal + best-hand evaluation)
- Side pots (multiple all-in players with different stacks)
- Player identity / authentication
- Study/review tools (hand replay, annotation)
- UI polish (responsive layout, mobile, accessibility)

---

## Branch Options

### Option A: Showdown + Side-Pot Closure

**What**: Implement hand evaluation, showdown logic, and side-pot splitting.

**Why choose this**:
- Completes the core game loop -- right now hands can only be won by last-player-standing (all others fold)
- Required before any real multi-player testing or study use
- Engine-level work that everything else builds on

**Risk**: Hand evaluation is algorithmically complex. Side pots add combinatorial edge cases. This is the hardest remaining engine work.

**Scope**: ~1-2 phases. Hand evaluator, showdown sequencing, side-pot accounting, tests.

**Choose if**: You want to finish the engine before anything else. The game isn't "real" until showdown works.

---

### Option B: Product Polish / Usability

**What**: UI improvements, better controls, responsive layout, keyboard shortcuts, better visual feedback.

**Why choose this**:
- The current UI is functional but minimal
- Better UX makes manual testing and demos more pleasant
- Lower risk than engine work -- mostly client-side

**Risk**: Polish is unbounded. Easy to spend time without clear completion criteria. Doesn't advance core functionality.

**Scope**: Open-ended. Best done as a bounded timebox (e.g., "one phase of polish").

**Choose if**: You want to demo the system to others and the current UI is too rough. Or you need a break from engine complexity.

---

### Option C: Identity + Study Features

**What**: Player identity (login/names persist across sessions), hand review tools, annotation, search/filter hand history.

**Why choose this**:
- Moves toward the "research" part of poker-research
- Identity enables tracking player tendencies over time
- Study tools are the eventual value proposition

**Risk**: Identity has design decisions (local-only vs. accounts? persistent vs. session-scoped?). Study features depend on having interesting hands to study, which requires showdown.

**Scope**: ~1-2 phases depending on depth.

**Choose if**: The research/study angle is more urgent than completing the game engine. Be aware that without showdown, study data is limited to fold-only outcomes.

---

## Decision Criteria

| Question | If yes, leans toward |
|----------|---------------------|
| Do I need hands to resolve by card strength? | A (Showdown) |
| Am I showing this to someone soon? | B (Polish) |
| Do I want to start tracking player data? | C (Identity) |
| Is the fold-only game loop blocking me? | A (Showdown) |
| Am I tired of engine work? | B (Polish) |
| Do I have a specific study question to answer? | C (Identity) |

---

## Recommended Sequence

**A then C then B.**

Rationale:
- Showdown completes the game. Without it, everything else is built on an incomplete loop.
- Identity + study features are the project's purpose (poker-research). Get there next.
- Polish is always valuable but never blocking. Do it when the core is solid.

This is a recommendation, not a rule. Pick what serves your current goals.

---

## Recording Your Decision

Once you decide, create the planning doc for the next phase:

```
ENGINE_PHASE8_PLAN.md    # if Option A
UI_POLISH_PLAN.md        # if Option B
IDENTITY_STUDY_PLAN.md   # if Option C
```

Reference this decision frame in the plan's motivation section so future-you knows why.
