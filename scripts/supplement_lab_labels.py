"""
Supplement auto-labels for lab screenshots with position-based player panel detection.
The lab UI has fixed seat positions, so we can detect player panels by looking for
text/name regions at known positions on the 400x740 viewport.

Also ensures card_back labels are present for opponent positions with face-down cards.
"""

import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

DATASET_DIR = os.path.join(os.path.dirname(__file__), '..', 'vision', 'dataset')

# Known approximate seat positions in 400x740 lab viewport (cx, cy)
# These are the player panel regions (name + stack text area)
SEAT_POSITIONS_6MAX = {
    # seat: (cx_pct, cy_pct, w_pct, h_pct) as percentages of image
    0: (0.50, 0.88, 0.25, 0.08),   # bottom center (hero)
    1: (0.12, 0.65, 0.20, 0.08),   # left mid
    2: (0.12, 0.25, 0.20, 0.08),   # left top
    3: (0.50, 0.08, 0.25, 0.08),   # top center
    4: (0.88, 0.25, 0.20, 0.08),   # right top
    5: (0.88, 0.65, 0.20, 0.08),   # right mid
}

PLAYER_PANEL = 3
CARD_BACK = 2

CLASS_NAMES = [
    "board_card", "hero_card", "card_back", "player_panel",
    "dealer_button", "chip", "pot_text", "action_button",
]


def detect_player_panels_by_text(img):
    """
    Detect player panels by finding small white/light text clusters
    at expected seat positions.
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    panels = []

    for seat_idx, (cx_pct, cy_pct, w_pct, h_pct) in SEAT_POSITIONS_6MAX.items():
        # Define search region around expected position
        search_cx = int(cx_pct * w)
        search_cy = int(cy_pct * h)
        search_w = int(w_pct * w * 1.5)  # wider search
        search_h = int(h_pct * h * 2.0)  # taller search

        x1 = max(0, search_cx - search_w // 2)
        y1 = max(0, search_cy - search_h // 2)
        x2 = min(w, search_cx + search_w // 2)
        y2 = min(h, search_cy + search_h // 2)

        roi = gray[y1:y2, x1:x2]
        if roi.size == 0:
            continue

        # Look for white/light text pixels (player names and stacks are white text)
        _, text_mask = cv2.threshold(roi, 180, 255, cv2.THRESH_BINARY)
        text_pixels = cv2.countNonZero(text_mask)
        total_pixels = roi.shape[0] * roi.shape[1]

        # If there's a meaningful amount of light text in this region,
        # it's likely a player panel
        text_ratio = text_pixels / max(total_pixels, 1)
        if text_ratio > 0.02 and text_pixels > 50:
            # Find the tight bounding box of the text
            coords = np.where(text_mask > 0)
            if len(coords[0]) > 0:
                ty1 = coords[0].min() + y1
                ty2 = coords[0].max() + y1
                tx1 = coords[1].min() + x1
                tx2 = coords[1].max() + x1

                # Add padding
                pad = 5
                bx1 = max(0, tx1 - pad)
                by1 = max(0, ty1 - pad)
                bx2 = min(w, tx2 + pad)
                by2 = min(h, ty2 + pad)

                bw = bx2 - bx1
                bh = by2 - by1

                # Validate size: player panels should be reasonable size
                if bw > 20 and bh > 10 and bw < w * 0.5 and bh < h * 0.15:
                    cx = (bx1 + bw / 2) / w
                    cy = (by1 + bh / 2) / h
                    nw = bw / w
                    nh = bh / h
                    panels.append(f"{PLAYER_PANEL} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    return panels


def detect_card_backs_lab(img):
    """
    Detect red card backs in lab screenshots.
    Lab card backs are red rectangles near player positions.
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Red card backs in lab UI
    r1 = cv2.inRange(hsv, np.array([0, 60, 50]), np.array([15, 255, 220]))
    r2 = cv2.inRange(hsv, np.array([155, 60, 50]), np.array([180, 255, 220]))
    mask = r1 | r2

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    backs = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        ratio = ch / max(cw, 1)

        # Card-like rectangle
        if 1.0 < ratio < 2.5 and 100 < area < 8000 and cw > 6 and ch > 10:
            # Must not be in the center board area (board cards are white)
            cy_pct = (y + ch / 2) / h
            cx_pct = (x + cw / 2) / w
            # Card backs are near seats, not at exact center
            is_center = (0.3 < cx_pct < 0.7) and (0.35 < cy_pct < 0.55)
            if not is_center:
                cx = (x + cw / 2) / w
                cy = (y + ch / 2) / h
                nw = cw / w
                nh = ch / h
                backs.append(f"{CARD_BACK} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    return backs


def supplement_labels(label_path, img_path):
    """Add missing player_panel and card_back labels to existing label file."""
    img = cv2.imread(img_path)
    if img is None:
        return 0

    # Read existing labels
    existing = []
    if os.path.exists(label_path):
        with open(label_path) as f:
            existing = [line.strip() for line in f if line.strip()]

    # Count existing classes
    existing_panels = sum(1 for l in existing if l.startswith(f"{PLAYER_PANEL} "))
    existing_backs = sum(1 for l in existing if l.startswith(f"{CARD_BACK} "))

    added = 0

    # Add player panels if few were detected
    if existing_panels < 2:
        new_panels = detect_player_panels_by_text(img)
        if new_panels:
            # Remove any existing panel labels (replace with better ones)
            existing = [l for l in existing if not l.startswith(f"{PLAYER_PANEL} ")]
            existing.extend(new_panels)
            added += len(new_panels)

    # Add card backs if none were detected
    if existing_backs == 0:
        new_backs = detect_card_backs_lab(img)
        if new_backs:
            existing.extend(new_backs)
            added += len(new_backs)

    if added > 0:
        with open(label_path, 'w') as f:
            f.write('\n'.join(existing))

    return added


def main():
    total_added = 0
    files_modified = 0

    for split in ['train', 'val']:
        img_dir = os.path.join(DATASET_DIR, 'images', split)
        lbl_dir = os.path.join(DATASET_DIR, 'labels', split)

        # Only process lab screenshots
        files = [f for f in os.listdir(img_dir) if f.startswith('lab_')]
        print(f"[{split}] Found {len(files)} lab images")

        for fname in files:
            img_path = os.path.join(img_dir, fname)
            lbl_path = os.path.join(lbl_dir, fname.replace('.png', '.txt'))
            added = supplement_labels(lbl_path, img_path)
            if added > 0:
                total_added += added
                files_modified += 1

    print(f"\nSupplemented {files_modified} files with {total_added} additional labels")

    # Recount class distribution
    class_counts = {}
    for split in ['train', 'val']:
        lbl_dir = os.path.join(DATASET_DIR, 'labels', split)
        for fname in os.listdir(lbl_dir):
            if not fname.startswith('lab_'):
                continue
            fpath = os.path.join(lbl_dir, fname)
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        cls_id = int(line.split()[0])
                        class_counts[cls_id] = class_counts.get(cls_id, 0) + 1

    print("\nUpdated lab class distribution:")
    for cls_id in sorted(class_counts.keys()):
        name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f"class_{cls_id}"
        print(f"  {name}: {class_counts[cls_id]}")


if __name__ == '__main__':
    main()
