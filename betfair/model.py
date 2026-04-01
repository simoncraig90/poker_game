"""
Sport-agnostic prediction model.

Same architecture pattern as vision/hand_strength_net — a feedforward network
that takes feature vectors and outputs probabilities.

Plug in any sport by changing the feature engineering in prepare_features().
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from pathlib import Path
import json
import numpy as np


class PredictionNet(nn.Module):
    """
    Feedforward network for outcome prediction.

    Input: feature vector (team stats, form, head-to-head, etc.)
    Output: probability distribution over outcomes (e.g. home/draw/away)
    """

    def __init__(self, input_dim, hidden_dim=128, num_outcomes=3, dropout=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_outcomes),
        )

    def forward(self, x):
        logits = self.net(x)
        return torch.softmax(logits, dim=-1)


def prepare_features(event_data):
    """
    Convert raw event data into a feature vector.

    Override this per sport. Examples:

    Football: [home_goals_avg, away_goals_avg, home_form, away_form,
               h2h_home_wins, h2h_draws, h2h_away_wins, home_possession_avg, ...]

    Tennis: [player1_elo, player2_elo, surface_win_pct_1, surface_win_pct_2,
             recent_form_1, recent_form_2, h2h_ratio, fatigue_score, ...]

    Horse racing: [official_rating, days_since_last, distance_win_pct,
                   going_preference, jockey_strike_rate, trainer_form, ...]
    """
    # Placeholder — implement per sport
    raise NotImplementedError("Implement prepare_features() for your sport")


def train(features, labels, input_dim, num_outcomes=3, epochs=100, lr=1e-3, batch_size=256):
    """
    Train the prediction model.

    Args:
        features: numpy array of shape (n_samples, input_dim)
        labels: numpy array of shape (n_samples,) with outcome indices
        input_dim: number of input features
        num_outcomes: number of possible outcomes
        epochs: training epochs
        lr: learning rate
        batch_size: batch size

    Returns:
        Trained PredictionNet model
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    X = torch.tensor(features, dtype=torch.float32)
    y = torch.tensor(labels, dtype=torch.long)
    dataset = TensorDataset(X, y)

    # 80/20 train/val split
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_set, val_set = torch.utils.data.random_split(dataset, [train_size, val_size])

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size)

    model = PredictionNet(input_dim, num_outcomes=num_outcomes).to(device)
    optimizer = optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    best_val_loss = float("inf")
    patience = 10
    patience_counter = 0

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            pred = model(X_batch)
            loss = criterion(pred, y_batch)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()

        # Validate
        model.eval()
        val_loss = 0
        correct = 0
        total = 0
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                pred = model(X_batch)
                val_loss += criterion(pred, y_batch).item()
                correct += (pred.argmax(dim=1) == y_batch).sum().item()
                total += len(y_batch)

        val_loss /= len(val_loader)
        accuracy = correct / total if total > 0 else 0

        if (epoch + 1) % 10 == 0:
            print(f"Epoch {epoch+1}/{epochs} — val_loss: {val_loss:.4f}, accuracy: {accuracy:.1%}")

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            torch.save(model.state_dict(), Path(__file__).parent / "models" / "prediction_net.pt")
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"Early stopping at epoch {epoch+1}")
                break

    return model


def predict(model, features):
    """
    Run inference on a feature vector.

    Returns: dict mapping outcome_index -> probability
    """
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        X = torch.tensor(features, dtype=torch.float32).unsqueeze(0).to(device)
        probs = model(X).squeeze(0).cpu().numpy()
    return {i: float(p) for i, p in enumerate(probs)}
