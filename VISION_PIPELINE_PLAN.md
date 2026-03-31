# Vision/ML Pipeline — What Was Built

The vision/ML pipeline is 19 Python files (~4,700 lines) that reads live PokerStars tables, identifies cards and game state, evaluates hand strength, and bridges to the Node.js engine for real-time advice.

---

## Screen Capture & Detection

### vision/capture.py — Single-Shot Screen Capture
- Captures PokerStars window using `mss`
- Green felt detection to locate the table region
- Returns raw frame for downstream processing

### vision/collect.py — Continuous Frame Collector
- Captures frames at 0.5-second intervals
- Saves to `vision/captures/training/` for labeling
- 621 frames collected from live sessions

### vision/detect.py — OCR-Based Table Analysis (Legacy)
- Reads player names, stack sizes, pot, community cards, dealer button, action buttons via OCR
- ~3,000ms per frame — replaced by YOLO pipeline
- Kept as fallback

---

## YOLO Object Detection

### vision/yolo_label.py — Auto-Labeling
- Auto-labeled 621 frames with 7,323 labels across 8 classes
- Classes: `board_card`, `hero_card`, `card_back`, `player_panel`, `dealer_button`, `chip`, `pot_text`, `action_button`
- Output: YOLO-format label files for training

### vision/yolo_train.py — Model Training
- Trained YOLOv8n (nano) on the labeled dataset
- Dataset: 527 train images, 94 validation images
- Results: mAP50 = 0.988, 4.3ms per frame on GPU
- Model saved to `vision/runs/poker/weights/best.pt` (6.2MB)

### vision/yolo_detect.py — Inference Module
- Loads trained YOLO model for real-time detection
- Returns bounding boxes with class labels and confidence scores
- Replaces the OCR pipeline with 700x speedup (3,000ms to 4.3ms)

---

## Card Identification

### vision/card_id.py — Template Matching
- Matches detected card crops against rank and suit templates
- Rank identification uses structural analysis (13 ranks, red and black variants)
- Suit identification uses solidity analysis (ratio of contour area to convex hull)
- Templates stored in `vision/templates/` (rank templates, suit templates, hero-specific templates)

### vision/card_cnn.py — CNN Classifier
- Convolutional neural network for card rank+suit classification
- 52-class output (13 ranks x 4 suits)
- 98.9% accuracy on validation set
- 4.7ms per card on GPU
- Model: `vision/models/card_cnn.pt` (4.1MB)
- Training data: `vision/card_crops/` — 1,405 labeled card images

### vision/gen_card_data.py — Card Training Data Generation
- Generates synthetic card crop data for CNN training
- Augments real crops from captured frames

### Cross-Validation
- Card identification results cross-checked 100% against PokerStars hand history files
- `vision/cross_check.py` — automated comparison of vision output vs hand history

---

## Hand Strength Evaluation

### vision/hand_strength.py — Neural Hand Strength Net
- Neural network estimating win probability given hole cards, board, and number of opponents
- Trained on 4M rows generated from 1M simulated hands
- Model: `vision/models/hand_strength.pt` (534KB)
- Training data: `vision/data/hand_strength_data.jsonl`

### Calibration Examples
| Hand | Opponents | Win Probability |
|---|---|---|
| AA preflop | 1 | 0.82 |
| AA preflop | 5 | 0.21 |

---

## Self-Play & Reinforcement Learning

### scripts/self-play.js — TAG Strategy Self-Play
- Tight-aggressive strategy bots playing 6-max tables
- 42,000 hands/sec throughput
- Used for baseline strategy generation and data collection

### scripts/generate-rl-data.js — RL Data Generation
- Records every decision point during self-play
- Captures full state features (position, stack, pot, cards, action history)
- Output: `vision/data/rl_training_data.jsonl` — 541K decision points

### vision/policy_net.py — Policy Network
- Neural network for action selection (fold/call/raise)
- Architecture: card embeddings + fully connected layers
- 60K parameters
- Model: `vision/models/policy_net.pt` (246KB)

### vision/train_policy.py — Imitation Learning
- Trains policy network on TAG self-play data
- 100% validation accuracy (imitating TAG decisions)
- Supervised learning as pre-training before RL fine-tuning

### vision/fast_selfplay.py — In-Process Neural Self-Play
- Python process communicates with Node.js engine via stdin/stdout JSON IPC
- Uses `scripts/engine-worker.js` as the Node.js subprocess
- 112 hands/sec — 100x faster than legacy HTTP approach
- Bot stats: VPIP 51%, PFR 8%, highest win rate at 6-max table

### scripts/engine-worker.js — IPC Engine Worker
- Exposes the Node.js poker engine over stdin/stdout as JSON messages
- Designed for fast Python-Node communication without HTTP overhead

### scripts/self-play-nn.js — HTTP Neural Self-Play (Legacy)
- Original neural self-play using HTTP requests to Python inference server
- ~1 hand/sec — replaced by fast_selfplay.py

### vision/inference_server.py — HTTP Inference Server (Legacy)
- Flask server exposing policy network for HTTP-based self-play
- Replaced by stdin/stdout IPC in fast_selfplay.py

### vision/train_bot.py — Bot Training Utilities
- Additional training scripts and utilities for bot development

---

## WebSocket Bridge & Live Reader

### vision/bridge.py — WebSocket Bridge
- Connects the vision pipeline output to the poker-lab engine
- Sends detected game state (cards, stacks, pot, actions) over WebSocket
- Engine returns advice (fold/call/raise with sizing)

### vision/live.py — Real-Time PokerStars Reader
- Continuous screen reading of live PokerStars tables
- `--bridge` flag enables WebSocket connection to engine for real-time advice
- Full pipeline latency: ~10ms (screen capture to advice)

### Pipeline Flow
```
Screen capture (mss)
    → YOLO detection (4.3ms) — find cards, panels, chips, buttons
    → Card CNN (4.7ms) — identify rank + suit
    → Hand strength net (<1ms) — win probability
    → WebSocket bridge
    → Engine advice
    ≈ 10ms total
```

---

## Batch Analysis

### vision/batch_analyze.py — Batch Frame Analysis
- Processes multiple captured frames through the full pipeline
- Used for validation and performance benchmarking

---

## Data Summary

| Dataset | Size | Location |
|---|---|---|
| Training frames | 621 frames | `vision/captures/training/` |
| Card crops | 1,405 images | `vision/card_crops/` |
| YOLO dataset | 527 train, 94 val | `vision/dataset/` |
| RL decision points | 541K rows | `vision/data/rl_training_data.jsonl` |
| Hand strength data | 4M rows | `vision/data/hand_strength_data.jsonl` |
| Hand histories | 200+ hands | `hands/poker_stars/` |

## Trained Models

| Model | File | Size |
|---|---|---|
| YOLOv8n (poker) | `vision/runs/poker/weights/best.pt` | 6.2MB |
| Card CNN | `vision/models/card_cnn.pt` | 4.1MB |
| Hand strength net | `vision/models/hand_strength.pt` | 534KB |
| Policy network | `vision/models/policy_net.pt` | 246KB |
