"""
Train the policy network using REINFORCE with baseline.

Loads decision data from vision/data/rl_training_data.jsonl,
trains the policy net to reinforce profitable actions and
discourage unprofitable ones.

Usage:
  python vision/train_policy.py
  python vision/train_policy.py --epochs 20 --lr 3e-4
  python vision/train_policy.py --data vision/data/rl_training_data.jsonl
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, random_split

from policy_net import PolicyNet, NUM_ACTIONS, count_parameters

# ── Paths ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "rl_training_data.jsonl"
MODEL_PATH = ROOT / "models" / "policy_net.pt"


# ── Dataset ──────────────────────────────────────────────────────────────

class RLDataset(Dataset):
    """Load JSONL decision records into tensors for policy gradient training."""

    def __init__(self, path: str | Path, max_rows: int = 0):
        self.cards = []       # (N, 7) long
        self.extra = []       # (N, 10) float
        self.actions = []     # (N,) long - action index taken
        self.rewards = []     # (N,) float - reward in BB
        self.legal_masks = [] # (N, 5) bool - which actions were legal

        print(f"Loading RL data from {path} ...")
        t0 = time.time()
        count = 0

        with open(path, "r") as f:
            for line in f:
                if max_rows and count >= max_rows:
                    break
                row = json.loads(line)
                s = row["s"]

                # Cards: 2 hero + 5 board
                cards = [s["heroCard1"], s["heroCard2"]] + s["boardCards"]
                self.cards.append(cards)

                # Extra features (13 floats)
                extra = [
                    s["potNorm"],
                    s["stackNorm"],
                    s["callNorm"],
                    s["potOdds"],
                    s["numOpponents"] / 5.0,
                ] + s["streetOneHot"] + [
                    s["posNorm"],
                    s.get("handStrength", 0.5),
                    s.get("betToPot", 0),
                    s.get("sprNorm", 0.5),
                ]
                self.extra.append(extra)

                # Action taken
                self.actions.append(row["a"])

                # Reward (already in BB from the generator)
                self.rewards.append(row["r"])

                # Legal action mask
                mask = [False] * NUM_ACTIONS
                for a in row["legal"]:
                    if 0 <= a < NUM_ACTIONS:
                        mask[a] = True
                self.legal_masks.append(mask)

                count += 1

        self.cards = torch.tensor(self.cards, dtype=torch.long)
        self.extra = torch.tensor(self.extra, dtype=torch.float32)
        self.actions = torch.tensor(self.actions, dtype=torch.long)
        self.rewards = torch.tensor(self.rewards, dtype=torch.float32)
        self.legal_masks = torch.tensor(self.legal_masks, dtype=torch.bool)

        elapsed = time.time() - t0
        print(f"  Loaded {len(self.rewards):,} decision points in {elapsed:.1f}s")
        print(f"  Reward stats: mean={self.rewards.mean():.3f}, std={self.rewards.std():.3f}, "
              f"min={self.rewards.min():.1f}, max={self.rewards.max():.1f}")

        # Action distribution
        for i in range(NUM_ACTIONS):
            frac = (self.actions == i).float().mean().item() * 100
            if frac > 0.1:
                print(f"  Action {i} ({['FOLD','CHECK','CALL','BET','RAISE'][i]}): {frac:.1f}%")

    def __len__(self):
        return len(self.rewards)

    def __getitem__(self, idx):
        return (self.cards[idx], self.extra[idx], self.actions[idx],
                self.rewards[idx], self.legal_masks[idx])


# ── Training ─────────────────────────────────────────────────────────────

def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")

    # Load data
    dataset = RLDataset(args.data, max_rows=args.max_rows)
    n = len(dataset)
    if n == 0:
        print("No data loaded. Run generate-rl-data.js first.")
        return

    n_val = max(1, int(n * 0.1))
    n_train = n - n_val
    train_ds, val_ds = random_split(dataset, [n_train, n_val],
                                     generator=torch.Generator().manual_seed(42))

    print(f"  Train: {n_train:,}  Val: {n_val:,}")

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=0, pin_memory=(device.type == "cuda"))
    val_loader = DataLoader(val_ds, batch_size=args.batch_size * 2, shuffle=False,
                            num_workers=0, pin_memory=(device.type == "cuda"))

    # Model
    model = PolicyNet().to(device)

    # Load existing model if continuing training
    if args.resume and MODEL_PATH.exists():
        checkpoint = torch.load(MODEL_PATH, map_location=device, weights_only=True)
        model.load_state_dict(checkpoint["model_state"])
        print(f"  Resumed from existing model (epoch {checkpoint.get('epoch', '?')})")

    print(f"  Parameters: {count_parameters(model):,}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    # Compute baseline (mean reward) for variance reduction
    all_rewards = dataset.rewards
    baseline = all_rewards.mean().item()
    reward_std = all_rewards.std().item() + 1e-8
    print(f"  Baseline reward: {baseline:.3f}, std: {reward_std:.3f}")

    best_val_loss = float("inf")
    print(f"\nTraining for {args.epochs} epochs...\n")

    for epoch in range(1, args.epochs + 1):
        # ── Train ──
        model.train()
        train_loss_sum = 0.0
        train_batches = 0
        t0 = time.time()

        for cards, extra, actions, rewards, legal_masks in train_loader:
            cards = cards.to(device, non_blocking=True)
            extra = extra.to(device, non_blocking=True)
            actions = actions.to(device, non_blocking=True)
            rewards = rewards.to(device, non_blocking=True)
            legal_masks = legal_masks.to(device, non_blocking=True)

            # Forward pass
            action_logits, sizing = model(cards, extra)

            # Mask illegal actions
            action_logits = action_logits.masked_fill(~legal_masks, -1e9)

            # ── PHASE 1: Imitation learning (first half of epochs) ──
            # Cross-entropy loss: learn to copy TAG's actions exactly
            # This teaches folding, position play, hand selection
            imitation_loss = F.cross_entropy(action_logits, actions)

            # ── PHASE 2: RL fine-tuning (second half of epochs) ──
            # Policy gradient: reinforce actions that led to profit
            log_probs = F.log_softmax(action_logits, dim=-1)
            action_log_probs = log_probs.gather(1, actions.unsqueeze(1)).squeeze(1)
            action_log_probs = action_log_probs.clamp(min=-20.0)

            advantage = (rewards - baseline) / reward_std
            advantage = advantage.clamp(-5.0, 5.0)
            policy_loss = -(advantage * action_log_probs).mean()

            # Entropy bonus
            probs = F.softmax(action_logits, dim=-1)
            safe_log_probs = torch.log(probs.clamp(min=1e-8))
            entropy = -(probs * safe_log_probs).sum(dim=-1)
            entropy_bonus = entropy.mean()

            # Pure imitation learning — cross-entropy on TAG actions
            # RL fine-tuning needs proper reward shaping (future work)
            loss = imitation_loss

            optimizer.zero_grad()
            loss.backward()
            # Gradient clipping
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            train_loss_sum += loss.item()
            train_batches += 1

        scheduler.step()
        avg_train_loss = train_loss_sum / max(train_batches, 1)

        # ── Validate ──
        model.eval()
        val_loss_sum = 0.0
        val_batches = 0
        val_correct = 0
        val_total = 0

        with torch.no_grad():
            for cards, extra, actions, rewards, legal_masks in val_loader:
                cards = cards.to(device, non_blocking=True)
                extra = extra.to(device, non_blocking=True)
                actions = actions.to(device, non_blocking=True)
                rewards = rewards.to(device, non_blocking=True)
                legal_masks = legal_masks.to(device, non_blocking=True)

                action_logits, sizing = model(cards, extra)
                action_logits = action_logits.masked_fill(~legal_masks, -1e9)

                log_probs = F.log_softmax(action_logits, dim=-1)
                action_log_probs = log_probs.gather(1, actions.unsqueeze(1)).squeeze(1)
                action_log_probs = action_log_probs.clamp(min=-20.0)
                advantage = (rewards - baseline) / reward_std
                advantage = advantage.clamp(-5.0, 5.0)
                policy_loss = -(advantage * action_log_probs).mean()

                val_loss_sum += policy_loss.item()
                val_batches += 1

                # Accuracy: does the model predict the same action?
                predicted = action_logits.argmax(dim=-1)
                val_correct += (predicted == actions).sum().item()
                val_total += len(actions)

        avg_val_loss = val_loss_sum / max(val_batches, 1)
        val_acc = val_correct / max(val_total, 1) * 100
        elapsed = time.time() - t0
        lr = optimizer.param_groups[0]["lr"]

        print(f"  Epoch {epoch:2d}/{args.epochs} | "
              f"train_loss={avg_train_loss:.4f} | val_loss={avg_val_loss:.4f} | "
              f"val_acc={val_acc:.1f}% | lr={lr:.2e} | {elapsed:.1f}s")

        # Save best model
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "model_state": model.state_dict(),
                "epoch": epoch,
                "val_loss": best_val_loss,
                "val_acc": val_acc,
                "baseline": baseline,
                "reward_std": reward_std,
            }, MODEL_PATH)
            print(f"    -> Saved best model (val_loss={best_val_loss:.4f})")

    print(f"\nTraining complete. Best val_loss={best_val_loss:.4f}")
    print(f"Model saved to {MODEL_PATH}")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train RL policy network")
    parser.add_argument("--data", type=str, default=str(DEFAULT_DATA),
                        help="Path to JSONL training data")
    parser.add_argument("--epochs", type=int, default=10,
                        help="Number of training epochs")
    parser.add_argument("--batch-size", type=int, default=2048,
                        help="Training batch size")
    parser.add_argument("--lr", type=float, default=3e-4,
                        help="Learning rate")
    parser.add_argument("--entropy-weight", type=float, default=0.01,
                        help="Entropy bonus weight for exploration")
    parser.add_argument("--max-rows", type=int, default=0,
                        help="Max rows to load (0 = all)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume training from existing model")

    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
