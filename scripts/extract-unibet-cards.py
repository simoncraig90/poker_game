"""
Extract card images from Unibet screenshots using YOLO board_card detections.
Also crops hero cards from known positions.
Saves individual card crops for template building.
"""
import sys
import os
import glob
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

import cv2
import numpy as np
from ultralytics import YOLO

model_path = os.path.join(os.path.dirname(__file__), '..', 'vision', 'runs', 'poker_lab', 'weights', 'best.pt')
model = YOLO(model_path)

CLASS_NAMES = ['board_card', 'hero_card', 'card_back', 'player_panel',
               'dealer_button', 'chip', 'pot_text', 'action_button']

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'vision', 'templates', 'unibet_cards')
os.makedirs(OUT_DIR, exist_ok=True)

# Also try to identify cards using existing CNN
try:
    from card_cnn_detect import CardCNNDetector
    cnn = CardCNNDetector()
    print("CNN detector loaded")
except Exception as e:
    cnn = None
    print(f"CNN not available: {e}")

test_files = sorted(glob.glob('client/unibet-table-*.png'))
print(f"Processing {len(test_files)} screenshots\n")

card_count = 0

for fpath in test_files:
    img = cv2.imread(fpath)
    if img is None:
        continue

    h, w = img.shape[:2]
    # Crop to iframe
    y1 = int(h * 0.16)
    y2 = int(h * 0.62)
    x1 = int(w * 0.07)
    x2 = int(w * 0.93)
    cropped = img[y1:y2, x1:x2]
    ch, cw = cropped.shape[:2]

    results = model(cropped, conf=0.3, verbose=False)

    fname = os.path.basename(fpath)
    board_boxes = []
    hero_boxes = []

    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f'cls_{cls_id}'
            bx1, by1, bx2, by2 = [int(v) for v in box.xyxy[0]]

            if cls_name == 'board_card' and conf > 0.5:
                board_boxes.append((bx1, by1, bx2, by2, conf))
            elif cls_name == 'hero_card' and conf > 0.3:
                hero_boxes.append((bx1, by1, bx2, by2, conf))

    # Extract board cards
    for i, (bx1, by1, bx2, by2, conf) in enumerate(board_boxes):
        # Add small padding
        pad = 3
        bx1 = max(0, bx1 - pad)
        by1 = max(0, by1 - pad)
        bx2 = min(cw, bx2 + pad)
        by2 = min(ch, by2 + pad)
        card_crop = cropped[by1:by2, bx1:bx2]

        if card_crop.shape[0] < 20 or card_crop.shape[1] < 15:
            continue

        # Try CNN identification
        label = "unknown"
        if cnn:
            try:
                cards = cnn.identify_cards(cropped, [(bx1+pad, by1+pad, bx2-pad, by2-pad)])
                if cards and cards[0] != '??':
                    label = cards[0]
            except:
                pass

        out_name = f"board_{fname.replace('.png','')}_{i}_{label}.png"
        cv2.imwrite(os.path.join(OUT_DIR, out_name), card_crop)
        card_count += 1
        print(f"  {fname}: board card {i} -> {label} (conf={conf:.2f}, {card_crop.shape[1]}x{card_crop.shape[0]})")

    # Also try to find hero cards by position (bottom-center of iframe)
    # From screenshots: hero cards at ~35-55% x, ~70-90% y of iframe
    hero_region = cropped[int(ch*0.65):int(ch*0.95), int(cw*0.25):int(cw*0.55)]
    if hero_region.shape[0] > 30 and hero_region.shape[1] > 30:
        # Look for white card regions
        gray = cv2.cvtColor(hero_region, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        card_contours = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            x, y, cw2, ch2 = cv2.boundingRect(cnt)
            aspect = ch2 / max(cw2, 1)
            # Cards are taller than wide, reasonable size
            if area > 500 and 1.1 < aspect < 2.0 and cw2 > 25 and ch2 > 40:
                card_contours.append((x, y, cw2, ch2, area))

        # Sort left to right
        card_contours.sort(key=lambda c: c[0])

        for i, (cx, cy, ccw, cch, area) in enumerate(card_contours[:2]):
            hero_card = hero_region[max(0,cy-2):cy+cch+2, max(0,cx-2):cx+ccw+2]
            if hero_card.shape[0] > 20 and hero_card.shape[1] > 15:
                label = "unknown"
                if cnn:
                    try:
                        # Map back to cropped coordinates
                        abs_x = int(cw*0.25) + cx
                        abs_y = int(ch*0.65) + cy
                        cards = cnn.identify_cards(cropped, [(abs_x, abs_y, abs_x+ccw, abs_y+cch)])
                        if cards and cards[0] != '??':
                            label = cards[0]
                    except:
                        pass

                out_name = f"hero_{fname.replace('.png','')}_{i}_{label}.png"
                cv2.imwrite(os.path.join(OUT_DIR, out_name), hero_card)
                card_count += 1
                print(f"  {fname}: hero card {i} -> {label} ({hero_card.shape[1]}x{hero_card.shape[0]})")

print(f"\nTotal cards extracted: {card_count}")
print(f"Saved to: {OUT_DIR}")
