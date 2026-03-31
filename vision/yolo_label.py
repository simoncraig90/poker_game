"""
Auto-label training frames for YOLOv8 using existing detection pipeline.
Outputs labels in YOLO format: class_id cx cy w h (normalized 0-1).

Classes:
  0: board_card    — face-up card on the board
  1: hero_card     — face-up hero hole card
  2: card_back     — face-down card (opponent hole cards)
  3: player_panel  — player name+stack panel
  4: dealer_button — red dealer button
  5: chip          — bet chip with amount
  6: pot_text      — pot amount text
  7: action_button — fold/call/raise/check/bet button
"""

import cv2
import numpy as np
import os
import sys
import shutil
import random

sys.path.insert(0, os.path.dirname(__file__))
from detect import (
    read_text_regions, find_dollar_amounts, find_pot,
    find_player_names, find_dealer_button, find_cards_by_color,
    find_action_buttons,
)

# Class IDs
BOARD_CARD = 0
HERO_CARD = 1
CARD_BACK = 2
PLAYER_PANEL = 3
DEALER_BUTTON = 4
CHIP = 5
POT_TEXT = 6
ACTION_BUTTON = 7

CLASS_NAMES = [
    "board_card", "hero_card", "card_back", "player_panel",
    "dealer_button", "chip", "pot_text", "action_button",
]


def detect_card_backs(image):
    """
    Find red card backs (opponent hole cards) above player panels.
    Card backs are small red rectangles.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Red card backs: dark red
    r1 = cv2.inRange(hsv, np.array([0, 80, 60]), np.array([10, 255, 200]))
    r2 = cv2.inRange(hsv, np.array([160, 80, 60]), np.array([180, 255, 200]))
    mask = r1 | r2

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    backs = []
    h_img, w_img = image.shape[:2]
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        ratio = h / max(w, 1)

        # Card back: small rectangle, aspect ~1.3-2.0, not too big/small
        if 1.1 < ratio < 2.5 and 200 < area < 5000 and w > 8 and h > 15:
            # Must be in upper 70% of image (above hero position)
            if y < h_img * 0.7:
                backs.append({"x": x, "y": y, "w": w, "h": h})

    return backs


def to_yolo(x, y, w, h, img_w, img_h):
    """Convert pixel bbox to YOLO format (cx, cy, w, h) normalized 0-1."""
    cx = (x + w / 2) / img_w
    cy = (y + h / 2) / img_h
    nw = w / img_w
    nh = h / img_h
    # Clamp to 0-1
    cx = max(0, min(1, cx))
    cy = max(0, min(1, cy))
    nw = max(0, min(1, nw))
    nh = max(0, min(1, nh))
    return cx, cy, nw, nh


def label_frame(img_path):
    """Generate YOLO labels for a single frame."""
    img = cv2.imread(img_path)
    if img is None:
        return None

    h, w = img.shape[:2]
    labels = []

    # 1. Board cards
    cards = find_cards_by_color(img)
    for c in cards["board"]:
        cx, cy, nw, nh = to_yolo(c["x"], c["y"], c["w"], c["h"], w, h)
        labels.append(f"{BOARD_CARD} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # 2. Hero cards
    for c in cards["hero"]:
        cx, cy, nw, nh = to_yolo(c["x"], c["y"], c["w"], c["h"], w, h)
        labels.append(f"{HERO_CARD} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # 3. Card backs
    backs = detect_card_backs(img)
    for b in backs:
        cx, cy, nw, nh = to_yolo(b["x"], b["y"], b["w"], b["h"], w, h)
        labels.append(f"{CARD_BACK} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # 4. OCR-based detections
    texts = read_text_regions(img)
    amounts = find_dollar_amounts(texts)
    pot = find_pot(texts, h)
    players = find_player_names(texts, amounts, w, h)
    actions = find_action_buttons(texts)
    dealer = find_dealer_button(img)

    # Player panels (combined name + stack bbox)
    for p in players:
        np_ = p["name_pos"]
        sp_ = p["stack_pos"]
        # Merge name and stack into one panel bbox
        x1 = min(np_["x"], sp_["x"])
        y1 = min(np_["y"], sp_["y"])
        x2 = max(np_["x"] + np_["w"], sp_["x"] + sp_["w"])
        y2 = max(np_["y"] + np_["h"], sp_["y"] + sp_["h"])
        # Add small padding
        pad = 3
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        cx, cy, nw, nh = to_yolo(x1, y1, x2 - x1, y2 - y1, w, h)
        labels.append(f"{PLAYER_PANEL} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # Dealer button
    if dealer:
        cx, cy, nw, nh = to_yolo(dealer["x"], dealer["y"], dealer["w"], dealer["h"], w, h)
        labels.append(f"{DEALER_BUTTON} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # Pot text
    if pot:
        cx, cy, nw, nh = to_yolo(pot["x"], pot["y"], pot["w"], pot["h"], w, h)
        labels.append(f"{POT_TEXT} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # Action buttons
    for a in actions:
        # Actions from find_action_buttons have cx, cy but not w, h
        # Find the original text region
        for t in texts:
            if t["text"].lower().strip() == a["action"]:
                cx, cy, nw, nh = to_yolo(t["x"], t["y"], t["w"], t["h"], w, h)
                labels.append(f"{ACTION_BUTTON} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
                break

    return labels


def build_dataset(train_ratio=0.85):
    """Build YOLO dataset from all training frames."""
    training_dir = os.path.join(os.path.dirname(__file__), "captures", "training")
    dataset_dir = os.path.join(os.path.dirname(__file__), "dataset")

    # Create YOLO directory structure
    for split in ["train", "val"]:
        os.makedirs(os.path.join(dataset_dir, "images", split), exist_ok=True)
        os.makedirs(os.path.join(dataset_dir, "labels", split), exist_ok=True)

    frames = sorted([f for f in os.listdir(training_dir) if f.startswith("frame_") and f.endswith(".png")])
    random.seed(42)
    random.shuffle(frames)

    split_idx = int(len(frames) * train_ratio)
    train_frames = frames[:split_idx]
    val_frames = frames[split_idx:]

    total_labels = 0
    labeled_frames = 0

    for split, frame_list in [("train", train_frames), ("val", val_frames)]:
        for i, fname in enumerate(frame_list):
            src = os.path.join(training_dir, fname)
            labels = label_frame(src)

            if labels is None:
                continue

            # Copy image
            dst_img = os.path.join(dataset_dir, "images", split, fname)
            shutil.copy2(src, dst_img)

            # Write labels
            label_name = fname.replace(".png", ".txt")
            dst_label = os.path.join(dataset_dir, "labels", split, label_name)
            with open(dst_label, "w") as f:
                f.write("\n".join(labels))

            total_labels += len(labels)
            labeled_frames += 1

            if (i + 1) % 50 == 0:
                print(f"  [{split}] {i+1}/{len(frame_list)} frames processed...")

    # Write dataset YAML
    yaml_path = os.path.join(dataset_dir, "poker.yaml")
    with open(yaml_path, "w") as f:
        f.write(f"path: {os.path.abspath(dataset_dir)}\n")
        f.write("train: images/train\n")
        f.write("val: images/val\n")
        f.write(f"nc: {len(CLASS_NAMES)}\n")
        f.write(f"names: {CLASS_NAMES}\n")

    print(f"\nDataset built:")
    print(f"  Train: {len(train_frames)} frames")
    print(f"  Val: {len(val_frames)} frames")
    print(f"  Total labels: {total_labels}")
    print(f"  Labeled frames: {labeled_frames}")
    print(f"  YAML: {yaml_path}")

    return yaml_path


if __name__ == "__main__":
    print("Auto-labeling training frames for YOLOv8...")
    print("=" * 50)
    yaml_path = build_dataset()
    print(f"\nReady to train:")
    print(f"  yolo train model=yolov8n.pt data={yaml_path} epochs=100 imgsz=640")
