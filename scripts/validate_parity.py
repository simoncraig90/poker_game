"""
Step 4: Validate detection parity between PokerStars and poker-lab screenshots.
Runs the retrained YOLO model on both types and compares class counts + confidences.
"""

import os
import sys
import glob

def main():
    from ultralytics import YOLO

    best_path = os.path.join(os.path.dirname(__file__), '..', 'vision', 'runs', 'poker_lab', 'weights', 'best.pt')
    model = YOLO(best_path)

    CLASS_NAMES = ['board_card', 'hero_card', 'card_back', 'player_panel',
                   'dealer_button', 'chip', 'pot_text', 'action_button']

    # Find test images
    captures_dir = os.path.join(os.path.dirname(__file__), '..', 'vision', 'captures')

    # PS screenshots (from training dir, pick a few)
    ps_dir = os.path.join(os.path.dirname(__file__), '..', 'vision', 'captures', 'training')
    ps_files = sorted(glob.glob(os.path.join(ps_dir, 'frame_*.png')))[:5]

    # Lab screenshots (from lab_gen, pick diverse ones)
    lab_dir = os.path.join(os.path.dirname(__file__), '..', 'vision', 'captures', 'lab_gen')
    # Pick screenshots at different phases
    lab_files = []
    for pattern in ['*_preflop.png', '*_flop.png', '*_turn.png', '*_river.png', '*_showdown.png', '*_complete.png']:
        matches = sorted(glob.glob(os.path.join(lab_dir, pattern)))
        if matches:
            lab_files.append(matches[0])
    lab_files = lab_files[:5]

    if not ps_files:
        print("WARNING: No PS screenshots found")
    if not lab_files:
        print("WARNING: No lab screenshots found")

    def run_detection(img_path):
        results = model(img_path, verbose=False, conf=0.25)
        detections = {}
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f"class_{cls_id}"
                if name not in detections:
                    detections[name] = []
                detections[name].append(conf)
        return detections

    def print_detections(label, img_path, dets):
        fname = os.path.basename(img_path)
        print(f"\n  {label}: {fname}")
        if not dets:
            print("    (no detections)")
            return
        for cls_name in sorted(dets.keys()):
            confs = dets[cls_name]
            avg_conf = sum(confs) / len(confs)
            print(f"    {cls_name}: {len(confs)} @ avg {avg_conf:.2f} (range {min(confs):.2f}-{max(confs):.2f})")

    # Run PS detections
    print("=" * 60)
    print("POKERSTARS SCREENSHOTS")
    print("=" * 60)
    ps_all = {}
    for f in ps_files:
        dets = run_detection(f)
        print_detections("PS", f, dets)
        for cls, confs in dets.items():
            if cls not in ps_all:
                ps_all[cls] = []
            ps_all[cls].extend(confs)

    # Run Lab detections
    print("\n" + "=" * 60)
    print("POKER-LAB SCREENSHOTS")
    print("=" * 60)
    lab_all = {}
    for f in lab_files:
        dets = run_detection(f)
        print_detections("Lab", f, dets)
        for cls, confs in dets.items():
            if cls not in lab_all:
                lab_all[cls] = []
            lab_all[cls].extend(confs)

    # Summary comparison
    print("\n" + "=" * 60)
    print("PARITY COMPARISON")
    print("=" * 60)
    all_classes = sorted(set(list(ps_all.keys()) + list(lab_all.keys())))
    print(f"\n{'Class':<18} {'PS count':>10} {'PS avg':>10} {'Lab count':>10} {'Lab avg':>10} {'Gap':>10}")
    print("-" * 68)
    for cls in all_classes:
        ps_confs = ps_all.get(cls, [])
        lab_confs = lab_all.get(cls, [])
        ps_avg = sum(ps_confs) / len(ps_confs) if ps_confs else 0
        lab_avg = sum(lab_confs) / len(lab_confs) if lab_confs else 0
        gap = abs(ps_avg - lab_avg)
        status = "OK" if gap < 0.15 else "WARN"
        ps_ct = len(ps_confs)
        lab_ct = len(lab_confs)
        print(f"  {cls:<16} {ps_ct:>8} {ps_avg:>10.3f} {lab_ct:>8} {lab_avg:>10.3f} {gap:>8.3f}  {status}")

    # Overall parity score
    gaps = []
    for cls in all_classes:
        ps_confs = ps_all.get(cls, [])
        lab_confs = lab_all.get(cls, [])
        if ps_confs and lab_confs:
            ps_avg = sum(ps_confs) / len(ps_confs)
            lab_avg = sum(lab_confs) / len(lab_confs)
            gaps.append(abs(ps_avg - lab_avg))
    if gaps:
        avg_gap = sum(gaps) / len(gaps)
        print(f"\n  Average confidence gap: {avg_gap:.3f}")
        if avg_gap < 0.10:
            print("  RESULT: EXCELLENT parity achieved!")
        elif avg_gap < 0.20:
            print("  RESULT: GOOD parity achieved")
        else:
            print("  RESULT: Further training may be needed")


if __name__ == '__main__':
    main()
