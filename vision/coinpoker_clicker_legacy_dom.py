"""
CoinPoker click adapter.

Uses CDP Runtime.evaluate to call JS .click() on standard DOM elements.
This works perfectly because CoinPoker's UI is React DOM (not canvas like Unibet).
No cursor movement, no focus stealing, no Emscripten focus issues.
"""

import json
import subprocess
import urllib.request


def _http_json(url):
    with urllib.request.urlopen(url, timeout=2) as resp:
        return json.loads(resp.read())


class CoinPokerClicker:
    """Click adapter for CoinPoker via CDP JS .click()."""

    # JavaScript selectors for each action.
    # Designed to work against both real CoinPoker and the replica.
    SELECTORS = {
        "FOLD": "#fold-btn",
        "CHECK": "#call-btn",     # CALL button doubles as CHECK when no bet
        "CALL": "#call-btn",
        "RAISE": "#raise-btn",
        "BET": "#raise-btn",      # RAISE button is the BET button when first to act
    }

    SLIDER_SELECTORS = {
        25:  '.preset[data-pct="25"]',
        50:  '.preset[data-pct="50"]',
        80:  '.preset[data-pct="80"]',
        100: '.preset[data-pct="100"]',  # NEVER use this except deliberately
    }

    BET_INPUT_SELECTOR = "#bet-input"
    BET_PLUS_SELECTOR = "#plus"
    BET_MINUS_SELECTOR = "#minus"

    def __init__(self, port=9222, target_match=None):
        self.port = port
        self.target_match = target_match or "coinpoker"

    def _find_target(self):
        try:
            tabs = _http_json(f"http://localhost:{self.port}/json")
            for t in tabs:
                url = t.get('url', '')
                if self.target_match in url and t.get('type') in ('page', 'iframe'):
                    return t
        except Exception as e:
            print(f"[CoinPokerClicker] target lookup error: {e}")
        return None

    def _eval(self, expression):
        target = self._find_target()
        if not target:
            return None
        node_script = (
            f"const CDP=require('chrome-remote-interface');"
            f"(async()=>{{"
            f"  const c = await CDP({{target:'{target['id']}',port:{self.port}}});"
            f"  await c.Runtime.enable();"
            f"  const r = await c.Runtime.evaluate({{returnByValue:true,expression:{json.dumps(expression)}}});"
            f"  console.log(r.result.value !== undefined ? r.result.value : '');"
            f"  await c.close();"
            f"}})().catch(e => process.stderr.write('ERR:'+e.message));"
        )
        try:
            p = subprocess.run(
                ["node", "-e", node_script],
                capture_output=True, text=True, timeout=3,
                cwd=r"C:\poker-research"
            )
            if p.returncode == 0:
                return p.stdout.strip()
            else:
                print(f"[CoinPokerClicker] eval error: {p.stderr}")
        except Exception as e:
            print(f"[CoinPokerClicker] subprocess error: {e}")
        return None

    def click(self, action, amount=None):
        """
        Click an action button.

        Args:
            action: 'FOLD', 'CHECK', 'CALL', 'RAISE', 'BET'
            amount: optional bet amount in EUR (e.g. 0.10) — sets the input first

        Returns:
            True if click registered (verified by JS click() returning normally)
        """
        action = action.upper()

        # Map action to selector
        if "FOLD" in action:
            sel = self.SELECTORS["FOLD"]
            cmd = "FOLD"
        elif "CHECK" in action:
            sel = self.SELECTORS["CHECK"]
            cmd = "CHECK"
        elif "CALL" in action:
            sel = self.SELECTORS["CALL"]
            cmd = "CALL"
        elif "RAISE" in action:
            sel = self.SELECTORS["RAISE"]
            cmd = "RAISE"
        elif "BET" in action:
            sel = self.SELECTORS["BET"]
            cmd = "BET"
        else:
            print(f"[CoinPokerClicker] Unknown action: {action}")
            return False

        # If RAISE/BET with amount, set input field first
        # Use direct value set + dispatchEvent for input
        # (NEVER use slider preset 100 = All-in)
        if cmd in ("RAISE", "BET") and amount is not None and amount > 0:
            set_input_js = (
                f"(function() {{"
                f"  const inp = document.querySelector({json.dumps(self.BET_INPUT_SELECTOR)});"
                f"  if (!inp) return false;"
                f"  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
                f"  setter.call(inp, {json.dumps(f'{amount:.2f}')});"
                f"  inp.dispatchEvent(new Event('input', {{bubbles:true}}));"
                f"  inp.dispatchEvent(new Event('change', {{bubbles:true}}));"
                f"  return true;"
                f"}})()"
            )
            r = self._eval(set_input_js)
            if r != "true":
                print(f"[CoinPokerClicker] Failed to set bet amount: {r}")

        # Click the action button
        click_js = (
            f"(function() {{"
            f"  const el = document.querySelector({json.dumps(sel)});"
            f"  if (!el) return 'NOT_FOUND';"
            f"  if (el.offsetParent === null) return 'NOT_VISIBLE';"
            f"  el.click();"
            f"  return 'OK';"
            f"}})()"
        )
        result = self._eval(click_js)
        success = (result == "OK")
        print(f"[CoinPokerClicker] {cmd} -> {result}")
        return success

    def click_slider_preset(self, pct):
        """Click a slider preset button (25, 50, 80, 100). NEVER 100 unless deliberate."""
        if pct == 100:
            print("[CoinPokerClicker] REFUSING to click All-in preset")
            return False
        sel = self.SLIDER_SELECTORS.get(pct)
        if not sel:
            return False
        js = (
            f"(function() {{"
            f"  const el = document.querySelector({json.dumps(sel)});"
            f"  if (!el) return 'NOT_FOUND';"
            f"  el.click();"
            f"  return 'OK';"
            f"}})()"
        )
        return self._eval(js) == "OK"

    def click_quick_join(self, table_id=None):
        """Click a Quick Join button. If table_id given, click specific table."""
        if table_id:
            sel = f'.quick-join[data-table-id="{table_id}"]'
        else:
            sel = '.quick-join'
        js = (
            f"(function() {{"
            f"  const el = document.querySelector({json.dumps(sel)});"
            f"  if (!el) return 'NOT_FOUND';"
            f"  el.click();"
            f"  return 'OK';"
            f"}})()"
        )
        return self._eval(js) == "OK"


if __name__ == "__main__":
    # Quick smoke test against replica
    c = CoinPokerClicker(port=9222, target_match="coinpoker-replica")
    print("Joining table...")
    print(c.click_quick_join())
    import time; time.sleep(1)
    print("Folding...")
    print(c.click("FOLD"))
