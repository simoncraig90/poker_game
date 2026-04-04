"""
CNN-based card detection — uses the trained card CNN for identification.

Usage:
    from card_cnn_detect import CardCNNDetector
    detector = CardCNNDetector()
    label = detector.identify(card_crop)  # "Ah", "Ks", etc.
"""

import cv2
import numpy as np
import os
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


# Suit pip templates (averaged from all 13 cards per suit)
SUIT_PIP_DIR = VISION_DIR / "templates" / "ps_cards"
_suit_templates = None

def _load_suit_templates():
    global _suit_templates
    if _suit_templates is not None:
        return _suit_templates
    _suit_templates = {}
    for suit_char, name in [('s', 'spade'), ('c', 'club'), ('h', 'heart'), ('d', 'diamond')]:
        path = SUIT_PIP_DIR / f"_suit_{name}.png"
        if path.exists():
            _suit_templates[suit_char] = cv2.imread(str(path))
    return _suit_templates


def detect_suit_from_pip(card_crop):
    """Detect suit using contour shape features of the pip area."""
    h, w = card_crop.shape[:2]

    # Suit pip area
    pip = card_crop[int(h * 0.30):int(h * 0.50), int(w * 0.02):int(w * 0.28)]
    if pip.size == 0:
        return None, 0

    pip = cv2.resize(pip, (50, 40))

    # First: determine color (red vs black)
    hsv = cv2.cvtColor(pip, cv2.COLOR_BGR2HSV)
    r1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
    r2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
    red_px = cv2.countNonZero(r1) + cv2.countNonZero(r2)

    gray = cv2.cvtColor(pip, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)

    # Get contour features
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return ('h' if red_px > 30 else 's'), 0.3

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < 20:
        return ('h' if red_px > 30 else 's'), 0.3

    hull_area = cv2.contourArea(cv2.convexHull(largest))
    solidity = area / max(1, hull_area)
    perimeter = cv2.arcLength(largest, True)
    circularity = 4 * 3.14159 * area / max(1, perimeter * perimeter)

    is_red = red_px > 30

    if is_red:
        # Heart: very low solidity (~0.34) due to indentation
        # Diamond: medium solidity (~0.58)
        if solidity < 0.50:
            return 'h', 0.8
        else:
            return 'd', 0.8
    else:
        # Spade: high solidity (~0.92) and circularity (~0.59)
        # Club: lower solidity (~0.78) and circularity (~0.32)
        if solidity > 0.85 and circularity > 0.45:
            return 's', 0.8
        else:
            return 'c', 0.8


class CardCNNDetector:
    def __init__(self):
        self.model = None
        self.idx_to_card = None
        self.device = torch.device("cpu")
        self._load()
        _load_suit_templates()  # preload suit templates

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
            cnn_label = self.idx_to_card[idx.item()]
            cnn_conf = conf.item()

            rank = cnn_label[0]

            # Determine if card is overlapping (narrow crop)
            is_overlapping = w < h * 0.80

            if is_overlapping:
                # Overlapping card: pip area contaminated
                # Use rank text color only (red/black) + default suit
                rank_region = card_crop[0:int(h * 0.25), 0:int(w * 0.35)]
                hsv_r = cv2.cvtColor(rank_region, cv2.COLOR_BGR2HSV)
                r1 = cv2.inRange(hsv_r, np.array([0, 80, 80]), np.array([15, 255, 255]))
                r2 = cv2.inRange(hsv_r, np.array([155, 80, 80]), np.array([180, 255, 255]))
                red_px = cv2.countNonZero(r1) + cv2.countNonZero(r2)
                gray_r = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
                _, dark = cv2.threshold(gray_r, 80, 255, cv2.THRESH_BINARY_INV)
                dark_px = cv2.countNonZero(dark)
                is_red = red_px > 100 and red_px > dark_px * 0.5
                # Default to most common suit per color
                suit = 'h' if is_red else 's'
                label = f"{rank}{suit}"
            else:
                # Non-overlapping: use contour-based suit detection
                pip_suit, pip_conf = detect_suit_from_pip(card_crop)
                if pip_suit and pip_conf > 0.5:
                    label = f"{rank}{pip_suit}"
                else:
                    label = cnn_label

            return label, cnn_conf

    def identify_hero_from_table(self, table_img, card_boxes):
        """Identify hero cards using template matching against PS board card captures.
        Normalizes both hero corners and template corners to a standard size before matching."""
        if not card_boxes:
            return []

        MATCH_W, MATCH_H = 48, 64  # standard size for comparison

        # Lazy-load templates — normalize all to standard size
        if not hasattr(self, '_card_templates') or self._card_templates is None:
            self._card_templates = {}
            tmpl_dir = os.path.join(os.path.dirname(__file__), '..', 'client', 'ps_assets', 'cards')
            if not os.path.isdir(tmpl_dir):
                tmpl_dir = os.path.join(os.path.dirname(__file__), 'templates', 'ps_board_cards')
            for f in os.listdir(tmpl_dir):
                if f.endswith('.png') and len(f) >= 5:
                    name = f[:-4]
                    img = cv2.imread(os.path.join(tmpl_dir, f))
                    if img is not None:
                        h, w = img.shape[:2]
                        # Upper-left corner: rank index + suit pip
                        corner = img[0:int(h * 0.40), 0:int(w * 0.45)]
                        normalized = cv2.resize(corner, (MATCH_W, MATCH_H))
                        self._card_templates[name] = normalized
            print(f"[tmpl] Loaded {len(self._card_templates)} card templates for matching")

        sorted_boxes = sorted(card_boxes, key=lambda c: c["x"])
        h, w = table_img.shape[:2]

        results = []

        for i, card in enumerate(sorted_boxes[:2]):
            x1 = max(0, card["x"])
            y1 = max(0, card["y"])
            card_w = card["w"]
            card_h = card["h"]
            # Crop left 40% to avoid overlap
            x2 = min(w, x1 + int(card_w * 0.40))
            y2 = min(h, y1 + card_h)
            narrow_crop = table_img[y1:y2, x1:x2]

            if narrow_crop.size == 0 or narrow_crop.shape[1] < 10:
                continue

            ch, cw = narrow_crop.shape[:2]

            # Upper-left corner: same proportions as template (40% height, full narrow width)
            corner = narrow_crop[0:int(ch * 0.40), :]
            if corner.size == 0:
                continue

            # Normalize to standard size
            normalized = cv2.resize(corner, (MATCH_W, MATCH_H))

            # Match against all 52 templates
            best_card = None
            best_score = -1
            for name, tmpl in self._card_templates.items():
                score = cv2.matchTemplate(normalized, tmpl, cv2.TM_CCOEFF_NORMED)[0][0]
                if score > best_score:
                    best_score = score
                    best_card = name

            if best_card and best_score > 0.3:
                results.append(best_card)

        return results

    def identify_cards(self, table_img, card_boxes):
        """Identify board cards using template matching (same as hero cards)."""
        if not card_boxes:
            return []

        MATCH_W, MATCH_H = 48, 64

        # Ensure templates are loaded
        if not hasattr(self, '_card_templates') or self._card_templates is None:
            self.identify_hero_from_table(table_img, [])  # triggers template load

        sorted_boxes = sorted(card_boxes, key=lambda c: c["x"])
        h, w = table_img.shape[:2]
        results = []

        for card in sorted_boxes:
            x1 = max(0, card["x"])
            y1 = max(0, card["y"])
            x2 = min(w, card["x"] + card["w"])
            y2 = min(h, card["y"] + card["h"])
            crop = table_img[y1:y2, x1:x2]

            if crop.size == 0 or crop.shape[1] < 10:
                continue

            ch, cw = crop.shape[:2]
            # Board cards don't overlap — use full left 45% width, top 40% height
            corner = crop[0:int(ch * 0.40), 0:int(cw * 0.45)]
            if corner.size == 0:
                continue

            normalized = cv2.resize(corner, (MATCH_W, MATCH_H))

            best_card = None
            best_score = -1
            for name, tmpl in self._card_templates.items():
                score = cv2.matchTemplate(normalized, tmpl, cv2.TM_CCOEFF_NORMED)[0][0]
                if score > best_score:
                    best_score = score
                    best_card = name

            if best_card and best_score > 0.3:
                results.append(best_card)

        return results
