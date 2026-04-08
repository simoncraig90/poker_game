# CFR Training Research — Stakes, Systems, and Strategy Pipeline

## Date: 2026-04-06

---

## 1. Current State

### CFR-100 HU Training (Completed)
- 100-bucket, 10M iterations, heads-up NL Hold'em
- 1.33M info sets, 139MB strategy file
- Trained on Proxmox (8-core, ~5.5hr, 1,780 iter/s)
- Saved to `vision/models/cfr_strategy_sixmax_100bucket.json`

### Bot Evaluation (20K hands, 6-max round-robin)

| Rank | Strategy | bb/100 | ELO |
|------|----------|--------|-----|
| 1 | FISH | +38.5 | 1856 |
| 2 | CFR-100 | +29.7 | 1703 |
| 3 | TAG | +29.0 | 1571 |
| 4 | CFR-50 | +24.6 | 1439 |
| 5 | LAG | +24.6 | 1294 |
| 6 | NIT | +3.6 | 1136 |

CFR-100 beats CFR-50 by +5.1 bb/100. FISH #1 is an artifact — heuristic bots don't exploit loose-passive play.

### Backtest vs Real PokerStars Hands (371 hands, 10NL 6-max)

**Raw CFR-100 alone:** 83% disagreement. HU CFR plays ~80% of hands — suicidal at 6-max.

**Hybrid (preflop chart + CFR-100 postflop):**

| Metric | bb/100 |
|--------|--------|
| Actual results | -154.3 |
| With preflop chart | -55.2 |
| Improvement | +99.1 |

- Preflop chart saves $37.76 by folding out-of-range hands (K3s EP, K5o EP, Q9s EP)
- Only misses $0.98 in wins it would have folded
- Postflop CFR-100 got 0% useful match rate — HU action trees don't map to 6-max

**Root cause:** CFR trained heads-up has no concept of positions, multiway pots, or 6-max action sequences. Preflop ranges completely wrong for 6-max. Postflop keys don't match real hand histories.

---

## 2. Bucket Granularity vs Stakes

| Buckets | Est. Info Sets (6-max) | Plays Like | Sufficient For |
|---------|------------------------|------------|----------------|
| 10 | ~5-10M | Knows strong/medium/weak, can't distinguish AKo from KQs | 2NL-5NL |
| 50 | ~50-200M | Distinguishes hand classes, has positional ranges | 5NL-25NL |
| 100 | ~500M-2B | Fine-grained hand distinctions, nuanced board texture | 25NL-100NL |
| 200+ | Billions | Solver-grade distinctions | 100NL+ |

### Why This Matters

- **2NL-5NL:** Opponents make massive fundamental errors. 10 buckets with correct positional ranges is enough — edge comes from not making big mistakes.
- **10NL-25NL:** Some regs pay attention. They notice if BTN range = UTG range. 50 buckets gives meaningful strategy differences for top pair vs overpair vs second pair.
- **50NL+:** Regs actively exploit. They track frequencies and adjust. Need 100+ buckets so bluff-to-value ratios hold across specific board textures.

---

## 3. Stakes Reference

| Stake | Blinds | Buy-in (100bb) | BB |
|-------|--------|----------------|-----|
| 2NL | $0.01/$0.02 | $2 | $0.02 |
| 5NL | $0.02/$0.05 | $5 | $0.05 |
| 10NL | $0.05/$0.10 | $10 | $0.10 |
| 25NL | $0.10/$0.25 | $25 | $0.25 |
| 50NL | $0.25/$0.50 | $50 | $0.50 |
| 100NL | $0.50/$1.00 | $100 | $1.00 |

### Revenue Projections (10bb/100 win rate, 4 tables)

| Stake | $/hr per table | 4 tables | 8hr session |
|-------|---------------|----------|-------------|
| 2NL | $0.20 | $0.80 | $6.40 |
| 5NL | $0.50 | $2.00 | $16.00 |
| 10NL | $1.00 | $4.00 | $32.00 |
| 25NL | $2.50 | $10.00 | $80.00 |
| 50NL | $5.00 | $20.00 | $160.00 |
| 100NL | $10.00 | $40.00 | $320.00 |

---

## 4. HU vs 6-Max CFR Training Requirements

### Game Tree Comparison

| | HU (trained) | 6-Max (needed) |
|---|---|---|
| Players | 2 | 6 |
| Info set key | STREET:bucket:s0:IP/OOP:history | STREET:bucket:s{stack}:POS:Np:history |
| Positions | 2 (IP, OOP) | 6 (BTN, SB, BB, UTG, MP, CO) |
| Preflop action points | 2 players | 6 players (UTG-MP-CO-BTN-SB-BB) |
| Branching per street | ~6 actions x 2 players | ~6 actions x up to 6 players |

### Training Resource Requirements

| | HU 100-bucket (done) | 6-Max 10-bucket | 6-Max 50-bucket | 6-Max 100-bucket |
|---|---|---|---|---|
| Info sets | 1.33M | ~5-10M | ~50-200M | ~500M-2B |
| RAM (raw JS) | ~2GB | ~2-4GB | ~64-128GB | 256GB+ |
| RAM (optimized) | ~1GB | ~1-2GB | ~30-60GB | 120-200GB |
| Time (10M iter) | ~5.5hr | ~24-72hr | ~2-4 days | Weeks |
| Speed | ~1,780 iter/s | ~200-500 iter/s | ~50-100 iter/s | ~10-20 iter/s |
| Storage (strategy) | 139MB | ~500MB-1GB | ~5-10GB | ~50-100GB |

Speed drops because each iteration traverses decisions for all 6 players. Memory is the real killer — each info set stores regret + strategy sums (2 floats per action x 6 actions = 48 bytes minimum).

---

## 5. Available Hardware

### Windows PC (Playing Machine)
- 32GB RAM
- Primary use: running Chrome + overlay + bot on 10 tables
- Cannot train anything above 10 buckets

### Proxmox Lab Host (192.168.0.200)
- 90GB total RAM
- Running VMs consuming ~78GB when all active
- Training requires stopping VMs (disrupts crypto operations on edge-lab)
- Can train 50-bucket if edge-lab (32GB) + AI-Core (32GB) stopped

### Current VM Allocation

| VM | ID | Status | RAM |
|----|-----|--------|-----|
| Torrent | 201 | running | 4GB |
| Scripts | 203 | running | 2GB |
| AI-Core | 204 | stopped/just started | 32GB |
| Altitude | 205 | stopped | 2GB |
| edge-lab | 210 | running | 32GB |
| Data | 220 | running | 4GB |
| CollectorA | 230 | running | 4GB |

### Cloud Options

| Provider | Instance | RAM | vCPU | Cost/hr | Est. 72hr | Notes |
|----------|----------|-----|------|---------|-----------|-------|
| Hetzner CCX43 | Dedicated | 64GB | 32 | ~€0.32 | ~€23 | Fits 50-bucket with memory optimization |
| Hetzner CCX63 | Dedicated | 128GB | 48 | ~€0.53 | ~€38 | 50-bucket without optimization |
| Hetzner CCX93 | Dedicated | 192GB | 96 | ~€0.80 | ~€58 | 100-bucket possible |
| Hetzner CCX93 (384GB) | Dedicated | 384GB | 96 | ~€1.00 | ~€200-500 | 100-bucket 6-max (1-3 weeks) |
| AWS r7i.4xlarge | On-demand | 128GB | 16 | ~$1.07 | ~$77 | More expensive, slower |
| GCP n2-highmem-16 | On-demand | 128GB | 16 | ~$0.96 | ~$69 | |

---

## 6. Client Memory Requirements (Playing 10 Tables)

### Strategy Loading on Windows PC (32GB)

| Strategy Size | Raw Python Dict | Mmap Binary | Feasible for 10 Tables? |
|---------------|----------------|-------------|------------------------|
| 139MB (current HU) | ~800MB-1GB | ~200MB | Yes (either method) |
| 1GB (6-max 10-bucket) | ~3-5GB | ~500MB | Yes (either method) |
| 5-10GB (6-max 50-bucket) | 15-30GB | ~1.5GB | Mmap only |
| 50-100GB (6-max 100-bucket) | Won't fit | ~3-5GB | Mmap only |

### Why Python Dicts Blow Up

Each info set: string key (~90 bytes overhead) + inner dict with 4-6 floats (~400 bytes) = ~500 bytes per info set.

At 50M info sets: 50M x 500 bytes = 25GB. System OOM.

### Memory-Mapped Binary Format (Required for 50+ Buckets)

- Sorted array of hashed info set keys (8 bytes each)
- Packed float32 action probabilities (4 bytes x 6 actions = 24 bytes)
- Binary search for lookup: O(log n), ~17 comparisons for 50M entries
- 30 bytes/entry x 50M = 1.5GB on disk, ~500MB resident via mmap
- OS pages in only accessed entries — 10 tables share same mapping

---

## 7. Engineering Work Required

### Training Infrastructure

| Task | Effort | Blocking? |
|------|--------|-----------|
| Bucket count config (10→50) | 5 min | Yes |
| Bet size alignment | 15 min | No (can run with 4 sizes) |
| Memory optimization (typed arrays in cfr.js) | 2-4 hours | Yes at 50 buckets on <128GB |
| Worker thread update for 6-player | 1-2 hours | No (can run single-threaded) |
| Binary checkpoints for large runs | 1 hour | No (can skip first run) |
| Skip exploitability computation | 0 | Just don't call it for 6-max |

### Client Updates (Using 6-Max Strategy)

| Component | Change | Effort |
|-----------|--------|--------|
| cfr_adapter.py | New key format (POS instead of IP/OOP, stack bucket, player count) | ~2hr |
| cfr-bot.js | Same key format for eval framework | ~1hr |
| unibet_ws.py | Pass position name + accumulate action history | ~30min |
| advisor_ws.py | Thread action history to adapter | ~30min |
| Mmap binary converter | Build once: JSON → binary with index | ~4hr |
| Mmap loader (Python) | Replace dict with mmap + binary search | ~2hr |
| Overlay | No change | 0 |

**Total: ~4hr training infra + ~10hr client work**

### Existing Code That Already Works

- `sixmax-holdem.js` (570 lines) — complete 6-max game model
- `cfr.js` trainer — already handles N players (line 231-241 loops all players)
- `train-cfr.js` — already supports `--game sixmax` flag
- Preflop chart + equity model — continues as fallback

---

## 8. 100NL Requirements

At 100NL the player pool changes: regs have HUDs, study solvers, exploit leaks.

### What the Bot Needs

| Component | Current State | 100NL Requirement |
|-----------|---------------|-------------------|
| Preflop ranges | Static chart | GTO ranges by position, stack depth, vs open/3bet/4bet |
| Postflop strategy | HU CFR (useless for 6-max) | 100-bucket 6-max CFR or solver imports |
| Opponent modeling | TODO (not built) | Critical — exploit fish, don't get exploited by regs |
| Bet sizing | 3 sizes (half/pot/allin) | 4-5 sizes (33%/50%/75%/125%/allin) |
| Board texture | Basic danger assessment | Range-vs-range equity on specific textures |
| Multi-street planning | None (street-by-street) | Turn/river planning based on flop action |

### Solver Import vs Training Your Own

| Approach | Cost | Quality | Effort | Covers |
|----------|------|---------|--------|--------|
| Train 100-bucket 6-max CFR | €200-500 cloud | Good but slow convergence | Weeks | Custom, retrainable |
| PioSolver + import | €250 one-time | GTO-accurate | Days | Pre-solved key spots |
| GTO Wizard API | €50/month | Pre-solved | Hours | Lookup only |
| MonkerSolver (6-max native) | €350 one-time | Best for 6-max preflop | Days | Full preflop + postflop |

PioSolver/MonkerSolver is the honest answer for 100NL. They've solved these game trees with full precision on hardware you can't match. Export solutions as lookup tables, load via same mmap binary interface.

---

## 9. Recommended Progression

| Phase | Stake | Strategy Source | Cost | Timeline |
|-------|-------|----------------|------|----------|
| Now | 2NL-5NL | Preflop chart + equity model (working) | Free | Current |
| Next | 5NL-25NL | 50-bucket 6-max CFR (cloud training) | €23-38 | This week |
| Then | 25NL-100NL | Solver imports (PioSolver/MonkerSolver) + opponent modeling | €250-350 | When 25NL profitable |

### Key Decisions

1. **50-bucket training:** Hetzner CCX43 (€23 with memory optimization) or CCX63 (€38 without). Cloud avoids disrupting crypto on Proxmox.
2. **Mmap binary loader:** Must-build for 50-bucket on 32GB Windows PC. Also future-proofs for solver imports.
3. **Opponent modeling:** More important than bucket count at 25NL+. Exploitative adjustments vs player types (VPIP/PFR tracking) matter more than GTO precision at microstakes.
4. **Solver imports for 100NL:** Better ROI than training your own 100-bucket CFR. Same client interface — strategy source is transparent to the bot.

---

## 10. Files Reference

| File | Role |
|------|------|
| scripts/cfr/train-cfr.js | Training script (supports --game sixmax) |
| scripts/cfr/cfr.js | CFR engine (handles N players) |
| scripts/cfr/sixmax-holdem.js | 6-max game model (complete) |
| scripts/cfr/full-holdem.js | HU game model (used for current training) |
| scripts/cfr/cfr-bot.js | Strategy adapter for bot evaluation |
| scripts/cfr/abstraction.js | Card bucketing and info set key construction |
| scripts/eval-bots.js | Round-robin bot evaluation framework |
| scripts/backtest-cfr100.js | CFR-100 backtest vs PS hand histories |
| scripts/backtest-hybrid.js | Hybrid backtest (preflop chart + CFR postflop) |
| vision/cfr_adapter.py | Python CFR adapter for live advisor |
| vision/models/cfr_strategy_sixmax_100bucket.json | Current HU CFR-100 strategy (139MB) |
| vision/models/cfr_strategy_50bucket.json | Old HU CFR-50 strategy (138MB) |
