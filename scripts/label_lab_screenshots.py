"""
Step 2: Auto-label generated poker-lab screenshots using the existing detection pipeline,
then add them to the YOLO training dataset.
"""

import os
import sys
import shutil
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))
from yolo_label import label_frame

LAB_DIR = os.path.join(os.path.dirname(__file__), '..', 'vision', 'captures', 'lab_gen')
DATASET_DIR = os.path.join(os.path.dirname(__file__), '..', 'vision', 'dataset')

TRAIN_IMG_DIR = os.path.join(DATASET_DIR, 'images', 'train')
TRAIN_LBL_DIR = os.path.join(DATASET_DIR, 'labels', 'train')
VAL_IMG_DIR = os.path.join(DATASET_DIR, 'images', 'val')
VAL_LBL_DIR = os.path.join(DATASET_DIR, 'labels', 'val')

def main():
    os.makedirs(TRAIN_IMG_DIR, exist_ok=True)
    os.makedirs(TRAIN_LBL_DIR, exist_ok=True)
    os.makedirs(VAL_IMG_DIR, exist_ok=True)
    os.makedirs(VAL_LBL_DIR, exist_ok=True)

    files = sorted([f for f in os.listdir(LAB_DIR) if f.endswith('.png')])
    print(f"Found {len(files)} lab screenshots in {LAB_DIR}")

    # 85/15 train/val split
    random.seed(42)
    random.shuffle(files)
    split_idx = int(len(files) * 0.85)
    train_files = files[:split_idx]
    val_files = files[split_idx:]

    total_labels = 0
    labeled_count = 0
    skipped = 0
    class_counts = {}

    for split, file_list in [('train', train_files), ('val', val_files)]:
        img_dir = TRAIN_IMG_DIR if split == 'train' else VAL_IMG_DIR
        lbl_dir = TRAIN_LBL_DIR if split == 'train' else VAL_LBL_DIR

        for i, fname in enumerate(file_list):
            src = os.path.join(LAB_DIR, fname)
            labels = label_frame(src)

            if labels is None or len(labels) == 0:
                skipped += 1
                continue

            # Copy image
            dst_img = os.path.join(img_dir, fname)
            shutil.copy2(src, dst_img)

            # Write labels
            label_name = fname.replace('.png', '.txt')
            dst_lbl = os.path.join(lbl_dir, label_name)
            with open(dst_lbl, 'w') as f:
                f.write('\n'.join(labels))

            total_labels += len(labels)
            labeled_count += 1

            # Count classes
            for lbl in labels:
                cls_id = int(lbl.split()[0])
                class_counts[cls_id] = class_counts.get(cls_id, 0) + 1

            if (i + 1) % 20 == 0:
                print(f"  [{split}] {i+1}/{len(file_list)} processed...")

    CLASS_NAMES = [
        "board_card", "hero_card", "card_back", "player_panel",
        "dealer_button", "chip", "pot_text", "action_button",
    ]

    print(f"\n=== Lab Screenshot Labeling Complete ===")
    print(f"  Total files: {len(files)}")
    print(f"  Labeled: {labeled_count} (train={len(train_files)}, val={len(val_files)})")
    print(f"  Skipped (no detections): {skipped}")
    print(f"  Total labels: {total_labels}")
    print(f"\n  Class distribution:")
    for cls_id in sorted(class_counts.keys()):
        name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f"class_{cls_id}"
        print(f"    {name}: {class_counts[cls_id]}")

    # Count total dataset size
    train_count = len(os.listdir(TRAIN_IMG_DIR))
    val_count = len(os.listdir(VAL_IMG_DIR))
    print(f"\n  Full dataset: {train_count} train + {val_count} val = {train_count + val_count} total")


if __name__ == '__main__':
    main()
