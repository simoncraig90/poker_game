# CLAUDE.md — Persistent Context for Claude Code

## Project Purpose

Poker research and practice platform. Combines a deterministic Node.js game engine with a Python vision/ML pipeline that reads live PokerStars tables and provides real-time advice. Includes self-play AI for strategy research and study tools for hand review.

## Current State

- Engine phases 1-8 complete (692 tests passing)
- Vision pipeline working: YOLO 98.8% mAP50, card identification via screen-card template matching
- **6-max CFR strategy trained**: 3M info sets, position-aware (BTN/SB/BB/UTG/MP/CO), 10 buckets
- **Real-time subgame solver**: Pluribus-style, 650-1000 CFR iterations per decision in 2s
- **Player profiling**: population-based clustering (FISH/NIT/TAG/LAG/WHALE) with strategy adjustments
- Heads-up CFR also available: 860k info sets, 50 buckets, position-aware (IP/OOP)
- Self-play at 42k hands/sec (TAG strategy, 6-max)
- BB/hour tracking in bot-players, browser client, and advisor overlay
- Kanban: 35 tasks across poker, crypto, equity, edge-lab projects

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
| `vision/models/cfr_strategy.json` | Active CFR strategy (6-max, 3M entries, 258MB) |
| `vision/models/cfr_strategy_sixmax.json` | 6-max CFR strategy (same as above) |
| `vision/models/cfr_strategy_full.json` | Heads-up CFR strategy (860k entries, 50 buckets) |
| `vision/data/` | Training data (rl_training_data.jsonl, hand_strength_data.jsonl) |
| `vision/templates/screen_cards/` | 52 card templates captured from lab browser (hearts/clubs fixed) |
| `vision/captures/training/` | 621 labeled frames from live PokerStars sessions |
| `vision/dataset/` | YOLO dataset (527 train, 94 val) |
| `vision/runs/poker_lab/weights/best.pt` | Trained YOLO model |
| `hands/poker_stars/` | Imported PokerStars hand histories (200+ hands) |
| `scripts/cfr/` | CFR engine, game models (heads-up + 6-max), solver, training |
| `scripts/player-profiler.js` | Population-based opponent classification |
| `scripts/opponent-profiles.json` | 67 PS opponent profiles with VPIP/PFR/AF |

## CFR Architecture

- **6-max game model** (`scripts/cfr/sixmax-holdem.js`): 6 players, 10 buckets, 2 bet sizes (half-pot + all-in), 2 max raises/street
- **Heads-up game model** (`scripts/cfr/full-holdem.js`): 2 players, 50 buckets, 3 bet sizes, 3 max raises/street
- **CFR trainer** (`scripts/cfr/cfr.js`): N-player MCCFR with external sampling
- **Real-time solver** (`scripts/cfr/cfr-solver.js`): JSON-line IPC, samples opponent hands, warm start
- **Info set key format (6-max)**: `STREET:bucket:stackBucket:POS:numPlayers:actionHistory`
- **Info set key format (HU)**: `STREET:bucket:stackBucket:POS:actionHistory`

## Server

```bash
node src/server/ws-server.js
# WebSocket server on port 9100
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

# Train 6-max CFR (10 buckets, ~6 min on desktop)
node --max-old-space-size=10240 scripts/cfr/train-cfr.js --game sixmax --iterations 100000 --threads 1

# Train heads-up CFR (50 buckets, ~25 min)
node --max-old-space-size=10240 scripts/cfr/train-cfr.js --game full --iterations 500000 --threads 1

# Player profiling
node scripts/player-profiler.js

# Bot players with CFR strategy
node scripts/bot-players.js

# Collect training frames from PokerStars
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/collect.py

# Generate RL training data
node scripts/generate-rl-data.js
```

## Hardware Constraints

- **Desktop**: 32GB RAM, RTX 4070 Ti Super (16GB VRAM), 20 CPU cores
- 6-max CFR with 10 buckets fits in 10GB (~3M info sets)
- 6-max CFR with 50 buckets exceeds V8 Map limit — needs mini PC with more RAM
- Multi-threaded CFR training uses ~1.5GB per worker thread
