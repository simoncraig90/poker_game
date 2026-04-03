"""
Tile poker table windows side-by-side on screen.

Finds all Poker Lab / localhost:9100 browser windows and snaps them
into an equal-width grid, same height, no overlap. Like PS multi-table tiling.

Usage:
  python scripts/tile-tables.py              # tile all found windows
  python scripts/tile-tables.py --open 4     # open 4 tables then tile
"""

import argparse
import os
import subprocess
import sys
import time

try:
    import win32gui
    import win32con
    from ctypes import windll, Structure, c_long, byref
except ImportError:
    print("ERROR: pip install pywin32")
    sys.exit(1)


class RECT(Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]


def get_work_area():
    """Get primary monitor work area (excludes taskbar)."""
    r = RECT()
    windll.user32.SystemParametersInfoW(0x0030, 0, byref(r), 0)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def find_poker_windows():
    """Find all Poker Lab browser windows, deduplicated."""
    candidates = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "Poker Lab" in title or "localhost:9100" in title:
                rect = win32gui.GetWindowRect(hwnd)
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                if w > 300 and h > 400:
                    candidates.append((hwnd, rect, title, w * h))
    win32gui.EnumWindows(cb, None)

    # Deduplicate overlapping windows
    candidates.sort(key=lambda x: -x[3])
    kept = []
    for hwnd, rect, title, area in candidates:
        overlaps = False
        for kh, kr, kt, ka in kept:
            ox = max(0, min(rect[2], kr[2]) - max(rect[0], kr[0]))
            oy = max(0, min(rect[3], kr[3]) - max(rect[1], kr[1]))
            if ox * oy > min(area, ka) * 0.3:
                overlaps = True
                break
        if not overlaps:
            kept.append((hwnd, rect, title, area))

    return [(hwnd, title) for hwnd, rect, title, area in kept]


def open_tables(n, port=9100):
    """Open N table windows using alternating browsers for true separate windows."""
    # Use different browsers to guarantee separate windows
    # Chrome absorbs all --new-window into one when already running
    browsers = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    # Find available browsers
    available = [b for b in browsers if os.path.exists(b)]
    if not available:
        available = ["chrome"]  # fallback to PATH

    for i in range(1, n + 1):
        url = f"http://localhost:{port}/?table={i}"
        browser = available[i % len(available)] if len(available) > 1 else available[0]
        user_dir = os.path.join(os.environ.get("TEMP", "/tmp"), f"poker-table-{i}")
        try:
            subprocess.Popen(
                [browser, f"--user-data-dir={user_dir}", f"--app={url}",
                 "--window-size=500,900", f"--window-position={i * 200},{50}",
                 "--no-first-run", "--no-default-browser-check"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            bname = os.path.basename(os.path.dirname(os.path.dirname(browser)))
            print(f"  Opened table {i}: {url} ({bname})")
        except Exception as e:
            subprocess.Popen(["start", url], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  Opened table {i}: {url} (default browser)")
        time.sleep(2)


def tile_windows(windows):
    """Snap windows side by side, equal width, full height."""
    x0, y0, screen_w, screen_h = get_work_area()
    n = len(windows)
    tile_w = screen_w // n

    for i, (hwnd, title) in enumerate(windows):
        x = x0 + i * tile_w
        y = y0
        clean = title.encode("ascii", "ignore").decode()[:40]

        # Restore if maximized
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.05)
        # Set position and size
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, tile_w, screen_h, 0)
        print(f"  Table {i + 1}: {clean} -> ({x},{y}) {tile_w}x{screen_h}")


def main():
    parser = argparse.ArgumentParser(description="Tile poker table windows")
    parser.add_argument("--open", type=int, default=0,
                        help="Open N new table windows before tiling")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()

    print("Poker Table Tiler")
    print("=" * 40)

    if args.open > 0:
        print(f"\nOpening {args.open} tables...")
        open_tables(args.open, args.port)
        print(f"Waiting for windows to load...")
        time.sleep(5)

    windows = find_poker_windows()
    print(f"\nFound {len(windows)} poker windows")

    if len(windows) == 0:
        print("No windows found. Open tables first:")
        print(f"  python scripts/tile-tables.py --open 2")
        return

    if len(windows) == 1:
        print("Only 1 window. Opening table 2 in Edge...")
        # Use Edge to guarantee a separate window from Chrome
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if not os.path.exists(edge):
            edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        url = f"http://localhost:{args.port}/?table=2"
        user_dir = os.path.join(os.environ.get("TEMP", "/tmp"), "poker-table-edge")
        try:
            subprocess.Popen(
                [edge, f"--user-data-dir={user_dir}", f"--app={url}",
                 "--no-first-run", "--no-default-browser-check"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            subprocess.Popen(["start", url], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  Opened: {url}")
        time.sleep(5)
        windows = find_poker_windows()
        print(f"  Now {len(windows)} windows")

    print(f"\nTiling {len(windows)} windows side by side...")
    tile_windows(windows)
    print("\nDone!")


if __name__ == "__main__":
    main()
