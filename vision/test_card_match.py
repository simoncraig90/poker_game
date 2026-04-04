"""
Quick diagnostic: capture the screen, find hero cards, and show match scores
for ALL 52 templates so we can see exactly what's happening.
"""
import cv2
import numpy as np
import mss
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from detect import find_cards_by_color
from advisor import find_table_region, crop_table, capture_screen

SCREEN_DIR = os.path.join(os.path.dirname(__file__), "templates", "screen_cards")

# Load all full templates (no _narrow)
templates = {}
for f in os.listdir(SCREEN_DIR):
    if f.endswith('.png') and '_narrow' not in f:
        label = f.replace('.png', '')
        templates[label] = cv2.imread(os.path.join(SCREEN_DIR, f))

print(f"Loaded {len(templates)} templates")

# Capture screen
frame = capture_screen()
region = find_table_region(frame)
if not region:
    print("No table found!")
    sys.exit(1)

table_img, offset = crop_table(frame, region)
cards = find_cards_by_color(table_img)

table_path = os.path.join(os.path.dirname(__file__), "data", "debug_table.png")
cv2.imwrite(table_path, table_img)
th, tw = table_img.shape[:2]
print(f"Saved table to {table_path}")
print(f"Table size: {tw}x{th}")
print(f"All detected cards: {len(cards.get('all', []))}")
for c in cards.get("all", []):
    zone = "HERO" if c["cy"] > th * 0.65 else ("BOARD" if c["cy"] > th * 0.15 else "TOP")
    print(f"  {zone}: x={c['x']} y={c['y']} w={c['w']} h={c['h']} cy/h={c['cy']/th:.2f}")

if not cards.get("hero"):
    print("No hero cards detected!")
    sys.exit(1)

h, w = table_img.shape[:2]
for i, card in enumerate(cards["hero"]):
    x1 = max(0, card["x"] - 2)
    y1 = max(0, card["y"] - 2)
    x2 = min(w, card["x"] + card["w"] + 2)
    y2 = min(h, card["y"] + card["h"] + 2)
    crop = table_img[y1:y2, x1:x2]
    crop_h, crop_w = crop.shape[:2]
    is_narrow = crop_w < crop_h * 0.55

    print(f"\n{'='*60}")
    print(f"Card {i}: {crop_w}x{crop_h} {'NARROW' if is_narrow else 'FULL'}")
    print(f"{'='*60}")

    # Save the crop for inspection
    save_path = os.path.join(os.path.dirname(__file__), "data", f"debug_card_{i}.png")
    cv2.imwrite(save_path, crop)
    print(f"Saved crop to {save_path}")
    print(f"Position: x={card['x']} y={card['y']} (cy={card['cy']}, table h={h})")
    print(f"cy/h = {card['cy']/h:.2f}")

    # Test 1: Full card matching (resize template to crop size)
    print("\n--- Full card match (top 10) ---")
    scores_full = []
    for label, tmpl in templates.items():
        th, tw = tmpl.shape[:2]
        if is_narrow:
            frac = crop_w / (crop_h * (tw / th))
            frac = min(1.0, max(0.3, frac))
            tmpl_work = tmpl[:, :int(tw * frac)]
        else:
            tmpl_work = tmpl
        tmpl_resized = cv2.resize(tmpl_work, (crop_w, crop_h))
        score = cv2.matchTemplate(crop, tmpl_resized, cv2.TM_CCOEFF_NORMED)[0][0]
        scores_full.append((label, score))
    scores_full.sort(key=lambda x: -x[1])
    for label, score in scores_full[:10]:
        print(f"  {label:4s} {score:.4f}")

    # Test 2: Rank region only (top 45%)
    print("\n--- Rank region match (top 10) ---")
    rank_crop = crop[0:int(crop_h * 0.45), :]
    scores_rank = []
    for label, tmpl in templates.items():
        th, tw = tmpl.shape[:2]
        if is_narrow:
            frac = crop_w / (crop_h * (tw / th))
            frac = min(1.0, max(0.3, frac))
            tmpl_work = tmpl[:, :int(tw * frac)]
        else:
            tmpl_work = tmpl
        tmpl_rank = tmpl_work[0:int(tmpl_work.shape[0] * 0.45), :]
        tmpl_rank_resized = cv2.resize(tmpl_rank, (rank_crop.shape[1], rank_crop.shape[0]))
        score = cv2.matchTemplate(rank_crop, tmpl_rank_resized, cv2.TM_CCOEFF_NORMED)[0][0]
        scores_rank.append((label, score))
    scores_rank.sort(key=lambda x: -x[1])
    for label, score in scores_rank[:10]:
        print(f"  {label:4s} {score:.4f}")

    # Test 3: Top-left corner only (top 40%, left portion)
    print("\n--- Corner match (top 10) ---")
    corner_h = int(crop_h * 0.40)
    corner_w = crop_w if is_narrow else int(crop_w * 0.50)
    corner_crop = crop[0:corner_h, 0:corner_w]
    scores_corner = []
    for label, tmpl in templates.items():
        th, tw = tmpl.shape[:2]
        if is_narrow:
            frac = crop_w / (crop_h * (tw / th))
            frac = min(1.0, max(0.3, frac))
            tmpl_work = tmpl[:, :int(tw * frac)]
        else:
            tmpl_work = tmpl
        tc_h = int(tmpl_work.shape[0] * 0.40)
        tc_w = tmpl_work.shape[1] if is_narrow else int(tmpl_work.shape[1] * 0.50)
        tmpl_corner = tmpl_work[0:tc_h, 0:tc_w]
        tmpl_corner_resized = cv2.resize(tmpl_corner, (corner_crop.shape[1], corner_crop.shape[0]))
        score = cv2.matchTemplate(corner_crop, tmpl_corner_resized, cv2.TM_CCOEFF_NORMED)[0][0]
        scores_corner.append((label, score))
    scores_corner.sort(key=lambda x: -x[1])
    for label, score in scores_corner[:10]:
        print(f"  {label:4s} {score:.4f}")

print("\nDone. Check vision/data/debug_card_*.png for the actual crops.")
