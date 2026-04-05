r"""
Visual parity test — measures how identical the lab client looks to PokerStars.

Compares a CDP screenshot of the lab client against a PS reference screenshot.
Computes SSIM (structural similarity) overall and per-region.

Usage:
    python vision/test_visual_parity.py
    python vision/test_visual_parity.py --reference path/to/ps_screenshot.png

Regions compared:
    - table_felt: the green oval area
    - board_cards: the 5 community card slots
    - hero_cards: the hero's hole cards
    - action_buttons: fold/call/raise buttons
    - seat_panels: player name/stack panels
    - pot_text: pot amount display

Target: overall SSIM > 0.95 (indistinguishable to a human)
"""

import cv2
import numpy as np
import sys
import os
import json
from pathlib import Path
from skimage.metrics import structural_similarity as ssim

sys.path.insert(0, str(Path(__file__).resolve().parent))


def find_table_region(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([25, 30, 20]), np.array([85, 255, 255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < frame.shape[0] * frame.shape[1] * 0.03:
        return None
    return cv2.boundingRect(largest)


def extract_table_normalized(frame, target_w=400, target_h=700):
    """Extract and normalize the table area to a standard size."""
    region = find_table_region(frame)
    if region is None:
        return None, None
    x, y, w, h = region
    # Pad to include hero cards and action buttons below felt
    pad_top = int(h * 0.05)
    pad_bottom = int(h * 0.45)
    pad_side = int(w * 0.15)
    y1 = max(0, y - pad_top)
    y2 = min(frame.shape[0], y + h + pad_bottom)
    x1 = max(0, x - pad_side)
    x2 = min(frame.shape[1], x + w + pad_side)
    table = frame[y1:y2, x1:x2]
    normalized = cv2.resize(table, (target_w, target_h))
    return normalized, (x1, y1, x2 - x1, y2 - y1)


# Regions as percentages of the normalized table image
REGIONS = {
    "table_felt": (0.05, 0.05, 0.90, 0.42),      # the green oval (felt only)
    "board_cards": (0.10, 0.26, 0.80, 0.14),       # community cards only
    "hero_cards": (0.10, 0.74, 0.55, 0.18),        # hero hole cards + panel
    "action_buttons": (0.05, 0.48, 0.90, 0.14),    # fold/call/raise
    "seat_panels": (0.0, 0.0, 1.0, 0.20),          # top opponents
    "pot_text": (0.25, 0.22, 0.50, 0.08),          # pot display
}


def compute_region_ssim(img1, img2, region_name):
    """Compute SSIM for a specific region."""
    h, w = img1.shape[:2]
    rx, ry, rw, rh = REGIONS[region_name]
    x1, y1 = int(rx * w), int(ry * h)
    x2, y2 = int((rx + rw) * w), int((ry + rh) * h)

    crop1 = img1[y1:y2, x1:x2]
    crop2 = img2[y1:y2, x1:x2]

    if crop1.shape != crop2.shape:
        crop2 = cv2.resize(crop2, (crop1.shape[1], crop1.shape[0]))

    gray1 = cv2.cvtColor(crop1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(crop2, cv2.COLOR_BGR2GRAY)

    score = ssim(gray1, gray2)
    return score, crop1, crop2


def capture_lab_screenshot(target_w=400, target_h=700):
    """Capture the lab client via CDP and return normalized table image."""
    try:
        import subprocess
        subprocess.Popen(
            [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             "--remote-debugging-port=9222", "--headless=new",
             "--disable-gpu", "--window-size=500,900"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        import time
        time.sleep(2)

        import websocket
        # Use chrome-remote-interface via Node.js for reliability
        result = subprocess.run(
            ["node", "-e", """
const CDP = require('chrome-remote-interface');
const fs = require('fs');
(async () => {
    const client = await CDP({ port: 9222 });
    const { Page, Emulation, Runtime, Network } = client;
    await Network.enable();
    await Network.setCacheDisabled({ cacheDisabled: true });
    await Emulation.setDeviceMetricsOverride({ width: 900, height: 600, deviceScaleFactor: 1, mobile: false });
    await Page.enable();
    await Page.navigate({ url: 'http://localhost:9100' });
    await Page.loadEventFired();
    await new Promise(r => setTimeout(r, 5000));
    // Inject board + action state
    // Disconnect WS and kill timers to freeze state
    await Runtime.evaluate({ expression: `
        if (ws) { ws.onmessage = null; ws.onclose = null; ws.close(); }
        var id = window.setTimeout(function(){}, 0);
        while (id--) { window.clearTimeout(id); window.clearInterval(id); }
    ` });
    await new Promise(r => setTimeout(r, 500));
    await Runtime.evaluate({ expression: `
        if (state && state.hand) {
            state.hand.board = ['2h','Qd','Jd','3c'];
            state.hand.pot = 233;
            state.hand.actionSeat = 0;
            state.hand.phase = 'TURN';
            state.hand.legalActions = { actions: ['FOLD','CALL','RAISE'], callAmount: 140, minRaise: 280, maxRaise: 800 };
            // Match all players from PS screenshot
            var players = [
                {seat:0, name:'Skurj_poker', stack:1887, cards:['Ts','As']},
                {seat:1, name:'gionni228', stack:1241},
                {seat:2, name:'segard', stack:995},
                {seat:3, name:'thangkorean', stack:1278},
                {seat:4, name:'samianovais', stack:794},
                {seat:5, name:'gioccolive', stack:1311},
            ];
            for (var p of players) {
                if (state.seats[p.seat]) {
                    state.seats[p.seat].player = {name: p.name};
                    state.seats[p.seat].stack = p.stack;
                    state.seats[p.seat].status = 'OCCUPIED';
                    if (p.cards) state.seats[p.seat].holeCards = p.cards;
                    if (p.seat !== 0) {
                        state.seats[p.seat].inHand = true;
                        state.seats[p.seat].folded = false;
                    }
                }
            }
            state.tableName = 'Haidea IV';
            state.button = 5;
            render();
        }
    ` });
    await new Promise(r => setTimeout(r, 500));
    const { data } = await Page.captureScreenshot({ format: 'png' });
    fs.writeFileSync('_lab_parity_test.png', Buffer.from(data, 'base64'));
    await client.close();
    process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
"""],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent)
        )

        subprocess.run(["taskkill", "/f", "/im", "chrome.exe"],
                       capture_output=True, timeout=5)

        lab_path = str(Path(__file__).resolve().parent.parent / "_lab_parity_test.png")
        if os.path.exists(lab_path):
            img = cv2.imread(lab_path)
            os.remove(lab_path)
            return extract_table_normalized(img, target_w, target_h)

    except Exception as e:
        print(f"  Lab capture failed: {e}")

    return None, None


def find_best_ps_reference():
    """Find the best PS reference screenshot (one with board cards visible)."""
    ss_dir = Path(os.path.expanduser("~")) / "OneDrive" / "Pictures" / "Screenshots"
    best = None
    best_time = 0
    for f in ss_dir.iterdir():
        if f.name.startswith("Screenshot 2026-04-04") and f.suffix == ".png":
            if f.stat().st_mtime > best_time:
                # Check if it has a table
                img = cv2.imread(str(f))
                if img is not None and find_table_region(img) is not None:
                    best = f
                    best_time = f.stat().st_mtime
    return best


def run_test(ps_reference=None):
    """Run the visual parity test."""
    print("=" * 60)
    print("  VISUAL PARITY TEST — Lab vs PokerStars")
    print("=" * 60)
    print()

    # 1. Load PS reference
    if ps_reference:
        ps_path = Path(ps_reference)
    else:
        ps_path = find_best_ps_reference()

    if not ps_path or not ps_path.exists():
        print("  ERROR: No PS reference screenshot found")
        return False

    print(f"  PS reference: {ps_path.name}")
    ps_img = cv2.imread(str(ps_path))
    ps_table, ps_region = extract_table_normalized(ps_img)
    if ps_table is None:
        print("  ERROR: Cannot find table in PS screenshot")
        return False

    # 2. Capture lab client
    print("  Capturing lab client...")
    lab_table, lab_region = capture_lab_screenshot()
    if lab_table is None:
        print("  ERROR: Cannot capture lab client")
        return False

    # 3. Compare regions
    print()
    print(f"  {'Region':<20} {'SSIM':>6}  {'Status'}")
    print(f"  {'-'*20} {'-'*6}  {'-'*10}")

    all_scores = {}
    all_pass = True
    thresholds = {
        "table_felt": 0.60,       # felt color/texture
        "board_cards": 0.50,      # card rendering
        "hero_cards": 0.40,       # card rendering with overlap
        "action_buttons": 0.40,   # button layout
        "seat_panels": 0.35,      # player panels
        "pot_text": 0.40,         # pot display
    }

    for region_name in REGIONS:
        score, crop_ps, crop_lab = compute_region_ssim(ps_table, lab_table, region_name)
        threshold = thresholds.get(region_name, 0.50)
        passed = score >= threshold
        status = "PASS" if passed else "FAIL"
        marker = "  " if passed else " <"
        print(f"  {region_name:<20} {score:>5.3f}  {status}{marker}")
        all_scores[region_name] = score
        if not passed:
            all_pass = False

        # Save comparison crops for inspection
        out_dir = Path(__file__).resolve().parent.parent / "vision" / "parity_debug"
        out_dir.mkdir(exist_ok=True)
        cv2.imwrite(str(out_dir / f"{region_name}_ps.png"), crop_ps)
        cv2.imwrite(str(out_dir / f"{region_name}_lab.png"), crop_lab)

    # Overall SSIM
    ps_gray = cv2.cvtColor(ps_table, cv2.COLOR_BGR2GRAY)
    lab_gray = cv2.cvtColor(lab_table, cv2.COLOR_BGR2GRAY)
    overall = ssim(ps_gray, lab_gray)
    all_scores["overall"] = overall

    print()
    print(f"  {'OVERALL':<20} {overall:>5.3f}  {'PASS' if overall >= 0.50 else 'FAIL'}")
    print()

    # Save full comparison
    out_dir = Path(__file__).resolve().parent.parent / "vision" / "parity_debug"
    cv2.imwrite(str(out_dir / "full_ps.png"), ps_table)
    cv2.imwrite(str(out_dir / "full_lab.png"), lab_table)

    # Save scores
    with open(str(out_dir / "scores.json"), "w") as f:
        json.dump(all_scores, f, indent=2)

    target = 0.95
    print(f"  Current: {overall:.3f}  Target: {target:.3f}  Gap: {target - overall:.3f}")
    print(f"  Debug images: vision/parity_debug/")
    print()

    if overall >= target:
        print("  RESULT: PASS — Lab is visually identical to PS")
    else:
        # Show worst region
        worst = min(all_scores.items(), key=lambda x: x[1] if x[0] != "overall" else 999)
        print(f"  RESULT: FAIL — Worst region: {worst[0]} ({worst[1]:.3f})")
        print(f"  Focus improvement on: {worst[0]}")

    print("=" * 60)
    return overall >= target


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", help="Path to PS reference screenshot")
    args = parser.parse_args()
    success = run_test(args.reference)
    sys.exit(0 if success else 1)
