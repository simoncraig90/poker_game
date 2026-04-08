"""
Launch CoinPoker, navigate to Practice Games, Quick-Join the top table, Buy-In.

End state: Unity table window open, hero seated at PR-NL 50/100, frames flowing
to C:\\Users\\Simon\\coinpoker_frames.jsonl. Both lobby + table windows positioned
at (0, 0) per the "windows on left of monitor" feedback.

Usage:
    python tools/coinpoker_open_practice.py
    python tools/coinpoker_open_practice.py --buyin 4000   # min buy-in instead of default

Lessons baked in (see notes at the bottom):
- Must launch with both --remote-debugging-port AND --remote-allow-origins=*
  (otherwise Python websocket-client gets 403 from CDP)
- The lobby chrome (tab nav) lives in lobby.html, NOT the cloudfront iframe
- The <a class="tab-button"> elements have CSS pointer-events:none; React onClick
  on them is also a no-op when invoked directly (defaultPrevented:true).
  The reliable way to click them is a real OS-level mouse click via Win32
  mouse_event at the element's getBoundingClientRect center.
- innerWidth in this Electron build maps 1:1 to OS pixels (no DPR transform
  needed for click coordinates) even though devicePixelRatio=1.5.
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

import websocket  # websocket-client


COINPOKER_EXE = r"C:\Program Files\CoinPoker\CoinPoker.exe"
FRAMES_PATH = r"C:\Users\Simon\coinpoker_frames.jsonl"
CDP_PORT = 9223


# ──────────────────────────── Win32 helpers ────────────────────────────

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


def find_windows_named(title, deadline_s=15, on_screen_only=True):
    """Return list of (hwnd, rect) for visible top-level windows with this title.
    on_screen_only: filter out windows positioned offscreen (Electron uses
    negative coords like (-21333,-21333) for hidden splash/loader windows
    even though IsWindowVisible reports True)."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        found = []

        def cb(h, lp):
            if not ctypes.windll.user32.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(h, buf, 256)
            if buf.value == title:
                r = RECT()
                ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
                if on_screen_only and (r.left < -10000 or r.top < -10000):
                    return True  # skip offscreen
                if on_screen_only and (r.right - r.left) < 100:
                    return True  # skip tiny / unrendered
                found.append((h, (r.left, r.top, r.right, r.bottom)))
            return True

        ctypes.windll.user32.EnumWindows(WNDENUMPROC(cb), 0)
        if found:
            return found
        time.sleep(0.3)
    return []


def move_window(hwnd, x, y, w, h):
    ctypes.windll.user32.MoveWindow(hwnd, x, y, w, h, True)


def click_screen(hwnd, sx, sy):
    """Real OS mouse click at screen (sx, sy), focusing hwnd first."""
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    ctypes.windll.user32.SetCursorPos(sx, sy)
    time.sleep(0.08)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.04)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


def client_origin(hwnd):
    pt = POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


# ──────────────────────────── CDP helpers ────────────────────────────

class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({
            "id": self._id, "method": method, "params": params or {},
        }))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._id:
                return m

    def evaluate(self, expression, await_promise=False):
        res = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        return res.get("result", {}).get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def get_targets():
    with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=3) as r:
        return json.loads(r.read())


def find_lobby_target(deadline_s=20):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            for t in get_targets():
                if "lobby.html" in t.get("url", ""):
                    return t
        except Exception:
            pass
        time.sleep(0.5)
    return None


# ──────────────────────────── Main flow ────────────────────────────

class LaunchError(RuntimeError):
    """Raised when an open-practice attempt fails partway through.
    Caught by the retry loop in main() so the whole flow can restart from
    a fresh CoinPoker process."""


def kill_existing():
    subprocess.run(
        ["taskkill", "/F", "/IM", "CoinPoker.exe"],
        capture_output=True, text=True, timeout=10,
    )
    # Cold-launch retry needs the OS to fully release the process before the
    # next launch — 2s was sometimes not enough on a slow box and the new
    # CoinPoker would attach to the dying instance's CDP port and immediately
    # die. 5s is the empirical floor.
    time.sleep(5)


def launch():
    DETACHED = 0x00000008
    p = subprocess.Popen(
        [COINPOKER_EXE,
         f"--remote-debugging-port={CDP_PORT}",
         "--remote-allow-origins=*"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=DETACHED,
    )
    return p.pid


def wait_for_login(cdp, deadline_s=30):
    """Wait for the cloudfront iframe to appear (means login completed)."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            for t in get_targets():
                if "cloudfront" in t.get("url", ""):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def click_dom_element_via_win32(cdp, page_hwnd, selector_js, label):
    """
    Use CDP to compute the bounding rect of a DOM element, then click it via
    Win32 mouse at the corresponding screen coordinate.

    selector_js: a JS expression that returns the element (e.g.
        "Array.from(document.querySelectorAll('a')).find(x=>x.getAttribute('href')==='/home/free-practice')"
    )
    """
    expr = f"""
    (function(){{
      const el = {selector_js};
      if (!el) return null;
      // Scroll horizontally into view in case it's off-screen in a horizontal-scroll container
      try {{ el.scrollIntoView({{block:'center', inline:'center'}}); }} catch(e) {{}}
      const r = el.getBoundingClientRect();
      return JSON.stringify({{x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)}});
    }})()
    """
    raw = cdp.evaluate(expr)
    if not raw:
        raise RuntimeError(f"could not locate element: {label}")
    info = json.loads(raw)
    cx, cy = client_origin(page_hwnd)
    sx, sy = cx + info["x"], cy + info["y"]
    click_screen(page_hwnd, sx, sy)
    print(f"[lobby] clicked {label} at ({sx},{sy})")


def try_open_practice(attempt, total_attempts, kill_first):
    """One end-to-end attempt at opening a practice table.

    Raises LaunchError on any partial-progress failure so the outer retry
    loop in main() can kill CoinPoker and try again from scratch.
    Returns 0 on success.
    """
    if kill_first:
        print(f"[1/8] Killing existing CoinPoker processes... (attempt {attempt}/{total_attempts})")
        kill_existing()

    print(f"[2/8] Launching CoinPoker on CDP port {CDP_PORT}...")
    pid = launch()
    print(f"      PID: {pid}")

    print("[3/8] Waiting for lobby window...")
    # Cold launches with auto-update can exceed 20s. Give the first attempt
    # extra slack; later attempts (already-warm Chromium cache) get the
    # original deadline.
    lobby_deadline = 45 if attempt == 1 else 25
    wins = find_windows_named("CoinPoker", deadline_s=lobby_deadline)
    if not wins:
        raise LaunchError("lobby window did not appear")
    # First "CoinPoker" window is the lobby (only one at this point)
    lobby_hwnd = wins[0][0]
    print(f"      lobby hwnd={lobby_hwnd}")
    move_window(lobby_hwnd, 0, 0, 1280, 1040)

    print("[4/8] Waiting for CDP + lobby login...")
    target = find_lobby_target(deadline_s=30)
    if not target:
        raise LaunchError("lobby.html target not visible on CDP")
    cdp = CDP(target["webSocketDebuggerUrl"])
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    # Cold launches: auto-login + reCAPTCHA-avoidance path can take >30s
    login_deadline = 60 if attempt == 1 else 30
    if not wait_for_login(cdp, deadline_s=login_deadline):
        print(f"WARN: cloudfront iframe did not appear within {login_deadline}s — continuing anyway")

    # Give the React tab nav a moment to mount
    time.sleep(2)

    print("[5/8] Clicking Practice Games tab...")
    # Wait until the tab nav is mounted (the <a href="/home/free-practice">)
    deadline = time.time() + 20
    while time.time() < deadline:
        raw = cdp.evaluate(
            "Array.from(document.querySelectorAll('a')).filter(x=>x.getAttribute('href')==='/home/free-practice').length"
        )
        if raw and int(raw) > 0:
            break
        time.sleep(0.3)
    # The tab list is in a horizontal-scroll-container; Practice Games is far
    # right and may be off-screen. element.scrollIntoView doesn't reliably
    # scroll the parent — manually scroll the container all the way right.
    cdp.evaluate(
        "(function(){"
        "  const c = document.querySelector('.horizontal-scroll-container');"
        "  if (c) c.scrollLeft = c.scrollWidth;"
        "})()"
    )
    time.sleep(0.4)
    click_dom_element_via_win32(
        cdp, lobby_hwnd,
        "Array.from(document.querySelectorAll('a')).find(x=>x.getAttribute('href')==='/home/free-practice')",
        "Practice Games tab",
    )

    print("[6/8] Waiting for table list, clicking first Quick Join...")
    # Quick Join buttons appear once the practice lobby content renders.
    # Retry the tab click once if the list never shows up — the first click
    # sometimes lands during a layout transition.
    deadline = time.time() + 15
    saw_buttons = False
    while time.time() < deadline:
        raw = cdp.evaluate("document.querySelectorAll('button.quick-join-button').length")
        if raw and int(raw) > 0:
            saw_buttons = True
            break
        time.sleep(0.3)
    if not saw_buttons:
        print("      table list not visible, retrying Practice Games click...")
        time.sleep(1)
        click_dom_element_via_win32(
            cdp, lobby_hwnd,
            "Array.from(document.querySelectorAll('a')).find(x=>x.getAttribute('href')==='/home/free-practice')",
            "Practice Games tab (retry)",
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            raw = cdp.evaluate("document.querySelectorAll('button.quick-join-button').length")
            if raw and int(raw) > 0:
                saw_buttons = True
                break
            time.sleep(0.3)
    if not saw_buttons:
        # Save a screenshot for debugging
        try:
            res = cdp.send("Page.captureScreenshot", {"format": "png"})
            import base64
            with open(r"C:\Users\Simon\coinpoker_lobby_debug.png", "wb") as f:
                f.write(base64.b64decode(res["result"]["data"]))
            print("      saved debug screenshot to C:\\Users\\Simon\\coinpoker_lobby_debug.png")
        except Exception as e:
            print(f"      screenshot failed: {e}")
        # Don't fall through and click a phantom button — bail to retry loop.
        raise LaunchError("quick-join buttons never rendered")
    try:
        click_dom_element_via_win32(
            cdp, lobby_hwnd,
            "document.querySelector('button.quick-join-button')",
            "Quick Join (first table)",
        )
    except RuntimeError as e:
        raise LaunchError(f"quick-join click failed: {e}")
    time.sleep(2)

    print("[7/8] Confirming Buy-In...")
    # Wait for buy-in modal to render
    deadline = time.time() + 10
    saw_buyin = False
    while time.time() < deadline:
        raw = cdp.evaluate(
            "Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Buy-In').length"
        )
        if raw and int(raw) > 0:
            saw_buyin = True
            break
        time.sleep(0.3)
    if not saw_buyin:
        raise LaunchError("buy-in modal never rendered")
    try:
        click_dom_element_via_win32(
            cdp, lobby_hwnd,
            "Array.from(document.querySelectorAll('button')).find(b=>(b.innerText||'').trim()==='Buy-In')",
            "Buy-In",
        )
    except RuntimeError as e:
        raise LaunchError(f"buy-in click failed: {e}")
    cdp.close()

    print("[8/8] Waiting for Unity table window + frame flow...")
    # The Unity table opens as a SECOND top-level "CoinPoker" window
    deadline = time.time() + 25
    table_hwnd = None
    while time.time() < deadline:
        wins = find_windows_named("CoinPoker", deadline_s=1)
        # The lobby_hwnd is already known; the new one is the table
        new_wins = [w for w in wins if w[0] != lobby_hwnd]
        if new_wins:
            table_hwnd = new_wins[0][0]
            break
        time.sleep(0.3)
    if not table_hwnd:
        # No table window means the buy-in didn't actually seat hero. Retry —
        # this is the failure mode that used to leave the script "succeeding"
        # but no frames flowing.
        raise LaunchError("table window not detected within 25s after buy-in")
    print(f"      table hwnd={table_hwnd}, moving to (0,0)")
    move_window(table_hwnd, 0, 0, 900, 700)

    # Verify frames file is fresh — the only signal that the patched DLL is
    # actually receiving events for this table. Without this, the previous
    # version of the script could "succeed" while frames were stale from a
    # prior session.
    if not os.path.exists(FRAMES_PATH):
        raise LaunchError(f"frames file does not exist at {FRAMES_PATH}")
    # Wait up to 15s for a fresh write — Unity startup + initial deal can lag
    fresh_deadline = time.time() + 15
    age = None
    while time.time() < fresh_deadline:
        age = time.time() - os.path.getmtime(FRAMES_PATH)
        if age < 10:
            break
        time.sleep(0.5)
    print(f"      frames.jsonl last modified {age:.0f}s ago")
    if age is None or age > 30:
        raise LaunchError(f"frames file is stale ({age:.0f}s) — table not sending events")

    print()
    print("=" * 60)
    print("READY: practice table open, frames flowing, hero seated")
    print("=" * 60)
    print("Next:")
    print("  python tools/phase2_gauntlet.py --target-rounds 50 --mode hero-turn --action FOLD")
    print("  (or --mode periodic --ignore-staleness for non-hero round-trip test)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-kill", action="store_true",
                    help="Don't kill an existing CoinPoker before launching")
    ap.add_argument("--keep-default-buyin", action="store_true",
                    help="Use whatever buy-in amount is preselected in the modal")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="How many cold-launch attempts before giving up (default 3)")
    args = ap.parse_args()

    total = max(1, args.max_attempts)
    last_err = None
    for attempt in range(1, total + 1):
        # First attempt honours --no-kill; retries always kill so we restart
        # from a clean process state.
        kill_first = (attempt > 1) or (not args.no_kill)
        try:
            return try_open_practice(attempt, total, kill_first)
        except LaunchError as e:
            last_err = e
            print(f"\n[!] attempt {attempt}/{total} failed: {e}")
            if attempt < total:
                print(f"[!] retrying from a fresh CoinPoker process...\n")
            continue
        except KeyboardInterrupt:
            print("\n[!] interrupted by user")
            return 130
        except Exception as e:
            # Unexpected — don't burn retries on programmer errors
            print(f"\nFATAL unexpected error: {type(e).__name__}: {e}")
            raise

    print(f"\nFATAL: open-practice failed after {total} attempts (last: {last_err})")
    return 2


if __name__ == "__main__":
    sys.exit(main())


# ──────────────────────────── NOTES ────────────────────────────
#
# Why this script exists (2026-04-08 session 12):
#   The user wanted "open the app and find a table" baked into the workflow so
#   future sessions can start the gauntlet without manual lobby clicking. This
#   used to stall (session 11) because hero went sit-out before the gauntlet
#   could fire.
#
# Tricky bits discovered while writing this:
#   1. Without --remote-allow-origins=*, websocket-client connecting to CDP
#      gets 403 Forbidden because the lobby's localhost:9223 origin is rejected
#      by Chromium's strict origin check.
#   2. The lobby tab nav lives in the lobby.html parent page (Electron app
#      shell), NOT in the cloudfront iframe. The iframe is the user's "Home"
#      dashboard view; section navigation is parent-rendered.
#   3. <a class="tab-button"> has CSS pointer-events:none and its React onClick
#      handler does e.preventDefault() but does not actually navigate when
#      called via JS (defaultPrevented:true, location unchanged). Real OS-level
#      mouse clicks via Win32 mouse_event DO work — that's the route we use.
#   4. innerWidth in this Electron build = OS-pixel width (1280 == 1280),
#      even though devicePixelRatio reports 1.5. Click coordinates do NOT need
#      a DPR transform.
#   5. The Unity table opens as a separate top-level window, also titled
#      "CoinPoker". After Buy-In, look for a NEW hwnd != the lobby hwnd.
