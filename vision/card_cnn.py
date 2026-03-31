"""
CNN card classifier for poker cards.

Two-head architecture: shared conv backbone with separate rank (13 classes)
and suit (4 classes) classification heads.

Input: card crop image resized to 64x96
Output: (rank, suit, confidence)

Usage:
    # Training
    python card_cnn.py train

    # Inference
    from card_cnn import CardCNN, identify_card_cnn
    result = identify_card_cnn(crop_image)
"""

import cv2
import json
import numpy as np
import os
import sys
from collections import Counter

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms as T

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(VISION_DIR, "models", "card_cnn.pt")
LABELS_FILE = os.path.join(VISION_DIR, "card_crops", "cnn_labels.json")
CROPS_DIR = os.path.join(VISION_DIR, "card_crops", "labeled")

INPUT_W, INPUT_H = 64, 96  # width x height

RANKS = list("23456789TJQKA")
SUITS = list("shdc")
RANK_TO_IDX = {r: i for i, r in enumerate(RANKS)}
SUIT_TO_IDX = {s: i for i, s in enumerate(SUITS)}
IDX_TO_RANK = {i: r for r, i in RANK_TO_IDX.items()}
IDX_TO_SUIT = {i: s for s, i in SUIT_TO_IDX.items()}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class CardCNN(nn.Module):
    """
    Two-head CNN for card classification.
    Shared convolutional backbone + separate FC heads for rank and suit.
    """

    def __init__(self):
        super().__init__()

        # Shared backbone: 4 conv blocks
        # Input: 3 x 96 x 64
        self.features = nn.Sequential(
            # Block 1: 3 -> 32, 96x64 -> 48x32
            nn.Conv2d(3, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),

            # Block 2: 32 -> 64, 48x32 -> 24x16
            nn.Conv2d(32, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),

            # Block 3: 64 -> 128, 24x16 -> 12x8
            nn.Conv2d(64, 128, 3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),

            # Block 4: 128 -> 128, 12x8 -> 6x4
            nn.Conv2d(128, 128, 3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )
        # After backbone: 128 x 6 x 4 = 3072

        self.flatten_size = 128 * 6 * 4

        # Shared FC
        self.shared_fc = nn.Sequential(
            nn.Linear(self.flatten_size, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),
        )

        # Rank head
        self.rank_head = nn.Sequential(
            nn.Linear(256, 64),
            nn.ReLU(inplace=True),
            nn.Linear(64, 13),
        )

        # Suit head
        self.suit_head = nn.Sequential(
            nn.Linear(256, 64),
            nn.ReLU(inplace=True),
            nn.Linear(64, 4),
        )

    def forward(self, x):
        """Returns (rank_logits, suit_logits)."""
        feat = self.features(x)
        feat = feat.view(feat.size(0), -1)
        shared = self.shared_fc(feat)
        rank_logits = self.rank_head(shared)
        suit_logits = self.suit_head(shared)
        return rank_logits, suit_logits


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class CardDataset(Dataset):
    """Card crop dataset with augmentation."""

    def __init__(self, image_paths, rank_labels, suit_labels, augment=False):
        self.image_paths = image_paths
        self.rank_labels = rank_labels
        self.suit_labels = suit_labels
        self.augment = augment

        # Augmentation transforms
        self.aug_transform = T.Compose([
            T.ToPILImage(),
            T.RandomAffine(degrees=3, translate=(0.05, 0.05), scale=(0.95, 1.05)),
            T.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        self.val_transform = T.Compose([
            T.ToPILImage(),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img = cv2.imread(self.image_paths[idx])
        if img is None:
            # Return a blank image if load fails
            img = np.zeros((INPUT_H, INPUT_W, 3), dtype=np.uint8)
        else:
            img = cv2.resize(img, (INPUT_W, INPUT_H))

        # Convert BGR -> RGB
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if self.augment:
            tensor = self.aug_transform(img)
        else:
            tensor = self.val_transform(img)

        return tensor, self.rank_labels[idx], self.suit_labels[idx]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def load_dataset():
    """Load labeled crops and split into train/val."""
    with open(LABELS_FILE, "r") as f:
        labels = json.load(f)

    image_paths = []
    rank_labels = []
    suit_labels = []

    for fname, info in labels.items():
        card_label = info["label"]
        if len(card_label) != 2:
            continue
        rank_char, suit_char = card_label[0], card_label[1]
        if rank_char not in RANK_TO_IDX or suit_char not in SUIT_TO_IDX:
            continue

        path = os.path.join(CROPS_DIR, fname)
        if not os.path.exists(path):
            continue

        image_paths.append(path)
        rank_labels.append(RANK_TO_IDX[rank_char])
        suit_labels.append(SUIT_TO_IDX[suit_char])

    # Shuffle deterministically
    import random
    combined = list(zip(image_paths, rank_labels, suit_labels))
    random.seed(42)
    random.shuffle(combined)
    image_paths, rank_labels, suit_labels = zip(*combined)

    # 80/20 split
    n = len(image_paths)
    split = int(n * 0.8)

    train_data = CardDataset(
        image_paths[:split],
        rank_labels[:split],
        suit_labels[:split],
        augment=True,
    )
    val_data = CardDataset(
        image_paths[split:],
        rank_labels[split:],
        suit_labels[split:],
        augment=False,
    )

    print(f"Dataset: {n} total, {split} train, {n - split} val")

    # Print class distribution
    rank_dist = Counter(rank_labels[:split])
    suit_dist = Counter(suit_labels[:split])
    print(f"Train rank dist: { {IDX_TO_RANK[k]: v for k, v in sorted(rank_dist.items())} }")
    print(f"Train suit dist: { {IDX_TO_SUIT[k]: v for k, v in sorted(suit_dist.items())} }")

    return train_data, val_data


def compute_class_weights(dataset):
    """Compute inverse-frequency weights for imbalanced classes."""
    rank_counts = Counter(dataset.rank_labels)
    suit_counts = Counter(dataset.suit_labels)

    n = len(dataset)

    rank_weights = torch.zeros(13)
    for i in range(13):
        count = rank_counts.get(i, 0)
        if count > 0:
            rank_weights[i] = n / (13.0 * count)
        else:
            rank_weights[i] = 1.0

    suit_weights = torch.zeros(4)
    for i in range(4):
        count = suit_counts.get(i, 0)
        if count > 0:
            suit_weights[i] = n / (4.0 * count)
        else:
            suit_weights[i] = 1.0

    return rank_weights, suit_weights


def train(epochs=60, batch_size=32, lr=1e-3):
    """Train the card CNN."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    train_data, val_data = load_dataset()

    train_loader = DataLoader(train_data, batch_size=batch_size, shuffle=True,
                              num_workers=0, pin_memory=True)
    val_loader = DataLoader(val_data, batch_size=batch_size, shuffle=False,
                            num_workers=0, pin_memory=True)

    model = CardCNN().to(device)
    print(f"Model params: {sum(p.numel() for p in model.parameters()):,}")

    # Class weights for imbalanced data
    rank_weights, suit_weights = compute_class_weights(train_data)
    rank_criterion = nn.CrossEntropyLoss(weight=rank_weights.to(device))
    suit_criterion = nn.CrossEntropyLoss(weight=suit_weights.to(device))

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_acc = 0.0
    best_epoch = 0

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0.0
        train_rank_correct = 0
        train_suit_correct = 0
        train_both_correct = 0
        train_total = 0

        for imgs, ranks, suits in train_loader:
            imgs = imgs.to(device)
            ranks = ranks.to(device, dtype=torch.long)
            suits = suits.to(device, dtype=torch.long)

            rank_logits, suit_logits = model(imgs)
            loss = rank_criterion(rank_logits, ranks) + suit_criterion(suit_logits, suits)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            train_loss += loss.item() * imgs.size(0)
            rank_pred = rank_logits.argmax(1)
            suit_pred = suit_logits.argmax(1)
            train_rank_correct += (rank_pred == ranks).sum().item()
            train_suit_correct += (suit_pred == suits).sum().item()
            train_both_correct += ((rank_pred == ranks) & (suit_pred == suits)).sum().item()
            train_total += imgs.size(0)

        scheduler.step()

        # Validate
        model.eval()
        val_rank_correct = 0
        val_suit_correct = 0
        val_both_correct = 0
        val_total = 0

        with torch.no_grad():
            for imgs, ranks, suits in val_loader:
                imgs = imgs.to(device)
                ranks = ranks.to(device, dtype=torch.long)
                suits = suits.to(device, dtype=torch.long)

                rank_logits, suit_logits = model(imgs)
                rank_pred = rank_logits.argmax(1)
                suit_pred = suit_logits.argmax(1)
                val_rank_correct += (rank_pred == ranks).sum().item()
                val_suit_correct += (suit_pred == suits).sum().item()
                val_both_correct += ((rank_pred == ranks) & (suit_pred == suits)).sum().item()
                val_total += imgs.size(0)

        train_acc = train_both_correct / max(train_total, 1)
        val_rank_acc = val_rank_correct / max(val_total, 1)
        val_suit_acc = val_suit_correct / max(val_total, 1)
        val_acc = val_both_correct / max(val_total, 1)

        if (epoch + 1) % 5 == 0 or epoch == 0 or val_acc > best_val_acc:
            print(f"Epoch {epoch+1:3d}/{epochs}  "
                  f"loss={train_loss/train_total:.4f}  "
                  f"train_acc={train_acc:.3f}  "
                  f"val_rank={val_rank_acc:.3f}  val_suit={val_suit_acc:.3f}  "
                  f"val_card={val_acc:.3f}"
                  f"{'  *BEST*' if val_acc > best_val_acc else ''}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_epoch = epoch + 1
            os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
            torch.save({
                "model_state_dict": model.state_dict(),
                "val_acc": val_acc,
                "val_rank_acc": val_rank_acc,
                "val_suit_acc": val_suit_acc,
                "epoch": epoch + 1,
                "ranks": RANKS,
                "suits": SUITS,
            }, MODEL_PATH)

    print(f"\nBest val card accuracy: {best_val_acc:.3f} at epoch {best_epoch}")
    print(f"Model saved to {MODEL_PATH}")

    return best_val_acc


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

_model = None
_device = None


def _load_model():
    """Load trained model (lazy, cached)."""
    global _model, _device
    if _model is not None:
        return _model, _device

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _model = CardCNN()

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"No trained model found at {MODEL_PATH}. Run training first.")

    checkpoint = torch.load(MODEL_PATH, map_location=_device, weights_only=True)
    _model.load_state_dict(checkpoint["model_state_dict"])
    _model.to(_device)
    _model.eval()

    return _model, _device


def _preprocess(card_img):
    """Preprocess a card crop for inference."""
    img = cv2.resize(card_img, (INPUT_W, INPUT_H))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    transform = T.Compose([
        T.ToPILImage(),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    return transform(img).unsqueeze(0)  # Add batch dimension


def identify_card_cnn(card_img):
    """
    Identify a card from its cropped image using the CNN.

    Args:
        card_img: BGR image (numpy array) of a cropped card

    Returns:
        (rank, suit, confidence) where:
            rank: str, one of '2'-'9','T','J','Q','K','A'
            suit: str, one of 's','h','d','c'
            confidence: float, 0-1 (product of rank and suit confidences)
    """
    model, device = _load_model()

    tensor = _preprocess(card_img).to(device)

    with torch.no_grad():
        rank_logits, suit_logits = model(tensor)

    rank_probs = F.softmax(rank_logits, dim=1)[0]
    suit_probs = F.softmax(suit_logits, dim=1)[0]

    rank_idx = rank_probs.argmax().item()
    suit_idx = suit_probs.argmax().item()

    rank_conf = rank_probs[rank_idx].item()
    suit_conf = suit_probs[suit_idx].item()

    rank = IDX_TO_RANK[rank_idx]
    suit = IDX_TO_SUIT[suit_idx]
    confidence = rank_conf * suit_conf

    return rank, suit, confidence


def identify_card_cnn_full(card_img):
    """
    Like identify_card_cnn but returns full label string and confidence.

    Args:
        card_img: BGR image (numpy array) of a cropped card

    Returns:
        (label, confidence) where label is like 'Ah', 'Ks', etc.
    """
    rank, suit, conf = identify_card_cnn(card_img)
    return rank + suit, conf


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        epochs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        train(epochs=epochs)

    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test on validation set and show per-class accuracy
        print("Loading model and testing...")
        model, device = _load_model()

        with open(LABELS_FILE, "r") as f:
            labels = json.load(f)

        correct = 0
        total = 0
        errors = []

        for fname, info in labels.items():
            path = os.path.join(CROPS_DIR, fname)
            if not os.path.exists(path):
                continue
            img = cv2.imread(path)
            if img is None:
                continue

            rank, suit, conf = identify_card_cnn(img)
            pred = rank + suit
            true = info["label"]

            total += 1
            if pred == true:
                correct += 1
            else:
                errors.append((fname, true, pred, conf))

        print(f"\nOverall accuracy: {correct}/{total} = {correct/max(total,1):.3f}")
        if errors:
            print(f"\nErrors ({len(errors)}):")
            for fname, true, pred, conf in errors[:20]:
                print(f"  {fname}: true={true}, pred={pred}, conf={conf:.3f}")

    elif len(sys.argv) > 1:
        # Identify a single image
        img = cv2.imread(sys.argv[1])
        if img is None:
            print(f"Cannot read {sys.argv[1]}")
            sys.exit(1)
        rank, suit, conf = identify_card_cnn(img)
        print(f"{rank}{suit} (confidence: {conf:.3f})")

    else:
        print("Usage:")
        print("  python card_cnn.py train [epochs]  - Train the CNN")
        print("  python card_cnn.py test             - Test on labeled data")
        print("  python card_cnn.py <image_path>     - Identify a single card")
