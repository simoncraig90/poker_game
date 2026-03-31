"""
Generate labeled card crop dataset for CNN training.

Scans training frames, extracts card crops via color detection,
labels them using the template matcher from card_id.py,
and saves crops organized by label.

Usage:
    python gen_card_data.py
"""

import cv2
import json
import os
import sys

# Add vision dir to path
VISION_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, VISION_DIR)

from card_id import identify_card, _extract_corner
from detect import find_cards_by_color

CAPTURES_DIR = os.path.join(VISION_DIR, "captures", "training")
EXISTING_CROPS_DIR = os.path.join(VISION_DIR, "card_crops", "all")
OUTPUT_DIR = os.path.join(VISION_DIR, "card_crops", "labeled")
LABELS_FILE = os.path.join(VISION_DIR, "card_crops", "cnn_labels.json")

# All 52 valid cards
RANKS = list("23456789TJQKA")
SUITS = list("shdc")
VALID_CARDS = {r + s for r in RANKS for s in SUITS}

MIN_CONFIDENCE = 0.55  # Minimum confidence to accept a template match label


def extract_crops_from_frame(image_path):
    """Extract card crops from a single frame using color detection."""
    img = cv2.imread(image_path)
    if img is None:
        return []

    cards = find_cards_by_color(img)
    crops = []

    h, w = img.shape[:2]

    # Process board cards
    for i, card in enumerate(cards.get("board", [])):
        x1 = max(0, card["x"] - 2)
        y1 = max(0, card["y"] - 2)
        x2 = min(w, card["x"] + card["w"] + 2)
        y2 = min(h, card["y"] + card["h"] + 2)
        crop = img[y1:y2, x1:x2]
        if crop.shape[0] > 30 and crop.shape[1] > 20:
            crops.append(("board", i, crop))

    # Process hero cards (handle overlapping)
    hero_cards = cards.get("hero", [])
    for i, card in enumerate(hero_cards):
        x1 = max(0, card["x"] - 2)
        y1 = max(0, card["y"] - 2)
        x2 = min(w, card["x"] + card["w"] + 2)
        y2 = min(h, card["y"] + card["h"] + 2)

        # If next card overlaps, truncate
        is_narrow = False
        if i + 1 < len(hero_cards):
            next_x = hero_cards[i + 1]["x"]
            if next_x < x2 - 10:
                x2 = next_x + 2
                is_narrow = True

        crop = img[y1:y2, x1:x2]
        if crop.shape[0] > 30 and crop.shape[1] > 20:
            crops.append(("hero", i, crop, is_narrow))

    return crops


def label_crop(crop, is_narrow=False):
    """Label a crop using the template matcher."""
    label, conf = identify_card(crop, is_narrow=is_narrow)
    if label and len(label) == 2 and label in VALID_CARDS and conf >= MIN_CONFIDENCE:
        return label, conf
    return None, 0.0


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    labels = {}  # filename -> {label, score, source}
    total_saved = 0
    skipped_low_conf = 0
    skipped_invalid = 0

    # Step 1: Label existing 129 crops
    print("=== Step 1: Labeling existing crops ===")
    if os.path.isdir(EXISTING_CROPS_DIR):
        existing_files = sorted(f for f in os.listdir(EXISTING_CROPS_DIR) if f.endswith(".png"))
        for fname in existing_files:
            img = cv2.imread(os.path.join(EXISTING_CROPS_DIR, fname))
            if img is None:
                continue
            # Determine if this is a hero card (narrower aspect ratio)
            h, w = img.shape[:2]
            is_narrow = w < h * 0.5
            label, conf = label_crop(img, is_narrow=is_narrow)
            if label:
                out_name = f"existing_{fname}"
                out_path = os.path.join(OUTPUT_DIR, out_name)
                cv2.imwrite(out_path, img)
                labels[out_name] = {"label": label, "score": round(conf, 4), "source": "existing"}
                total_saved += 1
            else:
                skipped_low_conf += 1

    print(f"  Labeled {total_saved} existing crops, skipped {skipped_low_conf}")

    # Step 2: Extract crops from training frames
    print("\n=== Step 2: Extracting crops from training frames ===")
    if not os.path.isdir(CAPTURES_DIR):
        print(f"  Training captures not found at {CAPTURES_DIR}")
    else:
        frame_files = sorted(f for f in os.listdir(CAPTURES_DIR) if f.endswith(".png"))
        print(f"  Found {len(frame_files)} training frames")

        frame_count = 0
        for fi, fname in enumerate(frame_files):
            frame_path = os.path.join(CAPTURES_DIR, fname)
            crops = extract_crops_from_frame(frame_path)

            for crop_info in crops:
                if len(crop_info) == 4:
                    region, idx, crop, is_narrow = crop_info
                else:
                    region, idx, crop = crop_info
                    is_narrow = False

                label, conf = label_crop(crop, is_narrow=is_narrow)
                if label:
                    # Use frame timestamp + region + index as filename
                    ts = fname.replace("frame_", "").replace(".png", "")
                    out_name = f"{ts}_{region}_{idx}.png"
                    out_path = os.path.join(OUTPUT_DIR, out_name)
                    cv2.imwrite(out_path, crop)
                    labels[out_name] = {"label": label, "score": round(conf, 4), "source": "frame"}
                    total_saved += 1
                else:
                    if conf > 0:
                        skipped_low_conf += 1
                    else:
                        skipped_invalid += 1

            frame_count += 1
            if frame_count % 50 == 0:
                print(f"  Processed {frame_count}/{len(frame_files)} frames, {total_saved} crops so far")

        print(f"  Processed all {frame_count} frames")

    # Save labels
    with open(LABELS_FILE, "w") as f:
        json.dump(labels, f, indent=2)

    # Print summary
    print(f"\n=== Summary ===")
    print(f"Total labeled crops: {total_saved}")
    print(f"Skipped (low confidence): {skipped_low_conf}")
    print(f"Skipped (invalid label): {skipped_invalid}")
    print(f"Labels saved to: {LABELS_FILE}")
    print(f"Crops saved to: {OUTPUT_DIR}")

    # Distribution
    from collections import Counter
    card_counts = Counter(v["label"] for v in labels.values())
    print(f"\nCard distribution ({len(card_counts)} unique cards):")
    for card in sorted(card_counts.keys()):
        print(f"  {card}: {card_counts[card]}")


if __name__ == "__main__":
    main()
