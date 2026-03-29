# Replay Fix Validation

Before/after comparison for fixes F1 (BET_RETURN) and F2 (inferred FOLD).

---

## Fix F1: BET_RETURN for Uncalled Bets

### Detection Method

Negative-delta ACTIONs are buffered until a non-ACTION event arrives. On flush:

| Buffer size | `amount` | Classification |
|------------|----------|----------------|
| 1 (single) | any | **BET_RETURN** — uncalled bet returned |
| 2+ (batch) | = 0 | **Collect sweep** — skip |
| 2+ (batch) | > 0 | **BET_RETURN** — partial uncalled return within collect |

### Before/After: Hand #260272208552

| Metric | Before | After |
|--------|--------|-------|
| BET_RETURN events | 0 | 1 (30c to BoriSuizo) |
| Pot (actions) | 55c | 25c |
| Pot (summary) | 25c | 25c |
| Pot match | FAIL | **PASS** |
| Stack check | FAIL | **PASS** |
| BoriSuizo end stack | $9.85 (-15c) | $10.15 (+15c) |

### Before/After: Hand #260272188638

| Metric | Before | After |
|--------|--------|-------|
| BET_RETURN events | 0 | 1 (180c to Bandifull) |
| Pot (actions) | $5.05 | $3.25 |
| Pot (summary) | $3.09 | $3.09 |
| Pot match | FAIL | MISMATCH (rake) |
| Stack check | FAIL (-$1.96 net) | **PASS** (net = -rake) |
| Rake detected | — | 16c (4.9%) |

### Before/After: Hand #260272235570

| Metric | Before | After |
|--------|--------|-------|
| BET_RETURN events | 0 | 1 (30c to Bandifull) |
| Pot (actions) | $1.00 | 70c |
| Pot (summary) | 66c | 66c |
| Pot match | FAIL | MISMATCH (rake) |
| Stack check | FAIL (+6c net) | **PASS** (net = -rake) |
| Rake detected | — | 4c (5.7%) |

### Result

Stack check passes for all three hands. The remaining pot mismatch is rake (platform fee deducted from pot before award). Rake is now properly identified and reported.

---

## Fix F2: Inferred FOLD (Previously CHECK)

### Rule Change

| Before | After |
|--------|-------|
| `roundId=10, betToCall > 0` → FOLD | `roundId=10` → always FOLD |
| `roundId=10, betToCall = 0` → CHECK | (no CHECK inferred) |

### Rationale

`betToCall` is always 0 for `roundId=10` regardless of whether the player owes chips. The server uses `roundId=10` universally for "this seat is skipped." It never sends `roundId=10` for a player who is actively checking.

### Before/After: Player Remaining Counts

**Hand #260272208552** (BoriSuizo raises, all fold):

| Before | After |
|--------|-------|
| BoriSuizo raises to 40c | BoriSuizo raises to 40c |
| Skurj_poker **checks** {inferred} | Skurj_poker **folds** {inferred} |
| Blurrr99 **checks** {inferred} | Blurrr99 **folds** {inferred} |
| Tragladit987 **checks** {inferred} | Tragladit987 **folds** {inferred} |
| Bandifull **checks** {inferred} | Bandifull **folds** {inferred} |
| **(5 players remaining)** | **(1 player remaining)** |

**Hand #260272188638** (multi-street, two callers):

| Street | Before (players remaining) | After (players remaining) |
|--------|---------------------------|--------------------------|
| FLOP | 5 | **2** |
| TURN | 5 | **2** |
| RIVER | 5 | **1** |

**Hand #260272235570** (flop bet, one caller folds):

| Street | Before (players remaining) | After (players remaining) |
|--------|---------------------------|--------------------------|
| FLOP | 6 | **3** |
| TURN | 6 | **2** |

### Cross-Validation with HAND_RESULT

Every inferred FOLD player shows "Loses main pot and mucks cards" in HAND_RESULT — consistent with having folded.

No inferred FOLD player shows "Takes down main pot" — no false positives.

---

## Summary

| Hand | Stack Check | Pot Match | Rake | Players Remaining |
|------|------------|-----------|------|-------------------|
| 260272208552 | **PASS** ✓ | **PASS** ✓ | 0c (0%) | **Correct** ✓ |
| 260272188638 | **PASS** ✓ | Rake 16c (4.9%) | ✓ | **Correct** ✓ |
| 260272235570 | **PASS** ✓ | Rake 4c (5.7%) | ✓ | **Correct** ✓ |

### Residual Ambiguity

1. **Rake is not an explicit event** — derived as `invested - awarded`. Cannot distinguish platform rake from rounding errors without more data. Observed rates (0-5.7%) are consistent with standard micro-stakes rake.

2. **Inferred FOLDs cannot distinguish "folded now" from "already folded"** — a player who folded on an earlier street still gets `roundId=10` on subsequent streets. The emitter emits FOLD each time. The replay consumer handles this correctly (setting `folded=true` is idempotent), but the action count may overcount folds.

3. **No inferred CHECKs remain** — if a player genuinely checks (first to act on a new street, no bet facing), this would come via `roundId=11` with a subsequent `ACTION` with `amount=0`. This path is untested (no check actions observed in this session).
