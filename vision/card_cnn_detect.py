"""
CNN-based card detection — uses the trained card CNN for identification.

Usage:
    from card_cnn_detect import CardCNNDetector
    detector = CardCNNDetector()
    label = detector.identify(card_crop)  # "Ah", "Ks", etc.
"""

import cv2
import numpy as np
import torch
import torch.nn as nn
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parent
MODEL_PATH = VISION_DIR / "models" / "card_cnn.pt"

CORNER_H, CORNER_W = 80, 60


class CardCNN(nn.Module):
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


class CardCNNDetector:
    def __init__(self):
        self.model = None
        self.idx_to_card = None
        self.device = torch.device("cpu")
        self._load()

    def _load(self):
        if not MODEL_PATH.exists():
            print("[CardCNN] Model not found")
            return
        checkpoint = torch.load(str(MODEL_PATH), map_location=self.device, weights_only=False)
        self.model = CardCNN().to(self.device)
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.eval()
        self.idx_to_card = checkpoint["idx_to_card"]
        print(f"[CardCNN] Loaded ({len(self.idx_to_card)} classes)")

    def identify(self, card_crop):
        """Identify a single card from its image crop.
        Returns (label, confidence) e.g. ("Ah", 0.98)."""
        if self.model is None:
            return "??", 0

        h, w = card_crop.shape[:2]
        if h < 10 or w < 10:
            return "??", 0

        # Extract corner — use only left 35% width to avoid adjacent card bleed
        # The rank character and suit pip are in the top-left corner
        corner = card_crop[0:int(h * 0.50), 0:int(w * 0.35)]
        corner = cv2.resize(corner, (CORNER_W, CORNER_H))

        tensor = torch.from_numpy(corner).permute(2, 0, 1).float().unsqueeze(0) / 255.0
        tensor = tensor.to(self.device)

        with torch.no_grad():
            logits = self.model(tensor)
            probs = torch.softmax(logits, dim=1)
            conf, idx = probs.max(1)
            label = self.idx_to_card[idx.item()]
            return label, conf.item()

    def identify_cards(self, table_img, card_boxes):
        """Identify multiple cards from YOLO detections."""
        results = []
        h, w = table_img.shape[:2]
        for card in card_boxes:
            x1 = max(0, card["x"] - 2)
            y1 = max(0, card["y"] - 2)
            x2 = min(w, card["x"] + card["w"] + 2)
            y2 = min(h, card["y"] + card["h"] + 2)
            crop = table_img[y1:y2, x1:x2]
            label, conf = self.identify(crop)
            if label != "??" and conf > 0.3:
                results.append(label)
        return results
