"""
Test the bot's click mechanism against a local HTML test table.

The test table has FOLD/CALL/RAISE buttons that log every click.
This verifies the click reaches the target without using real money.
"""

import subprocess
import time
import json
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

# Use Chrome at port 9222
PORT = 9222

def get_chrome_log():
    """Read the test page click log via CDP."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}/json") as resp:
            tabs = json.loads(resp.read())
        test_tab = next((t for t in tabs if 'test-table' in t.get('url', '')), None)
        return test_tab
    except Exception as e:
        return None


def test_click_via_cdp(button_name):
    """Use the cdp-auto-player.js node script to send a click."""
    cdp_script = os.path.join(ROOT, "scripts", "cdp-auto-player.js")
    proc = subprocess.Popen(
        ["node", cdp_script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )

    # Wait for READY
    ready = proc.stdout.readline().strip()
    if ready != "READY":
        print(f"  CDP not ready: {ready}")
        proc.terminate()
        return False

    # Send click command
    proc.stdin.write(f"{button_name}\n")
    proc.stdin.flush()
    result = proc.stdout.readline().strip()
    print(f"  CDP response: {result}")

    proc.terminate()
    return "CLICKED" in result


def main():
    print("Testing click mechanism against test-table.html...")
    print()

    # Check the test page is open
    tab = get_chrome_log()
    if not tab:
        print("ERROR: test-table.html not found in Chrome tabs")
        print("Open it with: node -e \"const CDP=require('chrome-remote-interface');CDP.New({port:9222,url:'file:///C:/poker-research/client/test-table.html'})\"")
        return

    print(f"Found test tab: {tab['id'][:8]}...")
    print(f"URL: {tab['url']}")
    print()

    # The cdp-auto-player.js looks for 'relaxg.com' iframe — won't find it on test page
    # We need a different approach: use CDP directly on the test tab
    print("NOTE: cdp-auto-player.js targets the relaxg iframe, won't work on test page.")
    print("This test script needs a direct CDP connection to the test tab.")


if __name__ == "__main__":
    main()
