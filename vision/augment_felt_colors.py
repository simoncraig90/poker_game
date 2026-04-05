r"""
Augment YOLO training data with varied felt colors.

Takes existing labeled images (green felt) and creates copies with the felt
recolored to blue, red, purple, dark green, etc. Labels stay the same since
card/button positions don't change.

This trains YOLO to detect cards on ANY felt color, not just green.

Usage:
    python vision/augment_felt_colors.py
    python vision/augment_felt_colors.py --multiplier 5
"""

import cv2
import numpy as np
import os
import shutil
from pathlib import Path


DATASET_DIR = Path(__file__).resolve().parent / "dataset"
TRAIN_IMGS = DATASET_DIR / "images" / "train"
TRAIN_LABELS = DATASET_DIR / "labels" / "train"
VAL_IMGS = DATASET_DIR / "images" / "val"
VAL_LABELS = DATASET_DIR / "labels" / "val"


def recolor_felt(img, target_hue, sat_shift=0, val_shift=0):
    """Shift the green felt to a different hue while preserving non-felt areas."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int16)

    # Mask for green felt pixels (the area we want to recolor)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    green_mask = (h >= 25) & (h <= 85) & (s >= 25) & (v >= 15)

    # Compute hue shift
    # Green center is ~60, shift to target
    hue_shift = target_hue - 55

    # Apply shift only to felt pixels
    hsv[green_mask, 0] = np.clip(hsv[green_mask, 0] + hue_shift, 0, 179)
    hsv[green_mask, 1] = np.clip(hsv[green_mask, 1] + sat_shift, 0, 255)
    hsv[green_mask, 2] = np.clip(hsv[green_mask, 2] + val_shift, 0, 255)

    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def augment_dataset(multiplier=4):
    """Create augmented copies of training images with different felt colors."""

    # Color variations: (name, target_hue, sat_shift, val_shift)
    variations = [
        ("blue", 110, 0, -10),
        ("darkblue", 120, 10, -30),
        ("red", 5, 10, -5),
        ("darkred", 0, 0, -25),
        ("purple", 140, 0, -10),
        ("teal", 90, -10, 0),
        ("olive", 40, -15, -15),
        ("dark", 55, -20, -40),       # very dark green
        ("bright", 55, 10, 20),       # bright green
        ("desaturated", 55, -30, 0),  # grayish green
    ]

    # Use subset of variations based on multiplier
    use_variations = variations[:min(multiplier, len(variations))]

    for split, img_dir, label_dir in [
        ("train", TRAIN_IMGS, TRAIN_LABELS),
        ("val", VAL_IMGS, VAL_LABELS),
    ]:
        if not img_dir.exists():
            continue

        orig_images = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg"))
        # Only augment original images (skip already-augmented ones)
        orig_images = [f for f in orig_images if not any(
            f.stem.endswith(f"_{v[0]}") for v in variations
        )]

        print(f"[{split}] {len(orig_images)} original images, {len(use_variations)} color variations")

        created = 0
        for img_path in orig_images:
            img = cv2.imread(str(img_path))
            if img is None:
                continue

            label_path = label_dir / (img_path.stem + ".txt")
            if not label_path.exists():
                continue

            for var_name, target_hue, sat_shift, val_shift in use_variations:
                # Create augmented image
                aug_img = recolor_felt(img, target_hue, sat_shift, val_shift)

                # Save with suffix
                aug_img_path = img_dir / f"{img_path.stem}_{var_name}{img_path.suffix}"
                cv2.imwrite(str(aug_img_path), aug_img)

                # Copy label file (positions don't change)
                aug_label_path = label_dir / f"{img_path.stem}_{var_name}.txt"
                shutil.copy2(str(label_path), str(aug_label_path))
                created += 1

        print(f"  Created {created} augmented images")

    # Count total
    train_count = len(list(TRAIN_IMGS.glob("*.*"))) if TRAIN_IMGS.exists() else 0
    val_count = len(list(VAL_IMGS.glob("*.*"))) if VAL_IMGS.exists() else 0
    print(f"\nTotal dataset: {train_count} train, {val_count} val")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--multiplier", type=int, default=4,
                        help="Number of color variations per image")
    args = parser.parse_args()
    augment_dataset(args.multiplier)
