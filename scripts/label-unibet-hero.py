"""
Auto-label hero_card bounding boxes on Unibet screenshots for YOLO training.

Uses OCR to find "Skurj" player name, then labels the card region above-right.
Also re-labels existing YOLO classes (board_card, etc.) using the existing model.

Output: YOLO format label files alongside the images.
"""
import cv2
import numpy as np
import os
import sys
import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from advisor import find_table_region, crop_table
from ultralytics import YOLO

# YOLO classes (same as training)
CLASS_NAMES = ['board_card', 'hero_card', 'card_back', 'player_panel',
               'dealer_button', 'chip', 'pot_text', 'action_button']
HERO_CARD_CLASS = 1

# Load existing YOLO model for board card etc detection
model_path = os.path.join(os.path.dirname(__file__), '..', 'vision', 'runs', 'poker_lab', 'weights', 'best.pt')
model = YOLO(model_path)

# Load EasyOCR for hero name detection
import easyocr
reader = easyocr.Reader(['en'], gpu=True, verbose=False)


def find_hero_card_boxes_via_ocr(table_img):
    """Find hero card bounding boxes using OCR name anchor."""
    h, w = table_img.shape[:2]

    # Find Skurj_uni41 name
    y_start = h // 3
    bottom = table_img[y_start:, :]
    results = reader.readtext(bottom, detail=1)

    hero_pos = None
    for box, text, conf in results:
        if conf > 0.3 and ('skurj' in text.lower() or 'uni41' in text.lower()):
            cy = int((box[0][1] + box[2][1]) / 2) + y_start
            cx = int((box[0][0] + box[2][0]) / 2)
            hero_pos = (cx, cy)
            break

    if hero_pos is None:
        return []

    hx, hy = hero_pos

    # Card positions relative to hero name (proportional)
    card_w = int(w * 0.065)
    card_h = int(h * 0.125)
    overlap = int(card_w * 0.15)
    dx = int(w * 0.068)
    dy = int(h * 0.095)

    cards_left = hx + dx
    cards_top = hy - dy

    boxes = []
    # Card 1
    x1 = max(0, cards_left)
    y1 = max(0, cards_top)
    x2 = min(w, cards_left + card_w)
    y2 = min(h, cards_top + card_h)
    boxes.append((x1, y1, x2, y2))

    # Card 2
    card2_left = cards_left + card_w - overlap
    x1 = max(0, card2_left)
    x2 = min(w, card2_left + card_w)
    boxes.append((x1, y1, x2, y2))

    return boxes


def to_yolo_format(box, img_w, img_h, class_id):
    """Convert (x1,y1,x2,y2) to YOLO format: class cx cy w h (normalized)."""
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2 / img_w
    cy = (y1 + y2) / 2 / img_h
    w = (x2 - x1) / img_w
    h = (y2 - y1) / img_h
    return f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def process_frame(img_path, output_dir):
    """Process a single frame: detect all elements + hero cards."""
    img = cv2.imread(img_path)
    if img is None:
        return False

    # Find table region
    region = find_table_region(img)
    if region is None:
        return False

    table_img, (ox, oy) = crop_table(img, region)
    th, tw = table_img.shape[:2]

    # Run existing YOLO for board_card, card_back, etc
    results = model(table_img, conf=0.3, verbose=False)

    labels = []
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            # Skip hero_card from old model (we'll add our own)
            if cls_id == HERO_CARD_CLASS:
                continue
            labels.append(to_yolo_format((x1, y1, x2, y2), tw, th, cls_id))

    # Find hero cards via OCR
    hero_boxes = find_hero_card_boxes_via_ocr(table_img)
    for box in hero_boxes:
        # Verify the box actually contains card-like content (not empty felt)
        x1, y1, x2, y2 = box
        crop = table_img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # Cards have high contrast (both dark and light pixels)
        has_content = gray.max() - gray.min() > 80
        if has_content:
            labels.append(to_yolo_format(box, tw, th, HERO_CARD_CLASS))

    if not labels:
        return False

    # Save the cropped table image and labels
    basename = os.path.splitext(os.path.basename(img_path))[0]
    img_out = os.path.join(output_dir, 'images', f'{basename}.png')
    lbl_out = os.path.join(output_dir, 'labels', f'{basename}.txt')

    cv2.imwrite(img_out, table_img)
    with open(lbl_out, 'w') as f:
        f.write('\n'.join(labels) + '\n')

    n_hero = sum(1 for l in labels if l.startswith(f'{HERO_CARD_CLASS} '))
    n_board = sum(1 for l in labels if l.startswith('0 '))
    print(f'  {basename}: {n_hero} hero, {n_board} board, {len(labels)} total')
    return True


def main():
    # Collect all Unibet frames
    frame_dirs = ['vision/captures/unibet']
    extra_files = glob.glob('client/unibet-*.png') + glob.glob('client/unibet_assets/reference/*.png')

    all_frames = []
    for d in frame_dirs:
        if os.path.isdir(d):
            all_frames.extend(os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.png'))
    all_frames.extend(extra_files)

    print(f'Total frames to process: {len(all_frames)}')

    # Output directory
    out_dir = 'vision/dataset_unibet'
    os.makedirs(os.path.join(out_dir, 'images'), exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'labels'), exist_ok=True)

    processed = 0
    for path in all_frames:
        ok = process_frame(path, out_dir)
        if ok:
            processed += 1

    print(f'\nProcessed: {processed}/{len(all_frames)} frames')
    print(f'Output: {out_dir}/')

    # Create dataset YAML
    yaml_path = os.path.join(out_dir, 'dataset.yaml')
    abs_path = os.path.abspath(out_dir).replace('\\', '/')
    with open(yaml_path, 'w') as f:
        f.write(f"""path: {abs_path}
train: images
val: images

names:
  0: board_card
  1: hero_card
  2: card_back
  3: player_panel
  4: dealer_button
  5: chip
  6: pot_text
  7: action_button
""")
    print(f'Dataset YAML: {yaml_path}')


if __name__ == '__main__':
    main()
