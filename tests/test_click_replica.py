"""
Test the bot's click mechanism against the unibet-replica.html test page.
Verifies clicks land on the right buttons via the verification panel.
"""

import os
import sys
import time
import json
import urllib.request
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

import ctypes
from ctypes import wintypes


def get_replica_state():
    """Query the replica's verification stats via CDP."""
    # Use node to query
    js = """
    (async () => {
      const CDP = require('chrome-remote-interface');
      const targets = await CDP.List({port: 9222});
      const t = targets.find(x => x.url.includes('unibet-replica'));
      if (!t) return console.log(JSON.stringify({error:'replica not open'}));
      const c = await CDP({target: t.id, port: 9222});
      await c.Runtime.enable();
      const r = await c.Runtime.evaluate({
        returnByValue: true,
        expression: 'JSON.stringify({total:stats.total, success:stats.success, miss:stats.miss, lastAction:document.getElementById("last-action").textContent})'
      });
      console.log(r.result.value);
      await c.close();
    })().catch(e => console.log(JSON.stringify({error:e.message})));
    """
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, cwd=ROOT, timeout=10)
    try:
        return json.loads(p.stdout.strip())
    except Exception:
        return None


def find_chrome_with_replica():
    """Find Chrome window that has the replica tab active."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    found = [None]
    def cb(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            if 'Relax Poker' in buf.value or 'Unibet' in buf.value:
                found[0] = hwnd
                return False
        return True
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0]


def click_at(sx, sy, hwnd):
    """Same click code as auto_player.py."""
    prev_hwnd = ctypes.windll.user32.GetForegroundWindow()
    saved = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(saved))

    # Force foreground
    fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(prev_hwnd, None)
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
    time.sleep(0.15)

    ctypes.windll.user32.SetCursorPos(sx, sy)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.05)

    ctypes.windll.user32.SetCursorPos(saved.x, saved.y)
    ctypes.windll.user32.SetForegroundWindow(prev_hwnd)


def get_render_widget_pos(hwnd):
    """Get render widget client size and screen origin."""
    render = ctypes.windll.user32.FindWindowExW(hwnd, None, "Chrome_RenderWidgetHostHWND", None)
    if not render:
        render = hwnd
    rect = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(render, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(render, ctypes.byref(pt))
    return pt.x, pt.y, rect.right, rect.bottom


def main():
    # Try DPI aware
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    print("Click Test on unibet-replica.html")
    print("=" * 50)

    # Get baseline
    before = get_replica_state()
    if not before:
        print("ERROR: cannot read replica state. Is it open?")
        return
    if before.get('error'):
        print(f"ERROR: {before['error']}")
        return
    print(f"Before: {before}")

    hwnd = find_chrome_with_replica()
    if not hwnd:
        print("ERROR: Chrome not found")
        return

    ox, oy, cw, ch = get_render_widget_pos(hwnd)
    print(f"Render widget: origin ({ox},{oy}) size {cw}x{ch}")

    # Calibrated replica positions (CSS)
    targets = {
        "FOLD":   (0.370, 0.938),
        "CALL":   (0.498, 0.938),
        "RAISE":  (0.628, 0.938),
    }

    results = []
    for name, (px, py) in targets.items():
        sx = ox + int(cw * px)
        sy = oy + int(ch * py)
        print(f"\nClicking {name} at screen ({sx},{sy}) — pct ({px},{py})")
        click_at(sx, sy, hwnd)
        time.sleep(0.5)
        after = get_replica_state()
        if after and after.get('lastAction') == name:
            print(f"  OK — {name} registered (total={after['total']})")
            results.append((name, True))
        else:
            print(f"  MISS — last action: {after.get('lastAction') if after else 'unknown'}")
            results.append((name, False))

    print("\n" + "=" * 50)
    passed = sum(1 for _, ok in results if ok)
    print(f"Result: {passed}/{len(results)} clicks landed correctly")
    final = get_replica_state()
    if final:
        print(f"Final: total={final['total']} success={final['success']} miss={final['miss']}")


if __name__ == "__main__":
    main()
