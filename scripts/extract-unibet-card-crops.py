"""
Extract card crops from all Unibet screenshots using the YOLO Unibet model.
Saves hero_card and board_card crops for CNN training.
Uses the board card template matching (100% accurate) to auto-label board cards,
and saves hero cards for manual labeling.
"""
import cv2
import numpy as np
import os
import sys
import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from ultralytics import YOLO
from advisor import find_table_region, crop_table
import unibet_card_detect as ucd

CLASS_NAMES = ['board_card', 'hero_card', 'card_back', 'player_panel',
               'dealer_button', 'chip', 'pot_text', 'action_button']

model = YOLO('vision/models/yolo_unibet.pt')

out_dir = 'vision/card_crops_unibet'
os.makedirs(os.path.join(out_dir, 'board'), exist_ok=True)
os.makedirs(os.path.join(out_dir, 'hero'), exist_ok=True)

# Collect all Unibet screenshots
all_files = []
all_files.extend(glob.glob('vision/captures/unibet/frame_*.png'))
all_files.extend(glob.glob('client/unibet-*.png'))

print(f'Processing {len(all_files)} files')

board_count = 0
hero_count = 0

for fpath in all_files:
    img = cv2.imread(fpath)
    if img is None:
        continue

    region = find_table_region(img)
    if region is None:
        continue

    table_img, _ = crop_table(img, region)
    th, tw = table_img.shape[:2]

    results = model(table_img, conf=0.4, verbose=False)

    fname = os.path.splitext(os.path.basename(fpath))[0]

    for r in results:
        for i, box in enumerate(r.boxes):
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]

            crop = table_img[y1:y2, x1:x2]
            if crop.shape[0] < 30 or crop.shape[1] < 20:
                continue

            if cls_id == 0:  # board_card
                # Auto-label using template matching (100% accurate on board cards)
                label = ucd.identify_card(crop)
                if label != '??':
                    out_path = os.path.join(out_dir, 'board', f'{label}_{fname}_{i}.png')
                    cv2.imwrite(out_path, crop)
                    board_count += 1

            elif cls_id == 1:  # hero_card
                out_path = os.path.join(out_dir, 'hero', f'{fname}_{i}_{x1}.png')
                cv2.imwrite(out_path, crop)
                hero_count += 1

print(f'\nExtracted: {board_count} board cards, {hero_count} hero cards')
print(f'Board cards auto-labeled in: {out_dir}/board/')
print(f'Hero cards (need labeling) in: {out_dir}/hero/')
