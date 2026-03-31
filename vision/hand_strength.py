"""
Hand-strength neural network.

Trains on engine-generated data to predict win probability given:
  - 2 hero hole cards (encoded 0-51)
  - 5 board cards (0-51, 52 = empty)
  - num_opponents (1-5)

Architecture:
  Card embedding (53 tokens -> 16-dim each) for 7 card slots,
  concatenated with num_opponents (normalized), passed through FC layers.

Usage:
  # Train
  python vision/hand_strength.py train

  # Inference example
  python vision/hand_strength.py predict --hero 0,1 --board 8,12,20,52,52 --opp 3
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, random_split

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "hand_strength_data.jsonl"
MODEL_PATH = ROOT / "models" / "hand_strength.pt"

# ── Dataset ──────────────────────────────────────────────────────────────────

class HandStrengthDataset(Dataset):
    """Loads JSONL rows into tensors."""

    def __init__(self, path: str | Path):
        self.cards = []    # (N, 7) int — 2 hero + 5 board
        self.opp = []      # (N,) float — num_opponents normalized
        self.targets = []   # (N,) float — equity

        print(f"Loading data from {path} ...")
        t0 = time.time()
        with open(path, "r") as f:
            for line in f:
                row = json.loads(line)
                cards = row["hero"] + row["board"]  # length 7
                self.cards.append(cards)
                self.opp.append(row["num_opponents"])
                # Use equity as target (smooth 0-1 signal, better than binary won)
                self.targets.append(row["equity"])

        self.cards = torch.tensor(self.cards, dtype=torch.long)
        self.opp = torch.tensor(self.opp, dtype=torch.float32)
        self.targets = torch.tensor(self.targets, dtype=torch.float32)

        # Normalize opponents to 0-1 range (1-5 -> 0-1)
        self.opp = (self.opp - 1.0) / 4.0

        elapsed = time.time() - t0
        print(f"  Loaded {len(self.targets):,} samples in {elapsed:.1f}s")

    def __len__(self):
        return len(self.targets)

    def __getitem__(self, idx):
        return self.cards[idx], self.opp[idx], self.targets[idx]


# ── Model ────────────────────────────────────────────────────────────────────

class HandStrengthNet(nn.Module):
    """
    Card-embedding network for hand-strength prediction.

    7 card slots (2 hero + 5 board) each embedded from vocab 53 -> 16 dims.
    Concatenated (7*16=112) + 1 opponent feature = 113 input to FC layers.
    """

    def __init__(self, embed_dim=16, hidden=256):
        super().__init__()
        self.card_embed = nn.Embedding(53, embed_dim)  # 0-51 = cards, 52 = empty
        input_dim = 7 * embed_dim + 1  # 7 card embeds + num_opponents

        self.fc = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.BatchNorm1d(hidden),
            nn.Dropout(0.2),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.BatchNorm1d(hidden),
            nn.Dropout(0.2),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.BatchNorm1d(hidden // 2),
            nn.Linear(hidden // 2, 1),
            nn.Sigmoid(),
        )

    def forward(self, cards, opp):
        """
        cards: (B, 7) long tensor — card indices 0-52
        opp:   (B,)   float tensor — normalized opponent count
        """
        emb = self.card_embed(cards)        # (B, 7, embed_dim)
        emb = emb.view(emb.size(0), -1)     # (B, 7 * embed_dim)
        x = torch.cat([emb, opp.unsqueeze(1)], dim=1)  # (B, 7*16+1)
        return self.fc(x).squeeze(1)         # (B,)


# ── Training ─────────────────────────────────────────────────────────────────

def train_model():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    # Load data
    dataset = HandStrengthDataset(DATA_PATH)
    n = len(dataset)
    n_val = int(n * 0.1)
    n_train = n - n_val
    train_ds, val_ds = random_split(dataset, [n_train, n_val],
                                     generator=torch.Generator().manual_seed(42))

    print(f"  Train: {n_train:,}  Val: {n_val:,}")

    train_loader = DataLoader(train_ds, batch_size=4096, shuffle=True,
                              num_workers=4, pin_memory=True, persistent_workers=True)
    val_loader = DataLoader(val_ds, batch_size=8192, shuffle=False,
                            num_workers=2, pin_memory=True, persistent_workers=True)

    # Model
    model = HandStrengthNet(embed_dim=16, hidden=256).to(device)
    param_count = sum(p.numel() for p in model.parameters())
    print(f"  Parameters: {param_count:,}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=30)
    criterion = nn.BCELoss()

    best_val_loss = float("inf")
    patience = 5
    patience_counter = 0
    epochs = 30

    print(f"\nTraining for up to {epochs} epochs (early stopping patience={patience})...\n")

    for epoch in range(1, epochs + 1):
        # ── Train ──
        model.train()
        train_loss = 0.0
        train_batches = 0
        t0 = time.time()

        for cards, opp, target in train_loader:
            cards = cards.to(device, non_blocking=True)
            opp = opp.to(device, non_blocking=True)
            target = target.to(device, non_blocking=True)

            pred = model(cards, opp)
            loss = criterion(pred, target)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            train_batches += 1

        scheduler.step()

        avg_train = train_loss / train_batches

        # ── Validate ──
        model.eval()
        val_loss = 0.0
        val_batches = 0
        val_mae = 0.0

        with torch.no_grad():
            for cards, opp, target in val_loader:
                cards = cards.to(device, non_blocking=True)
                opp = opp.to(device, non_blocking=True)
                target = target.to(device, non_blocking=True)

                pred = model(cards, opp)
                loss = criterion(pred, target)
                val_loss += loss.item()
                val_mae += (pred - target).abs().mean().item()
                val_batches += 1

        avg_val = val_loss / val_batches
        avg_mae = val_mae / val_batches
        elapsed = time.time() - t0
        lr = optimizer.param_groups[0]["lr"]

        print(f"  Epoch {epoch:2d}/{epochs} | "
              f"train_loss={avg_train:.4f} | val_loss={avg_val:.4f} | "
              f"val_mae={avg_mae:.4f} | lr={lr:.2e} | {elapsed:.1f}s")

        # Early stopping
        if avg_val < best_val_loss - 1e-5:
            best_val_loss = avg_val
            patience_counter = 0
            # Save best
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "model_state": model.state_dict(),
                "embed_dim": 16,
                "hidden": 256,
                "val_loss": best_val_loss,
                "epoch": epoch,
            }, MODEL_PATH)
            print(f"    -> Saved best model (val_loss={best_val_loss:.4f})")
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"\n  Early stopping at epoch {epoch}.")
                break

    print(f"\nTraining complete. Best val_loss={best_val_loss:.4f}")
    print(f"Model saved to {MODEL_PATH}")


# ── Inference ────────────────────────────────────────────────────────────────

def load_model(device=None):
    """Load the trained model. Returns (model, device)."""
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    checkpoint = torch.load(MODEL_PATH, map_location=device, weights_only=True)
    model = HandStrengthNet(
        embed_dim=checkpoint.get("embed_dim", 16),
        hidden=checkpoint.get("hidden", 256),
    ).to(device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    return model, device


def hand_strength(hero_cards, board, num_opponents):
    """
    Predict win probability for a hand.

    Args:
        hero_cards: list of 2 ints (0-51), e.g. [48, 49] for As, Ah
        board: list of 0-5 ints (0-51), e.g. [0, 4, 8] for flop 2c 3c 4c
        num_opponents: int 1-5

    Returns:
        float 0-1 — estimated win probability (equity)
    """
    model, device = load_model()

    # Pad board to 5 with 52
    board_padded = list(board) + [52] * (5 - len(board))

    cards = torch.tensor([hero_cards + board_padded], dtype=torch.long, device=device)
    opp = torch.tensor([(num_opponents - 1) / 4.0], dtype=torch.float32, device=device)

    with torch.no_grad():
        prob = model(cards, opp).item()

    return prob


def encode_card(rank, suit):
    """Encode a card from engine format to 0-51 int.

    rank: 2-14 (2=2, ..., 14=Ace)
    suit: 1-4 (1=clubs, 2=diamonds, 3=hearts, 4=spades)
    """
    return (rank - 2) * 4 + (suit - 1)


def decode_card_str(s):
    """Parse a card string like 'As' or 'Th' to int 0-51."""
    rank_map = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
                "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}
    suit_map = {"c": 1, "d": 2, "h": 3, "s": 4}
    r = rank_map[s[0].upper()]
    st = suit_map[s[1].lower()]
    return encode_card(r, st)


def decode_int_to_str(i):
    """Convert int 0-51 back to display string."""
    if i == 52:
        return "--"
    rank = i // 4 + 2
    suit = i % 4 + 1
    ranks = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
             9: "9", 10: "T", 11: "J", 12: "Q", 13: "K", 14: "A"}
    suits = {1: "c", 2: "d", 3: "h", 4: "s"}
    return ranks[rank] + suits[suit]


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python vision/hand_strength.py [train|predict]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "train":
        train_model()

    elif cmd == "predict":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("cmd")
        parser.add_argument("--hero", required=True, help="2 card ints, comma-separated (e.g. 48,49)")
        parser.add_argument("--board", default="52,52,52,52,52", help="up to 5 card ints")
        parser.add_argument("--opp", type=int, default=1, help="num opponents (1-5)")
        args = parser.parse_args()

        hero = [int(x) for x in args.hero.split(",")]
        board_raw = [int(x) for x in args.board.split(",")]
        # Remove padding
        board = [c for c in board_raw if c != 52]

        prob = hand_strength(hero, board, args.opp)
        hero_str = " ".join(decode_int_to_str(c) for c in hero)
        board_str = " ".join(decode_int_to_str(c) for c in board) if board else "(preflop)"
        print(f"Hero: {hero_str}")
        print(f"Board: {board_str}")
        print(f"Opponents: {args.opp}")
        print(f"Win probability: {prob:.4f} ({prob*100:.1f}%)")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
