"""
Policy network for the RL poker bot.

Architecture:
  - Card embeddings: 53 vocab (0-51 cards + 52 empty) -> 8-dim each
  - 7 card slots (2 hero + 5 board) = 56 dims from cards
  - Numeric features: pot, stack, call amount, pot odds, num opponents,
    street (4 one-hot), position, hand strength, bet-to-pot, SPR = 13 dims
  - Total input: 56 + 13 = 69 dims
  - 3 FC layers: 256 -> 128 -> 64
  - Two output heads:
    - Action head: 5 outputs (fold/check/call/bet/raise) with softmax
    - Sizing head: 1 output (bet/raise size as fraction of pot, sigmoid * 3)

~200K parameters.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


# Action indices must match the Node.js ACTION_MAP
ACTION_NAMES = ["FOLD", "CHECK", "CALL", "BET", "RAISE"]
NUM_ACTIONS = 5


class PolicyNet(nn.Module):
    """Policy network with card embeddings and dual output heads."""

    def __init__(self, card_vocab=53, card_embed_dim=8, num_card_slots=7,
                 num_extra_features=13, hidden_sizes=(256, 128, 64)):
        super().__init__()

        self.card_embed_dim = card_embed_dim
        self.num_card_slots = num_card_slots
        self.num_extra_features = num_extra_features

        # Card embedding layer (shared for all card slots)
        self.card_embed = nn.Embedding(card_vocab, card_embed_dim)

        # Input dimension after flattening card embeddings + extra features
        input_dim = num_card_slots * card_embed_dim + num_extra_features

        # Shared trunk
        layers = []
        prev_dim = input_dim
        for h in hidden_sizes:
            layers.extend([
                nn.Linear(prev_dim, h),
                nn.ReLU(),
                nn.LayerNorm(h),
                nn.Dropout(0.1),
            ])
            prev_dim = h

        self.trunk = nn.Sequential(*layers)

        # Action head: probability over 5 actions (logits, softmax applied later)
        self.action_head = nn.Linear(hidden_sizes[-1], NUM_ACTIONS)

        # Sizing head: bet/raise size as fraction of pot (0 to 3x pot)
        self.sizing_head = nn.Sequential(
            nn.Linear(hidden_sizes[-1], 1),
            nn.Sigmoid(),  # outputs 0-1, multiply by 3 for 0-3x pot
        )

    def forward(self, cards, extra_features):
        """
        cards: (B, 7) long tensor - card indices 0-52
        extra_features: (B, 13) float tensor - numeric features

        Returns:
            action_logits: (B, 5) - raw logits for action distribution
            sizing: (B, 1) - bet sizing as fraction of pot (0-3)
        """
        # Embed cards
        emb = self.card_embed(cards)             # (B, 7, 8)
        emb_flat = emb.view(emb.size(0), -1)    # (B, 56)

        # Concatenate with extra features
        x = torch.cat([emb_flat, extra_features], dim=1)  # (B, 66)

        # Shared trunk
        h = self.trunk(x)  # (B, 64)

        # Action head
        action_logits = self.action_head(h)  # (B, 5)

        # Sizing head
        sizing = self.sizing_head(h) * 3.0  # (B, 1), range 0-3

        return action_logits, sizing

    def get_action_probs(self, cards, extra_features, legal_mask=None):
        """
        Get action probabilities, optionally masked to legal actions only.

        legal_mask: (B, 5) bool tensor - True for legal actions
        """
        action_logits, sizing = self.forward(cards, extra_features)

        if legal_mask is not None:
            # Set illegal actions to -inf before softmax
            action_logits = action_logits.masked_fill(~legal_mask, float('-inf'))

        action_probs = F.softmax(action_logits, dim=-1)
        return action_probs, sizing


def count_parameters(model):
    return sum(p.numel() for p in model.parameters())


def build_feature_tensors(state_dict, device="cpu"):
    """
    Convert a state dictionary (from JSONL) to model input tensors.

    state_dict has keys: heroCard1, heroCard2, boardCards (list of 5),
    potNorm, stackNorm, callNorm, potOdds, numOpponents, streetOneHot (list of 4), posNorm
    """
    s = state_dict

    cards = torch.tensor(
        [s["heroCard1"], s["heroCard2"]] + s["boardCards"],
        dtype=torch.long, device=device
    ).unsqueeze(0)  # (1, 7)

    extra = torch.tensor(
        [s["potNorm"], s["stackNorm"], s["callNorm"], s["potOdds"],
         s["numOpponents"] / 5.0,  # normalize to 0-1
         ] + s["streetOneHot"] + [
         s["posNorm"],
         s.get("handStrength", 0.5),
         s.get("betToPot", 0.0),
         s.get("sprNorm", 0.5),
        ],
        dtype=torch.float32, device=device
    ).unsqueeze(0)  # (1, 13)

    return cards, extra


if __name__ == "__main__":
    # Quick sanity check
    model = PolicyNet()
    print(f"PolicyNet parameters: {count_parameters(model):,}")

    # Dummy forward pass
    cards = torch.randint(0, 53, (4, 7))
    extra = torch.randn(4, 13)
    logits, sizing = model(cards, extra)
    print(f"Action logits shape: {logits.shape}")  # (4, 5)
    print(f"Sizing shape: {sizing.shape}")          # (4, 1)

    probs, _ = model.get_action_probs(cards, extra)
    print(f"Action probs: {probs[0].detach().numpy()}")
    print(f"Sizing: {sizing[0].item():.3f}x pot")
