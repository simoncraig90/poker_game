"""
YOLOv8-based table detection — replaces the slow OCR pipeline.
Uses a trained poker model for fast element detection (~50ms/frame).
Falls back to OCR pipeline if no trained model exists.
"""

import cv2
import numpy as np
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

# Try multiple model locations (newest first)
_MODEL_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "runs", "poker_lab", "weights", "best.pt"),
    os.path.join(os.path.dirname(__file__), "runs", "poker", "weights", "best.pt"),
    os.path.join(os.path.dirname(__file__), "runs", "detect", "poker", "weights", "best.pt"),
]
MODEL_PATH = next((p for p in _MODEL_CANDIDATES if os.path.exists(p)), _MODEL_CANDIDATES[-1])

CLASS_NAMES = [
    "board_card", "hero_card", "card_back", "player_panel",
    "dealer_button", "chip", "pot_text", "action_button",
]

_model = None


def load_model():
    """Load the trained YOLO model (lazy, once)."""
    global _model
    if _model is not None:
        return _model

    if not os.path.exists(MODEL_PATH):
        print(f"No trained model at {MODEL_PATH}")
        print("Run: python vision/yolo_train.py")
        return None

    from ultralytics import YOLO
    _model = YOLO(MODEL_PATH)
    print(f"Loaded YOLO model from {MODEL_PATH}")
    return _model


def detect_elements(image, conf=0.5):
    """
    Detect all poker elements in an image using YOLO.
    Returns dict with lists of bounding boxes per class.
    """
    model = load_model()
    if model is None:
        return None

    results = model.predict(image, conf=conf, verbose=False)

    elements = {name: [] for name in CLASS_NAMES}

    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf_val = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()

            cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f"class_{cls_id}"
            elements[cls_name].append({
                "x": int(x1),
                "y": int(y1),
                "w": int(x2 - x1),
                "h": int(y2 - y1),
                "cx": int((x1 + x2) / 2),
                "cy": int((y1 + y2) / 2),
                "confidence": round(conf_val, 3),
            })

    return elements


def extract_game_state_yolo(table_img):
    """
    Extract game state using YOLO detection + card_id for card identification.
    Much faster than the OCR pipeline (~50ms vs ~3000ms).
    """
    from card_id import identify_cards
    from detect import read_text_regions, find_dollar_amounts, find_pot, find_player_names

    elements = detect_elements(table_img)
    if elements is None:
        # Fallback to OCR pipeline
        from detect import find_cards_by_color, find_action_buttons, find_dealer_button
        from live import extract_game_state
        return extract_game_state(table_img)

    h, w = table_img.shape[:2]

    # Identify cards from YOLO-detected regions
    board_ids = []
    if elements["board_card"]:
        board_ids = [label for label, _ in identify_cards(table_img, elements["board_card"])]

    hero_ids = []
    if elements["hero_card"]:
        hero_ids = [label for label, _ in identify_cards(table_img, elements["hero_card"])]

    # For player names and stacks, still use OCR on detected panel regions
    # (YOLO finds the panels, OCR reads just those small regions — much faster)
    players = []
    if elements["player_panel"]:
        for panel in elements["player_panel"]:
            # Crop panel region and OCR just that
            px1 = max(0, panel["x"] - 5)
            py1 = max(0, panel["y"] - 5)
            px2 = min(w, panel["x"] + panel["w"] + 5)
            py2 = min(h, panel["y"] + panel["h"] + 5)
            panel_crop = table_img[py1:py2, px1:px2]

            texts = read_text_regions(panel_crop)
            amounts = find_dollar_amounts(texts)

            if texts and amounts:
                name = texts[0]["text"]  # first text = name
                stack = amounts[0]["amount"]  # first amount = stack
                players.append({
                    "name": name,
                    "stack": stack,
                    "position": {"x": panel["cx"], "y": panel["cy"]},
                })

    # Pot — OCR just the pot text region
    pot_amount = None
    if elements["pot_text"]:
        pt = elements["pot_text"][0]
        pot_crop = table_img[
            max(0, pt["y"] - 2):min(h, pt["y"] + pt["h"] + 2),
            max(0, pt["x"] - 2):min(w, pt["x"] + pt["w"] + 2),
        ]
        texts = read_text_regions(pot_crop)
        pot = find_pot(texts, pt["h"])
        if pot and "amount" in pot:
            pot_amount = pot["amount"]

    # Action buttons
    hero_turn = len(elements["action_button"]) > 0
    actions = []
    if hero_turn:
        for btn in elements["action_button"]:
            btn_crop = table_img[
                max(0, btn["y"] - 2):min(h, btn["y"] + btn["h"] + 2),
                max(0, btn["x"] - 2):min(w, btn["x"] + btn["w"] + 2),
            ]
            texts = read_text_regions(btn_crop)
            if texts:
                actions.append(texts[0]["text"].lower())

    # Dealer button
    dealer = None
    if elements["dealer_button"]:
        db = elements["dealer_button"][0]
        dealer = {"x": db["cx"], "y": db["cy"]}

    return {
        "players": players,
        "pot": pot_amount,
        "board_cards": board_ids,
        "hero_cards": hero_ids,
        "hero_turn": hero_turn,
        "actions": actions,
        "dealer_button": dealer,
        "card_backs": len(elements["card_back"]),
        "timestamp": time.time(),
        "method": "yolo",
    }


if __name__ == "__main__":
    model = load_model()
    if model is None:
        print("Train a model first: python vision/yolo_train.py")
        sys.exit(1)

    # Test on a frame
    test_frame = os.path.join(os.path.dirname(__file__), "captures", "training", "frame_1774928953106.png")
    if os.path.exists(test_frame):
        img = cv2.imread(test_frame)
        t0 = time.time()
        elements = detect_elements(img)
        elapsed = (time.time() - t0) * 1000

        print(f"Detection time: {elapsed:.0f}ms")
        for cls_name, boxes in elements.items():
            if boxes:
                print(f"  {cls_name}: {len(boxes)}")
