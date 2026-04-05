r"""
Automated CSS parameter optimization for visual parity.

Uses Bayesian-like search to find optimal CSS values that maximize SSIM
between the lab client and PS reference screenshots.

Parameters optimized:
- Table felt size (width%, height%)
- Felt oval border-radius
- Board area Y position
- Action button Y position
- Hero card position (bottom%, left%)
- Hero card size (width, height)
- Felt info text opacity and position

Usage:
    python vision/optimize_parity.py --reference path/to/ps_screenshot.png
    python vision/optimize_parity.py --iterations 200
"""

import cv2
import numpy as np
import os
import sys
import json
import subprocess
import time
import tempfile
from pathlib import Path
from skimage.metrics import structural_similarity as ssim

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_visual_parity import extract_table_normalized, REGIONS, compute_region_ssim

# CSS parameters to optimize: (name, min, max, current_best)
PARAMS = {
    "table_w_pct": (80, 95, 88),          # table width as vw%
    "table_h_ratio": (0.45, 0.60, 0.52),  # height = width * ratio
    "felt_w_pct": (100, 115, 108),         # ::before width%
    "felt_h_pct": (85, 100, 92),           # ::before height%
    "felt_radius_x": (40, 55, 48),         # border-radius x%
    "felt_radius_y": (35, 48, 42),         # border-radius y%
    "board_top_pct": (35, 50, 42),         # board-area top%
    "btn_top_pct": (38, 55, 43),           # action-bar top%
    "hero_bottom_pct": (-10, 5, -5),       # hero-cards bottom%
    "hero_left_pct": (15, 35, 28),         # hero-cards left%
    "hero_card_w": (48, 72, 58),           # hero card width px
    "hero_card_h": (68, 102, 82),          # hero card height px
    "board_card_w": (44, 64, 54),          # board card width px
    "board_card_h": (62, 90, 78),          # board card height px
}

PROJECT_DIR = str(Path(__file__).resolve().parent.parent)
INDEX_PATH = os.path.join(PROJECT_DIR, "client", "index.html")
TABLE_JS_PATH = os.path.join(PROJECT_DIR, "client", "table.js")


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# Save originals
ORIG_INDEX = read_file(INDEX_PATH)
ORIG_TABLE_JS = read_file(TABLE_JS_PATH)


def apply_params(params):
    """Apply CSS parameter values to index.html and table.js."""
    html = ORIG_INDEX
    js = ORIG_TABLE_JS

    # Table dimensions
    tw = params["table_w_pct"]
    tr = params["table_h_ratio"]
    html = html.replace(
        "width: min(88vw, 780px);",
        f"width: min({tw}vw, 780px);"
    ).replace(
        "height: min(calc(88vw * 0.52), 420px);",
        f"height: min(calc({tw}vw * {tr}), 420px);"
    )

    # Felt ::before
    fw = params["felt_w_pct"]
    fh = params["felt_h_pct"]
    frx = params["felt_radius_x"]
    fry = params["felt_radius_y"]
    html = html.replace(
        "width: 108%; height: 92%;",
        f"width: {fw}%; height: {fh}%;"
    ).replace(
        "border-radius: 48% / 42%;",
        f"border-radius: {frx}% / {fry}%;"
    )

    # Board position
    bt = params["board_top_pct"]
    html = html.replace(
        "left: 50%; top: 42%;",
        f"left: 50%; top: {bt}%;"
    )

    # Button position
    bnt = params["btn_top_pct"]
    html = html.replace(
        "left: 50%; top: 43%;",
        f"left: 50%; top: {bnt}%;"
    )

    # Hero cards position
    hb = params["hero_bottom_pct"]
    hl = params["hero_left_pct"]
    html = html.replace(
        "bottom: -5%; left: 28%;",
        f"bottom: {hb}%; left: {hl}%;"
    )

    # Hero card size in JS
    hcw = int(params["hero_card_w"])
    hch = int(params["hero_card_h"])
    js = js.replace(
        "width:58px;height:82px",
        f"width:{hcw}px;height:{hch}px"
    )

    # Board card size in JS
    bcw = int(params["board_card_w"])
    bch = int(params["board_card_h"])
    js = js.replace(
        "width:54px;height:78px",
        f"width:{bcw}px;height:{bch}px"
    )

    write_file(INDEX_PATH, html)
    write_file(TABLE_JS_PATH, js)


def restore_originals():
    write_file(INDEX_PATH, ORIG_INDEX)
    write_file(TABLE_JS_PATH, ORIG_TABLE_JS)


def capture_lab(viewport_w=900, viewport_h=600):
    """Capture lab client screenshot via CDP."""
    if sys.platform == "win32":
        chrome_cmd = f'start "" "C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe" --remote-debugging-port=9222 --headless=new --disable-gpu --window-size={viewport_w},{viewport_h}'
    else:
        chrome_cmd = f'google-chrome-stable --headless=new --no-sandbox --disable-gpu --remote-debugging-port=9222 --window-size={viewport_w},{viewport_h} about:blank &'
    js_code = f"""
const CDP=require('chrome-remote-interface');const {{execSync}}=require('child_process');const fs=require('fs');
(async()=>{{try{{execSync('{chrome_cmd}',{{stdio:"ignore",timeout:5000}})}}catch(e){{}}
await new Promise(r=>setTimeout(r,2000));const c=await CDP({{port:9222}});const{{Page,Emulation,Runtime,Network}}=c;
await Network.enable();await Network.setCacheDisabled({{cacheDisabled:true}});
await Emulation.setDeviceMetricsOverride({{width:{viewport_w},height:{viewport_h},deviceScaleFactor:1,mobile:false}});
await Page.enable();await Page.navigate({{url:"http://localhost:9100"}});await Page.loadEventFired();
await new Promise(r=>setTimeout(r,4000));
await Runtime.evaluate({{expression:`if(ws){{ws.onmessage=null;ws.onclose=null;ws.close()}}var i=setTimeout(function(){{}},0);while(i--)clearTimeout(i)`}});
await new Promise(r=>setTimeout(r,300));
await Runtime.evaluate({{expression:`if(state&&state.hand){{state.hand.board=["2h","Qd","Jd","3c"];state.hand.pot=233;state.hand.actionSeat=0;state.hand.phase="TURN";state.hand.legalActions={{actions:["FOLD","CALL","RAISE"],callAmount:140,minRaise:280,maxRaise:800}};if(state.seats[0])state.seats[0].holeCards=["Ts","As"];state.tableName="Haidea IV";state.button=5;render()}}`}});
await new Promise(r=>setTimeout(r,500));
const{{data}}=await Page.captureScreenshot({{format:"png"}});fs.writeFileSync("_opt_lab.png",Buffer.from(data,"base64"));
await c.close();try{{execSync("taskkill //f //im chrome.exe 2>nul",{{stdio:"ignore"}})}}catch(e){{}}process.exit(0)}})().catch(e=>{{console.error(e);process.exit(1)}});
"""
    try:
        result = subprocess.run(
            ["node", "-e", js_code],
            capture_output=True, text=True, timeout=30,
            cwd=PROJECT_DIR
        )
    except subprocess.TimeoutExpired:
        pass
    finally:
        # Always kill Chrome to prevent RAM leaks
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/f", "/im", "chrome.exe"],
                           capture_output=True, timeout=5)
        else:
            subprocess.run(["pkill", "-9", "chrome"],
                           capture_output=True, timeout=5)
    lab_path = os.path.join(PROJECT_DIR, "_opt_lab.png")
    if os.path.exists(lab_path):
        img = cv2.imread(lab_path)
        os.remove(lab_path)
        return img
    return None


def evaluate(params, ps_reference):
    """Apply params, capture lab, compute SSIM."""
    apply_params(params)
    lab_img = capture_lab()
    if lab_img is None:
        return 0.0, {}

    ps_t, _ = extract_table_normalized(ps_reference)
    lab_t, _ = extract_table_normalized(lab_img)

    if ps_t is None or lab_t is None:
        return 0.0, {}

    ps_g = cv2.cvtColor(ps_t, cv2.COLOR_BGR2GRAY)
    lab_g = cv2.cvtColor(lab_t, cv2.COLOR_BGR2GRAY)
    overall = ssim(ps_g, lab_g)

    region_scores = {}
    for region in REGIONS:
        score, _, _ = compute_region_ssim(ps_t, lab_t, region)
        region_scores[region] = score

    return overall, region_scores


def random_neighbor(params, step=0.15):
    """Generate a random neighbor of the current params."""
    new_params = dict(params)
    # Mutate 2-4 random parameters
    keys = list(PARAMS.keys())
    n_mutate = np.random.randint(2, min(5, len(keys) + 1))
    for key in np.random.choice(keys, n_mutate, replace=False):
        lo, hi, _ = PARAMS[key]
        current = new_params[key]
        range_size = (hi - lo) * step
        delta = np.random.uniform(-range_size, range_size)
        new_params[key] = max(lo, min(hi, current + delta))
    return new_params


def optimize(ps_ref_path, iterations=100):
    """Run optimization loop."""
    ps_img = cv2.imread(ps_ref_path)
    if ps_img is None:
        print(f"Cannot load {ps_ref_path}")
        return

    print("=" * 60)
    print("  CSS PARAMETER OPTIMIZATION")
    print("=" * 60)
    print(f"  Reference: {os.path.basename(ps_ref_path)}")
    print(f"  Iterations: {iterations}")
    print(f"  Parameters: {len(PARAMS)}")
    print()

    # Start from current best
    best_params = {k: v[2] for k, v in PARAMS.items()}
    best_score, best_regions = evaluate(best_params, ps_img)
    print(f"  Initial SSIM: {best_score:.4f}")
    print(f"  Regions: {json.dumps({k: round(v, 3) for k, v in best_regions.items()})}")
    print()

    no_improve = 0
    for i in range(iterations):
        # Generate candidate
        step = max(0.05, 0.20 - i * 0.001)  # shrink step over time
        candidate = random_neighbor(best_params, step)

        score, regions = evaluate(candidate, ps_img)

        if score > best_score:
            improvement = score - best_score
            best_score = score
            best_params = candidate
            best_regions = regions
            no_improve = 0
            print(f"  [{i+1:3d}] IMPROVED: {best_score:.4f} (+{improvement:.4f})")
            # Show which params changed
            for k in PARAMS:
                if abs(candidate[k] - PARAMS[k][2]) > 0.01:
                    print(f"         {k}: {candidate[k]:.2f}")
        else:
            no_improve += 1
            if (i + 1) % 20 == 0:
                print(f"  [{i+1:3d}] no improvement ({no_improve} rounds), best={best_score:.4f}")

        # Early stop if stuck
        if no_improve > 40:
            print(f"  Stopping early after {no_improve} rounds without improvement")
            break

    # Restore best and save
    apply_params(best_params)
    print()
    print("=" * 60)
    print(f"  BEST SSIM: {best_score:.4f}")
    print(f"  Regions: {json.dumps({k: round(v, 3) for k, v in best_regions.items()})}")
    print(f"  Best params:")
    for k, v in sorted(best_params.items()):
        print(f"    {k}: {v:.2f}")
    print("=" * 60)

    # Save results
    results_path = os.path.join(PROJECT_DIR, "vision", "parity_debug", "optimization_results.json")
    with open(results_path, "w") as f:
        json.dump({"score": best_score, "params": best_params, "regions": best_regions}, f, indent=2)
    print(f"  Results saved to {results_path}")

    return best_params, best_score


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default=None)
    parser.add_argument("--iterations", type=int, default=50)
    args = parser.parse_args()

    ref = args.reference
    if not ref:
        # Find latest PS landscape screenshot
        ss_dir = os.path.join(os.path.expanduser("~"), "OneDrive", "Pictures", "Screenshots")
        for f in sorted(os.listdir(ss_dir), reverse=True):
            if f.startswith("Screenshot 2026-04-05 021715"):
                ref = os.path.join(ss_dir, f)
                break
        if not ref:
            for f in sorted(os.listdir(ss_dir), reverse=True):
                if f.startswith("Screenshot 2026-04-04"):
                    ref = os.path.join(ss_dir, f)
                    break

    if not ref:
        print("No reference screenshot found")
        sys.exit(1)

    try:
        optimize(ref, args.iterations)
    except KeyboardInterrupt:
        print("\nInterrupted — restoring originals")
        restore_originals()
    except Exception as e:
        print(f"Error: {e}")
        restore_originals()
        raise
