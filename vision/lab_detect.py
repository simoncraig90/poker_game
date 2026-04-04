"""
Lab client card detection — uses fixed positions instead of YOLO.

The lab client renders cards at known CSS positions. No need for
YOLO detection — just crop the known regions and run the CNN.

This gives us a YOLO-free detection path for automated testing.
"""

import cv2
import numpy as np


def detect_lab_cards(table_img):
    """
    Detect cards from the lab client using fixed position crops.

    Args:
        table_img: cropped table image from find_table_region + crop_table

    Returns:
        dict matching yolo_detect format:
          hero_card: list of {x, y, w, h, cx, cy}
          board_card: list of {x, y, w, h, cx, cy}
          + other empty fields for compatibility
    """
    th, tw = table_img.shape[:2]

    # Hero cards: positioned at ~23% x, ~71% y, each ~14% wide x 12% tall
    # Second card starts at ~27.5% x (4.5% gap = 68% overlap)
    hero_card_w = int(tw * 0.155)  # full card width (like PS YOLO boxes)
    hero_card_h = int(th * 0.10)

    hero_cards = []

    # Card 1 (left) — measured from lab screenshot
    hx1 = int(tw * 0.22)
    hy1 = int(th * 0.63)
    hero_cards.append({
        "x": hx1, "y": hy1, "w": hero_card_w, "h": hero_card_h,
        "cx": hx1 + hero_card_w // 2, "cy": hy1 + hero_card_h // 2,
    })

    # Card 2 (right, overlapping)
    hx2 = hx1 + int(tw * 0.045)  # 4.5% gap
    hy2 = hy1 + int(th * 0.01)   # slightly lower
    hero_cards.append({
        "x": hx2, "y": hy2, "w": hero_card_w, "h": hero_card_h,
        "cx": hx2 + hero_card_w // 2, "cy": hy2 + hero_card_h // 2,
    })

    # Check if hero cards are actually visible (not just green felt)
    # Hero card region should have significant non-green pixels
    visible_heroes = []
    for card in hero_cards:
        crop = table_img[card["y"]:card["y"]+card["h"], card["x"]:card["x"]+card["w"]]
        if crop.size == 0:
            continue
        # Check for white card pixels (cards are mostly white)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        white_px = np.sum(gray > 200)
        total_px = gray.size
        if white_px / total_px > 0.15:  # at least 15% white = card present
            visible_heroes.append(card)

    # Board cards: center of table, ~38% y, 5 cards side by side
    board_card_w = int(tw * 0.10)
    board_card_h = int(th * 0.08)
    board_y = int(th * 0.34)
    board_start_x = int(tw * 0.25)  # roughly centered for 5 cards
    board_gap = int(tw * 0.10)

    board_cards = []
    for i in range(5):
        bx = board_start_x + i * board_gap
        crop = table_img[board_y:board_y+board_card_h, bx:bx+board_card_w]
        if crop.size == 0:
            continue
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        white_px = np.sum(gray > 200)
        if white_px / gray.size > 0.15:
            board_cards.append({
                "x": bx, "y": board_y, "w": board_card_w, "h": board_card_h,
                "cx": bx + board_card_w // 2, "cy": board_y + board_card_h // 2,
            })

    return {
        "hero_card": visible_heroes,
        "board_card": board_cards,
        "card_back": [],
        "player_panel": [],
        "dealer_button": [],
        "chip": [],
        "pot_text": [],
        "action_button": [],
    }
