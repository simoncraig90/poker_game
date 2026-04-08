"""
EquityNet inference adapter.

Wraps the trained EquityNet model from train_equity.py for inference
inside the postflop engine. Replaces the heuristic _eval_strength()
with a real Monte-Carlo-trained equity prediction.

Cards are encoded as `(rank - 2) * 4 + suit` matching generate-equity-data.js.
"""

import os
import threading

# Card encoding constants
RANK_MAP = {'2': 0, '3': 1, '4': 2, '5': 3, '6': 4, '7': 5, '8': 6,
            '9': 7, 'T': 8, 'J': 9, 'Q': 10, 'K': 11, 'A': 12}
SUIT_MAP = {'c': 0, 'd': 1, 'h': 2, 's': 3}


def card_to_id(card_str):
    """'Ah' → integer 0-51 matching the training data encoding."""
    if not card_str or len(card_str) < 2:
        return 52  # padding
    r = RANK_MAP.get(card_str[0].upper(), 0)
    s = SUIT_MAP.get(card_str[1].lower(), 0)
    return r * 4 + s


def card_dict_to_id(card):
    """{'rank': 14, 'suit': 4} → integer 0-51.
    Note: this expects rank=2-14 and suit=1-4 (postflop_engine convention)."""
    return (card['rank'] - 2) * 4 + (card['suit'] - 1)


# Lazy-loaded singleton
_model = None
_model_lock = threading.Lock()


def _get_model():
    """Load EquityNet on first use."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            import torch
            import sys
            VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if VISION_DIR not in sys.path:
                sys.path.insert(0, VISION_DIR)
            from train_equity import EquityNet
            model_path = os.path.join(VISION_DIR, "models", "equity_model.pt")
            if not os.path.exists(model_path):
                print(f"[equity_nn] Model not found at {model_path}")
                return None
            m = EquityNet(embed_dim=32, hidden=256)
            m.load_state_dict(torch.load(model_path, map_location='cpu', weights_only=True))
            m.eval()
            _model = m
            print(f"[equity_nn] Loaded EquityNet from {model_path}")
            return _model
        except Exception as e:
            print(f"[equity_nn] Load failed: {e}")
            return None


def _board_features(hero_card_ids, board_card_ids):
    """Compute the 14 hand-crafted features the NN expects.
    Must match generate-equity-data.js output format."""
    # Decode back to rank/suit for feature computation
    def decode(idx):
        if idx >= 52:
            return None
        return (idx // 4 + 2, idx % 4)  # (rank 2-14, suit 0-3)

    hero = [decode(i) for i in hero_card_ids if i < 52]
    board = [decode(i) for i in board_card_ids if i < 52]

    if len(hero) < 2:
        return [0.0] * 14

    h1_rank, h1_suit = hero[0]
    h2_rank, h2_suit = hero[1]

    suited = 1 if h1_suit == h2_suit else 0
    pair = 1 if h1_rank == h2_rank else 0
    gap = abs(h1_rank - h2_rank) / 12.0
    high_rank = max(h1_rank, h2_rank) / 14.0
    low_rank = min(h1_rank, h2_rank) / 14.0

    # hits = how many board cards match hero ranks
    board_ranks = [c[0] for c in board]
    hits = sum(1 for c in board if c[0] in (h1_rank, h2_rank)) / 4.0

    # Hero flush draw
    hero_flush = 0
    if suited and board:
        same_suit = sum(1 for c in board if c[1] == h1_suit)
        hero_flush = 1 if same_suit >= 2 else 0

    # Board features
    paired = 0
    flush3 = 0
    flush4 = 0
    straight3 = 0
    straight4 = 0
    high_card = 0.0

    if board:
        # Paired board
        rank_counts = {}
        for r in board_ranks:
            rank_counts[r] = rank_counts.get(r, 0) + 1
        paired = 1 if max(rank_counts.values()) >= 2 else 0

        # Flush features
        suit_counts = {}
        for c in board:
            suit_counts[c[1]] = suit_counts.get(c[1], 0) + 1
        max_suit = max(suit_counts.values())
        flush3 = 1 if max_suit >= 3 else 0
        flush4 = 1 if max_suit >= 4 else 0

        # Straight features (consecutive ranks)
        unique_ranks = sorted(set(board_ranks))
        max_consec = 1
        cur = 1
        for i in range(1, len(unique_ranks)):
            if unique_ranks[i] == unique_ranks[i - 1] + 1:
                cur += 1
                max_consec = max(max_consec, cur)
            else:
                cur = 1
        straight3 = 1 if max_consec >= 3 else 0
        straight4 = 1 if max_consec >= 4 else 0

        high_card = max(board_ranks) / 14.0

    board_len = len(board) / 5.0

    return [
        float(suited), float(pair), gap, high_rank, low_rank,
        hits, float(hero_flush),
        float(paired), float(flush3), float(flush4),
        float(straight3), float(straight4), high_card,
        board_len,
    ]


def equity_nn(hero_cards, board_cards):
    """
    Predict hand equity (0-1) using the trained EquityNet.

    Args:
        hero_cards: list of card strings ['Ah', 'Kh'] OR list of dicts [{'rank':14,'suit':3}, ...]
        board_cards: list of card strings (0-5) OR list of dicts

    Returns:
        float 0-1, or None if model unavailable.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        import torch

        # Normalize inputs to card IDs
        if hero_cards and isinstance(hero_cards[0], dict):
            hero_ids = [card_dict_to_id(c) for c in hero_cards]
            board_ids = [card_dict_to_id(c) for c in board_cards]
        else:
            hero_ids = [card_to_id(c) for c in hero_cards]
            board_ids = [card_to_id(c) for c in board_cards]

        if len(hero_ids) < 2:
            return None

        # Pad board to 5 with id 52 (no card)
        board_padded = list(board_ids) + [52] * (5 - len(board_ids))

        # Build feature vector
        feats = _board_features(hero_ids, board_ids)

        # Tensors
        hero_t = torch.tensor([hero_ids], dtype=torch.long)
        board_t = torch.tensor([board_padded], dtype=torch.long)
        feats_t = torch.tensor([feats], dtype=torch.float32)

        with torch.no_grad():
            eq = model(hero_t, board_t, feats_t).item()
        return float(eq)
    except Exception as e:
        print(f"[equity_nn] Inference error: {e}")
        return None


if __name__ == "__main__":
    # Smoke test
    test_cases = [
        (['Ah', 'Ah'], [], "Pair of Aces preflop"),  # invalid (same card) but should not crash
        (['Ah', 'Ks'], [], "AKo preflop"),
        (['Ah', 'Kh'], ['Td', '4c', '9s'], "AK on dry low board"),
        (['Ah', 'Kh'], ['Ah', '4c', '9s'], "AK with top pair"),
        (['9h', '9c'], ['9d', '4c', '2s'], "Set of 9s"),
        (['Qs', 'Kd'], ['Ac', '9c', 'Jc', '2h', 'Jd'], "KQ no pair on paired board"),
        (['7c', '2d'], ['Ah', 'Kh', 'Qh', 'Jh', 'Th'], "72 vs broadway flush board"),
    ]
    for hero, board, desc in test_cases:
        eq = equity_nn(hero, board)
        print(f"  {desc}: {hero} on {board} → {eq:.3f}" if eq is not None else f"  {desc}: NONE")
