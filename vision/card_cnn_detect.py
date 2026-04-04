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
        """Identify hero cards using fixed crop positions to avoid overlap.
        YOLO tells us WHERE the cards are, but we crop each card individually
        using only the left portion of each YOLO box (where rank+suit are)."""
        if not card_boxes or len(card_boxes) < 2:
            return self.identify_cards(table_img, card_boxes)

        sorted_boxes = sorted(card_boxes, key=lambda c: c["x"])
        h, w = table_img.shape[:2]

        results = []
        colors = []

        for i, card in enumerate(sorted_boxes[:2]):
            x1 = max(0, card["x"] - 2)
            y1 = max(0, card["y"] - 2)
            # CRITICAL: only crop up to 40% of box width to avoid overlap
            card_w = card["w"]
            x2 = min(w, x1 + int(card_w * 0.40))
            y2 = min(h, card["y"] + card["h"] + 2)
            narrow_crop = table_img[y1:y2, x1:x2]

            if narrow_crop.size == 0 or narrow_crop.shape[1] < 10:
                continue

            # CNN on the narrow crop for rank
            ch, cw = narrow_crop.shape[:2]
            corner = narrow_crop[0:int(ch * 0.50), :]
            corner_resized = cv2.resize(corner, (CORNER_W, CORNER_H))
            tensor = torch.from_numpy(corner_resized).permute(2, 0, 1).float().unsqueeze(0) / 255.0

            with torch.no_grad():
                logits = self.model(tensor)
                probs = torch.softmax(logits, dim=1)
                conf, idx = probs.max(1)
                rank = self.idx_to_card[idx.item()][0]

            # Suit detection: use wider pip, cross-check with tighter pip
            pip_wide = narrow_crop[int(ch * 0.28):int(ch * 0.45), :]
            pip_tight = narrow_crop[int(ch * 0.32):int(ch * 0.40), int(cw*0.05):int(cw*0.70)]

            def _pip_shape(pip_img):
                pr = cv2.resize(pip_img, (40, 25))
                g = cv2.cvtColor(pr, cv2.COLOR_BGR2GRAY)
                _, m = cv2.threshold(g, 120, 255, cv2.THRESH_BINARY_INV)
                cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not cs: return 0.9, 0.6
                c = max(cs, key=cv2.contourArea)
                a = cv2.contourArea(c)
                if a < 10: return 0.9, 0.6
                ha = cv2.contourArea(cv2.convexHull(c))
                p = cv2.arcLength(c, True)
                return a/max(1,ha), 4*3.14159*a/max(1,p*p)

            sol_w, circ_w = _pip_shape(pip_wide)
            sol_t, circ_t = _pip_shape(pip_tight) if pip_tight.size > 0 else (sol_w, circ_w)

            # If the two crops disagree on suit category, use tighter (less contamination)
            def _suit_from_shape(sol, circ, is_r):
                if is_r:
                    return 'h' if sol < 0.96 else 'd'
                else:
                    return 'c' if sol < 0.82 and circ < 0.42 else 's'

            pip_resized = cv2.resize(pip_wide, (40, 25))

            # Color detection
            hsv = cv2.cvtColor(pip_resized, cv2.COLOR_BGR2HSV)
            r1 = cv2.inRange(hsv, np.array([0, 50, 50]), np.array([20, 255, 255]))
            r2 = cv2.inRange(hsv, np.array([150, 50, 50]), np.array([180, 255, 255]))
            red_px = cv2.countNonZero(r1) + cv2.countNonZero(r2)
            is_red = red_px > 20

            # Shape analysis
            gray = cv2.cvtColor(pip_resized, cv2.COLOR_BGR2GRAY)
            _, mask = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY_INV)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            sol, circ = 0.9, 0.6  # defaults
            if contours:
                c = max(contours, key=cv2.contourArea)
                area = cv2.contourArea(c)
                if area > 10:
                    hull_area = cv2.contourArea(cv2.convexHull(c))
                    peri = cv2.arcLength(c, True)
                    sol = area / max(1, hull_area)
                    circ = 4 * 3.14159 * area / max(1, peri * peri)

            # Classify suit from wide pip (more reliable shape)
            suit = _suit_from_shape(sol_w, circ_w, is_red)

            colors.append(is_red)
            results.append(f"{rank}{suit}")

        # Fix suited: if both same color, ensure same suit character
        if len(results) == 2 and len(colors) == 2:
            if colors[0] == colors[1]:
                s = 'h' if colors[0] else 's'
                results = [results[0][0] + s, results[1][0] + s]

        return results

    def identify_cards(self, table_img, card_boxes):
        """Identify multiple cards from YOLO detections."""
        sorted_boxes = sorted(card_boxes, key=lambda c: c["x"])
        colors = []  # track red/black for each card
        results = []
        h, w = table_img.shape[:2]
        for i, card in enumerate(sorted_boxes):
            x1 = max(0, card["x"] - 2)
            y1 = max(0, card["y"] - 2)
            x2 = min(w, card["x"] + card["w"] + 2)
            y2 = min(h, card["y"] + card["h"] + 2)
            crop = table_img[y1:y2, x1:x2]
            label, conf = self.identify(crop)

            # Detect color from ORIGINAL table position (not the crop)
            # For right cards in overlap, use a region that's definitely this card
            ch, cw = crop.shape[:2]
            if i > 0 and len(sorted_boxes) > 1:
                # Right card: overlap means left portion shows adjacent card
                # Use full top of crop for color — the red/black rank text is visible
                rank_region = crop[0:int(ch * 0.35), :]
            else:
                # Left card: use suit pip area (below rank, left side)
                # More reliable than rank text for color detection
                rank_region = crop[int(ch * 0.25):int(ch * 0.45), 0:int(cw * 0.25)]

            if rank_region.size > 0:
                hsv_r = cv2.cvtColor(rank_region, cv2.COLOR_BGR2HSV)
                r1 = cv2.inRange(hsv_r, np.array([0, 80, 80]), np.array([15, 255, 255]))
                r2 = cv2.inRange(hsv_r, np.array([155, 80, 80]), np.array([180, 255, 255]))
                red_px = cv2.countNonZero(r1) + cv2.countNonZero(r2)
                gray_r = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
                _, dark = cv2.threshold(gray_r, 80, 255, cv2.THRESH_BINARY_INV)
                dark_px = cv2.countNonZero(dark)
                is_red = red_px > 50
            else:
                is_red = label[1] in ('h', 'd') if len(label) == 2 else False

            colors.append(is_red)

            if label != "??" and conf > 0.3:
                results.append(label)
            else:
                results.append("??")

        # Fix suited detection using actual detected colors
        if len(results) == 2 and len(colors) == 2:
            r1, r2 = results[0][0], results[1][0]  # ranks
            c1, c2 = colors[0], colors[1]  # True=red, False=black
            # Assign suit based on detected color
            s1 = 'h' if c1 else 's'
            s2 = 'h' if c2 else 's'
            results = [f"{r1}{s1}", f"{r2}{s2}"]

        return [r for r in results if r != "??"]
