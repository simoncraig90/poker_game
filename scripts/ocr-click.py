"""
OCR-based screen click. Finds text and clicks its center.

Two capture sources:
  --source mss   (default): screen grab via mss. Captures whatever is
                 actually on screen. Fragile — fails if the target
                 window isn't on top.
  --source cdp:  CDP screenshot of a Chrome tab via the debug port.
                 Robust to focus state. Uses Win32 to map tab-relative
                 click coords to screen coords for the actual click.

Usage:
  python scripts/ocr-click.py "CASH GAME"
  python scripts/ocr-click.py "PLAY" --source cdp --region right
  python scripts/ocr-click.py "PLAY" --source cdp --tab-match unibet
  python scripts/ocr-click.py "4 NL" --source cdp --tab-match unibet --region left
  python scripts/ocr-click.py "Buy In" --source cdp --tab-match unibet
  python scripts/ocr-click.py "X" --x1 100 --y1 200 --x2 800 --y2 600
  python scripts/ocr-click.py "Foo" --no-click          (find but don't click)
  python scripts/ocr-click.py "Foo" --case-sensitive
  python scripts/ocr-click.py "PLAY" --min-conf 0.6
"""
import argparse
import sys
import time
import ctypes
import numpy as np
from PIL import Image, ImageEnhance
import mss
import easyocr


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("target", help="Text to find on screen")
    p.add_argument("--region", default="top",
                   choices=("top", "left", "right", "center", "bottom", "full"),
                   help="Pre-defined screen region to scan (default: top — "
                        "for backwards compat with the original CASH GAME usage)")
    p.add_argument("--x1", type=int, help="Custom region: left edge")
    p.add_argument("--y1", type=int, help="Custom region: top edge")
    p.add_argument("--x2", type=int, help="Custom region: right edge")
    p.add_argument("--y2", type=int, help="Custom region: bottom edge")
    p.add_argument("--no-click", action="store_true",
                   help="Just find and report, don't click")
    p.add_argument("--case-sensitive", action="store_true",
                   help="Match case-sensitively (default: case-insensitive)")
    p.add_argument("--min-conf", type=float, default=0.5,
                   help="Minimum OCR confidence (default 0.5)")
    p.add_argument("--source", default="mss", choices=("mss", "cdp"),
                   help="Capture source: 'mss' (screen grab, default) or "
                        "'cdp' (Chrome DevTools tab screenshot, robust "
                        "to focus state)")
    p.add_argument("--cdp-port", type=int, default=9222,
                   help="Chrome DevTools port (default 9222)")
    p.add_argument("--tab-match", default="unibet",
                   help="Substring to match the Chrome tab URL (default 'unibet')")
    return p.parse_args()


def grab_via_cdp(port: int, tab_match: str):
    """
    Take a screenshot via Chrome DevTools Protocol of the matching tab,
    by shelling out to scripts/cdp-tab-screenshot.js (a Node helper
    that uses chrome-remote-interface — needed because Python's raw
    websocket connection gets rejected by Chrome's origin check).

    Returns (PIL.Image, screen_origin_x, screen_origin_y) where the
    origin is the screen-coordinate of the top-left of the captured
    image (so click coords can be mapped back to screen).
    """
    import subprocess
    import os
    import tempfile

    helper = os.path.join(os.path.dirname(__file__), "cdp-tab-screenshot.js")
    if not os.path.exists(helper):
        raise RuntimeError(f"helper not found: {helper}")

    fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="cdp_grab_")
    os.close(fd)
    try:
        result = subprocess.run(
            ["node", helper, tmp_path, tab_match, str(port)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"cdp-tab-screenshot.js failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("cdp-tab-screenshot.js produced no output")
        img = Image.open(tmp_path).convert("RGB").copy()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    # Map tab-content coords to screen coords for the click
    origin_x, origin_y = _get_chrome_render_origin()
    return img, origin_x, origin_y


def _get_chrome_render_origin():
    """
    Return (x, y) screen-coordinate of the top-left of Chrome's
    render widget — i.e. where the captured tab content begins on screen.
    """
    user32 = ctypes.windll.user32
    # Find a top-level Chrome window
    hwnd = user32.FindWindowW("Chrome_WidgetWin_1", None)
    if not hwnd:
        return (0, 0)
    # Find the render widget child
    render = user32.FindWindowExW(hwnd, None, "Chrome_RenderWidgetHostHWND", None)
    target = render if render else hwnd
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(target, ctypes.byref(pt))
    return (pt.x, pt.y)


# Need wintypes for the render-origin helper
from ctypes import wintypes  # noqa: E402


def get_region(img, args):
    """Return (left, top, right, bottom, width_for_offset, top_for_offset)."""
    if args.x1 is not None and args.y1 is not None and args.x2 is not None and args.y2 is not None:
        return args.x1, args.y1, args.x2, args.y2

    W, H = img.width, img.height
    presets = {
        "top":    (0, 150, W // 2, 400),         # nav tab area (original)
        "left":   (0, 100, W // 2, H),           # left half (stake list, sidebar)
        "right":  (W // 2, 100, W, H),           # right half (PLAY button area)
        "center": (W // 4, H // 4, 3 * W // 4, 3 * H // 4),  # central dialog area
        "bottom": (0, H // 2, W, H),             # bottom half (action buttons, footer)
        "full":   (0, 0, W, H),                  # whole screen
    }
    return presets[args.region]


def main():
    args = parse_args()
    target = args.target

    # Screenshot via the chosen source
    origin_x = 0
    origin_y = 0
    if args.source == "cdp":
        try:
            img, origin_x, origin_y = grab_via_cdp(args.cdp_port, args.tab_match)
        except Exception as e:
            print(f"CDP grab failed: {e}")
            print("Falling back to mss screen grab")
            args.source = "mss"
    if args.source == "mss":
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    left, top, right, bottom = get_region(img, args)
    crop = img.crop((left, top, right, bottom))
    # Upsample 2x + boost contrast for OCR accuracy on small text
    crop2 = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    crop2 = ImageEnhance.Contrast(crop2).enhance(2.0)

    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    results = reader.readtext(np.array(crop2), paragraph=False)

    target_cmp = target if args.case_sensitive else target.upper()

    clicked = False
    for (bbox, text, conf) in results:
        text_cmp = text if args.case_sensitive else text.upper()
        if target_cmp in text_cmp and conf > args.min_conf:
            # Bounding box is in 2x-upsampled coords; convert back to
            # image coords, add the crop offset, and (for CDP source)
            # add the screen-origin of Chrome's render widget so the
            # final coords are absolute screen positions for the click.
            cx = int((bbox[0][0] + bbox[2][0]) / 2) / 2 + left + origin_x
            cy = int((bbox[0][1] + bbox[2][1]) / 2) / 2 + top + origin_y
            print(f"Found '{text}' at ({cx:.0f},{cy:.0f}) conf={conf:.2f}")

            if args.no_click:
                clicked = True
                break

            # Click
            ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
            time.sleep(0.2)
            ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)  # down
            ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)  # up
            print(f"Clicked '{target}' at ({cx:.0f},{cy:.0f})")
            clicked = True
            break

    if not clicked:
        print(f"'{target}' not found on screen")
        sys.exit(1)


if __name__ == "__main__":
    main()
