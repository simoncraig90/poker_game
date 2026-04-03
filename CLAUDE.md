# CLAUDE.md — Persistent Context for Claude Code

## Project Purpose

Poker research platform with two goals: (1) real-time play assistance via CFR strategy + vision pipeline, and (2) **anti-bot detection research** — building realistic bots and detection systems to test against each other. Combines a deterministic Node.js game engine with a Python vision/ML pipeline, PokerStars-like browser client (91.8% visual match), screen-reading bots, and multi-dimensional detection/humanness scoring.

## Current State

- Engine phases 1-8 complete (692 tests passing)
- Vision pipeline working: YOLO 98.8% mAP50, card identification via screen-card template matching
- **6-max CFR strategies trained**:
  - 10-bucket: 3M info sets, position-aware (BTN/SB/BB/UTG/MP/CO), 258MB
  - **50-bucket: 1.3M entries, 10M iterations, 138MB** (trained on Proxmox, 8 threads)
- **Real-time subgame solver**: Pluribus-style, 650-1000 CFR iterations per decision in 2s
- **Player profiling**: population-based clustering (FISH/NIT/TAG/LAG/WHALE) with strategy adjustments
- Heads-up CFR also available: 860k info sets, 50 buckets, position-aware (IP/OOP)
- **Bot evaluation framework**: round-robin with ELO, 95% CI, 20k hands (scripts/eval-bots.js)
- **Anti-bot detection system**: feature extraction for bet sizing precision, session stability, tilt resistance, bot scoring (scripts/bot-detector.js)
- **PS-like browser client**: 91.8% visual match to PokerStars, bet sizing slider, turn timer, auto-actions
- **Multi-table support**: server routes by `?table=N`, TableManager with per-table sessions/auto-deal
- **Screen-reading bot**: pure pixel detection + click automation, plays multiple tables simultaneously
- **Humanness scoring**: 4-dimension framework (timing/motor/behavioral/strategic), 0-100 scale
- **Advisor overlay**: supports `--table N` for multi-table, runs independently per table with YOLO + solver
- Self-play at 42k hands/sec (TAG strategy, 6-max)
- BB/hour tracking in bot-players, browser client, and advisor overlay

## Key Conventions

- **Engine is JavaScript** — all game logic lives in `src/`. Node.js engine is the source of truth for poker rules.
- **Vision/ML is Python** — all ML code lives in `vision/`. 19 Python files, ~5,500 lines.
- **Scripts** — `scripts/` has Node.js automation (self-play, data generation, CFR, IPC workers).
- **Tests** — `test/` has 692 engine tests. Run with `npm test`.

## Python Environment

- **Use Python 3.12 for ALL ML work:**
  ```
  C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe
  ```
- **Python 3.14 is the default `python` but has NO CUDA support.** Never use bare `python` for ML tasks.
- **Background command buffering:** Python 3.12 background commands buffer output. Use `-u` flag for unbuffered output, or run inline.

## Important Paths

| Path | Contents |
|---|---|
| `vision/models/` | Trained model weights and CFR strategies |
| `vision/models/cfr_strategy.json` | 6-max CFR strategy (10 buckets, 3M entries, 258MB) |
| `vision/models/cfr_strategy_50bucket.json` | **50-bucket 6-max CFR** (1.3M entries, 10M iters, 138MB) |
| `vision/models/cfr_strategy_sixmax.json` | 6-max CFR strategy (copy) |
| `vision/models/cfr_strategy_full.json` | Heads-up CFR strategy (860k entries, 50 buckets) |
| `vision/data/` | Training data (rl_training_data.jsonl, hand_strength_data.jsonl) |
| `vision/data/eval_results.json` | Latest bot evaluation results (rankings, ELO, CI) |
| `vision/data/detection_profiles.json` | Bot detection feature profiles |
| `vision/templates/screen_cards/` | 52 card templates captured from lab browser (hearts/clubs fixed) |
| `vision/captures/training/` | 621 labeled frames from live PokerStars sessions |
| `vision/dataset/` | YOLO dataset (527 train, 94 val) |
| `vision/runs/poker_lab/weights/best.pt` | Trained YOLO model |
| `hands/poker_stars/` | Imported PokerStars hand histories (200+ hands) |
| `scripts/cfr/` | CFR engine, game models (heads-up + 6-max), solver, training |
| `scripts/eval-bots.js` | Round-robin bot evaluation with ELO and 95% CI |
| `scripts/bot-detector.js` | Anti-bot detection feature extraction and scoring |
| `scripts/player-profiler.js` | Population-based opponent classification |
| `scripts/opponent-profiles.json` | 67 PS opponent profiles with VPIP/PFR/AF |
| `vision/screen_bot.py` | Screen-reading bot: pure pixel detection + click (single table) |
| `vision/multi_table_bot.py` | Multi-table screen bot: plays N tables simultaneously |
| `vision/client_bot.py` | Windows bot: OCR + click automation + PS frame comparison |
| `scripts/humanness-score.js` | Humanness scoring framework (timing/motor/behavioral/strategic) |
| `scripts/tile-tables.py` | Tile poker browser windows side-by-side |
| `scripts/launch-tables.js` | Open N table browser windows in grid layout |
| `src/server/table-manager.js` | Multi-table session manager (per-table state/clients/auto-deal) |

## CFR Architecture

- **6-max game model** (`scripts/cfr/sixmax-holdem.js`): 6 players, 10 buckets (desktop) or 50 buckets (Proxmox), 2 bet sizes (half-pot + all-in), 2 max raises/street
- **Heads-up game model** (`scripts/cfr/full-holdem.js`): 2 players, 50 buckets, 3 bet sizes, 3 max raises/street
- **CFR trainer** (`scripts/cfr/cfr.js`): N-player MCCFR with external sampling, multi-threaded (8 threads on Proxmox)
- **CFR bot adapter** (`scripts/cfr/cfr-bot.js`): Maps live game state to info set keys, NUM_BUCKETS=50 (matches 50-bucket strategy)
- **Real-time solver** (`scripts/cfr/cfr-solver.js`): JSON-line IPC, samples opponent hands, warm start
- **Info set key format (6-max, 10-bucket)**: `STREET:bucket:stackBucket:POS:numPlayers:actionHistory`
- **Info set key format (6-max, 50-bucket)**: `STREET:bucket:stackBucket:IP|OOP:actionHistory`
- **Info set key format (HU)**: `STREET:bucket:stackBucket:POS:actionHistory`

## Server

```bash
node src/server/ws-server.js
# WebSocket server on port 9100
# Multi-table: clients connect to ws://localhost:9100?table=N
# Server-side auto-deal (3s after hand end, like PokerStars)
```

## User

- **Name:** Simon
- **PokerStars username:** Skurj_poker
- **Stakes:** $0.05/$0.10 6-max NL Hold'em

## Key Commands

```bash
# Self-play (TAG strategy, 42k hands/sec)
node scripts/self-play.js

# Real-time advisor with subgame solver
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/advisor.py
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/advisor.py --debug

# Train 6-max CFR — 10 buckets on desktop (~6 min)
node --max-old-space-size=10240 scripts/cfr/train-cfr.js --game sixmax --iterations 100000 --threads 1

# Train 6-max CFR — 50 buckets on Proxmox (~18 min with 8 threads)
ssh root@proxmox "cd /opt/cfr-training && node --max-old-space-size=40960 scripts/cfr/train-cfr.js --game sixmax --iterations 2000000 --threads 8"

# Train heads-up CFR (50 buckets, ~25 min)
node --max-old-space-size=10240 scripts/cfr/train-cfr.js --game full --iterations 500000 --threads 1

# Bot evaluation (round-robin with ELO + CI)
node --max-old-space-size=4096 scripts/eval-bots.js --hands 20000 --strategies tag,fish,lag,nit,cfr50

# Anti-bot detection profiling
node --max-old-space-size=4096 scripts/bot-detector.js --hands 5000 --strategies tag,fish,lag,cfr50

# Generate RL training data (TAG or CFR strategy)
node scripts/generate-rl-data.js --strategy tag --hands 100000
node --max-old-space-size=4096 scripts/generate-rl-data.js --strategy cfr --hands 100000

# Screen-reading bot (single table, pure pixel reading)
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/screen_bot.py --hands 20

# Multi-table bot (plays all visible tables simultaneously)
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/multi_table_bot.py --max-actions 30

# Advisor overlay (per-table targeting)
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/advisor.py --table 1 --debug
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/advisor.py --table 2 --debug

# Multi-table setup
node scripts/launch-tables.js --tables 2
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe scripts/tile-tables.py
node scripts/bot-players.js --table 1
node scripts/bot-players.js --table 2

# Humanness scoring
node scripts/humanness-score.js

# Frame comparison (lab client vs PS captures)
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/client_bot.py --compare

# Player profiling
node scripts/player-profiler.js

# Bot players with CFR strategy
node scripts/bot-players.js

# Collect training frames from PokerStars
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/collect.py
```

## Hardware

- **Desktop**: 32GB RAM, RTX 4070 Ti Super (16GB VRAM), 20 CPU cores
  - 6-max CFR with 10 buckets fits in 10GB (~3M info sets)
  - 50-bucket CFR exceeds V8 Map limit on desktop — use Proxmox
- **Proxmox** (`ssh root@proxmox`): AMD Ryzen AI 9 HX 370, **92GB RAM**, 12c/24t, 1.9TB NVMe
  - SSH key auth configured for root
  - CFR training environment at `/opt/cfr-training/` (Node.js + engine + CFR scripts)
  - 50-bucket 6-max CFR trains in ~18 min with 8 threads (1,800 iter/s)
  - Uses ~15GB RSS for 10M iterations, 52GB total with VMs running
  - CPU: 33% utilization with 8 threads (nice -n 10), VMs unaffected
  - Running VMs: Torrent, Scripts, edge-lab, Data, CollectorA
- Multi-threaded CFR training uses ~1.5GB per worker thread
