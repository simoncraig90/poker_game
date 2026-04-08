"""
Unibet game state reader via WebSocket interception.

Reads the Relax Gaming XMPP WebSocket messages through Chrome DevTools Protocol.
Extracts hero cards, board cards, player actions, pot, and betting state.

100% accurate — reads the actual game data, no image processing.
"""

import json
import re
import threading
import time


class UnibetWSReader:
    """Reads Unibet poker game state from WebSocket messages via CDP."""

    def __init__(self, cdp_port=9222):
        self.cdp_port = cdp_port
        self.hero_cards = []      # ['6s', 'Td'] format
        self.board_cards = []     # ['Th', '4d', '9s']
        self.hand_id = None
        self.players = []
        self.hero_seat = -1
        self.dealer_seat = -1
        self.pot = 0
        self.bets = []            # per-seat bet amounts
        self.stacks = []          # per-seat stack amounts
        self.hero_turn = False
        self.facing_bet = False
        self.call_amount = 0
        self.phase = "WAITING"    # WAITING, PREFLOP, FLOP, TURN, RIVER
        self.position = "MP"     # BTN, SB, BB, UTG, MP, CO
        self._running = False
        self._thread = None
        self._callbacks = []      # list of functions to call on state change
        self._notify_timer = None
        self._notify_lock = threading.Lock()

    def on_state_change(self, callback):
        """Register a callback for state changes."""
        self._callbacks.append(callback)

    def _notify(self):
        """Debounced notify — waits 300ms after last message before calling callbacks.
        This ensures all WS data for a game state has arrived before the advisor acts."""
        with self._notify_lock:
            if self._notify_timer:
                self._notify_timer.cancel()
            self._notify_timer = threading.Timer(0.3, self._do_notify)
            self._notify_timer.daemon = True
            self._notify_timer.start()

    def _do_notify(self):
        state = self.get_state()
        for cb in self._callbacks:
            try:
                cb(state)
            except Exception:
                pass

    def get_state(self):
        """Get current game state as dict."""
        return {
            "hero_cards": self.hero_cards[:],
            "board_cards": self.board_cards[:],
            "hand_id": self.hand_id,
            "players": self.players[:],
            "hero_seat": self.hero_seat,
            "pot": self.pot,
            "hero_turn": self.hero_turn,
            "facing_bet": self.facing_bet,
            "call_amount": self.call_amount,
            "phase": self.phase,
            "num_opponents": max(1, sum(1 for p in self.players if p) - 1),
            "position": self.position,
            "hero_stack": self.stacks[self.hero_seat] if 0 <= self.hero_seat < len(self.stacks) else 0,
            "bets": self.bets[:],
        }

    def _parse_cards(self, card_str):
        """Parse card string like '9d4s' or 'td4dth7s' into list of cards."""
        cards = []
        i = 0
        while i < len(card_str) - 1:
            rank = card_str[i].upper()
            suit = card_str[i + 1].lower()
            if rank == '1' and i + 2 < len(card_str) and card_str[i + 1] == '0':
                rank = 'T'
                suit = card_str[i + 2].lower()
                i += 3
            else:
                i += 2
            # Normalize rank
            if rank == '1':
                rank = 'A'  # shouldn't happen but safety
            cards.append(f"{rank}{suit}")
        return cards

    def _parse_message(self, raw):
        """Parse an XMPP WebSocket message for game state."""
        if 'payLoad' not in raw:
            return

        # Decode HTML entities
        decoded = raw.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')

        # Extract JSON body
        body_match = re.search(r'<body>(.*?)</body>', decoded)
        if not body_match:
            return

        try:
            msg = json.loads(body_match.group(1))
        except json.JSONDecodeError:
            return

        pl = msg.get("payLoad")
        if not pl:
            return

        # Hand state update with hero cards
        if isinstance(pl.get("p"), list) and len(pl["p"]) >= 4:
            p = pl["p"]
            card_str = p[3] if isinstance(p[3], str) else ""

            if len(card_str) >= 4:
                new_hand_id = pl.get("hid")

                # New hand?
                if new_hand_id != self.hand_id:
                    self.hand_id = new_hand_id
                    self.board_cards = []
                    self.phase = "PREFLOP"
                    self.facing_bet = False
                    self.call_amount = 0
                    self.bets = []
                    self._position_locked = False

                # Parse hero cards (first 4 chars)
                self.hero_cards = self._parse_cards(card_str[:4])

                # Parse board cards if present (after position 4, starts with H for preflop)
                if len(card_str) > 4 and card_str[4] != 'H':
                    # The card string after hero cards might include board
                    pass  # Board comes separately

        # Game state array (player list, bets, actions)
        if isinstance(pl.get("c"), list) and len(pl["c"]) >= 2:
            c = pl["c"]

            # Player names (pipe-separated, empty = no player)
            if isinstance(c[0], str):
                self.players = c[0].split("|")
                # Find hero seat
                for i, name in enumerate(self.players):
                    if 'skurj' in name.lower() or 'uni41' in name.lower():
                        self.hero_seat = i

            # Board cards — process BEFORE bets so phase is correct for facing detection
            if len(c) > 7 and isinstance(c[7], str) and len(c[7]) >= 4:
                board_str = c[7]
                self.board_cards = self._parse_cards(board_str)
                n = len(self.board_cards)
                old_phase = self.phase
                if n == 0:
                    self.phase = "PREFLOP"
                elif n == 3:
                    self.phase = "FLOP"
                elif n == 4:
                    self.phase = "TURN"
                elif n >= 5:
                    self.phase = "RIVER"
                # Reset facing_bet on new street ONLY if no bets in this message
                if self.phase != old_phase:
                    bets_in_msg = c[3] if isinstance(c[3], list) else []
                    if not bets_in_msg or max(bets_in_msg) == 0:
                        self.facing_bet = False
                        self.call_amount = 0

            # Seat states: [0=empty, 1=active, 3=folded, 4=posted, etc]
            if isinstance(c[1], list):
                seat_states = c[1]
                if 0 <= self.hero_seat < len(seat_states):
                    hero_state = seat_states[self.hero_seat]
                    self.hero_turn = hero_state in [1, 4]
                    # If hero folded, clear cards and notify
                    if hero_state == 3 and self.hero_cards:
                        self.hero_cards = []
                        self.hero_turn = False
                        self.facing_bet = False
                        self._notify()

            # Bet amounts per seat
            if isinstance(c[3], list):
                self.bets = c[3]
                if 0 <= self.hero_seat < len(self.bets):
                    hero_bet = self.bets[self.hero_seat]
                    max_bet = max(self.bets)

                    # Detect facing a bet
                    if self.phase == "PREFLOP":
                        # Preflop: find the BB amount (second smallest blind)
                        non_zero = sorted([b for b in self.bets if b > 0])
                        bb_amt = non_zero[1] if len(non_zero) >= 2 else (non_zero[0] if non_zero else 4)
                        # Facing a raise = someone bet MORE than the BB
                        has_raise = any(b > bb_amt for b in self.bets)
                        new_facing = has_raise and max_bet > hero_bet
                    else:
                        # Postflop: any bet > 0 from another player
                        new_facing = max_bet > hero_bet and max_bet > 0

                    if new_facing:
                        self.facing_bet = True
                    elif hero_bet >= max_bet:
                        self.facing_bet = False

                    self.call_amount = max(0, max_bet - hero_bet)
                    if max_bet > 0:
                        print(f"[WS] bets={self.bets} hero={hero_bet} max={max_bet} facing={self.facing_bet} phase={self.phase}")

            # Detect position from blinds — lock once per hand
            if self.phase == "PREFLOP" and isinstance(c[3], list) and not getattr(self, '_position_locked', False):
                blind_seats = [(i, b) for i, b in enumerate(c[3]) if b > 0]
                blind_seats.sort(key=lambda x: x[1])
                if len(blind_seats) >= 2:
                    sb_seat = blind_seats[0][0]
                    bb_seat = blind_seats[1][0]
                    num_seats = len(c[3])
                    btn_seat = (sb_seat - 1) % num_seats
                    dist = (self.hero_seat - btn_seat) % num_seats
                    pos_map = {0: "BTN", 1: "SB", 2: "BB", 3: "UTG", 4: "MP", 5: "CO"}
                    self.position = pos_map.get(dist, "MP")
                    self._position_locked = True

            # (Board cards processed above, before bets)

            # Stacks per seat
            if isinstance(c[2], list):
                self.stacks = c[2]

            # Pot from action array element
            if len(c) > 4 and isinstance(c[4], list):
                # c[4] contains pot info [[pot_amount, type], ...]
                self.pot = sum(p[0] for p in c[4] if isinstance(p, list) and len(p) >= 1)
            if self.pot == 0:
                # Fallback: sum of all stacks subtracted from initial
                self.pot = sum(self.bets)

            self._notify()

    def start(self):
        """Start listening to WebSocket messages."""
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _listen_loop(self):
        """Background thread: connect to CDP via Node bridge and listen for WS messages."""
        import subprocess as sp
        import os as _os

        bridge_script = _os.path.join(_os.path.dirname(__file__), '..', 'scripts', 'cdp-ws-bridge.js')

        try:
            proc = sp.Popen(
                ['node', bridge_script, str(self.cdp_port)],
                stdout=sp.PIPE, stderr=sp.PIPE, text=True, bufsize=1
            )
            print("[WS] CDP bridge started")

            while self._running:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        err = proc.stderr.read()
                        if err:
                            print(f"[WS] Bridge died: {err[:200]}")
                        break
                    continue

                line = line.strip()
                if line.startswith('WS:'):
                    self._parse_message(line[3:])

            proc.terminate()
        except Exception as e:
            print(f"[WS] Failed: {e}")


if __name__ == "__main__":
    reader = UnibetWSReader()

    def on_change(state):
        hero = state["hero_cards"]
        board = state["board_cards"]
        turn = state["hero_turn"]
        facing = state["facing_bet"]
        call = state["call_amount"]
        phase = state["phase"]
        if hero:
            print(f"[{phase}] Hero: {' '.join(hero)} | Board: {' '.join(board)} | "
                  f"Turn: {turn} | Facing: {facing} (call {call})")

    reader.on_state_change(on_change)
    reader.start()

    print("Reading Unibet game state. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        reader.stop()
        print("\nStopped.")
