"""
Test YOLO detection on Unibet table screenshots.
Checks if the model trained on PS/lab data can detect elements on Unibet.
"""
import sys
import os
import glob
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

import cv2
import numpy as np

# Load YOLO
from ultralytics import YOLO
model_path = os.path.join(os.path.dirname(__file__), '..', 'vision', 'runs', 'poker_lab', 'weights', 'best.pt')
model = YOLO(model_path)

# Class names from training
CLASS_NAMES = ['board_card', 'hero_card', 'card_back', 'player_panel',
               'dealer_button', 'chip', 'pot_text', 'action_button']

# Test on captured Unibet frames
test_files = sorted(glob.glob('client/unibet-table-*.png'))
if not test_files:
    print("No Unibet screenshots found")
    sys.exit(1)

print(f"Testing {len(test_files)} Unibet screenshots\n")

for fpath in test_files[:5]:  # Test first 5
    img = cv2.imread(fpath)
    if img is None:
        continue

    # Crop to approximate iframe region (skip Unibet chrome)
    h, w = img.shape[:2]
    # Iframe at roughly y=16%-60% of full page, x=7%-93%
    y1 = int(h * 0.16)
    y2 = int(h * 0.62)
    x1 = int(w * 0.07)
    x2 = int(w * 0.93)
    cropped = img[y1:y2, x1:x2]

    results = model(cropped, conf=0.3, verbose=False)

    detections = {}
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f'cls_{cls_id}'
            if cls_name not in detections:
                detections[cls_name] = []
            detections[cls_name].append(conf)

    fname = os.path.basename(fpath)
    total = sum(len(v) for v in detections.values())
    print(f"{fname}: {total} detections")
    for cls_name, confs in sorted(detections.items()):
        avg_conf = sum(confs) / len(confs)
        print(f"  {cls_name}: {len(confs)}x (avg conf {avg_conf:.2f}, max {max(confs):.2f})")
    print()
