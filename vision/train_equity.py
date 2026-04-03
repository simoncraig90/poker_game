"""
Train a hand equity neural net with board texture awareness.

Input features:
  - Hero cards (2 ints, 0-51) → embeddings
  - Board cards (0-5 ints, 0-51) → embeddings
  - Board texture: paired, flush3, flush4, straight3, straight4, highCard
  - Hero features: suited, pair, gap, highRank, lowRank, hits, flushDraw

Target: Monte Carlo equity (0-1)

Usage:
  python vision/train_equity.py                     # train on generated data
  python vision/train_equity.py --epochs 50         # more epochs
  python vision/train_equity.py --data path.jsonl   # custom data file

Output: vision/models/equity_model.pt
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

VISION_DIR = Path(__file__).resolve().parent
DATA_PATH = VISION_DIR / "data" / "equity_training_data.jsonl"
MODEL_PATH = VISION_DIR / "models" / "equity_model.pt"


class EquityDataset(Dataset):
    def __init__(self, path):
        self.data = []
        print(f"Loading data from {path}...")
        with open(path) as f:
            for line in f:
                entry = json.loads(line)
                self.data.append(entry)
        print(f"Loaded {len(self.data):,} samples")

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        e = self.data[idx]

        # Hero cards (always 2)
        hero = torch.tensor(e["heroCards"], dtype=torch.long)

        # Board cards (0-5, padded to 5 with 52 = no card)
        board_cards = e["boardCards"] + [52] * (5 - len(e["boardCards"]))
        board = torch.tensor(board_cards, dtype=torch.long)

        # Numeric features
        features = torch.tensor([
            e["suited"], e["pair"], e["gap"], e["highRank"], e["lowRank"],
            e["hits"], e["heroFlushDraw"],
            e["paired"], e["flush3"], e["flush4"],
            e["straight3"], e["straight4"], e["highCard"],
            e["boardLen"] / 5.0,
        ], dtype=torch.float32)

        equity = torch.tensor([e["equity"]], dtype=torch.float32)

        return hero, board, features, equity


class EquityNet(nn.Module):
    """
    Neural net for hand equity prediction.
    Card embeddings + board texture features → equity (0-1).
    """
    def __init__(self, embed_dim=32, hidden=256):
        super().__init__()
        # 53 cards: 0-51 = real cards, 52 = padding/no card
        self.card_embed = nn.Embedding(53, embed_dim, padding_idx=52)

        # Hero: 2 cards → 2 * embed_dim
        # Board: 5 cards → 5 * embed_dim
        # Features: 14 numeric
        input_dim = 2 * embed_dim + 5 * embed_dim + 14

        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
            nn.Sigmoid(),
        )

    def forward(self, hero, board, features):
        hero_emb = self.card_embed(hero).flatten(1)    # (B, 2*E)
        board_emb = self.card_embed(board).flatten(1)   # (B, 5*E)
        x = torch.cat([hero_emb, board_emb, features], dim=1)
        return self.net(x)


def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    dataset = EquityDataset(args.data)

    # 90/10 split
    n = len(dataset)
    n_val = max(1, int(n * 0.1))
    n_train = n - n_val
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size * 2, shuffle=False, num_workers=0)

    model = EquityNet(embed_dim=args.embed_dim, hidden=args.hidden).to(device)
    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model: {param_count:,} parameters")

    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=3, factor=0.5)
    criterion = nn.MSELoss()

    best_val_loss = float("inf")

    for epoch in range(args.epochs):
        t0 = time.time()

        # Train
        model.train()
        train_loss = 0
        for hero, board, features, equity in train_loader:
            hero, board, features, equity = (
                hero.to(device), board.to(device), features.to(device), equity.to(device)
            )
            pred = model(hero, board, features)
            loss = criterion(pred, equity)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * hero.size(0)
        train_loss /= n_train

        # Validate
        model.eval()
        val_loss = 0
        val_mae = 0
        with torch.no_grad():
            for hero, board, features, equity in val_loader:
                hero, board, features, equity = (
                    hero.to(device), board.to(device), features.to(device), equity.to(device)
                )
                pred = model(hero, board, features)
                val_loss += criterion(pred, equity).item() * hero.size(0)
                val_mae += (pred - equity).abs().sum().item()
        val_loss /= n_val
        val_mae /= n_val

        scheduler.step(val_loss)

        elapsed = time.time() - t0
        rmse = math.sqrt(val_loss)
        lr = optimizer.param_groups[0]["lr"]
        marker = ""

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), str(MODEL_PATH))
            marker = " *saved*"

        print(
            f"  Epoch {epoch + 1:3d}/{args.epochs}"
            f"  train={math.sqrt(train_loss):.4f}"
            f"  val_RMSE={rmse:.4f}"
            f"  val_MAE={val_mae:.4f}"
            f"  lr={lr:.1e}"
            f"  {elapsed:.1f}s{marker}"
        )

    print(f"\nBest val RMSE: {math.sqrt(best_val_loss):.4f}")
    print(f"Model saved to {MODEL_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, default=str(DATA_PATH))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--embed-dim", type=int, default=32)
    parser.add_argument("--hidden", type=int, default=256)
    args = parser.parse_args()

    print("=" * 55)
    print("  EQUITY MODEL TRAINING")
    print("=" * 55)
    train(args)
