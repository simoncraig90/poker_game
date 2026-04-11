# 6-Max NLHE Advisor — Architecture & Roadmap

**Target:** Profitable at high-stakes 6-max cash (NL100–NL500+)
**Last updated:** 2026-04-11

---

## Executive Summary

The goal is a full-coverage 6-max decision system, not runtime solving from scratch. The correct approach is:

- **Offline exact library** for all high-frequency spots (precomputed, instant lookup)
- **Bounded live re-solve** seeded from the library for novel spots
- **Explicit approximation tiers** so the advisor knows when it's guessing

At high stakes, regs probe for leaks systematically. Any consistent gap in the strategy — multiway postflop, weird stack depths, 3-bet/4-bet pots — will be found and exploited. Coverage must be genuinely broad.

---

## Coverage Tiers

| Tier | Meaning | SLA |
|------|---------|-----|
| A | Direct library lookup — exact-ish solution precomputed offline | <200ms |
| B | Library seed + bounded live re-solve (100-500 CFR iterations) | <3s |
| C | Nearest supported abstraction + low-confidence flag | <1s |

### Coverage Matrix (v1 → high-stakes target)

| Spot | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|------|---------|---------|---------|---------|
| 6-max preflop all lines | A | A | A | A |
| HU flop SRP | A | A | A | A |
| HU turn SRP | C (heuristic) | A/B | A | A |
| HU river SRP | C (heuristic) | A/B | A | A |
| HU flop 3-bet pot | C | A/B | A | A |
| HU turn/river 3-bet pot | C | B | A/B | A |
| HU 4-bet pot | C | C | B | A/B |
| 3-way flop | C | B | A/B | A |
| 3-way turn/river | C | C | B | A/B |
| 4-6 way | C (check/fold bias) | C | C | B |

---

## Canonical Game Settings

### Stack depths
- **v1:** 100bb only
- **Later:** 40bb, 60bb, 150bb added as separate solve packs

### Preflop action menu
```
Unopened:
  UTG/HJ/CO: 2.2x
  BTN:       2.5x
  SB:        3.0x

Facing open:
  Fold / Call
  3-bet: IP 3.2x, OOP 4.0x, BB vs BTN/SB 4.5x

Facing 3-bet:
  Fold / Call
  4-bet: IP 2.2x 3-bet, OOP 2.5x 3-bet
  Jam if eff. stack <= 40bb

Facing 4-bet:
  Fold / Call / Jam
```

### Postflop action menu
```
Check
Bet 25% / 50% / 75% / 125% / Jam
Raise: 3x vs small bet, 2.5x vs medium/large, jam always legal
```

### Rake profiles
- v1: no-rake research baseline (clean GTO)
- Later: 5% cap 1bb (NL100 typical), 5% cap 0.5bb (NL500 typical)

---

## Preflop Scenarios Required

Full 6-max coverage requires ~15-20 scenarios. Currently built: 6.

| Scenario | Status |
|----------|--------|
| BTN_open_BB_call | Built, SPR3 done, other SPR in progress |
| CO_open_BTN_call | Built, 0 solves done |
| CO_open_BB_call | Built, 0 solves done |
| UTG_open_BB_call | Built, 0 solves done |
| UTG_open_CO_call | Built, 0 solves done |
| SB_3bet_BTN_call | Built, 0 solves done |
| BTN_open_SB_call | Not built |
| SB_open_BB_call | Not built |
| CO_open_SB_call | Not built |
| UTG_open_BTN_call | Not built |
| UTG_open_CO_3bet_BTN_call | Not built |
| BB_squeeze_vs_BTN_CO | Not built |
| ... (squeeze / cold-call multiway) | Not built |

---

## Gap Analysis vs High-Stakes Target

| Component | Current state | High-stakes requirement | Gap |
|-----------|--------------|------------------------|-----|
| Preflop | 6 scenarios, 100bb, no rake | 15-20 scenarios, multi-stack, rake-adjusted | Large |
| Flop HU SRP | 99 clusters, spr_3 done (57/594 total) | All 594 spots done | Medium |
| Flop 3bet/4bet pots | Not built | Required | Large |
| Turn/river | Equity heuristic + rules | Full trees from flop action path | Large |
| 3-way flop | Check/fold fallback | High-frequency cluster library | Large |
| 3-way turn/river | Not built | Common branches | Very large |
| Bounded re-solver | Not built | Required for novel spots | Large |
| Exploitability | ~0.5% pot | <0.3% pot | Medium |
| Rake adjustment | None | Per-stake rake profile | Medium |

---

## System Architecture

### Services (runtime)

```
CoinPoker / Unibet game state
        │
        ▼
  state-gateway          ← ingest + validate + normalize action history
        │
        ▼
  canonicalizer          ← position/stack/pot normalization, spot-family ID,
                            board-family mapping, tier selection (A/B/C)
        │
   ┌────┴────┐
   │         │
   ▼         ▼
library    resolve        ← mmap artifact lookup (Tier A)
service    service        ← bounded CFR re-solve seeded from library (Tier B)
   │         │            ← nearest abstraction fallback (Tier C)
   └────┬────┘
        │
        ▼
  advisor-service         ← merge results, compute confidence, enforce 5s SLA
        │
        ▼
     overlay              ← single-word action, color confidence, EV display
```

### Infrastructure
- **Solver precompute:** cloud VM (dedicated, hard RAM cap). Never run on Proxmox.
- **Runtime:** Windows laptop, mmap artifact loading, ~20-50MB resident per table
- **Artifact storage:** versioned binary blobs + JSON manifest (see format below)

---

## Artifact Format

### Implemented: strategy.bin v1 (EXACT path)

Binary format used by the Rust runtime-advisor. Deterministic, flat, little-endian.

```
Header (16 bytes):
  [0..4]   magic: b"STRT"
  [4..8]   version: u32 = 1
  [8..10]  n_actions: u16
  [10..12] n_hand_buckets: u16 = 12
  [12..16] reserved: [0; 4]

Action table (2 * n_actions bytes):
  [(kind_wire: u8, size_wire: u8)] per action

Strategy matrix (4 * 12 * n_actions bytes):
  f32[12][n_actions] row-major (one row per HandBucket)

Total: 16 + 50*N bytes (N = n_actions; typical 6-action spot = 316 bytes)
```

Artifact tree layout:
```
artifacts/solver/{artifact_key}/
  strategy.bin              ← binary strategy (format above)
  strategy.manifest.json    ← SHA-256 checksum, version, dimensions

artifacts/emergency/
  emergency_range_prior.bin ← 4,320 f64 entries (34.5 KB)
  emergency_range_prior.manifest.json
```

Artifact key format: `{pot_class}/{street}/{agg}_vs_{hero}_{n}way/{stack}/{board}/{rake}/mv{version}`

### Future: fp16 migration

Current f32 format is fine for Phase 1. Migrate to fp16 in Phase 2 when the library is large enough that file size matters.

---

## Runtime Output Schema

```json
{
  "tier": "A",
  "spot_family": "btn_vs_bb_srp_flop",
  "action_mix": [
    {"action": "check",   "freq": 0.41, "ev": 1.12},
    {"action": "bet_25",  "freq": 0.37, "ev": 1.08},
    {"action": "bet_75",  "freq": 0.22, "ev": 1.05}
  ],
  "top_action": "check",
  "confidence": 0.87,
  "latency_ms": 120
}
```

---

## Latency Budget (4 concurrent tables)

| Stage | Budget |
|-------|--------|
| Game state capture + parse | 50ms |
| Canonicalization | 25ms |
| Library lookup (Tier A) | 25–100ms |
| Bounded re-solve (Tier B) | 500–3500ms |
| Response + overlay update | 50ms |
| **Hard ceiling** | **5000ms** |

Target p95: preflop <150ms, cached postflop <750ms, re-solve path <3000ms.

---

## Phased Roadmap

### Phase 1 — NL2 to NL25 (current)

**Goal:** Working advisor, real edge, build bankroll.

What we finish:
1. Precompute remaining 3,507 solver spots on cloud VM (6 scenarios × 99 clusters × all SPR)
2. Wire `solver/lookup.py` into `PostflopEngine` — replace JS binary mmap on the flop
3. Build preflop scenario detector (`vision/preflop_scenario.py`) — who raised from where → scenario key
4. Wire flop action history into turn/river tree navigation (already supported in `lookup.py`)
5. Validate on captured hands before going live

Advisor at end of Phase 1: Tier A on HU SRP flop, Tier C (heuristic) everywhere else.

**Acceptance criteria:**
- All 6 preflop scenarios × 99 clusters × 6 SPR done
- Solver lookup replaces heuristic on flop for known scenarios
- Validation: preflop pass rate >85%, flop lookup hit rate >70%
- Preflight gate (`check_ready_for_live.py`) returns GO

---

### Phase 2 — NL25 to NL100

**Goal:** Full street coverage for HU pots. No more heuristic turn/river.

What we build:
- Full turn/river trees (branch from flop action path, not just 3 levels deep)
- 3-bet pot postflop coverage (own scenario class, own solve pass)
- Additional preflop scenarios (BTN vs SB, SB vs BB, squeeze spots)
- 40bb and 60bb stack packs
- Migrate artifacts to fp16 binary format (runtime memory drops 4x)

Advisor at end of Phase 2: Tier A/B on all HU postflop streets, Tier C on 3-way.

---

### Phase 3 — NL100 to NL200

**Goal:** 3-way postflop coverage. Rake-adjusted ranges. Sub-0.3% exploitability.

What we build:
- 3-way flop library for high-frequency spot classes
- Rake-adjusted solving for NL100 and NL200 profiles (5% cap 1bb)
- 150bb stack pack
- 4-bet pot postflop trees
- Bounded live re-solver (Tier B) for spots not in library
- Confidence model calibrated against real outcomes

Advisor at end of Phase 3: Tier A on all HU streets, Tier B on 3-way, explicit Tier C flags everywhere else.

---

### Phase 4 — NL200+

**Goal:** High-stakes ready. No systematic exploitable gaps.

What we build:
- 3-way turn/river coverage for common branches
- Smarter nearest-spot interpolation (distance metric, not just cluster key)
- Learned value/policy approximation layer on top of solved datasets
- Better multiway support
- Full confidence calibration

---

## What's Already Built

| Component | File | Status |
|-----------|------|--------|
| Rust solver (b-inary/postflop-solver) | `solver/postflop-solver/` | Working |
| JSON CLI bridge | `solver/solver_bridge.py` | Working |
| 99 board clusters | `solver/board_clusters.py` | Working |
| 6 preflop range sets | `solver/preflop_ranges.py` | Working |
| Precompute pipeline | `solver/precompute.py` | Working, needs cloud |
| Runtime lookup engine | `solver/lookup.py` | Working |
| CoinPoker game-state adapter | `vision/coinpoker_adapter.py` | Live |
| CoinPoker runner + overlay | `vision/coinpoker_runner.py` | Live |
| Preflop chart (all positions) | `vision/preflop_chart.py` | Live |
| Postflop engine (heuristic) | `vision/strategy/postflop_engine.py` | Live, to be replaced |
| Opponent model | `vision/strategy/opponent_model.py` | Live |
| Danger filter suite (5 filters) | `AdvisorStateMachine` | Live |
| HUD stats sniffer | `tools/coinpoker_stats_sniffer.py` | Live |
| Pre-flight validation gate | `tools/check_ready_for_live.py` | Live |
| **Rust runtime-advisor (EXACT+EMERGENCY)** | `rust/crates/runtime-advisor/` | **Working, 115 tests** |
| **Rust engine-core (hand eval)** | `rust/crates/engine-core/` | **Working, 27 tests** |
| **Rust artifact-store (integrity)** | `rust/crates/artifact-store/` | **Working, 14 tests** |
| **advisor-cli (JSON bridge)** | `rust/crates/advisor-cli/` | **Working, release binary** |
| **Strategy artifact builder** | `python/scripts/build_exact_artifact.py` | **Working, 28-artifact corpus** |
| **Mode router (Python wrapper)** | `python/advisor_service/mode_router.py` | **Working** |
| **Baseline replay runner** | `python/eval_lab/baseline_replay_runner.py` | **Working** |
| **Hit-rate report** | `python/eval_lab/hit_rate_report.py` | **Working** |
| **Latency benchmark** | `python/eval_lab/latency_bench.py` | **Working** |

---

## Immediate Blockers

1. **Precompute:** 3,507 remaining solver spots need a cloud VM with real memory caps. Never use Proxmox — it crashed the edge-lab VM twice.
2. **Scenario detector:** `vision/preflop_scenario.py` doesn't exist yet. Blocks wiring lookup into PostflopEngine.
3. **Turn/river trees:** Current solver output is 3 levels deep on the flop only. Full street coverage requires deeper trees — different solve configuration.

---

## What NOT To Build (revised)

1. **Full 4-street 6-max CFR from scratch.** The Rust solver already exists. Use it.
2. **Monte Carlo tree search at runtime.** Latency budget is 5s. MCTS is too slow.
3. **NN policy without solved data.** Build the solver library first, distil to NN later if needed.
4. **Check/fold bias for multiway as a permanent strategy.** Fine for Phase 1 micros, exploitable at NL100+. Must build proper 3-way coverage by Phase 3.
5. **Any solver workload on the Proxmox host.** Hard rule. Cloud VMs only, with MemoryMax cgroup cap.
