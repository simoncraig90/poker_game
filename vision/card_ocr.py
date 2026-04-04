"""
OCR-based card identification — reads rank + suit from card images.

Theme-independent: works on any poker client by reading the rank character
from the top-left corner and detecting suit by color + shape.

Usage:
    from card_ocr import identify_card_ocr
    label = identify_card_ocr(card_crop)  # returns "Ah", "Ks", etc.
"""

import cv2
import numpy as np

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=True, verbose=False)
    return _reader


# Rank character mapping (OCR output → standard)
RANK_MAP = {
    'A': 'A', 'a': 'A',
    'K': 'K', 'k': 'K',
    'Q': 'Q', 'q': 'Q',
    'J': 'J', 'j': 'J',
    '10': 'T', 'T': 'T', 't': 'T', '1O': 'T', 'IO': 'T',
    '9': '9', '8': '8', '7': '7', '6': '6',
    '5': '5', '4': '4', '3': '3', '2': '2',
}


def _detect_rank(corner_crop):
    """OCR the rank from the top-left corner of a card."""
    reader = _get_reader()

    # Preprocess: increase contrast, threshold
    gray = cv2.cvtColor(corner_crop, cv2.COLOR_BGR2GRAY)

    # Try both black text (spades/clubs) and red text (hearts/diamonds)
    # Black text on white background
    _, thresh_black = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    # Red text: extract red channel
    hsv = cv2.cvtColor(corner_crop, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(red1, red2)

    # Combine: any dark or red pixel is text
    text_mask = cv2.bitwise_or(thresh_black, red_mask)

    # OCR on the original corner (EasyOCR handles color)
    # Try with digits+letters first (to catch "10")
    results = reader.readtext(corner_crop, allowlist='AKQJT1098765432Oo')
    if results:
        for bbox, text, conf in results:
            raw = text.strip()
            text = raw.upper().replace('O', '0')  # O→0 fix
            # Direct match
            if text in RANK_MAP:
                return RANK_MAP[text], conf
            # "10" might be read as "1O", "IO", "10"
            if '10' in text or '1' in text:
                return 'T', conf
            # Handle partial reads
            for key in RANK_MAP:
                if key in text:
                    return RANK_MAP[key], conf

    # Fallback: try on thresholded image
    # Convert mask to 3-channel for EasyOCR
    thresh_bgr = cv2.cvtColor(cv2.bitwise_not(text_mask), cv2.COLOR_GRAY2BGR)
    results = reader.readtext(thresh_bgr, allowlist='AKQJT1098765432')
    if results:
        for bbox, text, conf in results:
            text = text.strip().upper()
            if text in RANK_MAP:
                return RANK_MAP[text], conf

    return None, 0


def _detect_suit(card_crop):
    """Detect suit from card image using color + contour shape."""
    h, w = card_crop.shape[:2]
    is_narrow = w < h * 0.60  # overlapping card

    # For narrow cards, check color from rank text area (more reliable)
    if is_narrow:
        rank_area = card_crop[0:int(h * 0.30), 0:int(w * 0.80)]
        hsv_r = cv2.cvtColor(rank_area, cv2.COLOR_BGR2HSV)
        red1r = cv2.inRange(hsv_r, np.array([0, 60, 60]), np.array([15, 255, 255]))
        red2r = cv2.inRange(hsv_r, np.array([155, 60, 60]), np.array([180, 255, 255]))
        red_rank = cv2.countNonZero(red1r) + cv2.countNonZero(red2r)
        # If rank text is red → hearts or diamonds
        # Can't easily distinguish h/d from narrow crop, use rank color only
        if red_rank > 50:
            return 'h'  # default red narrow = heart (most common)
        else:
            return 's'  # default black narrow = spade (most common)

    # Suit pip area: below rank text, top-left quadrant
    suit_area = card_crop[int(h * 0.28):int(h * 0.55), 0:int(w * 0.45)]
    if suit_area.size == 0:
        return None

    hsv = cv2.cvtColor(suit_area, cv2.COLOR_BGR2HSV)

    # Red detection (hearts/diamonds)
    red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
    red_px = cv2.countNonZero(red1) + cv2.countNonZero(red2)

    # Black detection (spades/clubs)
    gray = cv2.cvtColor(suit_area, cv2.COLOR_BGR2GRAY)
    _, black_mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    black_px = cv2.countNonZero(black_mask)

    is_red = red_px > black_px * 0.3 and red_px > 30

    if is_red:
        red_mask = cv2.bitwise_or(red1, red2)
        sh, sw = suit_area.shape[:2]
        # Heart vs diamond: check where the widest part is
        # Heart: widest in top third (the two bumps)
        # Diamond: widest in the middle
        row_widths = []
        for row in range(sh):
            pixels = cv2.countNonZero(red_mask[row:row+1, :])
            row_widths.append(pixels)
        if sum(row_widths) < 20:
            return 'h'  # too few pixels, default heart
        # Find the row with max width
        max_row = row_widths.index(max(row_widths))
        max_row_pct = max_row / max(1, sh)
        # Heart: widest near top (0.2-0.4), Diamond: widest near middle (0.4-0.6)
        if max_row_pct < 0.45:
            return 'h'  # heart — widest at top
        else:
            return 'd'  # diamond — widest at middle
    else:
        contours, _ = cv2.findContours(black_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)
            hull = cv2.convexHull(largest)
            hull_area = cv2.contourArea(hull)
            solidity = area / max(1, hull_area)
            # Spade: pointed top, smooth → solidity ~0.85+
            # Club: three bumps → solidity ~0.70-0.80
            if solidity > 0.82:
                return 's'  # spade
            else:
                return 'c'  # club
        return 's'  # default black = spade


def identify_card_ocr(card_crop):
    """
    Identify a card using OCR + color analysis.

    Args:
        card_crop: BGR image of a single card

    Returns:
        (label, confidence) where label is like "Ah", "Ks", etc.
        Returns ("??", 0) on failure.
    """
    if card_crop is None or card_crop.size == 0:
        return "??", 0

    h, w = card_crop.shape[:2]
    if h < 20 or w < 15:
        return "??", 0

    # Crop top-left corner for rank
    corner = card_crop[0:int(h * 0.35), 0:int(w * 0.50)]

    rank, conf = _detect_rank(corner)
    if rank is None:
        return "??", 0

    suit = _detect_suit(card_crop)
    if suit is None:
        return "??", 0

    return f"{rank}{suit}", conf


def identify_cards_ocr(table_img, card_boxes):
    """
    Identify multiple cards using OCR.

    Args:
        table_img: full table image
        card_boxes: list of {x, y, w, h} dicts from YOLO

    Returns:
        list of card labels like ["Ah", "Ks"]
    """
    results = []
    h, w = table_img.shape[:2]
    for card in card_boxes:
        x1 = max(0, card["x"] - 2)
        y1 = max(0, card["y"] - 2)
        x2 = min(w, card["x"] + card["w"] + 2)
        y2 = min(h, card["y"] + card["h"] + 2)
        crop = table_img[y1:y2, x1:x2]
        label, conf = identify_card_ocr(crop)
        if label != "??":
            results.append(label)
    return results
