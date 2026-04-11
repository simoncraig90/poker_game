# 4bp Family Design — Phase 13

## Data-Driven Analysis (from 39 4bp EMERGENCY decisions in Phase 12 baseline)

### SPR Distribution
- 37/39 decisions have SPR < 2.0
- Mean SPR: 1.0
- Only 2 decisions have SPR > 2.0 (3.2 and 7.1 — outliers)
- 12/39 decisions are forced (hero already all-in, only 1 legal action)
- 27 real strategy decisions remain

### What this means
At SPR < 2, intermediate bet sizes are strategically wrong:
- `cbet_small` (33% pot) at SPR 1.0 = betting 33% of pot when stack = pot.
  Hero bets 33BB into 100BB with 67BB behind. This is a terrible sizing —
  it accomplishes nothing. Either commit (jam) or don't (check).
- `cbet_medium` (60% pot) at SPR 1.0 = same problem, slightly less bad.

The correct 4bp strategy at SPR < 2 is **polarized: check or jam.**

### IP/OOP Split
- 36/39 keys are OOP (hero 4-bet from BB/SB/UTG against BTN opens)
- 3/39 keys are IP (hero at BTN, opponent opened from CO)
- OOP matrix is the priority; IP can use a slightly looser variant

## 4bp Action Menu

### Not facing a bet (check or bet)
```
4BP_ACTIONS = [("check", "none"), ("jam", "none")]
```

Two actions. Check or jam. No intermediate sizes.

### Why `jam` instead of `bet_to` with a large size
The `jam` action kind maps directly to "put all chips in." No amount resolution
needed, no rounding, no near-jam snap threshold. Clean and unambiguous.

The legalizer already handles `jam` — it sets amount = hero_stack + hero_committed.
If hero_stack = 0 (already all-in), the legalizer snaps jam → check.

### Facing a bet (fold/call/raise)
The strategy artifact only outputs "what action I want to take." When facing a bet,
the legal actions are [fold, call, raise_to/jam]. The artifact's check→fold and
jam→raise_to/jam mappings are handled by the legalizer's `kind_not_legal` snap:
- Strategy says "check" (index 0) → legalizer snaps to "fold" (passive/defensive)
- Strategy says "jam" (index 1) → legalizer snaps to "call" or "raise_to" based
  on stack depth

**This means the 2-action [check, jam] menu semantically maps to:**
- Index 0 = "passive" (check when possible, fold when facing bet)
- Index 1 = "aggressive" (jam when possible, call/raise when facing bet)

This is the correct polarized model for SPR < 2.

## 4bp Strategy Matrices

### OOP (hero 4-bet, acting first postflop)

At SPR < 2, OOP 4-bettor should:
- Jam with strong value (already committed, protect equity)
- Jam with some bluffs (balance, fold equity matters at low SPR)
- Check/fold everything else (no drawing equity to realize)

```python
4BP_OOP_MATRIX = [
    # [check/fold, jam/call]
    [0.00, 1.00],  # Monster: always jam
    [0.00, 1.00],  # VeryStrong: always jam
    [0.05, 0.95],  # Strong: almost always jam
    [0.10, 0.90],  # StrongTwoPair: usually jam
    [0.25, 0.75],  # WeakTwoPair: often jam
    [0.05, 0.95],  # Overpair: almost always jam (committed at this SPR)
    [0.30, 0.70],  # TopPairGoodKicker: jam > check
    [0.55, 0.45],  # TopPairWeak: mixed, lean check
    [0.75, 0.25],  # WeakPair: mostly check/fold
    [0.40, 0.60],  # StrongDraw: jam as semi-bluff (flop/turn only)
    [0.85, 0.15],  # WeakDraw: mostly check/fold
    [0.85, 0.15],  # Air: mostly check/fold, small bluff frequency
]
```

### IP (hero called 4-bet, acting last postflop)

IP has position advantage, can jam slightly wider for value and thin value:

```python
4BP_IP_MATRIX = [
    [0.00, 1.00],  # Monster
    [0.00, 1.00],  # VeryStrong
    [0.00, 1.00],  # Strong
    [0.05, 0.95],  # StrongTwoPair
    [0.20, 0.80],  # WeakTwoPair
    [0.00, 1.00],  # Overpair
    [0.20, 0.80],  # TopPairGoodKicker
    [0.45, 0.55],  # TopPairWeak: slightly more aggressive than OOP
    [0.70, 0.30],  # WeakPair
    [0.35, 0.65],  # StrongDraw
    [0.80, 0.20],  # WeakDraw
    [0.80, 0.20],  # Air: slightly more bluff jams than OOP
]
```

## Decision: Build 4bp Exact or Leave in EMERGENCY?

### Arguments for exact:
- Only 39 unique keys (39 artifacts) to reach 100% coverage
- The 4bp strategy is simpler than SRP (2 actions vs 3)
- 4bp pots are the highest-stakes decisions — wrong action costs a full stack
- Legalizer snap behavior is well-defined for check↔fold and jam↔call

### Arguments for leaving in EMERGENCY:
- 1:1 artifact-to-miss ratio (worst economics yet)
- The current EMERGENCY policy is conservative (checks with weak hands, calls/folds appropriately)
- 4bp is rare in real play (~3.4% of decisions)
- The OOP matrix above is a reasonable guess, not solver-verified

### Recommendation: **Build 4bp exact.**

The pot-weighted exposure override applies. 4bp pots are 5-10x the size of SRP pots.
One correct 4bp decision is worth 5-10 correct SRP decisions. The 39 artifacts take
~5 minutes to build and the legalizer snap behavior is already tested.

The EMERGENCY alternative (equity-based) will systematically under-jam in 4bp spots
because it doesn't understand commitment at low SPR. A player with 80% equity and
SPR 0.5 should ALWAYS jam, but the EMERGENCY equity threshold might suggest checking.

## Implementation Plan

1. Add `4BP_ACTIONS` to `build_exact_artifact.py`
2. Add `4BP_OOP_MATRIX` and `4BP_IP_MATRIX` to `build_exact_artifact.py`
3. Widen classification gate for FourBp in `classify.rs`
4. Build the 39 artifacts
5. Run baseline, verify 100% guided
6. Verify snap behavior is correct for forced situations (hero all-in)

## Risk: Legalizer snap quality

The 2-action [check, jam] menu will produce more `kind_not_legal` snaps than
the 3-action SRP menu. Specifically:
- "jam" when hero can't bet → snaps to "check" (correct)
- "check" when facing a bet → snaps to "fold" (correct for passive intent)
- "jam" when facing a bet → snaps to "call" or "raise_to" (needs verification)

The last case is the one to watch: when the strategy says "jam" but the legal
actions are [fold, call] (no raise possible), the legalizer should snap to "call"
(aggressive intent → stay in the hand). Verify this behavior in the legalizer tests.
