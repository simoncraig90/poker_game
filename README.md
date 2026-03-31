# Poker Research Platform

A poker research and practice platform combining a deterministic game engine, live PokerStars screen reading via computer vision, self-play AI, and interactive study tools.

## Architecture

The system has four layers:

1. **Deterministic Engine (Node.js)** — 12-module game engine handling all poker rules, betting logic, hand evaluation, and game flow. Source of truth for all game state. 692 tests.
2. **Persistence & Event Sourcing** — JSONL event log with session management, crash recovery, replay, and PokerStars hand history import.
3. **Browser Client (HTML/JS)** — Live table rendering, study tab with replay/quiz/blind review, and session browser with archive.
4. **Vision/ML Pipeline (Python)** — Screen capture, YOLO object detection, card CNN, hand strength evaluation, and WebSocket bridge for real-time advice.

## Key Capabilities

| Component | Performance |
|---|---|
| YOLO card/chip detection | 4.3ms per frame (GPU) |
| Card CNN identification | 4.7ms per card |
| Full vision pipeline | ~10ms total (screen to advice) |
| Self-play (TAG strategy) | 42,000 hands/sec (6-max) |
| Neural bot self-play | 112 hands/sec |
| OCR fallback | ~3,000ms (legacy) |

The vision pipeline captures the PokerStars window, detects cards/chips/buttons with YOLOv8, identifies card rank+suit with a CNN classifier, evaluates hand strength with a neural net, and sends game state to the engine over WebSocket for real-time advice.

## Tech Stack

- **Engine:** Node.js
- **Vision/ML:** Python 3.12, PyTorch (CUDA), YOLOv8 (ultralytics)
- **GPU:** RTX 4070 Ti Super
- **Client:** HTML/JS, WebSocket
- **Persistence:** JSONL event sourcing

## Quick Start

### Run the server

```bash
node src/server/ws-server.js
# Serves on port 9100
```

### Run the vision pipeline (live PokerStars reader)

```bash
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/live.py --bridge
```

### Self-play (TAG strategy, 42k hands/sec)

```bash
node scripts/self-play.js
```

### Neural bot self-play (112 hands/sec)

```bash
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/fast_selfplay.py
```

### RL training data generation

```bash
node scripts/generate-rl-data.js
```

### Train policy network

```bash
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe vision/train_policy.py
```

## Project Structure

```
src/                    Node.js game engine and server
  engine/               Core poker engine (12 modules)
  server/               WebSocket server (port 9100)
  import/               PokerStars hand history importer
  api/                  REST/WebSocket API layer
  cli/                  CLI tools

vision/                 Python vision/ML pipeline (19 files)
  capture.py            Screen capture and felt detection
  collect.py            Continuous frame collector
  detect.py             OCR-based table analysis (legacy)
  yolo_label.py         Auto-labeling for YOLO training
  yolo_train.py         YOLOv8 training
  yolo_detect.py        YOLO inference module
  card_id.py            Template-based card identification
  card_cnn.py           CNN card classifier
  hand_strength.py      Neural hand strength evaluator
  policy_net.py         Policy network for RL bot
  train_policy.py       Imitation learning trainer
  fast_selfplay.py      Neural self-play (Python↔Node IPC)
  bridge.py             WebSocket bridge to engine
  live.py               Real-time PokerStars reader
  models/               Trained model weights
  data/                 Training data (JSONL)
  templates/            Card rank/suit templates
  captures/training/    621 labeled frames
  card_crops/           1,405 labeled card images
  dataset/              YOLO dataset (527 train, 94 val)

scripts/                Automation and self-play scripts
  self-play.js          TAG strategy self-play
  generate-rl-data.js   Decision point recording
  engine-worker.js      stdin/stdout JSON worker for IPC
  self-play-nn.js       HTTP neural self-play (legacy)

client/                 Browser client (HTML/JS)
data/                   Game data and configuration
hands/                  Imported hand histories
  poker_stars/          PokerStars .txt imports (200+ hands)
test/                   Test suite (692 tests)
```
