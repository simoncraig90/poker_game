# Architecture

## System Overview

```
PokerStars (browser)
    | screen capture (mss)
    v
Vision Pipeline (Python)
    |-- YOLO detection (4.3ms) --> find cards, panels, chips, buttons
    |-- Card CNN (4.7ms) --> identify rank + suit
    |-- Hand strength net (<1ms) --> win probability
    +-- OCR fallback (3000ms) --> text reading (legacy)
    | WebSocket bridge
    v
Poker-Lab Engine (Node.js)
    |-- Deterministic game engine (12 modules)
    |-- Event-sourced persistence (JSONL)
    |-- Session management & recovery
    +-- Hand history import (PokerStars .txt)
    | WebSocket
    v
Browser Client (HTML/JS)
    |-- Live table rendering
    |-- Study tab (replay, quiz, blind review)
    +-- Session browser & archive

Self-Play System
    |-- TAG strategy (42k hands/sec)
    |-- Neural bot (112 hands/sec, imitation-trained)
    |-- Fast self-play (Python<->Node IPC)
    +-- RL training loop (REINFORCE + imitation)
```

## Component Relationships

### Vision Pipeline to Engine

The vision pipeline (`vision/live.py`) captures the PokerStars screen, runs YOLO detection and card CNN, then sends the detected game state to the engine via WebSocket (`vision/bridge.py`). The engine processes the state against its game model and returns advice. Total latency is approximately 10ms.

### Self-Play System

Two modes of self-play:

1. **TAG self-play** (`scripts/self-play.js`) — runs entirely in Node.js at 42,000 hands/sec. Uses hardcoded tight-aggressive strategy. Generates training data via `scripts/generate-rl-data.js`.

2. **Neural self-play** (`vision/fast_selfplay.py`) — Python drives a neural policy network, communicating with the Node.js engine via stdin/stdout JSON IPC (`scripts/engine-worker.js`). Runs at 112 hands/sec.

### Data Flow for Training

```
Self-play (42k hands/sec)
    --> generate-rl-data.js (541K decision points)
    --> train_policy.py (imitation learning)
    --> policy_net.pt (60K params)
    --> fast_selfplay.py (neural bot, 112 hands/sec)
    --> (future: REINFORCE fine-tuning)
```

### Data Flow for Live Play

```
PokerStars window
    --> capture.py (mss screen grab)
    --> yolo_detect.py (bounding boxes, 4.3ms)
    --> card_cnn.py (rank + suit, 4.7ms)
    --> hand_strength.py (win probability, <1ms)
    --> bridge.py (WebSocket to engine)
    --> ws-server.js (game state + advice)
    --> browser client (display)
```

## Key Files

| Layer | Entry Point | Purpose |
|---|---|---|
| Server | `src/server/ws-server.js` | WebSocket server (port 9100) |
| Engine | `src/engine/` | 12 modules: deck, dealer, evaluator, betting, etc. |
| Persistence | `src/engine/` | JSONL event sourcing, session recovery |
| Import | `src/import/` | PokerStars hand history parser |
| Client | `client/` | Browser UI with study tools |
| Vision | `vision/live.py` | Live reader entry point |
| YOLO | `vision/yolo_detect.py` | Object detection inference |
| Card ID | `vision/card_cnn.py` | Card classification |
| Hand Strength | `vision/hand_strength.py` | Win probability estimation |
| Bridge | `vision/bridge.py` | Vision-to-engine WebSocket |
| Self-Play | `scripts/self-play.js` | TAG strategy self-play |
| Neural Play | `vision/fast_selfplay.py` | Neural bot self-play |
| IPC Worker | `scripts/engine-worker.js` | Node.js engine over stdin/stdout |
