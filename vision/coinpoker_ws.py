"""
CoinPoker game state reader via WebSocket interception + DOM fallback.

Reads SmartFoxServer 2X (SFS2X) binary WebSocket messages through Chrome
DevTools Protocol, decoding the binary format to extract game events. Falls
back to DOM scraping of the React Native Web UI when binary decoding fails.

Exposes the same interface as UnibetWSReader for drop-in compatibility with
the advisor overlay and bot frameworks.

Protocol: SFS2X binary over WebSocket (ArrayBuffer)
Client: React Native Web (Expo) — DOM-based, not canvas
Events: HOLE_CARDS, DEALER_CARDS, USER_ACTION, USER_TURN, POT_INFO, etc.
"""

import json
import os
import re
import struct
import subprocess as sp
import threading
import time
import zlib


# ---------------------------------------------------------------------------
# SFS2X Binary Protocol Decoder (Python port)
# ---------------------------------------------------------------------------
# SFS2X serializes SFSObject/SFSArray with typed binary encoding.
# Each value is prefixed with a type tag byte.

SFS_NULL = 0x00
SFS_BOOL = 0x01
SFS_BYTE = 0x02
SFS_SHORT = 0x03
SFS_INT = 0x04
SFS_LONG = 0x05
SFS_FLOAT = 0x06
SFS_DOUBLE = 0x07
SFS_UTF_STRING = 0x08
SFS_BOOL_ARRAY = 0x09
SFS_BYTE_ARRAY = 0x0A
SFS_SHORT_ARRAY = 0x0B
SFS_INT_ARRAY = 0x0C
SFS_LONG_ARRAY = 0x0D
SFS_FLOAT_ARRAY = 0x0E
SFS_DOUBLE_ARRAY = 0x0F
SFS_UTF_STRING_ARRAY = 0x10
SFS_ARRAY = 0x11
SFS_OBJECT = 0x12
SFS_CLASS = 0x13
SFS_TEXT = 0x14


class SFSDecodeError(Exception):
    pass


class SFSDecoder:
    """Decodes SFS2X binary-serialized data."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    @property
    def remaining(self):
        return len(self.data) - self.pos

    def _check(self, n):
        if self.pos + n > len(self.data):
            raise SFSDecodeError(f"EOF: need {n} bytes at pos {self.pos}, have {self.remaining}")

    def read_byte(self) -> int:
        self._check(1)
        v = struct.unpack_from('>b', self.data, self.pos)[0]
        self.pos += 1
        return v

    def read_ubyte(self) -> int:
        self._check(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read_short(self) -> int:
        self._check(2)
        v = struct.unpack_from('>h', self.data, self.pos)[0]
        self.pos += 2
        return v

    def read_ushort(self) -> int:
        self._check(2)
        v = struct.unpack_from('>H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def read_int(self) -> int:
        self._check(4)
        v = struct.unpack_from('>i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_long(self) -> int:
        self._check(8)
        v = struct.unpack_from('>q', self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_float(self) -> float:
        self._check(4)
        v = struct.unpack_from('>f', self.data, self.pos)[0]
        self.pos += 4
        return round(v, 4)

    def read_double(self) -> float:
        self._check(8)
        v = struct.unpack_from('>d', self.data, self.pos)[0]
        self.pos += 8
        return round(v, 4)

    def read_utf(self) -> str:
        length = self.read_ushort()
        self._check(length)
        s = self.data[self.pos:self.pos + length].decode('utf-8', errors='replace')
        self.pos += length
        return s

    def read_text(self) -> str:
        length = self.read_int()
        if length < 0:
            raise SFSDecodeError(f"Negative text length: {length}")
        self._check(length)
        s = self.data[self.pos:self.pos + length].decode('utf-8', errors='replace')
        self.pos += length
        return s

    def read_value(self):
        type_tag = self.read_ubyte()

        if type_tag == SFS_NULL:
            return None
        elif type_tag == SFS_BOOL:
            return self.read_byte() != 0
        elif type_tag == SFS_BYTE:
            return self.read_byte()
        elif type_tag == SFS_SHORT:
            return self.read_short()
        elif type_tag == SFS_INT:
            return self.read_int()
        elif type_tag == SFS_LONG:
            return self.read_long()
        elif type_tag == SFS_FLOAT:
            return self.read_float()
        elif type_tag == SFS_DOUBLE:
            return self.read_double()
        elif type_tag == SFS_UTF_STRING:
            return self.read_utf()
        elif type_tag == SFS_TEXT:
            return self.read_text()

        elif type_tag == SFS_BOOL_ARRAY:
            n = self.read_ushort()
            return [self.read_byte() != 0 for _ in range(n)]
        elif type_tag == SFS_BYTE_ARRAY:
            n = self.read_int()
            if n < 0:
                raise SFSDecodeError(f"Negative byte array length: {n}")
            self._check(n)
            arr = list(self.data[self.pos:self.pos + n])
            self.pos += n
            return arr
        elif type_tag == SFS_SHORT_ARRAY:
            n = self.read_ushort()
            return [self.read_short() for _ in range(n)]
        elif type_tag == SFS_INT_ARRAY:
            n = self.read_ushort()
            return [self.read_int() for _ in range(n)]
        elif type_tag == SFS_LONG_ARRAY:
            n = self.read_ushort()
            return [self.read_long() for _ in range(n)]
        elif type_tag == SFS_FLOAT_ARRAY:
            n = self.read_ushort()
            return [self.read_float() for _ in range(n)]
        elif type_tag == SFS_DOUBLE_ARRAY:
            n = self.read_ushort()
            return [self.read_double() for _ in range(n)]
        elif type_tag == SFS_UTF_STRING_ARRAY:
            n = self.read_ushort()
            return [self.read_utf() for _ in range(n)]
        elif type_tag == SFS_ARRAY:
            return self.read_sfs_array()
        elif type_tag == SFS_OBJECT:
            return self.read_sfs_object()
        else:
            raise SFSDecodeError(f"Unknown SFS type 0x{type_tag:02x} at pos {self.pos - 1}")

    def read_sfs_object(self) -> dict:
        count = self.read_ushort()
        obj = {}
        for _ in range(count):
            key = self.read_utf()
            obj[key] = self.read_value()
        return obj

    def read_sfs_array(self) -> list:
        count = self.read_ushort()
        return [self.read_value() for _ in range(count)]


def decode_sfs_frame(raw_bytes: bytes):
    """Decode an SFS2X binary WebSocket frame.

    Returns (event_name, data_dict) or None if not decodable.
    """
    if len(raw_bytes) < 6:
        return None

    try:
        header = raw_bytes[0]
        if header == 0x80:
            # Standard SFS2X frame: header(1) + compressed(1) + length(4) + payload
            compressed = raw_bytes[1] != 0
            payload_len = struct.unpack_from('>i', raw_bytes, 2)[0]
            if payload_len <= 0 or payload_len > len(raw_bytes) - 6:
                return None
            payload = raw_bytes[6:6 + payload_len]
            if compressed:
                try:
                    payload = zlib.decompress(payload)
                except zlib.error:
                    return None
        elif header == SFS_OBJECT:
            # Raw SFSObject without framing header
            payload = raw_bytes
        else:
            # Try treating entire buffer as payload starting with type tag
            payload = raw_bytes

        dec = SFSDecoder(payload)
        type_tag = dec.read_ubyte()
        if type_tag != SFS_OBJECT:
            return None
        obj = dec.read_sfs_object()
        return _extract_event(obj)
    except (SFSDecodeError, Exception):
        return None


def _extract_event(obj: dict):
    """Extract event name and data from decoded SFS2X message."""
    controller = obj.get('c')
    params = obj.get('p', obj)

    # Extension response (controller=1): params contain 'cmd' + 'p'
    if controller == 1 and isinstance(params, dict) and 'cmd' in params:
        return (params['cmd'], params.get('p', params))

    # Direct event style
    for key in ('cmd', 'evt', 'event'):
        if key in params:
            return (params[key], params.get('p', params.get('data', params)))

    return ('_RAW', obj)


# ---------------------------------------------------------------------------
# Card Format Normalization
# ---------------------------------------------------------------------------
# CoinPoker/PokerBaazi card encoding: TBD from live data.
# Common SFS2X poker encodings:
#   - Numeric: rank*4+suit (0-51), or rank<<4|suit
#   - String: "Ah", "Kd", "Ts", "2c"
#   - Array of ints or array of strings
#
# We handle both numeric and string formats.

RANK_MAP = {
    0: '2', 1: '3', 2: '4', 3: '5', 4: '6', 5: '7', 6: '8', 7: '9',
    8: 'T', 9: 'J', 10: 'Q', 11: 'K', 12: 'A',
    # Alternate: 2=2 .. 14=A
    14: 'A', 13: 'K',
}

SUIT_MAP = {
    0: 'c', 1: 'd', 2: 'h', 3: 's',
    # Alt: clubs=0, diamonds=1, hearts=2, spades=3
}

# String rank aliases
RANK_ALIASES = {
    '10': 'T', 't': 'T', 'j': 'J', 'q': 'Q', 'k': 'K', 'a': 'A',
}

VALID_RANKS = set('23456789TJQKA')
VALID_SUITS = set('cdhs')


def normalize_card(card) -> str:
    """Convert various card representations to 'Ah' format.

    Handles:
      - Integer (0-51): rank*4+suit encoding
      - String: "Ah", "ah", "AH", "A♥", "ace_hearts", "14h", etc.
      - Dict: {"rank": "A", "suit": "h"} or {"r": 14, "s": 2}
    """
    if isinstance(card, int):
        # Try rank*4+suit (0-51)
        if 0 <= card <= 51:
            rank_idx = card // 4
            suit_idx = card % 4
            rank = RANK_MAP.get(rank_idx, '?')
            suit = SUIT_MAP.get(suit_idx, '?')
            return f"{rank}{suit}"
        # Try rank<<4|suit
        rank_idx = (card >> 4) & 0xF
        suit_idx = card & 0xF
        rank = RANK_MAP.get(rank_idx, RANK_MAP.get(rank_idx - 2, '?'))
        suit = SUIT_MAP.get(suit_idx, '?')
        return f"{rank}{suit}"

    if isinstance(card, dict):
        r = card.get('rank', card.get('r', card.get('value', '?')))
        s = card.get('suit', card.get('s', '?'))
        if isinstance(r, int):
            r = RANK_MAP.get(r, RANK_MAP.get(r - 2, str(r)))
        if isinstance(s, int):
            s = SUIT_MAP.get(s, '?')
        r = str(r).upper()
        s = str(s).lower()
        r = RANK_ALIASES.get(r.lower(), r)
        return f"{r}{s}"

    if isinstance(card, str):
        card = card.strip()
        # Unicode suit symbols
        card = card.replace('♠', 's').replace('♥', 'h').replace('♦', 'd').replace('♣', 'c')
        # Long names
        card = re.sub(r'_?of_?', '', card, flags=re.I)
        card = re.sub(r'spades?', 's', card, flags=re.I)
        card = re.sub(r'hearts?', 'h', card, flags=re.I)
        card = re.sub(r'diamonds?', 'd', card, flags=re.I)
        card = re.sub(r'clubs?', 'c', card, flags=re.I)
        card = re.sub(r'ace', 'A', card, flags=re.I)
        card = re.sub(r'king', 'K', card, flags=re.I)
        card = re.sub(r'queen', 'Q', card, flags=re.I)
        card = re.sub(r'jack', 'J', card, flags=re.I)
        card = card.replace('_', '').replace(' ', '')

        if len(card) >= 2:
            r = card[:-1].upper()
            s = card[-1].lower()
            r = RANK_ALIASES.get(r.lower(), r)
            if r in VALID_RANKS and s in VALID_SUITS:
                return f"{r}{s}"

    return f"?{card}"


def normalize_cards(cards) -> list:
    """Normalize a list/array of cards."""
    if not cards:
        return []
    if isinstance(cards, str):
        # Could be "AhKd" concatenated or "Ah,Kd" or "Ah Kd"
        if ',' in cards:
            return [normalize_card(c) for c in cards.split(',')]
        # Try 2-char chunks
        if len(cards) >= 2 and len(cards) % 2 == 0:
            result = []
            for i in range(0, len(cards), 2):
                chunk = cards[i:i+2]
                result.append(normalize_card(chunk))
            if all('?' not in c for c in result):
                return result
        return [normalize_card(cards)]
    if isinstance(cards, (list, tuple)):
        return [normalize_card(c) for c in cards]
    return [normalize_card(cards)]


# ---------------------------------------------------------------------------
# Game Events We Track
# ---------------------------------------------------------------------------
GAME_EVENTS = {
    'TABLE_INIT', 'PRE_HAND_START', 'GAME_START', 'GAME_READY',
    'HOLE_CARDS', 'DEALER_CARDS', 'USER_ACTION', 'USER_TURN',
    'POT_INFO', 'WINNER_INFO', 'CUMULATIVE_WINNER_INFO',
    'HAND_STRENGTH', 'PLAYER_INFO', 'SEAT_INFO', 'SEAT', 'TAKE_SEAT',
    'LEAVE_SEAT', 'SIT_OUT', 'USER_BALANCE',
    'SHOW_CARDS_REQUEST', 'REVEAL_CARDS_REQUEST',
    'ADVANCE_PLAYER_ACTION', 'GAME_DYNAMIC_PROPERTIES',
    'TRANSACTION_WINNINGS', 'STRADDLE',
}

# Actions
ACTION_FOLD = 'FOLD'
ACTION_CHECK = 'CHECK'
ACTION_CALL = 'CALL'
ACTION_RAISE = 'RAISE'
ACTION_BET = 'BET'
ACTION_ALLIN = 'ALL_IN'


# ---------------------------------------------------------------------------
# CoinPokerWSReader
# ---------------------------------------------------------------------------
class CoinPokerWSReader:
    """Reads CoinPoker game state from SFS2X WebSocket messages via CDP.

    Same interface as UnibetWSReader for compatibility with advisor/bots.
    """

    def __init__(self, cdp_port=9222, use_dom=False, debug=False):
        self.cdp_port = cdp_port
        self.use_dom = use_dom
        self.debug = debug

        # Game state (same fields as UnibetWSReader)
        self.hero_cards = []       # ['Ah', 'Kd'] format
        self.board_cards = []      # ['Th', '4d', '9s']
        self.hand_id = None
        self.players = []          # list of player name strings per seat
        self.hero_seat = -1
        self.dealer_seat = -1
        self.pot = 0
        self.bets = []             # per-seat bet amounts
        self.stacks = []           # per-seat stack amounts
        self.hero_turn = False
        self.facing_bet = False
        self.call_amount = 0
        self.phase = "WAITING"     # WAITING, PREFLOP, FLOP, TURN, RIVER
        self.position = "MP"       # BTN, SB, BB, UTG, MP, CO
        self.hero_name = ""        # Set from PLAYER_INFO or config
        self.num_seats = 6         # Default 6-max

        self._running = False
        self._thread = None
        self._callbacks = []
        self._position_locked = False
        self._last_action = None
        self._discovery_keys = set()  # Track discovered SFS keys for protocol mapping

    # -- Public API (same as UnibetWSReader) --

    def on_state_change(self, callback):
        """Register a callback for state changes."""
        self._callbacks.append(callback)

    def _notify(self):
        for cb in self._callbacks:
            try:
                cb(self.get_state())
            except Exception:
                pass

    def get_state(self) -> dict:
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

    def start(self):
        """Start listening to WebSocket messages."""
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    # -- Event Handlers --

    def _handle_sfs_event(self, event: str, data: dict):
        """Process a decoded SFS2X game event."""
        if self.debug:
            print(f"[COIN-SFS] {event}: {json.dumps(data, default=str)[:300]}")

        # Discover new keys for protocol mapping
        if isinstance(data, dict):
            for key in data:
                if key not in self._discovery_keys:
                    self._discovery_keys.add(key)
                    if self.debug:
                        print(f"[COIN-DISCOVER] New key: {key} = {repr(data[key])[:100]}")

        if event == 'TABLE_INIT':
            self._on_table_init(data)
        elif event == 'GAME_START' or event == 'PRE_HAND_START':
            self._on_game_start(data)
        elif event == 'HOLE_CARDS':
            self._on_hole_cards(data)
        elif event == 'DEALER_CARDS':
            self._on_dealer_cards(data)
        elif event == 'USER_TURN':
            self._on_user_turn(data)
        elif event == 'USER_ACTION':
            self._on_user_action(data)
        elif event == 'POT_INFO':
            self._on_pot_info(data)
        elif event == 'WINNER_INFO' or event == 'CUMULATIVE_WINNER_INFO':
            self._on_winner_info(data)
        elif event == 'PLAYER_INFO' or event == 'SEAT_INFO':
            self._on_player_info(data)
        elif event == 'HAND_STRENGTH':
            self._on_hand_strength(data)
        elif event == 'SEAT' or event == 'TAKE_SEAT':
            self._on_seat(data)
        elif event == 'LEAVE_SEAT' or event == 'SIT_OUT':
            self._on_leave(data)
        elif event == '_RAW':
            # Unknown format — log for discovery
            if self.debug:
                print(f"[COIN-RAW] {json.dumps(data, default=str)[:500]}")

    def _on_table_init(self, data):
        """Handle TABLE_INIT — initial table state with all player info."""
        # Common SFS2X poker field names (varies by implementation):
        # 'players'/'pl'/'seats' = array of player objects
        # 'dealer'/'dl'/'btnSeat' = dealer seat
        # 'tableId'/'tid' = table identifier
        # 'maxSeats'/'ms' = max seats

        players = self._find_field(data, ['players', 'pl', 'seats', 'seatInfo'])
        if isinstance(players, (list, dict)):
            self._parse_players(players)

        dealer = self._find_field(data, ['dealer', 'dl', 'btnSeat', 'dealerSeat', 'ds'])
        if isinstance(dealer, int):
            self.dealer_seat = dealer

        max_seats = self._find_field(data, ['maxSeats', 'ms', 'maxPlayers'])
        if isinstance(max_seats, int) and 2 <= max_seats <= 10:
            self.num_seats = max_seats

        self._notify()

    def _on_game_start(self, data):
        """Handle GAME_START / PRE_HAND_START — new hand begins."""
        hand_id = self._find_field(data, ['handId', 'hid', 'gameId', 'gid', 'roundId'])
        if hand_id is not None:
            self.hand_id = hand_id

        # Reset state for new hand
        self.board_cards = []
        self.phase = "PREFLOP"
        self.hero_turn = False
        self.facing_bet = False
        self.call_amount = 0
        self._position_locked = False
        self._last_action = None

        # Update dealer
        dealer = self._find_field(data, ['dealer', 'dl', 'btnSeat', 'dealerSeat', 'ds'])
        if isinstance(dealer, int):
            self.dealer_seat = dealer
            self._compute_position()

        # Update blinds/bets
        blinds = self._find_field(data, ['blinds', 'bets', 'forcedBets'])
        if isinstance(blinds, (list, dict)):
            self._parse_bets(blinds)

        self._notify()

    def _on_hole_cards(self, data):
        """Handle HOLE_CARDS — hero receives hole cards."""
        # Cards could be: 'cards', 'holeCards', 'hc', 'c', or 'playerCards'
        cards = self._find_field(data, ['cards', 'holeCards', 'hc', 'c', 'playerCards'])
        if cards is not None:
            self.hero_cards = normalize_cards(cards)
            if self.debug:
                print(f"[COIN] HOLE_CARDS: {self.hero_cards}")

        # Some implementations include seat info
        seat = self._find_field(data, ['seat', 'seatId', 'si', 'seatNo'])
        if isinstance(seat, int):
            self.hero_seat = seat
            self._compute_position()

        self._notify()

    def _on_dealer_cards(self, data):
        """Handle DEALER_CARDS — community cards dealt."""
        cards = self._find_field(data, ['cards', 'dealerCards', 'communityCards', 'cc', 'c', 'board'])
        if cards is not None:
            new_board = normalize_cards(cards)
            if len(new_board) > len(self.board_cards):
                self.board_cards = new_board
            elif len(new_board) > 0 and new_board != self.board_cards:
                # Could be incremental (just the new card)
                if len(new_board) == 1 and len(self.board_cards) < 5:
                    self.board_cards.append(new_board[0])
                else:
                    self.board_cards = new_board

        # Update phase based on board card count
        old_phase = self.phase
        n = len(self.board_cards)
        if n == 0:
            self.phase = "PREFLOP"
        elif n == 3:
            self.phase = "FLOP"
        elif n == 4:
            self.phase = "TURN"
        elif n >= 5:
            self.phase = "RIVER"

        # Reset facing_bet on new street
        if self.phase != old_phase:
            self.facing_bet = False
            self.call_amount = 0
            self.bets = [0] * max(len(self.bets), self.num_seats)

        if self.debug:
            print(f"[COIN] DEALER_CARDS: {self.board_cards} phase={self.phase}")

        self._notify()

    def _on_user_turn(self, data):
        """Handle USER_TURN — it's a player's turn to act."""
        seat = self._find_field(data, ['seat', 'seatId', 'si', 'seatNo', 'userId', 'uid'])
        is_hero = False

        if isinstance(seat, int) and seat == self.hero_seat:
            is_hero = True
        elif isinstance(seat, str) and self.hero_name and seat.lower() == self.hero_name.lower():
            is_hero = True

        # Check for username match
        player_name = self._find_field(data, ['userName', 'name', 'un', 'playerName', 'pn'])
        if player_name and self.hero_name and str(player_name).lower() == self.hero_name.lower():
            is_hero = True

        if is_hero:
            self.hero_turn = True

            # Extract bet-to-call info
            to_call = self._find_field(data, [
                'callAmount', 'toCall', 'call', 'ca', 'amountToCall',
                'minBet', 'currentBet'
            ])
            if isinstance(to_call, (int, float)):
                self.call_amount = float(to_call)
                self.facing_bet = self.call_amount > 0
            else:
                # No explicit call amount — check if any bet is larger than hero's
                if self.bets and 0 <= self.hero_seat < len(self.bets):
                    max_bet = max(self.bets)
                    hero_bet = self.bets[self.hero_seat]
                    self.call_amount = max(0, max_bet - hero_bet)
                    self.facing_bet = self.call_amount > 0

            if self.debug:
                print(f"[COIN] USER_TURN: hero={is_hero} facing={self.facing_bet} call={self.call_amount}")
        else:
            self.hero_turn = False

        self._notify()

    def _on_user_action(self, data):
        """Handle USER_ACTION — a player performed an action."""
        seat = self._find_field(data, ['seat', 'seatId', 'si', 'seatNo'])
        action = self._find_field(data, ['action', 'actionType', 'act', 'a', 'type'])
        amount = self._find_field(data, ['amount', 'amt', 'betAmount', 'value', 'v'])

        if isinstance(action, str):
            action = action.upper().replace('-', '_').replace(' ', '_')

        # Track bets
        if isinstance(seat, int) and isinstance(amount, (int, float)):
            while len(self.bets) <= seat:
                self.bets.append(0)
            self.bets[seat] = float(amount)

        # If hero's turn was set and someone else acted, update facing_bet
        if isinstance(seat, int) and seat != self.hero_seat:
            if action in (ACTION_RAISE, ACTION_BET, ACTION_ALLIN):
                if self.bets and 0 <= self.hero_seat < len(self.bets):
                    max_bet = max(self.bets)
                    hero_bet = self.bets[self.hero_seat]
                    self.call_amount = max(0, max_bet - hero_bet)
                    self.facing_bet = self.call_amount > 0

        # If hero acted, clear hero_turn
        if isinstance(seat, int) and seat == self.hero_seat:
            self.hero_turn = False
            self.facing_bet = False
            if action == ACTION_FOLD:
                self.hero_cards = []

        self._last_action = {
            'seat': seat, 'action': action, 'amount': amount
        }

        self._notify()

    def _on_pot_info(self, data):
        """Handle POT_INFO — pot size update."""
        pot = self._find_field(data, ['pot', 'potAmount', 'totalPot', 'tp', 'mainPot', 'mp', 'amount'])
        if isinstance(pot, (int, float)):
            self.pot = float(pot)
        elif isinstance(pot, list):
            # Array of side pots: [{amount: X}, ...] or [X, Y, ...]
            total = 0
            for p in pot:
                if isinstance(p, (int, float)):
                    total += p
                elif isinstance(p, dict):
                    amt = p.get('amount', p.get('amt', p.get('v', 0)))
                    total += float(amt) if isinstance(amt, (int, float)) else 0
            self.pot = total

        # Fallback: sum of bets
        if self.pot == 0 and self.bets:
            self.pot = sum(self.bets)

        self._notify()

    def _on_winner_info(self, data):
        """Handle WINNER_INFO — hand complete."""
        # Hand is over, reset for next hand
        if self.debug:
            print(f"[COIN] WINNER: {json.dumps(data, default=str)[:200]}")

        self.hero_turn = False
        self.facing_bet = False
        self.phase = "WAITING"
        self._notify()

    def _on_player_info(self, data):
        """Handle PLAYER_INFO / SEAT_INFO — player data update."""
        players = self._find_field(data, ['players', 'pl', 'seats', 'seatInfo'])
        if isinstance(players, (list, dict)):
            self._parse_players(players)

        # Stack updates
        stacks = self._find_field(data, ['stacks', 'chips', 'balances'])
        if isinstance(stacks, list):
            self.stacks = [float(s) if isinstance(s, (int, float)) else 0 for s in stacks]

        self._notify()

    def _on_hand_strength(self, data):
        """Handle HAND_STRENGTH — built-in hand evaluation."""
        # Informational only — we compute our own equity
        if self.debug:
            strength = self._find_field(data, ['strength', 'handRank', 'rank', 'handType'])
            print(f"[COIN] HAND_STRENGTH: {strength}")

    def _on_seat(self, data):
        """Handle SEAT / TAKE_SEAT events."""
        seat = self._find_field(data, ['seat', 'seatId', 'si', 'seatNo'])
        name = self._find_field(data, ['userName', 'name', 'un', 'playerName', 'pn'])

        if isinstance(seat, int) and name:
            while len(self.players) <= seat:
                self.players.append('')
            self.players[seat] = str(name)

            # Detect hero seat
            if self.hero_name and str(name).lower() == self.hero_name.lower():
                self.hero_seat = seat
                self._compute_position()

        self._notify()

    def _on_leave(self, data):
        """Handle LEAVE_SEAT / SIT_OUT events."""
        seat = self._find_field(data, ['seat', 'seatId', 'si', 'seatNo'])
        if isinstance(seat, int) and 0 <= seat < len(self.players):
            self.players[seat] = ''

        self._notify()

    # -- DOM fallback parsing --

    def _handle_dom_state(self, data: dict):
        """Process DOM-scraped state from the bridge."""
        if self.debug and data.get('_testIds'):
            # Log discovered test IDs once for protocol discovery
            test_ids = data.get('_testIds', [])
            new_ids = [t for t in test_ids if t not in self._discovery_keys]
            if new_ids:
                for tid in new_ids[:10]:
                    self._discovery_keys.add(tid)
                    print(f"[COIN-DOM] TestID: {tid}")

        # Update action buttons / hero turn
        actions = data.get('actions', [])
        if actions:
            self.hero_turn = True
            # Parse call amount from button text like "Call $2.50"
            for act in actions:
                m = re.search(r'call\s*\$?([\d,.]+)', act, re.I)
                if m:
                    self.call_amount = float(m.group(1).replace(',', ''))
                    self.facing_bet = True
                    break
            else:
                # Check/Bet available but no Call = not facing a bet
                if any('check' in a.lower() for a in actions):
                    self.facing_bet = False
                    self.call_amount = 0
        else:
            self.hero_turn = False

        # Pot from DOM
        pot_str = data.get('pot', '')
        if pot_str:
            try:
                self.pot = float(pot_str.replace(',', ''))
            except ValueError:
                pass

        self._notify()

    # -- Helpers --

    def _find_field(self, data, candidates: list):
        """Find the first matching field name in a dict."""
        if not isinstance(data, dict):
            return None
        for key in candidates:
            if key in data:
                return data[key]
        # Case-insensitive fallback
        lower_map = {k.lower(): k for k in data}
        for key in candidates:
            real_key = lower_map.get(key.lower())
            if real_key:
                return data[real_key]
        return None

    def _parse_players(self, players):
        """Parse player info from various formats."""
        if isinstance(players, list):
            self.players = []
            self.stacks = []
            for i, p in enumerate(players):
                if isinstance(p, dict):
                    name = p.get('name', p.get('userName', p.get('un', p.get('pn', ''))))
                    stack = p.get('chips', p.get('stack', p.get('balance', p.get('bal', 0))))
                    self.players.append(str(name) if name else '')
                    self.stacks.append(float(stack) if isinstance(stack, (int, float)) else 0)

                    # Detect hero
                    if self.hero_name and str(name).lower() == self.hero_name.lower():
                        self.hero_seat = i
                elif isinstance(p, str):
                    self.players.append(p)
                    self.stacks.append(0)
                else:
                    self.players.append('')
                    self.stacks.append(0)

        elif isinstance(players, dict):
            # Keyed by seat number: {"0": {...}, "1": {...}}
            max_seat = max(int(k) for k in players.keys() if str(k).isdigit()) + 1 if players else 0
            self.players = [''] * max(max_seat, self.num_seats)
            self.stacks = [0] * max(max_seat, self.num_seats)
            for seat_str, p in players.items():
                try:
                    seat = int(seat_str)
                except ValueError:
                    continue
                if isinstance(p, dict):
                    name = p.get('name', p.get('userName', p.get('un', '')))
                    stack = p.get('chips', p.get('stack', p.get('balance', 0)))
                    if 0 <= seat < len(self.players):
                        self.players[seat] = str(name) if name else ''
                        self.stacks[seat] = float(stack) if isinstance(stack, (int, float)) else 0
                        if self.hero_name and str(name).lower() == self.hero_name.lower():
                            self.hero_seat = seat

    def _parse_bets(self, blinds):
        """Parse bet/blind info."""
        if isinstance(blinds, list):
            self.bets = [float(b) if isinstance(b, (int, float)) else 0 for b in blinds]
        elif isinstance(blinds, dict):
            max_seat = self.num_seats
            self.bets = [0] * max_seat
            for key, val in blinds.items():
                try:
                    seat = int(key)
                    if 0 <= seat < max_seat:
                        self.bets[seat] = float(val) if isinstance(val, (int, float)) else 0
                except (ValueError, TypeError):
                    pass

    def _compute_position(self):
        """Compute hero position from dealer seat."""
        if self.dealer_seat < 0 or self.hero_seat < 0:
            return
        if self._position_locked:
            return

        n = max(self.num_seats, len(self.players))
        if n == 0:
            return

        # Count active players
        active = sum(1 for p in self.players if p) if self.players else n
        dist = (self.hero_seat - self.dealer_seat) % n

        if active <= 2:
            # Heads-up
            self.position = "BTN" if dist == 0 else "BB"
        elif active == 3:
            pos_map = {0: "BTN", 1: "SB", 2: "BB"}
            self.position = pos_map.get(dist, "MP")
        else:
            # 6-max (or full ring with 6-max position names)
            pos_map = {0: "BTN", 1: "SB", 2: "BB", 3: "UTG", 4: "MP", 5: "CO"}
            self.position = pos_map.get(dist, "MP")

        self._position_locked = True

    # -- Listen Loop --

    def _listen_loop(self):
        """Background thread: run CDP bridge and parse output."""
        bridge_script = os.path.join(
            os.path.dirname(__file__), '..', 'scripts', 'cdp-coinpoker-bridge.js'
        )

        args = ['node', bridge_script, str(self.cdp_port)]
        if self.use_dom:
            args.append('--dom')

        try:
            proc = sp.Popen(
                args,
                stdout=sp.PIPE, stderr=sp.PIPE, text=True, bufsize=1
            )
            print(f"[COIN] CDP bridge started (port={self.cdp_port}, dom={self.use_dom})")

            while self._running:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        err = proc.stderr.read()
                        if err:
                            print(f"[COIN] Bridge died: {err[:300]}")
                        break
                    continue

                line = line.strip()
                if not line:
                    continue

                try:
                    if line.startswith('SFS:'):
                        msg = json.loads(line[4:])
                        event = msg.get('event', '_RAW')
                        data = msg.get('data', {})
                        self._handle_sfs_event(event, data)
                    elif line.startswith('SFS_SENT:'):
                        # Outgoing messages — track hero actions
                        msg = json.loads(line[9:])
                        event = msg.get('event', '')
                        if event == 'USER_ACTION' and self.debug:
                            print(f"[COIN-SENT] {json.dumps(msg.get('data', {}), default=str)[:200]}")
                    elif line.startswith('SIO:'):
                        # Socket.io message (lobby/chat)
                        if self.debug:
                            print(f"[COIN-SIO] {line[4:][:200]}")
                    elif line.startswith('DOM:'):
                        dom_data = json.loads(line[4:])
                        self._handle_dom_state(dom_data)
                except json.JSONDecodeError:
                    if self.debug:
                        print(f"[COIN] Bad JSON: {line[:100]}")
                except Exception as e:
                    if self.debug:
                        print(f"[COIN] Error: {e}")

            proc.terminate()
        except FileNotFoundError:
            print("[COIN] Error: 'node' not found. Install Node.js and chrome-remote-interface.")
        except Exception as e:
            print(f"[COIN] Failed: {e}")

    # -- Direct Binary Decode (without CDP bridge) --

    def decode_binary(self, raw_bytes: bytes):
        """Decode a raw SFS2X binary frame and update state.

        Can be called directly if you have the raw WebSocket frame bytes
        (e.g., from a browser extension or proxy).
        """
        result = decode_sfs_frame(raw_bytes)
        if result:
            event, data = result
            self._handle_sfs_event(event, data)
            return (event, data)
        return None


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='CoinPoker game state reader')
    parser.add_argument('--port', type=int, default=9222, help='CDP port (default: 9222)')
    parser.add_argument('--dom', action='store_true', help='Use DOM scraping mode')
    parser.add_argument('--debug', action='store_true', help='Verbose debug output')
    parser.add_argument('--hero', type=str, default='', help='Hero username for seat detection')
    args = parser.parse_args()

    reader = CoinPokerWSReader(
        cdp_port=args.port,
        use_dom=args.dom,
        debug=args.debug
    )
    if args.hero:
        reader.hero_name = args.hero

    def on_change(state):
        hero = state["hero_cards"]
        board = state["board_cards"]
        turn = state["hero_turn"]
        facing = state["facing_bet"]
        call_amt = state["call_amount"]
        phase = state["phase"]
        pot = state["pot"]
        pos = state["position"]
        if hero or (phase != "WAITING" and board):
            print(f"[{phase}] Hero: {' '.join(hero)} | Board: {' '.join(board)} | "
                  f"Pot: {pot} | Pos: {pos} | Turn: {turn} | "
                  f"Facing: {facing} (call {call_amt})")

    reader.on_state_change(on_change)
    reader.start()

    print("Reading CoinPoker game state via SFS2X. Press Ctrl+C to stop.")
    if args.dom:
        print("(DOM scraping mode)")
    else:
        print("(WebSocket binary interception mode)")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        reader.stop()
        print("\nStopped.")
