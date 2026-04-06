"""
Train a suit-only CNN classifier on Unibet card crops.
4 classes: clubs, diamonds, hearts, spades.
Focuses on the suit symbol shape/color which is the hard part.
"""
import os
import random
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

SUITS = ['c', 'd', 'h', 's']
SUIT_TO_IDX = {s: i for i, s in enumerate(SUITS)}


class SuitDataset(Dataset):
    def __init__(self, samples, augment=False):
        self.samples = samples
        self.augment = augment

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, suit_idx = self.samples[idx]
        img = cv2.imread(path)
        if img is None:
            img = np.zeros((64, 48, 3), dtype=np.uint8)

        # Crop to suit symbol area (bottom 60% of card) — this is where suit info is
        h, w = img.shape[:2]
        suit_region = img[int(h * 0.30):, :]
        suit_region = cv2.resize(suit_region, (48, 48))

        if self.augment:
            if random.random() > 0.5:
                factor = random.uniform(0.7, 1.3)
                suit_region = np.clip(suit_region * factor, 0, 255).astype(np.uint8)
            if random.random() > 0.5:
                dx = random.randint(-2, 2)
                dy = random.randint(-2, 2)
                M = np.float32([[1, 0, dx], [0, 1, dy]])
                suit_region = cv2.warpAffine(suit_region, M, (48, 48))
            # Random horizontal flip (cards are symmetric)
            if random.random() > 0.5:
                suit_region = cv2.flip(suit_region, 1)

        suit_region = suit_region.astype(np.float32) / 255.0
        suit_region = np.transpose(suit_region, (2, 0, 1))
        return torch.tensor(suit_region), suit_idx


class SuitCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.AdaptiveAvgPool2d((3, 3)),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 3 * 3, 128),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(128, 4),
        )

    def forward(self, x):
        return self.classifier(self.features(x))


def main():
    data_dir = 'vision/card_crops_unibet/labeled'

    # Also use the board card templates (100% correctly labeled)
    ps_dir = 'vision/templates/unibet_cards'

    samples = []
    suit_counts = {s: 0 for s in SUITS}

    # Labeled crops from Unibet captures
    for f in os.listdir(data_dir):
        if not f.endswith('.png') or len(f) < 3:
            continue
        suit = f[1]  # Second char of label
        if suit not in SUIT_TO_IDX:
            continue
        samples.append((os.path.join(data_dir, f), SUIT_TO_IDX[suit]))
        suit_counts[suit] += 1

    # Board card templates (clean, 100% accurate)
    for f in os.listdir(ps_dir):
        if f.endswith('.png') and not f.startswith('test') and len(f) > 5:
            # Format: board_unibet-table-X_Y_unknown.png
            # or hero_unibet-table-X_Y_unknown.png
            # We need the ones with known labels
            pass  # already included via labeled dir

    print(f'Total samples: {len(samples)}')
    print(f'Suit distribution: {suit_counts}')

    random.shuffle(samples)
    split = max(int(len(samples) * 0.8), len(samples) - 10)
    train_samples = samples[:split]
    val_samples = samples[split:]

    # Heavy augmentation to compensate for few samples
    augmented_train = train_samples * 30

    train_ds = SuitDataset(augmented_train, augment=True)
    val_ds = SuitDataset(val_samples, augment=False)

    train_dl = DataLoader(train_ds, batch_size=32, shuffle=True, num_workers=0)
    val_dl = DataLoader(val_ds, batch_size=32, shuffle=False, num_workers=0)

    print(f'Train: {len(train_ds)}, Val: {len(val_ds)}')

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Device: {device}')

    model = SuitCNN().to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)

    best_val_acc = 0
    best_model_path = 'vision/models/suit_cnn_unibet.pt'

    for epoch in range(80):
        model.train()
        correct = 0
        total = 0

        for imgs, labels in train_dl:
            imgs, labels = imgs.to(device), labels.to(device)
            outputs = model(imgs)
            loss = criterion(outputs, labels)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            _, predicted = outputs.max(1)
            correct += predicted.eq(labels).sum().item()
            total += labels.size(0)

        train_acc = correct / total
        scheduler.step()

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

        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), best_model_path)

        if (epoch + 1) % 10 == 0:
            print(f'Epoch {epoch+1}/80: train_acc={train_acc:.3f} val_acc={val_acc:.3f} best={best_val_acc:.3f}')

    print(f'\nBest val accuracy: {best_val_acc:.3f}')
    print(f'Model saved: {best_model_path}')

    # Test on validation set with per-class breakdown
    model.load_state_dict(torch.load(best_model_path, weights_only=True))
    model.eval()
    confusion = {s: {s2: 0 for s2 in SUITS} for s in SUITS}
    with torch.no_grad():
        for imgs, labels in val_dl:
            imgs, labels = imgs.to(device), labels.to(device)
            outputs = model(imgs)
            _, predicted = outputs.max(1)
            for true, pred in zip(labels, predicted):
                confusion[SUITS[true.item()]][SUITS[pred.item()]] += 1

    print('\nConfusion matrix:')
    print('     ', '  '.join(SUITS))
    for s in SUITS:
        row = [str(confusion[s][s2]).rjust(3) for s2 in SUITS]
        print(f'  {s}: {"  ".join(row)}')


if __name__ == '__main__':
    main()
