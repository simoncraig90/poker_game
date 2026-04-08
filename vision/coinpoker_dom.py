"""
CoinPoker DOM reader.

Connects to CoinPoker (or replica) via Chrome DevTools Protocol,
polls the rendered DOM at ~5 Hz, and produces universal state dicts
matching the format used by unibet_ws.UnibetWSReader.

Universal state dict (target output):
{
    "hero_cards": ["Ah", "Kh"],
    "board_cards": ["Td", "9c", "2s"],
    "hand_id": "h_2379",
    "facing_bet": True,
    "call_amount": 12,    # cents
    "pot": 50,            # cents
    "num_opponents": 5,
    "position": "BTN",
    "hero_stack": 1000,   # cents
    "phase": "FLOP",
    "bets": [0, 12, 0, 0, 4, 0],
    "players": [...],
    "hero_seat": 1,
    "hero_turn": True,
}
"""

import json
import threading
import time
import subprocess
import urllib.request


def _http_json(url):
    with urllib.request.urlopen(url, timeout=2) as resp:
        return json.loads(resp.read())


class CoinPokerReader:
    """Polls CoinPoker (or replica) DOM and produces universal state dicts."""

    # JavaScript that runs in the page to extract current game state.
    # Designed to work against both the real CoinPoker iframe AND the replica.
    EXTRACT_JS = r"""
    (function() {
        function txt(sel) {
            const el = document.querySelector(sel);
            return el ? (el.innerText || el.textContent || '').trim() : '';
        }
        function elExists(sel) { return !!document.querySelector(sel); }
        function num(s) {
            if (!s) return 0;
            const m = String(s).replace(/[^0-9.]/g, '');
            return m ? parseFloat(m) : 0;
        }
        function cents(s) { return Math.round(num(s) * 100); }

        // Hero cards — replica uses #hero-cards .card, real CoinPoker uses similar
        const heroCards = Array.from(document.querySelectorAll('#hero-cards .card'))
            .map(c => (c.innerText || c.textContent || '').trim())
            .filter(c => c.length >= 2);

        // Board cards
        const boardCards = Array.from(document.querySelectorAll('#board .card'))
            .map(c => (c.innerText || c.textContent || '').trim())
            .filter(c => c.length >= 2);

        // Hero stack
        const heroStack = cents(txt('#hero-stack'));

        // Pot
        const pot = cents(txt('#pot'));

        // Phase (from verification panel in replica, or detected from board count)
        let phase = txt('#phase');
        if (!phase || phase === '—') {
            if (boardCards.length === 0) phase = heroCards.length >= 2 ? 'PREFLOP' : 'WAITING';
            else if (boardCards.length === 3) phase = 'FLOP';
            else if (boardCards.length === 4) phase = 'TURN';
            else if (boardCards.length === 5) phase = 'RIVER';
            else phase = 'WAITING';
        }

        // Hand id
        const handIdRaw = txt('#hand-num');
        const handId = handIdRaw && handIdRaw !== '—' ? 'h' + handIdRaw : null;

        // Facing bet — detect by whether CALL button shows an amount or just CHECK
        const callBtn = document.getElementById('call-btn');
        let facingBet = false;
        let callAmount = 0;
        if (callBtn && callBtn.offsetParent !== null) {
            const ct = (callBtn.innerText || '').trim().toUpperCase();
            if (ct.startsWith('CALL')) {
                facingBet = true;
                callAmount = cents(ct.replace(/[^0-9.]/g, ''));
            }
        }

        // hero_turn: action bar visible
        const actionBar = document.getElementById('action-bar');
        const heroTurn = actionBar && actionBar.classList.contains('visible');

        // Position — replica doesn't track this, default MP
        // Real CoinPoker would have it from seat layout
        const position = txt('#position') || 'MP';

        // Players & bets — replica is single-table, just hero
        // Real CoinPoker we'd query .seat divs
        const players = ['Hero'];
        const bets = [0];
        const heroSeat = 0;

        return JSON.stringify({
            hero_cards: heroCards,
            board_cards: boardCards,
            hand_id: handId,
            facing_bet: facingBet,
            call_amount: callAmount,
            pot: pot,
            num_opponents: 5,  // default, real impl scans seats
            position: position,
            hero_stack: heroStack,
            phase: phase,
            bets: bets,
            players: players,
            hero_seat: heroSeat,
            hero_turn: heroTurn,
        });
    })()
    """

    def __init__(self, port=9222, target_match=None, poll_hz=5):
        """
        Args:
            port: CDP debug port
            target_match: substring to match in target URL (e.g. "coinpoker-replica" or "coinpoker.ai")
            poll_hz: how often to poll the DOM
        """
        self.port = port
        self.target_match = target_match or "coinpoker"
        self.poll_interval = 1.0 / poll_hz
        self.target_id = None
        self.websocket_url = None
        self._callback = None
        self._last_state = None
        self._running = False
        self._thread = None

    def on_state_change(self, callback):
        """Register callback fired when state changes. callback(state_dict)"""
        self._callback = callback

    def _find_target(self):
        """Find the target tab via CDP HTTP."""
        try:
            tabs = _http_json(f"http://localhost:{self.port}/json")
            for t in tabs:
                url = t.get('url', '')
                if self.target_match in url and t.get('type') in ('page', 'iframe'):
                    return t
        except Exception as e:
            print(f"[CoinPokerReader] _find_target error: {e}")
        return None

    def _eval_via_node(self, expression):
        """Evaluate JS in the target via a node helper subprocess.
        Using subprocess avoids needing a Python CDP library."""
        target = self._find_target()
        if not target:
            return None
        node_script = (
            f"const CDP=require('chrome-remote-interface');"
            f"(async()=>{{"
            f"  const c = await CDP({{target:'{target['id']}',port:{self.port}}});"
            f"  await c.Runtime.enable();"
            f"  const r = await c.Runtime.evaluate({{returnByValue:true,expression:{json.dumps(expression)}}});"
            f"  console.log(r.result.value || '');"
            f"  await c.close();"
            f"}})().catch(e => process.stderr.write('ERR:'+e.message));"
        )
        try:
            p = subprocess.run(
                ["node", "-e", node_script],
                capture_output=True, text=True, timeout=3,
                cwd=r"C:\poker-research"
            )
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except Exception as e:
            print(f"[CoinPokerReader] eval error: {e}")
        return None

    def get_state(self):
        """Query DOM once, return state dict."""
        result = self._eval_via_node(self.EXTRACT_JS)
        if not result:
            return None
        try:
            return json.loads(result)
        except Exception as e:
            print(f"[CoinPokerReader] parse error: {e}; result={result[:200]}")
            return None

    def _poll_loop(self):
        while self._running:
            try:
                state = self.get_state()
                if state is not None:
                    # Detect change vs last state
                    sig = (
                        tuple(state.get('hero_cards', [])),
                        tuple(state.get('board_cards', [])),
                        state.get('facing_bet'),
                        state.get('call_amount'),
                        state.get('pot'),
                        state.get('hand_id'),
                        state.get('hero_turn'),
                    )
                    if sig != self._last_state:
                        self._last_state = sig
                        if self._callback:
                            try:
                                self._callback(state)
                            except Exception as e:
                                print(f"[CoinPokerReader] callback error: {e}")
            except Exception as e:
                print(f"[CoinPokerReader] poll error: {e}")
            time.sleep(self.poll_interval)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"[CoinPokerReader] Started polling (target={self.target_match}, port={self.port})")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)


if __name__ == "__main__":
    # Quick test against the replica
    reader = CoinPokerReader(port=9222, target_match="coinpoker-replica")
    state = reader.get_state()
    print(json.dumps(state, indent=2) if state else "no state — is the replica open?")
