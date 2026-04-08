# 6-Max Poker Bot — Production Architecture

## Principal Engineer: Claude | Date: 2026-04-06

---

## Executive Verdict

Do NOT train full 6-max CFR across all 4 streets at 50+ buckets. The memory math kills it:

- 6 players x 6 actions x 4 streets x 50 buckets x 3 stack depths = hundreds of millions of info sets
- At 48 bytes/info set (regret + strategy sums) = 10-100GB just for training tables
- The resulting strategy file is 5-10GB, Python dict = 25GB, OOM on the 32GB client

Instead: **stratified architecture**. Each street uses the cheapest strategy source that's profitable at the target stakes.

---

## 1. Architecture Overview

```
                         GAME STATE (from WS reader)
                                  |
                    +-------------+-------------+
                    |                           |
              PREFLOP                     POSTFLOP
           (Deterministic)            (Stratified Engine)
                    |                           |
            Position-based             +--------+--------+
            range chart                |        |        |
            (0 MB, instant)         FLOP     TURN    RIVER
                    |                  |        |        |
                    |              2-player   Equity   Equity
                    |              CFR-50    + Rules  + Rules
                    |              (mmap)    (0 MB)   (0 MB)
                    |                  |        |        |
                    +-------+----------+--------+--------+
                            |
                    OPPONENT MODEL
                    (adjust frequencies)
                            |
                    ACTION + SIZING
                            |
                        OVERLAY
```

### Why This Design

| Street | % of decisions | Current accuracy | Strategy source | Justification |
|--------|---------------|------------------|-----------------|---------------|
| Preflop | ~40% | Good (chart +99 bb/100) | Deterministic chart | Already profitable. No CFR needed. |
| Flop | ~30% | Bad (threshold heuristics) | 2-player CFR-50 | Most EV-dense street. Opponent count usually 2-3 by flop. |
| Turn | ~20% | Bad | Equity + rule engine | Lower branching, fewer decisions. Rules + equity sufficient for 25NL. |
| River | ~10% | Bad | Equity + rule engine | Binary decisions (value bet / bluff / check-fold). Rules work. |

The insight: **by the flop, most 6-max hands are heads-up or 3-way.** The 6-max preflop complexity collapses. A 2-player CFR with position awareness covers 70%+ of flop spots. The remaining multiway flops use equity + rules.

---

## 2. Module Structure

```
vision/
  strategy/
    __init__.py
    preflop.py           # Deterministic chart (exists: preflop_chart.py)
    postflop_engine.py   # Street router: flop→CFR, turn/river→rules
    flop_cfr.py          # Mmap binary CFR loader + lookup
    turn_river_rules.py  # Equity-based decision engine
    opponent_model.py    # VPIP/PFR/AF tracker + frequency adjustment
    sizing.py            # Bet/raise sizing logic
    binary_format.py     # Mmap strategy reader/writer

  models/
    preflop_ranges.json          # Static chart data (tiny)
    flop_cfr_strategy.bin        # Mmap binary: flop-only 2-player CFR
    flop_cfr_strategy.idx        # Index file for binary search

scripts/
  cfr/
    train-flop-cfr.js    # Flop-only CFR trainer (new)
    flop-holdem.js        # 2-player flop game model (new)
    export-binary.js      # JSON strategy → binary mmap format (new)
    cfr.js                # CFR engine (existing, needs typed array refactor)
    abstraction.js        # Card bucketing (existing)
```

---

## 3. Preflop Module (No Changes Needed)

Use `preflop_chart.py` exactly as-is. The backtest proved it: +99 bb/100 improvement, 80.6% agreement with actual play.

**Interface:**

```python
def get_preflop_action(hero_cards, position, facing_raise, num_raisers=0):
    """
    Returns: {
        'action': 'RAISE' | 'CALL' | 'FOLD' | 'CHECK',
        'hand_key': 'AKs',
        'in_range': True/False
    }
    """
```

Zero memory. Zero latency. Deterministic.

---

## 4. Flop CFR — The Core Investment

### 4.1 Why Flop-Only

Full 4-street 6-max CFR at 50 buckets = 50-200M info sets = 64-128GB training RAM.

**Flop-only 2-player CFR at 50 buckets:**

| Parameter | Value |
|-----------|-------|
| Players | 2 (IP vs OOP — by the flop, usually HU/3-way) |
| Streets | 1 (flop only) |
| Buckets | 50 |
| Bet sizes | 4 (check, half-pot, pot, all-in) |
| Max raises | 3 per street |
| Positions | 6 (IP: BTN/CO/MP, OOP: SB/BB/EP) mapped to 2 |
| Stack depths | 3 (short <40bb, medium 40-120bb, deep 120bb+) |
| Board texture | Encoded in bucket (strength relative to board) |
| Est. info sets | ~500K-2M |
| Training RAM | ~500MB-2GB |
| Training time | 1-2 hours |
| Strategy file | 5-20MB |
| Client RAM (mmap) | ~10-50MB |

This **trains on your laptop**. No cloud needed. Retrainable in an hour when you want to experiment.

### 4.2 Flop Game Model (`flop-holdem.js`)

```
State:
  - hero_bucket: 0-49 (hand strength vs this board)
  - villain_bucket: 0-49
  - pot_size_bucket: 0-3 (small/medium/large/huge relative to stacks)
  - position: IP or OOP
  - action_history: string of encoded actions
  - is_terminal: bool
  - payoff: [hero_payoff, villain_payoff]

Actions:
  - CHECK, FOLD, CALL
  - BET_33 (1/3 pot), BET_66 (2/3 pot), BET_POT, BET_ALLIN
  - RAISE_HALF, RAISE_POT, RAISE_ALLIN

Info set key:
  "FLOP:{bucket}:s{stack}:{pos}:{history}"

Example:
  "FLOP:34:s1:IP:kbhc"
  = bucket 34, medium stack, in position, villain checked, hero bet half, villain called
```

### 4.3 Training Flow

```
Deal iteration:
  1. Sample random 2 hole cards for each player
  2. Sample random 3-card flop
  3. Compute strength bucket for each player (vs this board)
  4. Create initial state with blinds already posted + preflop action complete
  5. Run CFR traversal for player 0, then player 1

The flop CFR doesn't model preflop — it assumes both players are already in the pot.
The preflop chart handles the entry decision independently.
```

### 4.4 Memory Math (Training)

```
Info sets: ~1M (generous estimate)
Per info set:
  - regret_sum: Float32Array[9 actions] = 36 bytes
  - strategy_sum: Float32Array[9 actions] = 36 bytes
  - Total: 72 bytes

Total: 1M x 72 bytes = 72MB

With Map overhead in JS: ~200-300MB
With typed array flat storage: ~100-150MB

Fits in 2GB. Trains on anything.
```

---

## 5. Turn/River Rule Engine

No CFR. Pure equity + heuristics with opponent-model adjustments.

### 5.1 Decision Logic

```python
def get_postflop_action(equity, adjusted_equity, facing_bet, pot, call_amount,
                         stack, board_danger, opponent_type, street):
    """
    equity:          raw Monte Carlo or NN equity (0-1)
    adjusted_equity: equity discounted by opponent bet sizing
    facing_bet:      bool
    pot:             current pot in cents
    call_amount:     amount to call in cents
    stack:           hero stack in cents
    board_danger:    {warnings: [...], suppress_raise: bool}
    opponent_type:   'FISH' | 'NIT' | 'TAG' | 'LAG' | 'UNKNOWN'
    street:          'TURN' | 'RIVER'
    """
```

### 5.2 Turn/River Thresholds (Base)

| Situation | Action | Threshold |
|-----------|--------|-----------|
| Not facing bet, equity > 0.70 | BET 66% pot | Value bet |
| Not facing bet, equity 0.50-0.70 | CHECK | Pot control |
| Not facing bet, equity < 0.50 | CHECK (bluff 10%) | Give up or bluff |
| Facing bet, equity > pot odds + 10% | CALL or RAISE | +EV call |
| Facing bet, equity near pot odds | CALL (if implied odds) | Marginal call |
| Facing bet, equity < pot odds | FOLD | -EV fold |
| Facing bet, equity > 0.85 | RAISE | Value raise |

### 5.3 Opponent-Adjusted Thresholds

```python
OPPONENT_ADJUSTMENTS = {
    'FISH': {
        'value_bet_threshold': -0.05,    # bet thinner (they call wider)
        'bluff_frequency': -0.05,         # bluff less (they don't fold)
        'call_threshold': -0.05,          # call wider (they bluff too much)
        'equity_discount': 0.05,          # discount less (they bet weak)
    },
    'NIT': {
        'value_bet_threshold': +0.10,    # only bet strong (they fold everything else)
        'bluff_frequency': +0.15,         # bluff more (they fold too much)
        'call_threshold': +0.10,          # fold more vs their bets (they only bet strong)
        'equity_discount': 0.35,          # heavy discount (their bets are strong)
    },
    'TAG': {
        'value_bet_threshold': 0,
        'bluff_frequency': 0,
        'call_threshold': 0,
        'equity_discount': 0.20,
    },
    'LAG': {
        'value_bet_threshold': -0.05,
        'bluff_frequency': -0.10,         # bluff less (they 3bet bluffs themselves)
        'call_threshold': -0.05,          # call wider (they bluff often)
        'equity_discount': 0.10,          # discount less (they bet wide)
    },
}
```

This is where profitability at microstakes lives. Fish call too much (value bet thinner, don't bluff). Nits fold too much (bluff more, fold to their raises). This alone is worth more than 50 extra buckets.

---

## 6. Opponent Model

### 6.1 Data Tracked Per Player

```python
@dataclass
class PlayerProfile:
    name: str
    hands_seen: int = 0
    vpip: int = 0          # voluntarily put $ in pot
    pfr: int = 0           # preflop raise
    postflop_bets: int = 0 # bets + raises postflop
    postflop_calls: int = 0
    postflop_folds: int = 0
    went_to_showdown: int = 0
    won_at_showdown: int = 0
    three_bet: int = 0     # 3-bet count
    cbet: int = 0          # continuation bet count
    cbet_opportunity: int = 0
```

### 6.2 Classification

```python
def classify(profile) -> str:
    if profile.hands_seen < 15:
        return 'UNKNOWN'

    vpip_pct = profile.vpip / profile.hands_seen
    pfr_pct = profile.pfr / profile.hands_seen
    af = (profile.postflop_bets / max(1, profile.postflop_calls))

    if vpip_pct > 0.45 and af < 1.5:
        return 'FISH'       # loose-passive
    if vpip_pct > 0.35 and af > 2.5:
        return 'LAG'        # loose-aggressive
    if vpip_pct < 0.18 and af < 2.0:
        return 'NIT'        # tight-passive
    if vpip_pct < 0.28 and af > 1.5:
        return 'TAG'        # tight-aggressive
    if vpip_pct > 0.60:
        return 'WHALE'      # plays everything

    return 'TAG'  # default assumption
```

### 6.3 Integration Point

The opponent model feeds into:
1. **Flop CFR:** No direct integration (CFR plays GTO baseline). But the adapter can bias toward certain actions when opponent is classified.
2. **Turn/River rules:** Direct threshold adjustments (Section 5.3).
3. **Preflop:** Widen 3-bet range vs fish, tighten vs nits. Simple chart modifications.
4. **Bet sizing:** Larger vs fish (they call anyway), smaller vs nits (induce folds cheaper).

---

## 7. Binary Strategy Format

### 7.1 File Layout

```
HEADER (32 bytes):
  magic:        4 bytes  "CFR1"
  version:      4 bytes  uint32 = 1
  num_entries:  4 bytes  uint32
  num_actions:  4 bytes  uint32 (max actions per info set, e.g. 9)
  bucket_count: 4 bytes  uint32
  reserved:     12 bytes (zero)

INDEX (num_entries x 12 bytes):
  key_hash:   8 bytes  uint64 (FNV-1a hash of info set key string)
  data_offset: 4 bytes uint32 (byte offset into DATA section)

  Sorted by key_hash for binary search.

DATA (num_entries x num_actions x 4 bytes):
  probabilities: float32[num_actions]
  Packed sequentially. Actions in fixed order:
    [FOLD, CHECK, CALL, BET_33, BET_66, BET_POT, BET_ALLIN, RAISE_HALF, RAISE_POT]
  Unused actions = 0.0.
```

### 7.2 Size Calculation

```
Flop-only CFR (1M info sets, 9 actions):
  Header:  32 bytes
  Index:   1M x 12 = 12MB
  Data:    1M x 36 = 36MB
  Total:   ~48MB on disk
  Resident via mmap: ~5-10MB (only accessed entries paged in)
```

### 7.3 Lookup (Python)

```python
import mmap
import struct
import hashlib

class MmapStrategy:
    """Memory-mapped binary strategy lookup."""

    def __init__(self, bin_path, idx_path):
        self.bin_file = open(bin_path, 'rb')
        self.mm = mmap.mmap(self.bin_file.fileno(), 0, access=mmap.ACCESS_READ)

        # Read header
        magic = self.mm[0:4]
        assert magic == b'CFR1'
        self.num_entries = struct.unpack_from('<I', self.mm, 8)[0]
        self.num_actions = struct.unpack_from('<I', self.mm, 12)[0]
        self.bucket_count = struct.unpack_from('<I', self.mm, 16)[0]

        self.header_size = 32
        self.index_entry_size = 12
        self.index_start = self.header_size
        self.data_start = self.index_start + self.num_entries * self.index_entry_size

        self.ACTION_NAMES = [
            'FOLD', 'CHECK', 'CALL', 'BET_33', 'BET_66',
            'BET_POT', 'BET_ALLIN', 'RAISE_HALF', 'RAISE_POT'
        ]

    def _hash_key(self, key: str) -> int:
        """FNV-1a 64-bit hash."""
        h = 0xcbf29ce484222325
        for b in key.encode('utf-8'):
            h ^= b
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return h

    def lookup(self, info_set_key: str) -> dict | None:
        """Binary search for info set key. Returns action probabilities or None."""
        target = self._hash_key(info_set_key)

        lo, hi = 0, self.num_entries - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            offset = self.index_start + mid * self.index_entry_size
            entry_hash = struct.unpack_from('<Q', self.mm, offset)[0]

            if entry_hash == target:
                data_offset = struct.unpack_from('<I', self.mm, offset + 8)[0]
                abs_offset = self.data_start + data_offset
                probs = struct.unpack_from(f'<{self.num_actions}f', self.mm, abs_offset)
                return {
                    self.ACTION_NAMES[i]: probs[i]
                    for i in range(self.num_actions)
                    if probs[i] > 0.001
                }
            elif entry_hash < target:
                lo = mid + 1
            else:
                hi = mid - 1

        return None

    def close(self):
        self.mm.close()
        self.bin_file.close()
```

Lookup cost: ~17 comparisons (log2 of 1M), zero memory allocation, zero dict construction for misses. Under 1 microsecond per lookup.

---

## 8. CFR Training Engine — Typed Array Refactor

### 8.1 Current Problem

`cfr.js` stores regrets and strategies in `Map<string, Object>`:

```javascript
// Current: ~500 bytes per info set due to JS object/Map overhead
this.regretSum = new Map();   // key -> { FOLD: 0.5, CALL: 1.2, ... }
this.strategySum = new Map(); // same
```

At 50M info sets: 50M x 500 bytes = 25GB. OOM.

### 8.2 Proposed: Flat Typed Arrays + String Table

```javascript
class CompactCFRStore {
    constructor(maxInfoSets, numActions) {
        this.maxInfoSets = maxInfoSets;
        this.numActions = numActions;

        // String table: info set key strings
        // Use a hash map: hash(key) -> index into flat arrays
        this.keyHashes = new BigUint64Array(maxInfoSets);  // 8 bytes each
        this.keyStrings = new Array(maxInfoSets);           // for export only

        // Flat regret and strategy storage
        // regrets[i * numActions + a] = regret for info set i, action a
        this.regrets = new Float32Array(maxInfoSets * numActions);    // 4 bytes each
        this.strategies = new Float32Array(maxInfoSets * numActions);  // 4 bytes each

        this.count = 0;
        this.indexMap = new Map(); // hash -> index (for insertion)
    }

    // Memory per info set: 8 (hash) + 4*9*2 (regret+strategy) = 80 bytes
    // At 1M info sets: 80MB. At 50M: 4GB. At 200M: 16GB.
}
```

### 8.3 Memory Comparison

| Info Sets | Current (JS Map) | Proposed (Typed Arrays) | Reduction |
|-----------|-----------------|------------------------|-----------|
| 1M | 500MB | 80MB | 6x |
| 10M | 5GB | 800MB | 6x |
| 50M | 25GB | 4GB | 6x |
| 200M | 100GB | 16GB | 6x |

The flop-only CFR at 1M info sets uses **80MB** with typed arrays. No optimization needed.

For the eventual full 6-max CFR (if ever), typed arrays make 50M info sets fit in 4GB — trainable on the Proxmox host without stopping VMs.

---

## 9. Deployment Flow

```
TRAINING (cloud or Proxmox, one-time)
  |
  1. node scripts/cfr/train-flop-cfr.js --buckets 50 --iterations 5000000
  |    Output: vision/models/flop_cfr_checkpoint.json (~100-200MB)
  |
  2. node scripts/cfr/export-binary.js --input flop_cfr_checkpoint.json
  |    Output: vision/models/flop_cfr_strategy.bin (~48MB)
  |            vision/models/flop_cfr_strategy.idx
  |
  v
TRANSFER
  |
  scp or rsync to Windows PC
  |
  v
RUNTIME (Windows PC, 10 tables)
  |
  Python process loads:
    - preflop_chart.py        (0 MB, instant)
    - MmapStrategy(.bin)      (~10MB resident, instant)
    - EquityModel(.pt)        (~5MB)
    - OpponentTracker          (~1MB per 100 players)
  |
  Total resident memory: ~20-50MB
  |
  Per decision:
    1. Preflop? → chart lookup (instant)
    2. Flop?    → MmapStrategy.lookup(key) (<1us)
    3. Turn/River? → equity + rules (~1ms for NN inference)
    4. Apply opponent adjustment
    5. Compute bet sizing
    6. Send to overlay
```

---

## 10. Upgrade Path

### Phase 1: Now (days)
- Build flop-only 2-player CFR trainer
- Train on laptop/Proxmox (1-2 hours)
- Build mmap binary format
- Wire into advisor
- **Result: profitable at 5NL-10NL**

### Phase 2: Month 1 (if needed)
- Add turn CFR (same 2-player model, single street)
- Combined flop+turn strategy: ~100MB on disk, ~20MB resident
- Improve opponent model with cross-session persistence
- **Result: competitive at 10NL-25NL**

### Phase 3: When profitable at 25NL
- Buy PioSolver (€250)
- Solve key spots: SRP IP, SRP OOP, 3bet pots, 4bet pots
- Export as same binary format — drop-in replacement
- **Result: competitive at 25NL-100NL**

### Phase 4: Optional
- Full 6-max CFR with typed arrays (fits in 16GB for 50M info sets)
- Train on Hetzner if solver imports leave gaps
- Only if specific spots are underperforming

---

## 11. What NOT To Build

1. **Full 4-street 6-max CFR at 50+ buckets.** Memory math doesn't justify it when flop-only + rules covers 90% of the EV.

2. **Monte Carlo tree search at runtime.** Latency budget is <100ms. MCTS needs seconds.

3. **Neural network policy.** Training data doesn't exist yet. Build the CFR/rules system first, generate training data from it, then consider NN distillation later.

4. **River solver.** River decisions are almost always binary (bet or check, call or fold). Equity + pot odds + opponent type handles this. A solver adds complexity without EV at 25NL.

5. **GTO preflop ranges.** The static chart is already +99 bb/100 vs your play. Dynamic preflop adjustments via opponent model are higher EV than computing exact GTO opens.

---

## 12. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Flop CFR bucket count too coarse at 50 | Loses subtle spots vs TAG regs | Increase to 100 buckets (still fits in memory for flop-only) |
| Opponent model misclassifies (too few hands) | Bad adjustments | Default to TAG profile (play GTO) until 25+ hands |
| Mmap binary has hash collisions | Wrong strategy looked up | FNV-1a 64-bit has <1 collision per billion entries. Verify with test suite. |
| Turn/river rules too simple for 25NL | Exploitable by good regs | Upgrade to turn CFR (Phase 2) or solver imports (Phase 3) |
| Equity model inaccurate on rare boards | Bad postflop decisions | Equity model already trained on 50K+ hands. Monitor and retrain if needed. |

---

## 13. Implementation Priority

| # | Task | Effort | Blocks | EV Impact |
|---|------|--------|--------|-----------|
| 1 | `flop-holdem.js` game model | 3hr | Training | High — enables flop CFR |
| 2 | Train flop CFR (50-bucket, 5M iter) | 1-2hr compute | Binary export | High |
| 3 | `export-binary.js` converter | 2hr | Client deployment | High |
| 4 | `binary_format.py` mmap loader | 2hr | Client deployment | High |
| 5 | `postflop_engine.py` street router | 2hr | Live play | High |
| 6 | `turn_river_rules.py` with opponent adjustments | 3hr | Live play | Medium-High |
| 7 | `opponent_model.py` upgrade (classification + adjustments) | 2hr | Better decisions | Medium-High |
| 8 | `sizing.py` opponent-aware bet sizing | 1hr | Better sizing | Medium |
| 9 | Wire into `advisor_ws.py` | 1hr | Live play | High (integration) |
| 10 | Backtest against PS hands | 1hr | Validation | Medium |

**Total: ~18 hours of engineering. No cloud costs. Trainable on any machine with 2GB free RAM.**

---

## 14. Success Criteria

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Flop CFR coverage | >60% of real flop decisions match an info set | Backtest vs PS hands |
| Preflop + flop agreement | >50% with actual winning play | Backtest |
| Hybrid backtest improvement | >+50 bb/100 vs actual | Backtest vs PS hands |
| Bot eval vs heuristic bots | #1 or #2 in round-robin | eval-bots.js |
| Client memory (10 tables) | <500MB total strategy | Measure at runtime |
| Decision latency | <50ms per action | Measure at runtime |
| Profitable at 5NL | >5 bb/100 over 10K+ hands | Live play |
