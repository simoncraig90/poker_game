"""
Capture card templates from live PokerStars session.

Watches the screen, detects cards via YOLO, uses OCR to identify the rank,
detects suit by color, and saves each unique card as a template.

Run while playing — it captures silently in the background.

Usage:
  python vision/capture_ps_templates.py
  python vision/capture_ps_templates.py --debug
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

OUT_DIR = Path(__file__).resolve().parent / "templates" / "ps_cards"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Track what we've captured
captured = set()
for f in OUT_DIR.iterdir():
    if f.suffix == ".png" and len(f.stem) == 2:
        captured.add(f.stem)


def _build_rank_templates():
    """Build rank templates from captured cards — crop the rank corner from each."""
    rank_tmpls = {}  # rank -> list of corner crops
    for f in OUT_DIR.iterdir():
        if f.suffix != ".png" or len(f.stem) != 2:
            continue
        rank = f.stem[0]
        img = cv2.imread(str(f))
        if img is None:
            continue
        h, w = img.shape[:2]
        corner = img[0:int(h * 0.32), 0:int(w * 0.48)]
        if rank not in rank_tmpls:
            rank_tmpls[rank] = []
        rank_tmpls[rank].append(corner)
    return rank_tmpls


def detect_rank_hybrid(corner):
    """Detect rank using OCR + template matching against known rank corners."""
    import easyocr
    global _reader, _rank_templates, _rank_templates_built
    if '_reader' not in globals() or _reader is None:
        _reader = easyocr.Reader(['en'], gpu=True, verbose=False)

    RANK_MAP = {
        'A': 'A', 'K': 'K', 'Q': 'Q', 'J': 'J',
        '10': 'T', 'T': 'T', '1': 'T',
        '9': '9', '8': '8', '7': '7', '6': '6',
        '5': '5', '4': '4', '3': '3', '2': '2',
    }

    # Try OCR first
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
            for ch in text:
                if ch in RANK_MAP:
                    return RANK_MAP[ch], max(conf, 0.3)

    # OCR failed — use template matching against known rank corners
    if '_rank_templates' not in globals() or not _rank_templates:
        _rank_templates = _build_rank_templates()

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

        if best_score > 0.85:
            return best_rank, best_score
        elif best_score < 0.85:
            # Very low match — this is likely a rank we don't have (4)
            # Check which ranks we're missing
            have_ranks = set(_rank_templates.keys())
            all_ranks = set("AKQJT98765432")
            missing_ranks = all_ranks - have_ranks
            if len(missing_ranks) == 1:
                return missing_ranks.pop(), 0.5  # only one possibility

    return None, 0


def detect_suit_color(corner):
    """Detect if card is red or black from corner crop."""
    hsv = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
    red_px = cv2.countNonZero(red1) + cv2.countNonZero(red2)
    return "red" if red_px > 50 else "black"


def detect_suit_shape(suit_crop, is_red):
    """Detect specific suit from the suit pip area."""
    if is_red:
        hsv = cv2.cvtColor(suit_crop, cv2.COLOR_BGR2HSV)
        red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
        red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
        mask = cv2.bitwise_or(red1, red2)
    else:
        gray = cv2.cvtColor(suit_crop, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 'h' if is_red else 's'

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    hull_area = cv2.contourArea(cv2.convexHull(largest))
    solidity = area / max(1, hull_area)

    if is_red:
        # Heart: indentation at top → lower solidity
        # Diamond: convex → higher solidity
        # Also check: widest row position
        sh = mask.shape[0]
        row_widths = [cv2.countNonZero(mask[r:r+1, :]) for r in range(sh)]
        max_row = row_widths.index(max(row_widths)) if row_widths else 0
        max_pct = max_row / max(1, sh)
        # Heart: widest at ~30% from top, Diamond: widest at ~50%
        return 'h' if max_pct < 0.45 else 'd'
    else:
        # Spade: smooth → higher solidity
        # Club: three bumps → lower solidity
        return 's' if solidity > 0.80 else 'c'


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    model = load_model()
    print(f"\n{'='*55}")
    print(f"  PS CARD TEMPLATE CAPTURE")
    print(f"{'='*55}")
    print(f"  Already have: {len(captured)} cards ({', '.join(sorted(captured)) if captured else 'none'})")
    print(f"  Need: {52 - len(captured)} more")
    print(f"  Play hands normally — templates captured automatically")
    print(f"  Ctrl+C to stop\n")

    # Warm up OCR
    print("  Loading OCR...", end="", flush=True)
    import easyocr
    global _reader
    _reader = easyocr.Reader(['en'], gpu=True, verbose=False)
    print(" done")

    with mss.mss() as sct:
        while True:
          try:
            time.sleep(0.5)

            # Capture screen
            monitor = sct.monitors[1]
            img = np.array(sct.grab(monitor))
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

            # Find table
            region = find_table_region(frame)
            if region is None:
                continue

            table_img, _ = crop_table(frame, region)
            th, tw = table_img.shape[:2]

            # Detect elements
            elements = detect_elements(table_img, conf=0.4)
            if elements is None:
                continue

            # Process all visible cards (hero + board)
            all_cards = []
            for key in ["hero_card", "board_card"]:
                for card in elements.get(key, []):
                    all_cards.append(card)

            for card in all_cards:
                x1 = max(0, card["x"] - 2)
                y1 = max(0, card["y"] - 2)
                x2 = min(tw, card["x"] + card["w"] + 2)
                y2 = min(th, card["y"] + card["h"] + 2)
                crop = table_img[y1:y2, x1:x2]
                if crop.size == 0:
                    continue

                ch, cw = crop.shape[:2]

                # Skip narrow/overlapping cards — need clean single cards
                if cw < ch * 0.55:
                    continue

                # OCR rank
                corner = crop[0:int(ch * 0.35), 0:int(cw * 0.50)]
                rank, conf = detect_rank_hybrid(corner)

                # Detect suit color
                color = detect_suit_color(corner)
                is_red = color == "red"

                # Suit from pip area
                suit_area = crop[int(ch * 0.28):int(ch * 0.55), 0:int(cw * 0.45)]
                if suit_area.size > 0:
                    suit = detect_suit_shape(suit_area, is_red)
                else:
                    suit = 'h' if is_red else 's'

                label = None
                if rank is not None and conf >= 0.2:
                    label = f"{rank}{suit}"

                # Elimination fallback: if OCR fails, match against existing
                # templates. If no good match, this is an unknown card.
                if label is None or label in captured:
                    # Check if this card matches any existing template well
                    best_match_score = 0
                    best_match_label = None
                    for existing_label, existing_path in [(l, str(OUT_DIR / f"{l}.png")) for l in captured]:
                        tmpl = cv2.imread(existing_path)
                        if tmpl is None:
                            continue
                        resized = cv2.resize(tmpl, (cw, ch))
                        score = cv2.matchTemplate(crop, resized, cv2.TM_CCOEFF_NORMED)[0][0]
                        if score > best_match_score:
                            best_match_score = score
                            best_match_label = existing_label

                    if best_match_score < 0.5:
                        # Low match against all known cards — this is a new card!
                        # Figure out which missing card it is
                        missing = []
                        for r in "AKQJT98765432":
                            for s in "shdc":
                                if f"{r}{s}" not in captured:
                                    missing.append(f"{r}{s}")
                        # Filter by color
                        if is_red:
                            candidates = [m for m in missing if m[1] in ('h', 'd')]
                        else:
                            candidates = [m for m in missing if m[1] in ('s', 'c')]

                        if len(candidates) == 1:
                            label = candidates[0]  # only one possibility
                        elif rank is not None:
                            # OCR gave us rank even with low conf
                            matches = [c for c in candidates if c[0] == rank]
                            if len(matches) == 1:
                                label = matches[0]
                            elif matches:
                                label = matches[0]
                            elif candidates:
                                label = candidates[0]
                        elif candidates:
                            # Save as first candidate, can rename later
                            label = candidates[0]

                    if label is None or label in captured:
                        continue

                # Save template
                out_path = OUT_DIR / f"{label}.png"
                cv2.imwrite(str(out_path), crop)
                captured.add(label)

                remaining = 52 - len(captured)
                print(f"  Captured: {label} ({cw}x{ch}) — {len(captured)}/52 ({remaining} remaining)")

                if len(captured) >= 52:
                    print(f"\n  ALL 52 CARDS CAPTURED!")
                    return

            if args.debug and len(all_cards) > 0:
                print(f"  [debug] {len(all_cards)} cards visible, {len(captured)} captured")

          except KeyboardInterrupt:
            raise
          except Exception as e:
            print(f"  [error] {e}")
            time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  Stopped. Captured {len(captured)}/52 cards.")
        missing = []
        for r in "AKQJT98765432":
            for s in "shdc":
                if f"{r}{s}" not in captured:
                    missing.append(f"{r}{s}")
        if missing:
            print(f"  Missing: {', '.join(missing)}")
        print(f"  Templates saved to {OUT_DIR}")
