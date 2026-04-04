r"""
Capture clean board card images from live PokerStars for the lab client.

Board cards are displayed individually (no overlap) on the table felt,
making them perfect source images for the lab client's card rendering.

Run while playing PS -- captures board cards automatically each hand.

Usage:
  python vision/capture_board_cards.py
  python vision/capture_board_cards.py --debug
"""

import cv2
import numpy as np
import os
import sys
import time
from pathlib import Path

import mss

sys.path.insert(0, str(Path(__file__).resolve().parent))

from advisor import find_table_region, crop_table
from yolo_detect import load_model, detect_elements

# Output: lab client card images
OUT_DIR = Path(__file__).resolve().parent.parent / "client" / "ps_assets" / "cards"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Also save to vision templates for CNN training
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "ps_board_cards"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

# Track what we have
ALL_CARDS = set()
for rank in "23456789TJQKA":
    for suit in "cdhs":
        ALL_CARDS.add(f"{rank}{suit}")

# Count all existing cards but always allow overwriting with new captures
captured = set()
_always_recapture = set()  # cards we know are clones
for f in OUT_DIR.iterdir():
    if f.suffix == ".png" and f.stem in ALL_CARDS:
        if f.stat().st_size > 5000:
            captured.add(f.stem)
# Never consider ourselves "done" — always capture new unique cards
# even if we already have a clone for that slot


def detect_rank_from_corner(corner):
    """Detect rank using template matching against captured cards, OCR fallback."""
    global _rank_templates, _reader

    # Build rank templates from already-captured cards (corner crops)
    if '_rank_templates' not in globals() or _rank_templates is None:
        _rank_templates = {}
        for f in OUT_DIR.iterdir():
            if f.suffix == ".png" and f.stem in ALL_CARDS and f.stat().st_size > 5000:
                rank = f.stem[0]
                img = cv2.imread(str(f))
                if img is None:
                    continue
                h, w = img.shape[:2]
                tmpl_corner = img[0:int(h * 0.35), 0:int(w * 0.50)]
                if rank not in _rank_templates:
                    _rank_templates[rank] = []
                _rank_templates[rank].append(tmpl_corner)
        print(f"  [tmpl] Built rank templates for: {sorted(_rank_templates.keys())}")

    # Template matching first
    if _rank_templates:
        ch, cw = corner.shape[:2]
        best_rank = None
        best_score = 0
        for rank, tmpls in _rank_templates.items():
            for tmpl in tmpls:
                resized = cv2.resize(tmpl, (cw, ch))
                score = cv2.matchTemplate(corner, resized, cv2.TM_CCOEFF_NORMED)[0][0]
                if score > best_score:
                    best_score = score
                    best_rank = rank
        if best_score > 0.70:
            return best_rank, best_score

    # OCR fallback
    if '_reader' not in globals() or _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=True, verbose=False)

    RANK_MAP = {
        'A': 'A', 'K': 'K', 'Q': 'Q', 'J': 'J',
        '10': 'T', 'T': 'T', '1': 'T',
        '9': '9', '8': '8', '7': '7', '6': '6',
        '5': '5', '4': '4', '3': '3', '2': '2',
    }

    for allowlist in ['AKQJT1098765432', None]:
        results = _reader.readtext(corner, allowlist=allowlist)
        for bbox, text, conf in results:
            text = text.strip().upper().replace('O', '0')
            if conf < 0.15:
                continue
            if text in RANK_MAP:
                return RANK_MAP[text], conf
            if '10' in text or ('1' in text and len(text) <= 3):
                return 'T', conf
            for ch_c in text:
                if ch_c in RANK_MAP:
                    return RANK_MAP[ch_c], max(conf, 0.3)
    return None, 0


def detect_suit(crop):
    """Detect suit from card crop using color + shape analysis."""
    ch, cw = crop.shape[:2]
    corner = crop[0:int(ch * 0.35), 0:int(cw * 0.50)]

    # Color: red or black?
    hsv = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
    red_px = cv2.countNonZero(red1) + cv2.countNonZero(red2)
    is_red = red_px > 50

    # Shape analysis on suit pip area
    suit_area = crop[int(ch * 0.28):int(ch * 0.55), 0:int(cw * 0.45)]
    if suit_area.size == 0:
        return 'h' if is_red else 's'

    if is_red:
        hsv2 = cv2.cvtColor(suit_area, cv2.COLOR_BGR2HSV)
        r1 = cv2.inRange(hsv2, np.array([0, 60, 60]), np.array([15, 255, 255]))
        r2 = cv2.inRange(hsv2, np.array([155, 60, 60]), np.array([180, 255, 255]))
        mask = cv2.bitwise_or(r1, r2)
    else:
        gray = cv2.cvtColor(suit_area, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 'h' if is_red else 's'

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    hull_area = cv2.contourArea(cv2.convexHull(largest))
    solidity = area / max(1, hull_area)

    if is_red:
        sh = mask.shape[0]
        row_widths = [cv2.countNonZero(mask[r:r + 1, :]) for r in range(sh)]
        max_row = row_widths.index(max(row_widths)) if row_widths else 0
        max_pct = max_row / max(1, sh)
        return 'h' if max_pct < 0.45 else 'd'
    else:
        return 's' if solidity > 0.80 else 'c'


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    model = load_model()
    print(f"\n{'=' * 60}")
    print(f"  BOARD CARD CAPTURE — for lab client PS-identical cards")
    print(f"{'=' * 60}")
    print(f"  Have: {len(captured)}/52 ({', '.join(sorted(captured)) if len(captured) < 20 else '...'})")
    print(f"  Need: {52 - len(captured)} more")
    print(f"  Output: {OUT_DIR}")
    print(f"  Play hands — board cards captured automatically")
    print(f"  ONLY board cards (no hero overlap)")
    print(f"  Ctrl+C to stop\n")

    print("  Loading OCR...", end="", flush=True)
    import easyocr
    global _reader
    _reader = easyocr.Reader(['en'], gpu=True, verbose=False)
    print(" done\n")

    last_board_hash = None

    with mss.mss() as sct:
        while True:  # run continuously, replacing clones with real captures
            try:
                time.sleep(0.8)

                monitor = sct.monitors[1]
                img = np.array(sct.grab(monitor))
                frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                region = find_table_region(frame)
                if region is None:
                    continue

                table_img, _ = crop_table(frame, region)
                th, tw = table_img.shape[:2]

                elements = detect_elements(table_img, conf=0.4)
                if elements is None:
                    continue

                board_cards = elements.get("board_card", [])
                if len(board_cards) < 3:
                    continue  # need at least flop

                # Hash board card positions to avoid re-processing same board
                board_hash = hash(tuple(
                    (c["cx"], c["cy"]) for c in sorted(board_cards, key=lambda c: c["cx"])
                ))
                if board_hash == last_board_hash:
                    continue
                last_board_hash = board_hash

                new_this_frame = 0
                for card in board_cards:
                    x1 = max(0, card["x"])
                    y1 = max(0, card["y"])
                    x2 = min(tw, card["x"] + card["w"])
                    y2 = min(th, card["y"] + card["h"])
                    crop = table_img[y1:y2, x1:x2]
                    if crop.size == 0:
                        continue

                    ch, cw = crop.shape[:2]
                    if cw < 20 or ch < 30:
                        continue

                    # Board cards should be roughly card-shaped (not too wide)
                    aspect = ch / cw
                    if aspect < 1.0 or aspect > 2.0:
                        continue

                    # Identify rank
                    corner = crop[0:int(ch * 0.35), 0:int(cw * 0.50)]
                    rank, conf = detect_rank_from_corner(corner)
                    if rank is None or conf < 0.2:
                        # Save unidentified crops for manual labeling
                        unk_dir = OUT_DIR.parent.parent / "vision" / "unknown_cards"
                        unk_dir.mkdir(parents=True, exist_ok=True)
                        unk_path = unk_dir / f"unknown_{int(time.time())}_{i}.png"
                        cv2.imwrite(str(unk_path), crop)
                        if args.debug:
                            print(f"    ? rank unknown (conf={conf:.2f}) -> saved {unk_path.name}")
                        continue

                    # Identify suit
                    suit = detect_suit(crop)
                    label = f"{rank}{suit}"

                    if label not in ALL_CARDS:
                        continue

                    if label in captured:
                        # Still save if the existing file might be a clone
                        # (check if identical size to another same-rank card)
                        existing = OUT_DIR / f"{label}.png"
                        if existing.exists():
                            es = existing.stat().st_size
                            is_clone = False
                            for other in OUT_DIR.iterdir():
                                if other.stem != label and other.stem[0] == label[0] and other.suffix == ".png":
                                    if abs(other.stat().st_size - es) < 100:
                                        is_clone = True
                                        break
                            if not is_clone:
                                continue

                    # Save clean board card
                    out_path = OUT_DIR / f"{label}.png"
                    cv2.imwrite(str(out_path), crop)

                    # Also save to template dir
                    tmpl_path = TEMPLATE_DIR / f"{label}.png"
                    cv2.imwrite(str(tmpl_path), crop)

                    captured.add(label)
                    new_this_frame += 1
                    print(f"  ✓ {label}  ({len(captured)}/52)")

                if new_this_frame > 0:
                    remaining = ALL_CARDS - captured
                    if len(remaining) <= 10:
                        print(f"    Still need: {', '.join(sorted(remaining))}")

            except KeyboardInterrupt:
                break
            except Exception as e:
                if args.debug:
                    print(f"  Error: {e}")
                time.sleep(1)

    print(f"\n{'=' * 60}")
    print(f"  DONE — captured {len(captured)}/52 cards")
    missing = ALL_CARDS - captured
    if missing:
        print(f"  Missing: {', '.join(sorted(missing))}")
    else:
        print(f"  COMPLETE! All 52 cards captured.")
    print(f"  Saved to: {OUT_DIR}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
