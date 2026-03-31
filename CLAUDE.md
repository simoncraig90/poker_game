# CLAUDE.md — Persistent Context for Claude Code

## Project Purpose

Poker research and practice platform. Combines a deterministic Node.js game engine with a Python vision/ML pipeline that reads live PokerStars tables and provides real-time advice. Includes self-play AI for strategy research and study tools for hand review.

## Current State

- Engine phases 1-8 complete (692 tests passing)
- Vision pipeline working: YOLO 98.8% mAP50, Card CNN 98.9% accuracy, hand strength net trained on 4M rows
- Self-play at 42k hands/sec (TAG strategy, 6-max)
- Neural bot imitating TAG at 112 hands/sec (VPIP 51%, PFR 8%)
- Kanban: 26 tasks across poker, crypto, equity, edge-lab projects

## Key Conventions

- **Engine is JavaScript** — all game logic lives in `src/`. Node.js engine is the source of truth for poker rules.
- **Vision/ML is Python** — all ML code lives in `vision/`. 19 Python files, ~4,700 lines.
- **Scripts** — `scripts/` has Node.js automation (self-play, data generation, IPC workers).
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
| `vision/models/` | Trained model weights (card_cnn.pt, hand_strength.pt, policy_net.pt) |
| `vision/data/` | Training data (rl_training_data.jsonl, hand_strength_data.jsonl) |
| `vision/templates/` | Card rank/suit templates for template matching |
| `vision/captures/training/` | 621 labeled frames from live PokerStars sessions |
| `vision/card_crops/` | 1,405 labeled card images |
| `vision/dataset/` | YOLO dataset (527 train, 94 val) |
| `vision/runs/poker/weights/best.pt` | Trained YOLO model (6.2MB) |
| `hands/poker_stars/` | Imported PokerStars hand histories (200+ hands) |

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

# Fast neural self-play (112 hands/sec)
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/fast_selfplay.py

# Collect training frames from PokerStars
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/collect.py

# YOLO detection on a frame
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/yolo_detect.py

# Live PokerStars reader with engine bridge
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/live.py --bridge

# Generate RL training data
node scripts/generate-rl-data.js

# Train policy network
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/train_policy.py
```
