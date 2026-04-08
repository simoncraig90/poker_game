"""
Launch CoinPoker in headless-equivalent mode (window hidden).

CoinPoker doesn't honor --headless flag, but we can hide the window
after launch via Win32 ShowWindow(SW_HIDE). The Electron renderer
keeps running normally, CDP stays accessible, DOM stays alive.

Usage:
    python scripts/coinpoker_headless.py [--port 9223] [--profile name]

This launches CoinPoker, hides its window, and prints the debug port.
The auto-player can then connect on that port.

Multi-instance:
    python scripts/coinpoker_headless.py --port 9223 --profile acc1 &
    python scripts/coinpoker_headless.py --port 9224 --profile acc2 &
    ...
"""

import os
import sys
import time
import argparse
import subprocess
import ctypes
from ctypes import wintypes


COINPOKER_EXE = r"C:\Program Files\CoinPoker\CoinPoker.exe"


def kill_existing(port=None):
    """Kill all CoinPoker processes (or only ones using this port)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "CoinPoker.exe"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass
    time.sleep(1)


def launch_coinpoker(port, profile_dir=None):
    """Launch CoinPoker.exe with debug port and optional user-data-dir."""
    args = [COINPOKER_EXE, f"--remote-debugging-port={port}"]
    if profile_dir:
        args.append(f"--user-data-dir={profile_dir}")

    # Use DETACHED_PROCESS so it doesn't die when this script exits
    DETACHED = 0x00000008
    p = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED,
    )
    return p.pid


def find_coinpoker_window(timeout=15):
    """Wait for CoinPoker window to appear, return its hwnd."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    deadline = time.time() + timeout

    while time.time() < deadline:
        found = [None]

        def cb(hwnd, lparam):
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value
            if title == "CoinPoker":
                found[0] = hwnd
                return False
            return True

        ctypes.windll.user32.EnumWindows(WNDENUMPROC(cb), 0)
        if found[0]:
            return found[0]
        time.sleep(0.5)
    return None


def hide_window(hwnd):
    """Hide a window via SW_HIDE. The process keeps running normally."""
    SW_HIDE = 0
    ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
    return ctypes.windll.user32.IsWindowVisible(hwnd) == 0


def verify_cdp(port):
    """Check that CDP is accessible on the port."""
    import urllib.request
    import json
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/json", timeout=1) as resp:
                targets = json.loads(resp.read())
                if targets:
                    return targets
        except Exception:
            pass
        time.sleep(0.5)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9223,
                        help="Chrome debug port")
    parser.add_argument("--profile", type=str, default=None,
                        help="User-data-dir name (for multi-account isolation)")
    parser.add_argument("--no-hide", action="store_true",
                        help="Skip window hiding (debug mode)")
    parser.add_argument("--keep-running", action="store_true",
                        help="Keep this script running to monitor CoinPoker")
    args = parser.parse_args()

    print(f"[headless] Launching CoinPoker on port {args.port}")
    if args.profile:
        profile_dir = os.path.join(os.environ.get("LOCALAPPDATA", "."), "coinpoker-profiles", args.profile)
        os.makedirs(profile_dir, exist_ok=True)
        print(f"[headless] Profile: {profile_dir}")
    else:
        profile_dir = None

    pid = launch_coinpoker(args.port, profile_dir)
    print(f"[headless] PID: {pid}")

    print("[headless] Waiting for window...")
    hwnd = find_coinpoker_window(timeout=20)
    if not hwnd:
        print("[headless] FAILED: window did not appear within 20 seconds")
        return 1
    print(f"[headless] Window found: hwnd={hwnd}")

    if args.no_hide:
        print("[headless] Skipping hide (--no-hide)")
    else:
        print("[headless] Hiding window...")
        if hide_window(hwnd):
            print("[headless] Window hidden ✓")
        else:
            print("[headless] WARN: hide may have failed")

    print("[headless] Verifying CDP...")
    targets = verify_cdp(args.port)
    if not targets:
        print("[headless] FAILED: CDP not responding")
        return 1
    print(f"[headless] CDP OK, {len(targets)} targets")
    for t in targets:
        print(f"  [{t.get('type')}] {(t.get('url','') or '')[:80]}")

    print()
    print(f"[headless] CoinPoker is running headless on port {args.port}")
    print(f"[headless] To play: python vision/coinpoker_player.py --target=live (port={args.port})")
    print(f"[headless] To restore window: python -c \"import ctypes; ctypes.windll.user32.ShowWindow({hwnd}, 5)\"")

    if args.keep_running:
        print("[headless] Monitoring (Ctrl+C to exit)...")
        try:
            while True:
                time.sleep(5)
                # Verify CDP still alive
                targets = verify_cdp(args.port)
                if not targets:
                    print("[headless] WARN: CDP no longer responding!")
        except KeyboardInterrupt:
            print("\n[headless] Exiting (CoinPoker remains running)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
