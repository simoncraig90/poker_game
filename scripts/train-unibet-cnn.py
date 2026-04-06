"""
Train a CNN card classifier on Unibet card crops.
52 classes (2c through As), same as PS card CNN.
"""
import os
import sys
import glob
import random

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

# Card labels
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
SUITS = ['c', 'd', 'h', 's']
CARD_LABELS = [f'{r}{s}' for r in RANKS for s in SUITS]  # 52 classes
LABEL_TO_IDX = {label: i for i, label in enumerate(CARD_LABELS)}


class CardDataset(Dataset):
    def __init__(self, samples, augment=False):
        self.samples = samples
        self.augment = augment

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label_idx = self.samples[idx]
        img = cv2.imread(path)
        if img is None:
            img = np.zeros((64, 48, 3), dtype=np.uint8)

        # Resize to fixed size
        img = cv2.resize(img, (48, 64))

        if self.augment:
            # Random brightness
            if random.random() > 0.5:
                factor = random.uniform(0.7, 1.3)
                img = np.clip(img * factor, 0, 255).astype(np.uint8)
            # Random small shift
            if random.random() > 0.5:
                dx = random.randint(-3, 3)
                dy = random.randint(-3, 3)
                M = np.float32([[1, 0, dx], [0, 1, dy]])
                img = cv2.warpAffine(img, M, (48, 64))

        # Normalize to [0, 1] and convert to tensor (C, H, W)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))  # HWC -> CHW
        return torch.tensor(img), label_idx


class CardCNN(nn.Module):
    def __init__(self, num_classes=52):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(128, 128, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool2d((4, 3)),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 4 * 3, 256),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(256, num_classes),
        )

    def forward(self, x):
        x = self.features(x)
        x = self.classifier(x)
        return x


def main():
    data_dir = 'vision/card_crops_unibet/labeled'

    # Load all labeled samples
    samples = []
    label_counts = {}
    for f in os.listdir(data_dir):
        if not f.endswith('.png'):
            continue
        label = f[:2]  # First 2 chars = card label (e.g., "Ac")
        if label not in LABEL_TO_IDX:
            print(f'  Skipping unknown label: {label} ({f})')
            continue
        path = os.path.join(data_dir, f)
        samples.append((path, LABEL_TO_IDX[label]))
        label_counts[label] = label_counts.get(label, 0) + 1

    print(f'Total samples: {len(samples)}')
    print(f'Unique cards: {len(label_counts)}')
    print(f'Labels: {sorted(label_counts.items())}')

    if len(samples) < 10:
        print('Not enough samples!')
        return

    # Split: 80% train, 20% val
    random.shuffle(samples)
    split = int(len(samples) * 0.8)
    train_samples = samples[:split]
    val_samples = samples[split:]

    # Augment training data by repeating with augmentation
    # This is needed because we have very few samples
    augmented_train = train_samples * 20  # 20x oversampling with augmentation

    train_ds = CardDataset(augmented_train, augment=True)
    val_ds = CardDataset(val_samples, augment=False)

    train_dl = DataLoader(train_ds, batch_size=32, shuffle=True, num_workers=0)
    val_dl = DataLoader(val_ds, batch_size=32, shuffle=False, num_workers=0)

    print(f'Train: {len(train_ds)}, Val: {len(val_ds)}')

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Device: {device}')

    model = CardCNN(num_classes=52).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=15, gamma=0.5)

    best_val_acc = 0
    best_model_path = 'vision/models/card_cnn_unibet.pt'

    for epoch in range(50):
        model.train()
        total_loss = 0
        correct = 0
        total = 0

        for imgs, labels in train_dl:
            imgs, labels = imgs.to(device), labels.to(device)
            outputs = model(imgs)
            loss = criterion(outputs, labels)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item()
            _, predicted = outputs.max(1)
            correct += predicted.eq(labels).sum().item()
            total += labels.size(0)

        train_acc = correct / total

        # Validation
        model.eval()
        val_correct = 0
        val_total = 0
        with torch.no_grad():
            for imgs, labels in val_dl:
                imgs, labels = imgs.to(device), labels.to(device)
                outputs = model(imgs)
                _, predicted = outputs.max(1)
                val_correct += predicted.eq(labels).sum().item()
                val_total += labels.size(0)

        val_acc = val_correct / max(val_total, 1)
        scheduler.step()

        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), best_model_path)

        if (epoch + 1) % 5 == 0:
            print(f'Epoch {epoch+1}/50: loss={total_loss/len(train_dl):.4f} '
                  f'train_acc={train_acc:.3f} val_acc={val_acc:.3f} best={best_val_acc:.3f}')

    print(f'\nBest val accuracy: {best_val_acc:.3f}')
    print(f'Model saved: {best_model_path}')


if __name__ == '__main__':
    main()
