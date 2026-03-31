"""
Batch analyze collected training frames.
Runs detection on all frames and outputs a summary + saves annotated images.
"""

import os
import sys
import json
import cv2
import time

sys.path.insert(0, os.path.dirname(__file__))
from detect import read_text_regions, find_dollar_amounts, find_pot, find_player_names, find_dealer_button, find_cards_by_color


def analyze_frame(img_path):
    """Analyze a single frame, return structured result."""
    img = cv2.imread(img_path)
    if img is None:
        return None

    h, w = img.shape[:2]
    texts = read_text_regions(img)
    amounts = find_dollar_amounts(texts)
    pot = find_pot(texts, h)
    players = find_player_names(texts, amounts, w, h)
    cards = find_cards_by_color(img)
    dealer = find_dealer_button(img)

    return {
        "file": os.path.basename(img_path),
        "size": f"{w}x{h}",
        "texts": len(texts),
        "amounts": len(amounts),
        "players": [{
            "name": p["name"],
            "stack": p["stack"],
        } for p in players],
        "pot": pot.get("amount") if pot and isinstance(pot, dict) and "amount" in pot else None,
        "cards": len(cards),
        "dealer": bool(dealer),
    }


def main():
    dirs = [
        os.path.join(os.path.dirname(__file__), "captures", "training"),
        os.path.join(os.path.dirname(__file__), "captures"),
    ]

    frames = []
    for d in dirs:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".png") and not f.endswith("_detected.png"):
                    if f.startswith("table_") or f.startswith("frame_"):
                        frames.append(os.path.join(d, f))

    if not frames:
        print("No frames found to analyze.")
        return

    frames.sort()
    print(f"Analyzing {len(frames)} frames...")
    print("=" * 50)

    results = []
    for i, path in enumerate(frames):
        print(f"\n[{i+1}/{len(frames)}] {os.path.basename(path)}")
        t0 = time.time()
        result = analyze_frame(path)
        elapsed = time.time() - t0

        if result:
            result["time_s"] = round(elapsed, 2)
            results.append(result)
            print(f"  {result['size']} | {len(result['players'])} players | pot: ${result['pot']:.2f}" if result['pot'] else f"  {result['size']} | {len(result['players'])} players | no pot")
            print(f"  {result['texts']} texts | {result['amounts']} amounts | {result['cards']} cards | dealer: {result['dealer']}")
            print(f"  Time: {elapsed:.1f}s")
            for p in result["players"]:
                print(f"    {p['name']}: ${p['stack']:.2f}")

    # Summary
    print("\n" + "=" * 50)
    print(f"SUMMARY: {len(results)} frames analyzed")
    avg_time = sum(r["time_s"] for r in results) / len(results) if results else 0
    avg_players = sum(len(r["players"]) for r in results) / len(results) if results else 0
    print(f"  Avg time: {avg_time:.1f}s")
    print(f"  Avg players detected: {avg_players:.1f}")
    print(f"  Frames with pot: {sum(1 for r in results if r['pot'])}/{len(results)}")
    print(f"  Frames with dealer: {sum(1 for r in results if r['dealer'])}/{len(results)}")
    print(f"  Frames with cards: {sum(1 for r in results if r['cards'] > 0)}/{len(results)}")

    # Save results
    out = os.path.join(os.path.dirname(__file__), "analysis_results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
