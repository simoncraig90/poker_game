"""
PokerStars table reader.
Takes a captured table image and extracts:
- Player names and stacks
- Pot amount
- Board cards
- Hero cards
- Dealer button position
"""

import cv2
import numpy as np
import os
import sys

try:
    import easyocr
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    OCR_ENGINE = "easyocr"
except Exception:
    reader = None
    OCR_ENGINE = "none"


def read_text_regions(image):
    """Use EasyOCR to find all text in the image."""
    if reader is None:
        return []
    results = reader.readtext(image)
    # results: list of (bbox, text, confidence)
    parsed = []
    for bbox, text, conf in results:
        if conf < 0.3:
            continue
        # bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        x1, x2 = int(min(xs)), int(max(xs))
        y1, y2 = int(min(ys)), int(max(ys))
        parsed.append({
            "text": text,
            "confidence": round(conf, 3),
            "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
        })
    return parsed


def _normalize_ocr_money(s):
    """Fix common EasyOCR misreads in dollar amounts."""
    import re
    # S → $ when followed by digit or O (OCR misreads 0 as O)
    s = re.sub(r'S([0-9O])', r'$\1', s)
    # O → 0 in numeric contexts (after $ or digit or dot)
    s = re.sub(r'(?<=[\$\d.])O', '0', s)
    s = re.sub(r'O(?=[\d.])', '0', s)
    return s


def find_dollar_amounts(texts):
    """Extract dollar amounts from OCR results."""
    import re
    amounts = []
    for t in texts:
        s = _normalize_ocr_money(t["text"].strip())
        # Extract any dollar amount pattern: $X, $X.XX, $X,XXX.XX
        match = re.search(r'\$[\d,]+\.?\d*', s)
        if match:
            try:
                val = match.group().replace("$", "").replace(",", "")
                amt = float(val)
                amounts.append({**t, "amount": amt})
            except ValueError:
                pass
    return amounts


def find_pot(texts, img_h):
    """Find the pot amount — typically has 'Pot' prefix, centered vertically."""
    import re
    for t in texts:
        s = t["text"]
        if "pot" in s.lower() or "Pot" in s:
            # Extract amount from pot text (handles both $ and S, O and 0)
            normalized = _normalize_ocr_money(s)
            match = re.search(r'\$[\d,]+\.?\d*', normalized)
            if match:
                try:
                    amt = float(match.group().replace("$", "").replace(",", ""))
                    return {**t, "amount": amt}
                except ValueError:
                    pass
            return t
    # Fallback: look for dollar amount in the top 40% center
    amounts = find_dollar_amounts(texts)
    for a in amounts:
        if a["cy"] < img_h * 0.4:
            return a
    return None


def find_player_names(texts, amounts, img_w, img_h):
    """
    Find player names — text near dollar amounts that isn't itself a dollar amount.
    Group name + stack pairs.
    """
    amount_set = set(id(a) for a in amounts)
    players = []

    # Words that are table info or action buttons, not player names
    ignore_words = {"pokerstars", "no limit", "hold'em", "holdem", "pot limit",
                    "zoom", "tournament", "sit & go", "sit and go", "table",
                    "fold", "call", "raise", "raise to", "check", "bet",
                    "all-in", "all in", "allin", "win", "muck", "show"}

    for amt in amounts:
        # Find the closest non-dollar text above or near this amount
        best = None
        best_dist = 999999
        for t in texts:
            if "$" in t["text"] or "pot" in t["text"].lower():
                continue
            if len(t["text"].strip()) < 2:
                continue
            # Skip table info text
            if any(w in t["text"].lower() for w in ignore_words):
                continue
            # Skip blind level text (contains /)
            if "/" in t["text"]:
                continue
            # Skip text that looks like a dollar amount after OCR normalization
            normalized = _normalize_ocr_money(t["text"].strip())
            if "$" in normalized:
                continue
            # Must be close horizontally and slightly above
            dx = abs(t["cx"] - amt["cx"])
            dy = amt["cy"] - t["cy"]  # positive = name is above
            if dx < 100 and 0 < dy < 60:
                dist = dx + dy
                if dist < best_dist:
                    best_dist = dist
                    best = t

        if best:
            players.append({
                "name": best["text"],
                "stack": amt["amount"],
                "name_pos": {"x": best["x"], "y": best["y"], "w": best["w"], "h": best["h"]},
                "stack_pos": {"x": amt["x"], "y": amt["y"], "w": amt["w"], "h": amt["h"]},
                "cx": (best["cx"] + amt["cx"]) // 2,
                "cy": (best["cy"] + amt["cy"]) // 2,
            })

    return players


def find_cards_by_color(image):
    """
    Find card regions by looking for white rectangles on the green felt.
    Distinguishes board cards (center, larger) from hero cards (bottom, large).
    Returns dict with 'board' and 'hero' lists.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # White cards: very bright pixels
    _, white_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

    # Find contours of white regions
    contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    all_cards = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        ratio = ch / max(cw, 1)

        # Card-like: aspect ratio ~1.3-1.6, minimum size to avoid noise
        if 1.1 < ratio < 2.0 and area > 1500 and cw > 25 and ch > 40:
            all_cards.append({
                "x": x, "y": y, "w": cw, "h": ch,
                "cx": x + cw // 2, "cy": y + ch // 2,
                "area": area,
            })

    # Sort left to right
    all_cards.sort(key=lambda c: c["cx"])

    # Separate board cards (center of table, 25-55% height, 20-80% width) from hero cards (bottom, >65% height)
    # Board cards must be horizontally centered and have minimum size to avoid UI noise
    board = [c for c in all_cards if c["cy"] < h * 0.55 and c["cy"] > h * 0.15
             and c["cx"] > w * 0.20 and c["cx"] < w * 0.80 and c["w"] > 40 and c["h"] > 60]
    hero = [c for c in all_cards if c["cy"] > h * 0.65]

    return {"board": board, "hero": hero, "all": all_cards}


def find_action_buttons(texts):
    """Detect action buttons (Fold/Call/Raise/Check/Bet/All-in) — indicates hero's turn."""
    actions = []
    action_words = {"fold", "call", "raise", "raise to", "check", "bet", "all-in", "all in"}
    for t in texts:
        lower = t["text"].lower().strip()
        if lower in action_words or lower.startswith("raise"):
            actions.append({"action": lower, "x": t["cx"], "y": t["cy"], "text": t["text"]})
    return actions


def find_bet_chips(texts, amounts, img_h):
    """Find bet amounts placed between seats and center (not stacks, not pot)."""
    import re
    bets = []
    for a in amounts:
        # Bet chips are typically in the middle zone (20-65% height)
        # and are small amounts
        y_pct = a["cy"] / img_h
        if 0.15 < y_pct < 0.75:
            # Check if this amount is NOT part of a player name/stack pair
            # (pot is already handled separately)
            text = a["text"].lower()
            if "pot" not in text:
                bets.append({"amount": a["amount"], "x": a["cx"], "y": a["cy"]})
    return bets


def identify_card(card_img):
    """
    Identify rank and suit from a cropped card image.
    Returns string like 'Ah', 'Ks', 'Tc', '2d' or None if unreadable.
    """
    ch, cw = card_img.shape[:2]
    if ch < 20 or cw < 15:
        return None

    # 1. Rank: OCR the top-left corner
    corner = card_img[0:int(ch * 0.35), 0:int(cw * 0.5)]
    big = cv2.resize(corner, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    rank = "?"
    if reader is not None:
        results = reader.readtext(big, allowlist='23456789TJQKA10')
        if results:
            rank = results[0][1].strip()
            if rank == '10':
                rank = 'T'
            # Fix common OCR misreads
            if rank == '0':
                rank = 'Q'  # Q often misread as 0
            if rank == 'I':
                rank = 'J'  # J sometimes misread as I
            if rank == '1':
                rank = 'A'  # A sometimes misread as 1
            if len(rank) > 1:
                rank = rank[0]  # Take first char if multiple

    if rank not in '23456789TJQKA':
        return None

    # 2. Color: red vs black in corner
    hsv_corner = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv_corner, np.array([0, 80, 80]), np.array([10, 255, 255]))
    red2 = cv2.inRange(hsv_corner, np.array([160, 80, 80]), np.array([180, 255, 255]))
    is_red = cv2.countNonZero(red1) + cv2.countNonZero(red2) > 20

    # 3. Suit: analyze small suit symbol below rank (28-48% height, 5-35% width)
    suit_region = card_img[int(ch * 0.28):int(ch * 0.48), int(cw * 0.05):int(cw * 0.35)]
    gray = cv2.cvtColor(suit_region, cv2.COLOR_BGR2GRAY)
    hsv_suit = cv2.cvtColor(suit_region, cv2.COLOR_BGR2HSV)

    if is_red:
        r1 = cv2.inRange(hsv_suit, np.array([0, 80, 80]), np.array([10, 255, 255]))
        r2 = cv2.inRange(hsv_suit, np.array([160, 80, 80]), np.array([180, 255, 255]))
        mask = r1 | r2
    else:
        _, mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    solidity = 0.5
    if contours:
        c = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(c)
        hull_area = cv2.contourArea(cv2.convexHull(c))
        solidity = area / max(hull_area, 1)

    # Classify suit by solidity:
    # Hearts: red, solidity < 0.96 (concavity between bumps)
    # Diamonds: red, solidity >= 0.96 (convex shape)
    # Clubs: black, solidity < 0.85 (concavity between lobes)
    # Spades: black, solidity >= 0.85 (mostly convex)
    if is_red:
        suit = 'h' if solidity < 0.96 else 'd'
    else:
        suit = 'c' if solidity < 0.85 else 's'

    return rank + suit


def identify_cards(image, card_boxes):
    """Identify rank+suit for a list of card bounding boxes."""
    h, w = image.shape[:2]
    results = []
    for card in card_boxes:
        x1 = max(0, card['x'] - 2)
        y1 = max(0, card['y'] - 2)
        x2 = min(w, card['x'] + card['w'] + 2)
        y2 = min(h, card['y'] + card['h'] + 2)
        crop = image[y1:y2, x1:x2]
        ident = identify_card(crop)
        results.append(ident or '??')
    return results


def find_dealer_button(image):
    """Find the red dealer button circle."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # Red circle: H=0-10 or 170-180, high S, high V
    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = mask1 | mask2

    # Find circular contours
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for c in contours:
        area = cv2.contourArea(c)
        if 100 < area < 3000:
            # Check circularity
            perimeter = cv2.arcLength(c, True)
            if perimeter > 0:
                circularity = 4 * np.pi * area / (perimeter * perimeter)
                if circularity > 0.5:
                    x, y, w, h = cv2.boundingRect(c)
                    return {"x": x, "y": y, "w": w, "h": h, "cx": x + w // 2, "cy": y + h // 2}

    return None


def analyze_table(image_path):
    """Full analysis of a table screenshot."""
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: cannot read {image_path}")
        return None

    h, w = img.shape[:2]
    print(f"Image: {w}x{h}")
    print(f"OCR engine: {OCR_ENGINE}")
    print()

    # OCR
    print("Reading text...")
    texts = read_text_regions(img)
    print(f"Found {len(texts)} text regions")

    for t in texts:
        print(f"  [{t['confidence']:.2f}] '{t['text']}' at ({t['cx']},{t['cy']})")

    # Dollar amounts
    amounts = find_dollar_amounts(texts)
    print(f"\nDollar amounts: {len(amounts)}")
    for a in amounts:
        print(f"  ${a['amount']:.2f} at ({a['cx']},{a['cy']})")

    # Pot
    pot = find_pot(texts, h)
    if pot:
        print(f"\nPot: {pot['text']} at ({pot['cx']},{pot['cy']})")

    # Players
    players = find_player_names(texts, amounts, w, h)
    print(f"\nPlayers: {len(players)}")
    for p in players:
        print(f"  {p['name']}: ${p['stack']:.2f} at ({p['cx']},{p['cy']})")

    # Cards
    cards = find_cards_by_color(img)
    board_ids = identify_cards(img, cards["board"]) if cards["board"] else []
    hero_ids = identify_cards(img, cards["hero"]) if cards["hero"] else []

    print(f"\nBoard cards: {' '.join(board_ids) if board_ids else '(none)'}")
    print(f"Hero cards: {' '.join(hero_ids) if hero_ids else '(none)'}")

    # Action buttons
    actions = find_action_buttons(texts)
    if actions:
        print(f"\nAction buttons (hero's turn!):")
        for a in actions:
            print(f"  {a['text']} at ({a['x']},{a['y']})")

    # Dealer button
    dbtn = find_dealer_button(img)
    if dbtn:
        print(f"\nDealer button at ({dbtn['cx']},{dbtn['cy']})")

    # Draw detections on image
    annotated = img.copy()
    for t in texts:
        cv2.rectangle(annotated, (t["x"], t["y"]), (t["x"] + t["w"], t["y"] + t["h"]), (0, 255, 255), 1)
    for c in cards["board"]:
        cv2.rectangle(annotated, (c["x"], c["y"]), (c["x"] + c["w"], c["y"] + c["h"]), (255, 255, 0), 2)
    for c in cards["hero"]:
        cv2.rectangle(annotated, (c["x"], c["y"]), (c["x"] + c["w"], c["y"] + c["h"]), (0, 255, 255), 2)
    if dbtn:
        cv2.circle(annotated, (dbtn["cx"], dbtn["cy"]), dbtn["w"], (0, 0, 255), 2)
    for p in players:
        np_ = p["name_pos"]
        cv2.rectangle(annotated, (np_["x"], np_["y"]), (np_["x"] + np_["w"], np_["y"] + np_["h"]), (0, 255, 0), 2)

    out_path = image_path.replace(".png", "_detected.png")
    cv2.imwrite(out_path, annotated)
    print(f"\nAnnotated image saved to {out_path}")

    return {
        "players": players,
        "pot": pot,
        "board_cards": board_ids,
        "hero_cards": hero_ids,
        "actions": actions,
        "dealer_button": dbtn,
        "texts": texts,
    }


if __name__ == "__main__":
    # Analyze the most recent capture
    cap_dir = os.path.join(os.path.dirname(__file__), "captures")
    if len(sys.argv) > 1:
        img_path = sys.argv[1]
    else:
        # Find latest table capture
        files = sorted([f for f in os.listdir(cap_dir) if f.startswith("table_")], reverse=True)
        if not files:
            print("No captures found. Run capture.py first.")
            sys.exit(1)
        img_path = os.path.join(cap_dir, files[0])

    print(f"Analyzing: {img_path}")
    print("=" * 50)
    analyze_table(img_path)
