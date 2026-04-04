"""
Test CNN card detection accuracy on all 52 cards, 10 variations each.

Creates test images by taking each PS template and applying
random transformations (brightness, shift, scale, noise) to simulate
how cards appear at different table positions.

No screen capture needed — pure offline test.

Usage:
  python vision/test_card_cnn_accuracy.py
"""

import cv2
import numpy as np
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_cnn_detect import CardCNNDetector

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "ps_cards"
RANKS = "AKQJT98765432"
SUITS = "shdc"
ALL_CARDS = [f"{r}{s}" for r in RANKS for s in SUITS]

TESTS_PER_CARD = 10


def augment(img):
    """Random augmentation to simulate different rendering positions."""
    h, w = img.shape[:2]
    result = img.copy()

    # Random brightness (±15%)
    factor = 0.85 + np.random.random() * 0.30
    result = np.clip(result * factor, 0, 255).astype(np.uint8)

    # Random slight shift (±3 pixels)
    dx, dy = np.random.randint(-3, 4), np.random.randint(-3, 4)
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    result = cv2.warpAffine(result, M, (w, h), borderValue=(245, 245, 245))

    # Random scale (±8%)
    scale = 0.92 + np.random.random() * 0.16
    new_h, new_w = int(h * scale), int(w * scale)
    scaled = cv2.resize(result, (new_w, new_h))
    # Crop or pad back to original size
    if new_h >= h and new_w >= w:
        y_off = (new_h - h) // 2
        x_off = (new_w - w) // 2
        result = scaled[y_off:y_off + h, x_off:x_off + w]
    else:
        canvas = np.ones((h, w, 3), dtype=np.uint8) * 245
        y_off = (h - new_h) // 2
        x_off = (w - new_w) // 2
        canvas[y_off:y_off + new_h, x_off:x_off + new_w] = scaled
        result = canvas

    # Random noise (30% chance)
    if np.random.random() < 0.3:
        noise = np.random.normal(0, 3, result.shape).astype(np.int16)
        result = np.clip(result.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    return result


def main():
    cnn = CardCNNDetector()

    print("=" * 60)
    print("  CARD CNN ACCURACY TEST — 52 cards x 10 variations")
    print("=" * 60)
    print()

    total = 0
    passed = 0
    rank_passed = 0
    suit_passed = 0
    failures = []

    for label in ALL_CARDS:
        path = TEMPLATE_DIR / f"{label}.png"
        if not path.exists():
            print(f"  SKIP: {label} — template missing")
            continue

        img = cv2.imread(str(path))
        card_pass = 0

        for trial in range(TESTS_PER_CARD):
            # Apply augmentation
            test_img = augment(img) if trial > 0 else img.copy()

            detected, conf = cnn.identify(test_img)
            total += 1

            rank_ok = detected[0] == label[0] if len(detected) >= 1 else False
            suit_ok = detected == label
            if rank_ok:
                rank_passed += 1
            if suit_ok:
                suit_passed += 1
                passed += 1
                card_pass += 1
            else:
                failures.append({
                    "expected": label,
                    "detected": detected,
                    "trial": trial,
                    "conf": conf,
                    "rank_ok": rank_ok,
                })

        status = "PASS" if card_pass == TESTS_PER_CARD else f"{card_pass}/{TESTS_PER_CARD}"
        if card_pass < TESTS_PER_CARD:
            wrong = [f for f in failures if f["expected"] == label]
            det_set = set(f["detected"] for f in wrong)
            print(f"  [{status}] {label} — misdetected as: {det_set}")
        else:
            print(f"  [{status}] {label}")

    print()
    print("-" * 60)
    print(f"  Total tests:  {total}")
    print(f"  Exact match:  {passed}/{total} ({passed / total * 100:.1f}%)")
    print(f"  Rank correct: {rank_passed}/{total} ({rank_passed / total * 100:.1f}%)")
    print(f"  Suit correct: {suit_passed}/{total} ({suit_passed / total * 100:.1f}%)")

    if failures:
        print(f"\n  Unique failures: {len(set((f['expected'], f['detected']) for f in failures))}")
        # Group by expected
        by_exp = {}
        for f in failures:
            by_exp.setdefault(f["expected"], []).append(f["detected"])
        for exp, dets in sorted(by_exp.items()):
            from collections import Counter
            counts = Counter(dets)
            det_str = ", ".join(f"{d}({c})" for d, c in counts.most_common(3))
            print(f"    {exp} → {det_str}")

    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
