"""
Train a small CNN for card identification (52 classes).

Uses the captured PS card templates as training data.
Augments with rotation, brightness, scale to handle rendering differences.

Usage:
  python vision/train_card_cnn.py
  python vision/train_card_cnn.py --epochs 100

Output: vision/models/card_cnn.pt
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

VISION_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = VISION_DIR / "templates" / "ps_cards"
MODEL_PATH = VISION_DIR / "models" / "card_cnn.pt"

# All 52 cards
RANKS = "AKQJT98765432"
SUITS = "shdc"
ALL_CARDS = [f"{r}{s}" for r in RANKS for s in SUITS]
CARD_TO_IDX = {c: i for i, c in enumerate(ALL_CARDS)}
IDX_TO_CARD = {i: c for c, i in CARD_TO_IDX.items()}

# Use corner crop (top 55%, left 55%) — that's where rank + suit info is
CORNER_H, CORNER_W = 80, 60


class CardDataset(Dataset):
    """Dataset of card corner crops with augmentation."""

    def __init__(self, template_dir, augment=True, samples_per_card=200):
        self.augment = augment
        self.samples = []
        self.labels = []

        for f in sorted(template_dir.iterdir()):
            if f.suffix != ".png" or len(f.stem) != 2:
                continue
            label = f.stem
            if label not in CARD_TO_IDX:
                continue

            img = cv2.imread(str(f))
            h, w = img.shape[:2]
            # Use tight left corner — rank + suit pip only (avoids overlap)
            corner = img[0:int(h * 0.50), 0:int(w * 0.35)]

            idx = CARD_TO_IDX[label]
            for _ in range(samples_per_card):
                self.samples.append(corner.copy())
                self.labels.append(idx)

        print(f"Dataset: {len(self.samples)} samples, {len(set(self.labels))} classes")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        corner = self.samples[i].copy()
        label = self.labels[i]

        if self.augment:
            corner = self._augment(corner)

        # Resize to fixed size
        corner = cv2.resize(corner, (CORNER_W, CORNER_H))

        # To tensor (CHW, float, normalized)
        tensor = torch.from_numpy(corner).permute(2, 0, 1).float() / 255.0
        return tensor, label

    def _augment(self, img):
        """Random augmentation to simulate different card positions/lighting."""
        h, w = img.shape[:2]

        # Random brightness (±20%)
        factor = 0.8 + np.random.random() * 0.4
        img = np.clip(img * factor, 0, 255).astype(np.uint8)

        # Random slight scale (±10%)
        scale = 0.9 + np.random.random() * 0.2
        new_h, new_w = int(h * scale), int(w * scale)
        img = cv2.resize(img, (new_w, new_h))
        # Crop back to original or pad
        if new_h > h:
            y = (new_h - h) // 2
            img = img[y:y + h, :w]
        elif new_h < h:
            pad = np.ones((h, w, 3), dtype=np.uint8) * 245
            pad[:new_h, :min(new_w, w)] = img[:, :min(new_w, w)]
            img = pad

        # Random slight shift (±5 pixels)
        dx, dy = np.random.randint(-5, 6), np.random.randint(-5, 6)
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        img = cv2.warpAffine(img, M, (w, h), borderValue=(245, 245, 245))

        # Random slight rotation (±3 degrees)
        angle = np.random.uniform(-3, 3)
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderValue=(245, 245, 245))

        # Random noise
        if np.random.random() < 0.3:
            noise = np.random.normal(0, 5, img.shape).astype(np.int16)
            img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        return img


class CardCNN(nn.Module):
    """Small CNN for 52-class card identification from corner crops."""

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(128, 128, 3, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 3)),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 4 * 3, 256),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(256, 52),
        )

    def forward(self, x):
        x = self.features(x)
        return self.classifier(x)


def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    dataset = CardDataset(TEMPLATE_DIR, augment=True, samples_per_card=args.samples)

    # 90/10 split
    n = len(dataset)
    n_val = max(52, int(n * 0.1))
    n_train = n - n_val
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=128, shuffle=False, num_workers=0)

    model = CardCNN().to(device)
    params = sum(p.numel() for p in model.parameters())
    print(f"Model: {params:,} parameters")

    optimizer = optim.Adam(model.parameters(), lr=0.001)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.CrossEntropyLoss()

    best_acc = 0

    for epoch in range(args.epochs):
        t0 = time.time()

        model.train()
        train_loss = 0
        train_correct = 0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            pred = model(x)
            loss = criterion(pred, y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * x.size(0)
            train_correct += (pred.argmax(1) == y).sum().item()
        train_acc = train_correct / n_train

        model.eval()
        val_correct = 0
        val_loss = 0
        with torch.no_grad():
            for x, y in val_loader:
                x, y = x.to(device), y.to(device)
                pred = model(x)
                val_loss += criterion(pred, y).item() * x.size(0)
                val_correct += (pred.argmax(1) == y).sum().item()
        val_acc = val_correct / n_val
        val_loss /= n_val

        scheduler.step(val_loss)
        elapsed = time.time() - t0
        marker = ""

        if val_acc > best_acc:
            best_acc = val_acc
            torch.save({
                "model_state": model.state_dict(),
                "card_to_idx": CARD_TO_IDX,
                "idx_to_card": IDX_TO_CARD,
            }, str(MODEL_PATH))
            marker = " *saved*"

        print(
            f"  Epoch {epoch + 1:3d}/{args.epochs}"
            f"  train_acc={train_acc:.3f}"
            f"  val_acc={val_acc:.3f}"
            f"  val_loss={val_loss:.4f}"
            f"  {elapsed:.1f}s{marker}"
        )

        if val_acc >= 1.0:
            print(f"\n  Perfect accuracy reached!")
            break

    print(f"\nBest val accuracy: {best_acc:.3f}")
    print(f"Model saved to {MODEL_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--samples", type=int, default=200, help="Augmented samples per card")
    args = parser.parse_args()

    print("=" * 55)
    print("  CARD CNN TRAINING")
    print("=" * 55)
    train(args)
