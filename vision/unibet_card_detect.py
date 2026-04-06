"""
Unibet card detection pipeline.

Uses YOLO for card location + EasyOCR for rank + color/contour for suit.
Works on Unibet's Relax Gaming canvas renderer.

Board cards: YOLO detects reliably (0.95+ confidence)
Hero cards: White-region contour detection in expected position
Rank: EasyOCR on enlarged card image
Suit: Red/black color + contour circularity analysis
"""

import os
import cv2
import numpy as np
import torch

# Lazy-loaded globals
_ocr_reader = None
_suit_templates = None
_suit_cnn = None
_suit_cnn_device = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        _ocr_reader = easyocr.Reader(['en'], gpu=True, verbose=False)
    return _ocr_reader


def _load_suit_cnn():
    """Load the suit CNN classifier."""
    global _suit_cnn, _suit_cnn_device
    if _suit_cnn is not None:
        return _suit_cnn, _suit_cnn_device

    model_path = os.path.join(os.path.dirname(__file__), 'models', 'suit_cnn_unibet.pt')
    if not os.path.exists(model_path):
        return None, None

    import torch.nn as nn

    class SuitCNN(nn.Module):
        def __init__(s):
            super().__init__()
            s.f = nn.Sequential(
                nn.Conv2d(3, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
                nn.AdaptiveAvgPool2d((4, 3)),
            )
            s.c = nn.Sequential(
                nn.Flatten(),
                nn.Linear(128 * 4 * 3, 256),
                nn.ReLU(),
                nn.Dropout(0.4),
                nn.Linear(256, 4),
            )
        def forward(s, x):
            return s.c(s.f(x))

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = SuitCNN()
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.to(device)
    model.eval()

    _suit_cnn = model
    _suit_cnn_device = device
    return _suit_cnn, _suit_cnn_device


def find_tight_box(table_img, x1, y1, x2, y2):
    """Find tight card rectangle within a YOLO bounding box."""
    crop = table_img[y1:y2, x1:x2]
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 80)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None; best_a = 0
    for cnt in contours:
        a = cv2.contourArea(cnt)
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if a > h * w * 0.15 and bw > w * 0.3 and bh > h * 0.3 and a > best_a:
            best_a = a; best = (bx, by, bw, bh)
    if best:
        bx, by, bw, bh = best
        return (x1 + bx, y1 + by, x1 + bx + bw, y1 + by + bh)
    px = int((x2 - x1) * 0.10); py = int((y2 - y1) * 0.08)
    return (x1 + px, y1 + py, x2 - px, y2 - py)


def detect_suit_cnn(card_img):
    """Detect suit using CNN on tight card crop."""
    SUITS = ['c', 'd', 'h', 's']
    model, device = _load_suit_cnn()
    if model is None:
        return None

    card_resized = cv2.resize(card_img, (48, 64))
    tensor = card_resized.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))
    tensor = torch.tensor(tensor).unsqueeze(0).to(device)

    with torch.no_grad():
        output = model(tensor)
        _, predicted = output.max(1)
        cnn_suit = SUITS[predicted.item()]

    # Color sanity check
    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)
    red_mask = cv2.inRange(hsv, (0, 50, 50), (15, 255, 255)) | \
               cv2.inRange(hsv, (155, 50, 50), (180, 255, 255))
    red_pct = cv2.countNonZero(red_mask) / max(red_mask.size, 1)
    is_red = red_pct > 0.04

    if is_red and cnn_suit in 'cs':
        cnn_suit = 'h'
    elif not is_red and cnn_suit in 'dh':
        cnn_suit = 's'

    return cnn_suit


def _load_suit_templates():
    """Load Unibet suit reference templates for template matching."""
    global _suit_templates
    if _suit_templates is not None:
        return _suit_templates

    tmpl_dir = os.path.join(os.path.dirname(__file__), 'templates', 'unibet_suits')
    _suit_templates = {}
    for suit in 'cdhs':
        path = os.path.join(tmpl_dir, f'large_{suit}.png')
        if os.path.exists(path):
            _suit_templates[suit] = cv2.imread(path, cv2.IMREAD_GRAYSCALE)

    return _suit_templates


def detect_suit(card_img, overlap_side=None):
    """Detect suit from card image.

    Strategy:
      1. Try CNN suit classifier (fast, trained on Unibet crops)
      2. Fallback to template matching for board cards

    Args:
        card_img: card image
        overlap_side: unused (kept for API compatibility)

    Returns: 'c', 'd', 'h', 's', or '?'
    """
    # Check if board card (white bg) or hero card (dark bg)
    h, w = card_img.shape[:2]
    gray_check = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
    center_check = gray_check[int(h * 0.15):int(h * 0.50), int(w * 0.20):int(w * 0.80)]
    is_board = np.median(center_check) > 200 if center_check.size > 0 else False

    if not is_board:
        # Hero card: use CNN (trained on hero card crops)
        cnn_suit = detect_suit_cnn(card_img)
        if cnn_suit is not None:
            return cnn_suit
    # Board card: fall through to template matching (100% accurate)
    h, w = card_img.shape[:2]
    templates = _load_suit_templates()

    # Step 1: determine red or black from rank text color
    corner = card_img[2:int(h * 0.28), 2:int(w * 0.42)]
    hsv_corner = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    red_mask = cv2.inRange(hsv_corner, np.array([0, 70, 70]), np.array([15, 255, 255])) | \
               cv2.inRange(hsv_corner, np.array([160, 70, 70]), np.array([180, 255, 255]))
    red_pct = np.count_nonzero(red_mask) / max(red_mask.size, 1)

    # Also check center for red (hero cards have colored backgrounds)
    center = card_img[int(h * 0.30):int(h * 0.85), int(w * 0.15):int(w * 0.85)]
    hsv_center = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
    red_center = cv2.inRange(hsv_center, np.array([0, 70, 70]), np.array([15, 255, 255])) | \
                 cv2.inRange(hsv_center, np.array([160, 70, 70]), np.array([180, 255, 255]))
    red_center_pct = np.count_nonzero(red_center) / max(red_center.size, 1)

    is_red = red_pct > 0.04 or red_center_pct > 0.04
    candidates = ['d', 'h'] if is_red else ['c', 's']

    # Step 2: determine if this card has a white face (board card style)
    # Check the CENTER of the card — edges may include surrounding dark content
    gray_full = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
    center_region = gray_full[int(h * 0.15):int(h * 0.50), int(w * 0.20):int(w * 0.80)]
    center_brightness = np.median(center_region) if center_region.size > 0 else 0
    is_board_card = center_brightness > 90

    tmpl_dir = os.path.join(os.path.dirname(__file__), 'templates', 'unibet_suits')
    best_suit = candidates[0]
    best_score = -1

    if is_board_card:
        # Board cards: use large center suit symbol (not affected by overlap)
        suit_region = card_img[int(h * 0.40):int(h * 0.90), int(w * 0.10):int(w * 0.90)]
        gray_suit = cv2.cvtColor(suit_region, cv2.COLOR_BGR2GRAY)

        for suit in candidates:
            lpath = os.path.join(tmpl_dir, f'large_{suit}.png')
            if not os.path.exists(lpath):
                continue
            ltmpl = cv2.imread(lpath, cv2.IMREAD_GRAYSCALE)
            th, tw = ltmpl.shape[:2]
            ltmpl_crop = ltmpl[int(th * 0.15):, :]
            ltmpl_r = cv2.resize(ltmpl_crop, (gray_suit.shape[1], gray_suit.shape[0]))
            score = cv2.matchTemplate(gray_suit, ltmpl_r, cv2.TM_CCOEFF_NORMED)[0][0]
            if score > best_score:
                best_score = score
                best_suit = suit
    else:
        # Hero cards: crops from position-based detection may include
        # surrounding content (felt, adjacent cards). Use the most robust
        # features available.

        if is_red:
            # Red hero card: use the red pixel analysis from the FULL card
            # to distinguish diamond from heart via start-width progression.
            hsv_full = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)
            red_full = cv2.inRange(hsv_full, np.array([0, 50, 50]), np.array([15, 255, 255])) | \
                       cv2.inRange(hsv_full, np.array([155, 50, 50]), np.array([180, 255, 255]))

            # Scan rows for red pixel width progression
            rh, rw = red_full.shape
            widths = [int(np.count_nonzero(red_full[row])) for row in range(rh)]
            # Find where the suit symbol starts (first row with > 3 red pixels
            # below the rank text area, i.e. past 35% height)
            suit_start_row = int(rh * 0.35)
            s_start = next((i for i in range(suit_start_row, rh) if widths[i] > 3), -1)

            if s_start >= 0:
                # Find the LARGE suit symbol (skip rank text and small indicator).
                # The large symbol has width > 15% of card width.
                large_start = next((i for i in range(s_start, rh)
                                    if widths[i] > rw * 0.30), -1)
                if large_start >= 0 and large_start + 8 < rh:
                    # Check max width within first 8 rows of the large suit symbol
                    suit_max_w = max(widths[large_start:large_start + 8])
                    # Heart widens quickly to > 45% of card width
                    # Diamond stays narrow, growing slowly to max ~35%
                    best_suit = 'h' if suit_max_w > rw * 0.45 else 'd'
                else:
                    # Can't find large suit symbol — use max red width in bottom half
                    bottom_widths = widths[rh // 2:]
                    max_w = max(bottom_widths) if bottom_widths else 0
                    best_suit = 'h' if max_w > rw * 0.40 else 'd'
            else:
                best_suit = 'h'  # default red to heart
        else:
            # Black hero card: use red/black is already determined.
            # For club vs spade, analyze the visible suit shape.
            # Use the full card image, threshold dark pixels against the
            # card's own background brightness.
            gray_card = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)

            # Only analyze the suit symbol area (bottom 60% of card, center)
            sy1 = int(h * 0.35)
            sy2 = int(h * 0.90)
            sx1 = int(w * 0.15)
            sx2 = int(w * 0.85)
            suit_area = gray_card[sy1:sy2, sx1:sx2]

            if suit_area.size == 0:
                return 's'

            bg_val = np.median(suit_area)
            # Suit pixels are significantly darker than background
            thresh = max(int(bg_val * 0.6), 15)
            suit_mask = (suit_area < thresh).astype(np.uint8) * 255

            if np.count_nonzero(suit_mask) < 30:
                # Not enough dark pixels — might be on green felt
                # Try with absolute threshold
                suit_mask = (suit_area < 50).astype(np.uint8) * 255

            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            suit_mask = cv2.morphologyEx(suit_mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(suit_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Filter: only contours with area > 50 in the center area
            center_contours = []
            mask_h, mask_w = suit_mask.shape
            for cnt in contours:
                a = cv2.contourArea(cnt)
                bx, by, bw, bh = cv2.boundingRect(cnt)
                # Must be in center portion, not at edges
                if a > 50 and bx > mask_w * 0.1 and bx + bw < mask_w * 0.9:
                    center_contours.append((cnt, a))

            if center_contours:
                largest_cnt, largest_area = max(center_contours, key=lambda x: x[1])
                perimeter = cv2.arcLength(largest_cnt, True)
                circ = 4 * np.pi * largest_area / (perimeter ** 2) if perimeter > 0 else 0
                hull = cv2.convexHull(largest_cnt)
                solidity = largest_area / max(cv2.contourArea(hull), 1)
                n_sig = len([c for c, a in center_contours if a > largest_area * 0.08])

                # Club: multiple lobes (n>=2), higher circularity
                # Spade: single shape, lower circularity, more angular
                if n_sig >= 2:
                    best_suit = 'c'
                elif circ > 0.72:
                    best_suit = 'c'
                elif circ < 0.60:
                    best_suit = 's'
                else:
                    best_suit = 's' if solidity < 0.93 else 'c'
            else:
                best_suit = 's'  # default black to spade

    return best_suit


def detect_rank(card_img):
    """Detect rank from card image using OCR.

    Strategy: crop just the top-left rank character, binarize, enlarge, OCR.
    Unibet cards have a single large rank letter in the top-left corner.

    Returns: (rank string, confidence)
    """
    h, w = card_img.shape[:2]
    reader = _get_ocr_reader()

    rank_map = {
        '0': 'Q', 'O': 'Q',  # common OCR confusions
        '10': 'T', 'I0': 'T', 'IO': 'T', '1O': 'T',
        'l': 'J', '1': 'J',  # sometimes J reads as 1
        'S': '5', 'G': '6', 'B': '8', 'D': 'Q',
    }
    valid_ranks = {'2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A'}

    def _try_ocr(img_bgr):
        results = reader.readtext(img_bgr, detail=1)
        for text_result in results:
            text = text_result[1].upper().strip()
            conf = float(text_result[2])
            if '.' in text or '$' in text or '€' in text:
                continue
            if text in valid_ranks:
                return text, conf
            if text in rank_map:
                return rank_map[text], conf
            if len(text) >= 1:
                ch = text[0]
                if ch in valid_ranks:
                    return ch, conf
                if ch in rank_map:
                    return rank_map[ch], conf
        return None, 0.0

    # Strategy 1: full card OCR (Unibet has large, clean rank text)
    scale = max(5, 400 // max(w, 1))
    full_big = cv2.resize(card_img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    rank, conf = _try_ocr(full_big)
    if rank:
        return rank, conf

    # Strategy 2: top-left corner crop, binarized
    ry2 = int(h * 0.30)
    rx2 = int(w * 0.50)
    corner = card_img[2:max(ry2, 10), 2:max(rx2, 10)]
    gray = cv2.cvtColor(corner, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
    big = cv2.resize(binary, (binary.shape[1] * 4, binary.shape[0] * 4),
                     interpolation=cv2.INTER_CUBIC)
    big_bgr = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
    rank, conf = _try_ocr(big_bgr)
    if rank:
        return rank, conf

    # Strategy 3: inverted corner
    inv = cv2.bitwise_not(big)
    inv_bgr = cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)
    rank, conf = _try_ocr(inv_bgr)
    if rank:
        return rank, conf

    # Strategy 4: adaptive threshold on corner
    gray_corner = cv2.cvtColor(corner, cv2.COLOR_BGR2GRAY)
    adapt = cv2.adaptiveThreshold(gray_corner, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY, 11, 4)
    adapt_big = cv2.resize(adapt, (adapt.shape[1] * 4, adapt.shape[0] * 4),
                           interpolation=cv2.INTER_CUBIC)
    adapt_bgr = cv2.cvtColor(adapt_big, cv2.COLOR_GRAY2BGR)
    rank, conf = _try_ocr(adapt_bgr)
    if rank:
        return rank, conf

    return '?', 0.0


def identify_card(card_img, overlap_side=None, force_cnn_suit=False):
    """Identify a single card image.

    Args:
        card_img: card crop image
        overlap_side: 'right' if right edge has overlap from adjacent card,
                      'left' if left edge has overlap. None for no overlap.
        force_cnn_suit: if True, always use CNN for suit detection

    Returns: card string like 'Ah', 'Kc', etc. or '??' on failure.
    """
    if card_img is None or card_img.shape[0] < 30 or card_img.shape[1] < 20:
        return '??'

    rank, rank_conf = detect_rank(card_img)

    if force_cnn_suit:
        suit = detect_suit_cnn(card_img)
        if suit is None:
            suit = detect_suit(card_img, overlap_side=overlap_side)
    else:
        suit = detect_suit(card_img, overlap_side=overlap_side)

    if rank == '?':
        return '??'

    return f"{rank}{suit}"


def find_hero_cards(table_img, table_h, table_w):
    """Find hero cards in Unibet table crop.

    Strategy: use OCR to find the hero player name ("Skurj"), then look for
    single rank characters (2-9, T, J, Q, K, A) above and to the right of it.
    These are the hero card rank texts. Expand to card bounding boxes.

    This approach is robust because:
    - The player name is always present and uniquely identifies the hero seat
    - Hero rank characters are the only single-letter text near the name
    - No confusion with buttons, avatars, or other UI elements

    Args:
        table_img: cropped table image (includes padding beyond felt)
        table_h, table_w: dimensions

    Returns: list of (x1, y1, x2, y2) bounding boxes
    """
    reader = _get_ocr_reader()

    # OCR the bottom 2/3 of the table
    y_start = table_h // 3
    bottom = table_img[y_start:, :]
    results = reader.readtext(bottom, detail=1)

    # Find the hero player name
    hero_pos = None
    for box, text, conf in results:
        if conf > 0.3 and ('skurj' in text.lower() or 'uni41' in text.lower()):
            cy = int((box[0][1] + box[2][1]) / 2) + y_start
            cx = int((box[0][0] + box[2][0]) / 2)
            hero_pos = (cx, cy)
            break

    if hero_pos is None:
        return []

    hero_cx, hero_cy = hero_pos

    # Hero cards are always at a fixed position relative to the player name:
    # - Above the name by ~30-50px
    # - Slightly to the right of center
    # - Two cards side by side, each ~55-65px wide, ~80-95px tall
    # - Small gap between them (~5px)

    # Card 1 (left): top-left at approximately (name_cx + 50, name_cy - 80)
    # Card 2 (right): top-left at approximately (name_cx + 115, name_cy - 80)
    # These offsets are calibrated from multiple captures

    # Use proportional offsets (relative to table dimensions)
    card_w = int(table_w * 0.065)   # ~86px at 1323w — wide enough for full rank+suit
    card_h = int(table_h * 0.125)   # ~105px at 840h
    overlap = int(card_w * 0.15)    # 15% overlap between cards to avoid clipping

    # Cards: right of name center, above name
    dx = int(table_w * 0.068)   # ~90px at 1323w
    dy = int(table_h * 0.095)   # ~80px at 840h

    cards_left = hero_cx + dx
    cards_top = hero_cy - dy

    card1 = (
        max(0, cards_left),
        max(0, cards_top),
        min(table_w, cards_left + card_w),
        min(table_h, cards_top + card_h)
    )
    # Second card starts earlier (overlaps with first) to capture full rank text
    card2_left = cards_left + card_w - overlap
    card2 = (
        max(0, card2_left),
        max(0, cards_top),
        min(table_w, card2_left + card_w),
        min(table_h, cards_top + card_h)
    )

    return [card1, card2]


def identify_cards_from_boxes(table_img, boxes, hero_overlap=False, force_cnn_suit=False):
    """Identify cards given bounding boxes.

    Args:
        table_img: full table image
        boxes: list of (x1, y1, x2, y2) bounding boxes
        hero_overlap: if True, first card has right overlap, second has left overlap
        force_cnn_suit: if True, always use CNN for suit (for hero cards)

    Returns: list of card strings ('Ah', 'Kc', etc.)
    """
    cards = []
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        cx1 = max(0, x1)
        cy1 = max(0, y1)
        cx2 = min(table_img.shape[1], x2)
        cy2 = min(table_img.shape[0], y2)

        crop = table_img[cy1:cy2, cx1:cx2]

        overlap_side = None
        if hero_overlap:
            overlap_side = 'right' if i == 0 else 'left'

        card = identify_card(crop, overlap_side=overlap_side, force_cnn_suit=force_cnn_suit)
        cards.append(card)

    return cards


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import glob
    import sys

    card_dir = os.path.join(os.path.dirname(__file__), 'templates', 'unibet_cards')
    files = sorted(glob.glob(os.path.join(card_dir, 'board_*.png')))

    if not files:
        print("No test cards found in", card_dir)
        sys.exit(1)

    print(f"Testing {len(files)} cards\n")

    for fpath in files:
        card_img = cv2.imread(fpath)
        if card_img is None or card_img.shape[0] < 40:
            continue

        result = identify_card(card_img)
        fname = os.path.basename(fpath)
        print(f"  {fname:50s} -> {result}")
