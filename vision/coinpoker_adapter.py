"""
CoinPoker → AdvisorStateMachine adapter.

Consumes the JSONL stream produced by the patched PBClient.dll
(``C:\\Users\\Simon\\coinpoker_frames.jsonl``) and converts ``cmd_bean``
events into the dict shape expected by ``AdvisorStateMachine.process_state``.

This module is pure conversion: no I/O, no advisor wiring. Feed frames in
one at a time via ``builder.ingest(frame)`` and call ``builder.snapshot()``
when you want a state dict to hand to the advisor.

Frame envelope (from the patched HandlePipeMessage hook)::

    {
      "cmd_bean": {
        "BeanData": "<json-string>",       # double-encoded inner payload
        "Cmd": "game.X",
        "RoomName": "PR-NL ..."
      },
      "room_name": "PR-NL ..."
    }

The state shape we emit (matches AdvisorStateMachine.process_state)::

    {
      "hero_cards":     [str, str],     # e.g. ["9c", "5d"]
      "board_cards":    [str, ...],     # 0/3/4/5 community cards
      "hand_id":        str,            # gameHandId
      "facing_bet":     bool,
      "call_amount":    int,            # chip units, scaled by CHIP_SCALE
      "phase":          str,            # PREFLOP|FLOP|TURN|RIVER
      "num_opponents":  int,
      "pot":            int,            # chip units, scaled
      "hero_stack":     int,
      "position":       str,            # UTG|MP|CO|BTN|SB|BB
      "bets":           [int, ...],     # current-round bets per active seat
      "hero_seat":      int,
      "players":        [dict, ...],    # for opponent tracker
      "hero_turn":      bool,
    }

Chip scaling: CoinPoker BeanData uses floats with 2dp (e.g. 430794.89).
We multiply by 100 and round to int so the advisor can do its arithmetic
in integer "cents". The big blind in cents = bb_chips * 100. Pot odds /
bet sizing logic is scale-invariant so this is purely a precision concern.
"""

from __future__ import annotations

import json
from typing import Any, Optional


CHIP_SCALE = 100  # multiply float chips by this to get int "cents"


# ── card normalization ────────────────────────────────────────────────────────

_VALUE_MAP = {
    "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5", "SIX": "6",
    "SEVEN": "7", "EIGHT": "8", "NINE": "9", "TEN": "T",
    "JACK": "J", "QUEEN": "Q", "KING": "K", "ACE": "A",
}
_SUIT_MAP = {
    "CLUBS": "c", "DIAMONDS": "d", "HEARTS": "h", "SPADES": "s",
}


def card_to_str(card: dict) -> str:
    """``{"suit":"CLUBS","value":"NINE"}`` → ``"9c"``."""
    return _VALUE_MAP[card["value"]] + _SUIT_MAP[card["suit"]]


def cards_to_strs(cards) -> list[str]:
    if not cards:
        return []
    return [card_to_str(c) for c in cards]


# ── chip helpers ──────────────────────────────────────────────────────────────

def chips(v) -> int:
    """Round float chips to scaled int. None → 0."""
    if v is None:
        return 0
    return int(round(float(v) * CHIP_SCALE))


# ── position derivation ───────────────────────────────────────────────────────

# Position name lists, indexed clockwise from BB. UTG aliases to EP for the
# preflop chart but we emit UTG since that's also accepted (line 140 of
# preflop_chart.py: ``"EP": EP_RAISE, "UTG": EP_RAISE``).
_POSITION_NAMES = {
    6: ["BB", "UTG", "MP", "CO", "BTN", "SB"],
    5: ["BB", "UTG", "MP", "BTN", "SB"],
    4: ["BB", "UTG", "BTN", "SB"],
    3: ["BB", "BTN", "SB"],
    2: ["BB", "BTN"],  # HU: BTN is the SB (posts SB)
}


def derive_position(hero_seat: int, big_blind_seat: int, active_seats: list[int]) -> str:
    """
    Walk active seats clockwise (= ascending seat id, with wrap) starting at
    BB, return hero's positional label.

    ``active_seats`` must be the list of seat ids that are in this hand.
    Returns 'MP' as a safe default if hero isn't in the list or table size
    is unsupported.
    """
    if hero_seat not in active_seats or big_blind_seat not in active_seats:
        return "MP"
    n = len(active_seats)
    names = _POSITION_NAMES.get(n)
    if names is None:
        return "MP"
    ordered = sorted(active_seats)
    bb_idx = ordered.index(big_blind_seat)
    hero_idx = ordered.index(hero_seat)
    offset = (hero_idx - bb_idx) % n
    return names[offset]


def derive_blinds_from_dealer(
    dealer_seat: Optional[int],
    active_seats: list[int],
) -> tuple[Optional[int], Optional[int]]:
    """
    Derive (small_blind_seat, big_blind_seat) from the dealer button position
    and the list of active seat ids for this hand.

    Why this exists: in CoinPoker's live event stream, ``game.game_alldata``
    (which carries explicit ``smallBlindSeatId`` / ``bigBlindSeatId`` fields)
    only fires once per session — at table join. Subsequent hands receive
    only ``game.pre_hand_start_info``, which carries ``dealerSeatId`` but
    not the blind seats. Without this helper the builder's ``bb_seat`` stays
    fixed to whatever it was on join, so positional output is wrong on every
    hand after the first.

    Convention:
        - Heads-up (2 active): dealer is the SB; the other player is the BB.
        - 3+ handed: SB is the next active seat clockwise (= ascending seat
          id with wrap) from the dealer; BB is the next active seat after SB.
        - Dead button is supported: dealer_seat does not have to be active.
        - Returns (None, None) if we can't derive (no dealer, <2 actives).
    """
    if dealer_seat is None or len(active_seats) < 2:
        return None, None
    ordered = sorted(active_seats)

    def _next_active_after(seat: int) -> int:
        for s in ordered:
            if s > seat:
                return s
        return ordered[0]  # wrapped

    if len(ordered) == 2:
        # HU: dealer is SB. If dealer isn't actually in the active list
        # (dead button HU is rare but possible), fall back to "first active
        # is SB, other is BB".
        if dealer_seat in ordered:
            sb = dealer_seat
            bb = _next_active_after(sb)
        else:
            sb = ordered[0]
            bb = ordered[1]
        return sb, bb

    sb = _next_active_after(dealer_seat)
    bb = _next_active_after(sb)
    return sb, bb


# ── builder ───────────────────────────────────────────────────────────────────

class CoinPokerStateBuilder:
    """
    Stateful converter from cmd_bean events to advisor state dicts.

    Construction takes the hero's userId (the integer that appears as
    ``userId`` inside seat records) — that's the only piece of identity
    we need to recognize ourselves across hands. The seat number can move
    between hands (sit-out / rejoin) so we re-derive it from each
    seatInfo.
    """

    def __init__(self, hero_user_id: int):
        self.hero_user_id = int(hero_user_id)

        # Per-hand state, reset on new hand_id
        self.hand_id: Optional[str] = None
        self.dealer_seat: Optional[int] = None
        self.sb_seat: Optional[int] = None
        self.bb_seat: Optional[int] = None
        self.bb_amount: int = 0  # chip-scaled
        self.sb_amount: int = 0
        self.ante_amount: int = 0

        # Seat state (keyed by seatId int) — values are dicts with
        # userId, userName, chips (int), bet (int), is_playing, last_action
        self.seats: dict[int, dict[str, Any]] = {}

        # Dealt-in seat ids for the current hand. Populated from the very
        # first seatInfo of the hand and never shrunk — folds and end-of-
        # hand "Sitout" updates flip is_playing on the seat dict, but
        # position derivation needs to remember who STARTED the hand or
        # the rotation breaks late in the hand. Reset on new hand.
        self.hand_active_seats: set[int] = set()

        # Hero data
        self.hero_seat: Optional[int] = None
        self.hero_cards: list[str] = []

        # Board / phase / pot
        self.board: list[str] = []
        self.phase: str = "PREFLOP"
        self.pot: int = 0

        # Turn tracking
        self.whose_turn_seat: Optional[int] = None
        self.round_max_bet: int = 0  # in scaled chips, the highest bet posted this round

    # ── frame ingestion ──

    def ingest(self, frame: dict) -> None:
        """Apply one cmd_bean frame. Returns nothing; query via ``snapshot``."""
        cb = frame.get("cmd_bean") or {}
        cmd = cb.get("Cmd")
        if not cmd:
            return
        bean_raw = cb.get("BeanData")
        if isinstance(bean_raw, str) and bean_raw:
            try:
                bean = json.loads(bean_raw)
            except json.JSONDecodeError:
                return
        elif isinstance(bean_raw, dict):
            bean = bean_raw
        else:
            return

        handler = _DISPATCH.get(cmd)
        if handler is not None:
            handler(self, bean)

    # ── per-event handlers ──

    def _on_pre_hand_start(self, bean: dict) -> None:
        new_hand = str(bean.get("gameHandId") or "")
        if new_hand and new_hand != self.hand_id:
            self._reset_for_new_hand(new_hand)
            self.dealer_seat = bean.get("dealerSeatId")
            self.bb_amount = chips(bean.get("bbAmount"))
            self.sb_amount = chips(bean.get("sbAmount"))
            self.ante_amount = chips(bean.get("anteAmount"))

    def _on_game_alldata(self, bean: dict) -> None:
        new_hand = str(bean.get("gameHandId") or "")
        if new_hand and new_hand != self.hand_id:
            self._reset_for_new_hand(new_hand)

        init = bean.get("gameInitResponseData") or {}
        self.dealer_seat = init.get("dealerSeatId") or self.dealer_seat
        self.sb_seat = init.get("smallBlindSeatId") or self.sb_seat
        self.bb_seat = init.get("bigBlindSeatId") or self.bb_seat
        self.bb_amount = chips(init.get("bigBlind")) or self.bb_amount
        self.sb_amount = chips(init.get("smallBlind")) or self.sb_amount
        self.ante_amount = chips(init.get("ante")) or self.ante_amount
        wt = init.get("whoseTurnSeatId")
        if wt is not None:
            self.whose_turn_seat = wt

        # Initial board (usually empty preflop, but may carry FLOP if we
        # joined mid-hand)
        dc = init.get("dealerCards") or {}
        self._merge_dealer_cards(dc)

        # Seats
        seat_block = bean.get("seatInfoRsponseData") or {}
        self._update_seats(seat_block.get("seatResponseDataList") or [])

        # Pot
        pot_block = bean.get("potInfoResponseData") or {}
        if pot_block.get("totalPotAmount") is not None:
            self.pot = chips(pot_block["totalPotAmount"])
        round_name = pot_block.get("roundName")
        if round_name:
            self._set_phase(round_name)

    def _on_seat_info(self, bean: dict) -> None:
        new_hand = str(bean.get("gameHandId") or "")
        if new_hand and new_hand != self.hand_id:
            # seatInfo can arrive before pre_hand_start_info — adopt the id
            self._reset_for_new_hand(new_hand)
        self._update_seats(bean.get("seatResponseDataList") or [])

    # Captions that represent an actual player decision (not a passive
    # post-blind / ante / sit). When we see one of these, the action has
    # passed to the next player and ``whose_turn_seat`` should clear so
    # ``hero_turn`` doesn't stay True between hero acting and the next
    # ``game.user_turn`` arriving.
    _ACTION_CAPTIONS = {
        "fold", "check", "call", "bet", "raise", "allin", "all-in", "all in",
    }

    def _on_seat(self, bean: dict) -> None:
        # Single-seat update (typically a bet/post). Mutate in place.
        seat_id = bean.get("seatId")
        if seat_id is None:
            return
        s = self.seats.setdefault(seat_id, {
            "seatId": seat_id, "userId": None, "userName": None,
            "chips": 0, "bet": 0, "is_playing": True, "last_action": "",
        })
        if bean.get("userName") is not None:
            s["userName"] = bean["userName"]
        if bean.get("userChips") is not None:
            s["chips"] = chips(bean["userChips"])
        if bean.get("betAmout") is not None:
            s["bet"] = chips(bean["betAmout"])
        cap = bean.get("newCaption") or bean.get("caption")
        if cap:
            s["last_action"] = cap

        # Track the highest bet posted this round so we can derive
        # facing_bet / call_amount when it's our turn.
        if s["bet"] > self.round_max_bet:
            self.round_max_bet = s["bet"]

        # If this caption represents a real player action, the action has
        # passed and the previous turn-holder is no longer "to act". The
        # next ``game.user_turn`` event will set ``whose_turn_seat`` to the
        # new actor; until then we leave it None so ``hero_turn`` correctly
        # reads False between hero's action and the server confirming the
        # next player's turn.
        if cap and cap.strip().lower() in self._ACTION_CAPTIONS:
            self.whose_turn_seat = None

    def _on_pot_info(self, bean: dict) -> None:
        if bean.get("totalPotAmount") is not None:
            self.pot = chips(bean["totalPotAmount"])
        round_name = bean.get("roundName")
        if round_name:
            self._set_phase(round_name)

    def _on_hole_cards(self, bean: dict) -> None:
        cards = bean.get("holeCards") or []
        self.hero_cards = cards_to_strs(cards)

    def _on_dealer_cards(self, bean: dict) -> None:
        dc = bean.get("dealerCards") or {}
        self._merge_dealer_cards(dc)

    def _on_user_turn(self, bean: dict) -> None:
        # whoseTurn is a username — map to seatId via current seats.
        name = bean.get("whoseTurn")
        if name:
            for seat_id, s in self.seats.items():
                if s.get("userName") == name:
                    self.whose_turn_seat = seat_id
                    break
        rmb = bean.get("roundMaxBet")
        if rmb is not None:
            scaled = chips(rmb)
            # The server sometimes sends ``roundMaxBet=0.0`` in a user_turn
            # event even when there's a real bet to call (e.g. before the
            # actor has been "officially" served the up-to-date round
            # info). Treat that as no signal — the per-seat bets we've
            # already absorbed are authoritative. Phase changes reset
            # round_max_bet via _set_phase, not via user_turn.
            if scaled > 0:
                self.round_max_bet = scaled

    def _on_reset(self, bean: dict) -> None:
        # End-of-hand cleanup. Don't drop hand_id yet — pre_hand_start_info
        # for the next hand will overwrite it. We DO clear hero_cards so
        # the advisor's "Waiting for cards..." path triggers cleanly.
        self.hero_cards = []
        self.board = []
        self.phase = "PREFLOP"
        self.round_max_bet = 0
        self.whose_turn_seat = None

    # ── helpers ──

    def _reset_for_new_hand(self, new_hand_id: str) -> None:
        self.hand_id = new_hand_id
        self.hero_cards = []
        self.board = []
        self.phase = "PREFLOP"
        self.pot = 0
        self.round_max_bet = 0
        self.whose_turn_seat = None
        # Blind seats must be re-derived per hand. game.game_alldata only
        # fires once per session (on join), so without this reset bb_seat
        # would stick at whatever the initial-join value was even as the
        # button rotates. snapshot() falls back to derive_blinds_from_dealer
        # when these are None.
        self.sb_seat = None
        self.bb_seat = None
        # Forget last hand's dealt-in lineup — the next seatInfo will
        # repopulate it for the new hand.
        self.hand_active_seats = set()
        # Reset per-seat per-round state but keep identities/chips
        for s in self.seats.values():
            s["bet"] = 0
            s["last_action"] = ""

    def _set_phase(self, round_name: str) -> None:
        rn = round_name.upper()
        if rn in ("PREFLOP", "FLOP", "TURN", "RIVER"):
            if rn != self.phase:
                # Phase change resets per-round bets
                self.round_max_bet = 0
                for s in self.seats.values():
                    s["bet"] = 0
            self.phase = rn
        # ANTE / SHOWDOWN / etc. don't map to advisor phases — leave alone

    def _merge_dealer_cards(self, dc: dict) -> None:
        flop = dc.get("FLOP") or []
        turn = dc.get("TURN") or []
        river = dc.get("RIVER") or []
        new_board: list[str] = []
        new_board.extend(cards_to_strs(flop))
        if turn:
            # TURN may be a single card dict in a list, or a single dict
            if isinstance(turn, dict):
                new_board.append(card_to_str(turn))
            else:
                new_board.extend(cards_to_strs(turn))
        if river:
            if isinstance(river, dict):
                new_board.append(card_to_str(river))
            else:
                new_board.extend(cards_to_strs(river))
        # Only update if non-empty (don't wipe a populated board with a
        # later "no change" snapshot that has nulls)
        if new_board:
            self.board = new_board
            # Phase follows board length deterministically. game.potInfo
            # may carry roundName too, but dealer_cards usually arrives
            # first and we want phase to track immediately so the advisor
            # doesn't process a flop board with phase=PREFLOP.
            inferred = self._phase_from_board(len(new_board))
            if inferred and inferred != self.phase:
                self._set_phase(inferred)

    @staticmethod
    def _phase_from_board(n: int) -> Optional[str]:
        if n == 0: return "PREFLOP"
        if n == 3: return "FLOP"
        if n == 4: return "TURN"
        if n == 5: return "RIVER"
        return None

    def _update_seats(self, seat_list: list[dict]) -> None:
        for s_in in seat_list:
            seat_id = s_in.get("seatId")
            if seat_id is None:
                continue
            user_id = s_in.get("userId")
            user_name = s_in.get("userName")
            s = self.seats.setdefault(seat_id, {})
            s["seatId"] = seat_id
            s["userId"] = user_id
            s["userName"] = user_name
            s["chips"] = chips(s_in.get("userChips"))
            s["bet"] = chips(s_in.get("betAmout"))
            s["is_playing"] = bool(s_in.get("isPlaying"))
            s["last_action"] = s_in.get("newCaption") or s_in.get("lastAction") or ""
            if user_id == self.hero_user_id:
                self.hero_seat = seat_id
            if s["bet"] > self.round_max_bet:
                self.round_max_bet = s["bet"]
            # Add to the dealt-in lineup the FIRST time we see this seat
            # marked is_playing in this hand. Never remove — late-hand
            # seatInfo events flip is_playing False (Sitout / showdown
            # cleanup) but the player still belonged to this hand for
            # position-derivation purposes.
            if s["is_playing"]:
                self.hand_active_seats.add(seat_id)

    # ── snapshot ──

    def _active_seat_ids(self) -> list[int]:
        return sorted(sid for sid, s in self.seats.items() if s.get("is_playing"))

    def _dealt_in_seat_ids(self) -> list[int]:
        """
        Sorted list of seats that were dealt into the current hand.
        Used for position derivation, which needs the lineup snapshot taken
        at hand-start, not the live "still has cards" set.
        Falls back to live ``_active_seat_ids`` for the cold-start case
        where no seatInfo has yet seeded ``hand_active_seats``.
        """
        if self.hand_active_seats:
            return sorted(self.hand_active_seats)
        return self._active_seat_ids()

    def snapshot(self) -> Optional[dict]:
        """
        Build the dict expected by ``AdvisorStateMachine.process_state``.
        Returns None if we don't have enough state yet (no hand_id or
        hero seat unknown).
        """
        if not self.hand_id or self.hero_seat is None:
            return None

        active = self._active_seat_ids()
        num_opp = max(0, len(active) - 1)

        # Position uses the dealt-in lineup (frozen at hand start), not the
        # live "still in the hand" list. Otherwise, once players fold, the
        # rotation breaks because the seat list shrinks under us.
        dealt_in = self._dealt_in_seat_ids()

        # If game.game_alldata didn't set blind seats for this hand (the
        # normal case in the live event stream — see derive_blinds_from_dealer
        # docstring), derive them from the dealer button + dealt-in seats.
        bb_seat = self.bb_seat
        if bb_seat is None:
            _, bb_seat = derive_blinds_from_dealer(self.dealer_seat, dealt_in)

        position = "MP"
        if bb_seat is not None and dealt_in:
            position = derive_position(self.hero_seat, bb_seat, dealt_in)

        hero = self.seats.get(self.hero_seat) or {}
        hero_stack = hero.get("chips", 0)
        hero_bet = hero.get("bet", 0)

        # facing_bet / call_amount: if any other active seat has bet more
        # than us this round, we're facing a bet.
        max_other_bet = 0
        for sid, s in self.seats.items():
            if sid == self.hero_seat or not s.get("is_playing"):
                continue
            if s.get("bet", 0) > max_other_bet:
                max_other_bet = s["bet"]
        # Also respect round_max_bet from user_turn (server-authoritative)
        max_round_bet = max(self.round_max_bet, max_other_bet, hero_bet)
        call_amount = max(0, max_round_bet - hero_bet)

        # Preflop, BB never "faces" their own posted blind unless someone
        # raised. We mark facing_bet only when call_amount > 0 (i.e. someone
        # actually put in more chips than us this round).
        facing_bet = call_amount > 0

        bets = [self.seats[sid].get("bet", 0) for sid in active]

        players = []
        for sid in active:
            s = self.seats[sid]
            players.append({
                "seat": sid,
                "user_id": s.get("userId"),
                "name": s.get("userName"),
                "stack": s.get("chips", 0),
                "bet": s.get("bet", 0),
                "last_action": s.get("last_action", ""),
            })

        hero_turn = self.whose_turn_seat == self.hero_seat

        return {
            "hero_cards":    list(self.hero_cards),
            "board_cards":   list(self.board),
            "hand_id":       self.hand_id,
            "facing_bet":    facing_bet,
            "call_amount":   call_amount,
            "phase":         self.phase,
            "num_opponents": num_opp,
            "pot":           self.pot,
            "hero_stack":    hero_stack,
            "position":      position,
            "bets":          bets,
            "hero_seat":     self.hero_seat,
            "players":       players,
            "hero_turn":     hero_turn,
            # Phase 2 wiring: needed by _compute_shadow_range_equity to
            # derive villain positions for the range model. Was used
            # internally for hero position derivation but not exposed.
            "bb_seat":       self.bb_seat,
            "dealer_seat":   getattr(self, "dealer_seat", None),
        }


# Dispatch table — bound after the class so methods are resolvable.
_DISPATCH = {
    "game.pre_hand_start_info":  CoinPokerStateBuilder._on_pre_hand_start,
    "game.game_alldata":         CoinPokerStateBuilder._on_game_alldata,
    "game.seatInfo":             CoinPokerStateBuilder._on_seat_info,
    "game.seat":                 CoinPokerStateBuilder._on_seat,
    "game.potInfo":              CoinPokerStateBuilder._on_pot_info,
    "game.hole_cards":           CoinPokerStateBuilder._on_hole_cards,
    "game.dealer_cards":         CoinPokerStateBuilder._on_dealer_cards,
    "game.user_turn":            CoinPokerStateBuilder._on_user_turn,
    "game.reset_data":           CoinPokerStateBuilder._on_reset,
}
