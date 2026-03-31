"""
Card identification via template matching.
No OCR needed — matches card corners against a library of known rank+suit templates.
"""

import cv2
import numpy as np
import os

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
CORNER_W, CORNER_H = 40, 56


def _load_templates():
    """Load rank and suit templates from disk."""
    ranks = {}
    suits = {}
    hero_ranks = {}

    rank_dir = os.path.join(TEMPLATE_DIR, "ranks")
    suit_dir = os.path.join(TEMPLATE_DIR, "suits")
    hero_dir = os.path.join(TEMPLATE_DIR, "hero_ranks")

    if os.path.isdir(rank_dir):
        for f in os.listdir(rank_dir):
            if f.endswith(".png"):
                label = f.replace(".png", "")
                img = cv2.imread(os.path.join(rank_dir, f))
                if img is not None:
                    ranks[label] = img

    if os.path.isdir(suit_dir):
        for f in os.listdir(suit_dir):
            if f.endswith(".png"):
                label = f.replace(".png", "")
                img = cv2.imread(os.path.join(suit_dir, f))
                if img is not None:
                    suits[label] = img

    if os.path.isdir(hero_dir):
        for f in os.listdir(hero_dir):
            if f.endswith(".png"):
                label = f.replace(".png", "")
                img = cv2.imread(os.path.join(hero_dir, f))
                if img is not None:
                    hero_ranks[label] = img

    # Also load full corner templates as fallback
    full = {}
    for f in os.listdir(TEMPLATE_DIR):
        if f.endswith(".png") and len(f.replace(".png", "")) == 2:
            label = f.replace(".png", "")
            img = cv2.imread(os.path.join(TEMPLATE_DIR, f))
            if img is not None:
                full[label] = img

    return ranks, suits, full, hero_ranks


# Load once at import
_ranks, _suits, _full, _hero_ranks = _load_templates()


def _extract_corner(card_img):
    """Extract and normalize the top-left corner of a card image."""
    h, w = card_img.shape[:2]
    if h < 10 or w < 10:
        return np.zeros((CORNER_H, CORNER_W, 3), dtype=np.uint8)
    # Normal card: use top 40%, left 50%
    # Narrow card (overlapping): use top 40%, wider fraction
    if w < h * 0.5:
        corner = card_img[0:int(h * 0.4), :]
    else:
        corner = card_img[0:int(h * 0.4), 0:int(w * 0.5)]
    if corner.size == 0:
        return np.zeros((CORNER_H, CORNER_W, 3), dtype=np.uint8)
    return cv2.resize(corner, (CORNER_W, CORNER_H))


def _detect_color(corner):
    """Detect if the card corner has red or black ink."""
    hsv = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, np.array([0, 80, 80]), np.array([10, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([160, 80, 80]), np.array([180, 255, 255]))
    return "r" if cv2.countNonZero(red1) + cv2.countNonZero(red2) > 20 else "b"


def _match_rank(corner, use_hero_templates=False):
    """Match the rank character from a corner image."""
    # Extract rank region (top 55%)
    rank_region = corner[0:int(CORNER_H * 0.55), :]
    color = _detect_color(rank_region)

    templates = _hero_ranks if use_hero_templates else _ranks
    best_rank = "?"
    best_score = -1

    for label, tmpl in templates.items():
        # Rank templates are named like 'K_r', 'K_b' — prefer same color
        parts = label.split("_")
        rank_char = parts[0]
        tmpl_color = parts[1] if len(parts) > 1 else None

        # Skip wrong-color templates
        if tmpl_color and tmpl_color != color:
            continue

        tmpl_rank = tmpl
        if use_hero_templates:
            # Hero templates are full corners — extract rank region
            tmpl_rank = tmpl[0:int(CORNER_H * 0.55), :]

        if tmpl_rank.shape != rank_region.shape:
            tmpl_rank = cv2.resize(tmpl_rank, (rank_region.shape[1], rank_region.shape[0]))
        score = cv2.matchTemplate(rank_region, tmpl_rank, cv2.TM_CCOEFF_NORMED)[0][0]
        if score > best_score:
            best_score = score
            best_rank = rank_char

    return best_rank, float(best_score)


def _classify_suit(card_img):
    """
    Classify suit using color detection + contour solidity.
    More robust than template matching for suits.

    Red + low solidity (concave top) = heart
    Red + high solidity (convex) = diamond
    Black + low solidity (lobed) = club
    Black + high solidity (pointed top) = spade
    """
    h, w = card_img.shape[:2]

    # Use the small suit symbol region (28-48% height, 5-40% width)
    suit_region = card_img[int(h * 0.28):int(h * 0.48), int(w * 0.05):int(w * 0.40)]
    if suit_region.size == 0:
        # Fallback for narrow cards: wider region
        suit_region = card_img[int(h * 0.28):int(h * 0.48), :]

    gray = cv2.cvtColor(suit_region, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(suit_region, cv2.COLOR_BGR2HSV)

    # Detect color
    r1 = cv2.inRange(hsv, np.array([0, 80, 80]), np.array([10, 255, 255]))
    r2 = cv2.inRange(hsv, np.array([160, 80, 80]), np.array([180, 255, 255]))
    red_px = cv2.countNonZero(r1) + cv2.countNonZero(r2)
    is_red = red_px > 10

    # Get suit mask
    if is_red:
        mask = r1 | r2
    else:
        _, mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

    # Find largest contour and compute solidity
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return ("d" if is_red else "s"), 0.5

    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < 5:
        return ("d" if is_red else "s"), 0.5

    hull_area = cv2.contourArea(cv2.convexHull(c))
    solidity = area / max(hull_area, 1)

    if is_red:
        suit = "h" if solidity < 0.96 else "d"
    else:
        suit = "c" if solidity < 0.85 else "s"

    confidence = 0.9  # solidity-based classification is reliable
    return suit, confidence


def _match_full(corner):
    """Match against full corner templates as a fallback/confirmation."""
    best_label = "??"
    best_score = -1

    for label, tmpl in _full.items():
        if tmpl.shape != corner.shape:
            tmpl = cv2.resize(tmpl, (corner.shape[1], corner.shape[0]))
        score = cv2.matchTemplate(corner, tmpl, cv2.TM_CCOEFF_NORMED)[0][0]
        if score > best_score:
            best_score = score
            best_label = label

    return best_label, float(best_score)


def identify_card(card_img, is_narrow=False):
    """
    Identify a card from its cropped image using template matching.
    Returns (label, confidence) where label is like 'Ah', 'Ks', etc.
    confidence is 0-1 (average of rank and suit match scores).
    is_narrow: True for overlapping hero cards (uses hero-specific templates).
    """
    corner = _extract_corner(card_img)

    if is_narrow:
        # For narrow/overlapping cards, use hero-specific rank templates
        rank, rank_score = _match_rank(corner, use_hero_templates=True)
        # Use solidity-based suit classification on the full card image
        suit, suit_score = _classify_suit(card_img)
        label = rank + suit
        confidence = (rank_score + suit_score) / 2
        return label, confidence

    # Match rank via templates
    rank, rank_score = _match_rank(corner)

    # Classify suit via color + solidity (more robust than template matching)
    suit, suit_score = _classify_suit(card_img)

    label = rank + suit
    confidence = (rank_score + suit_score) / 2

    # Full template match as fallback only when rank is uncertain
    if rank_score < 0.5:
        full_label, full_score = _match_full(corner)
        if full_score > confidence:
            return full_label, full_score

    return label, confidence


def identify_cards(image, card_boxes):
    """
    Identify rank+suit for a list of card bounding boxes.
    Handles overlapping cards (e.g. hero hand) by cropping only the visible portion.
    Returns list of (label, confidence) tuples.
    """
    h, w = image.shape[:2]
    results = []

    for i, card in enumerate(card_boxes):
        x1 = max(0, card["x"] - 2)
        y1 = max(0, card["y"] - 2)
        x2 = min(w, card["x"] + card["w"] + 2)
        y2 = min(h, card["y"] + card["h"] + 2)

        # If next card overlaps, truncate crop to visible portion only
        is_narrow = False
        if i + 1 < len(card_boxes):
            next_x = card_boxes[i + 1]["x"]
            if next_x < x2 - 10:
                x2 = next_x + 2
                is_narrow = True

        crop = image[y1:y2, x1:x2]
        label, conf = identify_card(crop, is_narrow=is_narrow)
        results.append((label, conf))

    return results


def add_template(card_img, label):
    """
    Add a new template to the library from a card image.
    label should be like 'Ah', '2c', etc.
    """
    corner = _extract_corner(card_img)
    rank_char = label[0]
    suit_char = label[1]

    # Save full corner
    cv2.imwrite(os.path.join(TEMPLATE_DIR, f"{label}.png"), corner)
    _full[label] = corner

    # Save rank if new
    rank_dir = os.path.join(TEMPLATE_DIR, "ranks")
    rank_region = corner[0:int(CORNER_H * 0.55), :]
    rank_path = os.path.join(rank_dir, f"{rank_char}.png")
    if not os.path.exists(rank_path):
        cv2.imwrite(rank_path, rank_region)
        _ranks[rank_char] = rank_region

    # Save suit if new
    suit_dir = os.path.join(TEMPLATE_DIR, "suits")
    suit_region = corner[int(CORNER_H * 0.45):, :]
    suit_path = os.path.join(suit_dir, f"{suit_char}.png")
    if not os.path.exists(suit_path):
        cv2.imwrite(suit_path, suit_region)
        _suits[suit_char] = suit_region


if __name__ == "__main__":
    print(f"Templates loaded: {len(_ranks)} ranks, {len(_suits)} suits, {len(_full)} full")
    print(f"Ranks: {sorted(_ranks.keys())}")
    print(f"Suits: {sorted(_suits.keys())}")

    # Test on all card crops
    crop_dir = os.path.join(os.path.dirname(__file__), "card_crops", "all")
    if os.path.isdir(crop_dir):
        files = sorted(os.listdir(crop_dir))[:20]
        for f in files:
            img = cv2.imread(os.path.join(crop_dir, f))
            if img is not None:
                label, conf = identify_card(img)
                print(f"  {f}: {label} ({conf:.3f})")
