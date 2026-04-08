"""
Tests for vision.coinpoker_adapter — the cmd_bean → advisor state converter.

Mostly fixture-driven against real frames captured from the patched
PBClient.dll. The fixture file is checked in alongside this test so the
suite is reproducible without a live CoinPoker session.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from coinpoker_adapter import (
    CHIP_SCALE,
    CoinPokerStateBuilder,
    card_to_str,
    cards_to_strs,
    chips,
    derive_blinds_from_dealer,
    derive_position,
)

HERO_USER_ID = 1571120  # precious0864449 — from the live JWT and seat records
FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "coinpoker_session.jsonl")


def load_fixture():
    if not os.path.exists(FIXTURE_PATH):
        raise unittest.SkipTest(f"fixture missing: {FIXTURE_PATH}")
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── pure helpers ──────────────────────────────────────────────────────────────

class TestCardConversion(unittest.TestCase):
    def test_card_values(self):
        self.assertEqual(card_to_str({"suit": "CLUBS", "value": "NINE"}), "9c")
        self.assertEqual(card_to_str({"suit": "DIAMONDS", "value": "TEN"}), "Td")
        self.assertEqual(card_to_str({"suit": "HEARTS", "value": "ACE"}), "Ah")
        self.assertEqual(card_to_str({"suit": "SPADES", "value": "TWO"}), "2s")
        self.assertEqual(card_to_str({"suit": "CLUBS", "value": "KING"}), "Kc")

    def test_cards_to_strs_empty(self):
        self.assertEqual(cards_to_strs(None), [])
        self.assertEqual(cards_to_strs([]), [])

    def test_cards_to_strs_full(self):
        cs = cards_to_strs([
            {"suit": "HEARTS", "value": "THREE"},
            {"suit": "DIAMONDS", "value": "QUEEN"},
            {"suit": "HEARTS", "value": "FOUR"},
        ])
        self.assertEqual(cs, ["3h", "Qd", "4h"])


class TestChipScaling(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(chips(0), 0)
        self.assertEqual(chips(None), 0)
        self.assertEqual(chips(100), 100 * CHIP_SCALE)
        self.assertEqual(chips(50.0), 50 * CHIP_SCALE)

    def test_decimal_precision(self):
        # 430794.89 — the live stack we observed for Bugabug
        self.assertEqual(chips(430794.89), 43079489)


class TestPositionDerivation(unittest.TestCase):
    def test_six_handed_full_ring(self):
        # 6 active seats, BB=1, hero on each seat → all 6 positions
        active = [1, 2, 3, 4, 5, 6]
        bb = 1
        self.assertEqual(derive_position(1, bb, active), "BB")
        self.assertEqual(derive_position(2, bb, active), "UTG")
        self.assertEqual(derive_position(3, bb, active), "MP")
        self.assertEqual(derive_position(4, bb, active), "CO")
        self.assertEqual(derive_position(5, bb, active), "BTN")
        self.assertEqual(derive_position(6, bb, active), "SB")

    def test_five_handed(self):
        active = [1, 2, 3, 4, 5]  # the live hand 2463610211 setup
        bb = 1                    # bigBlindSeatId from the frame
        # dealer=4 (BTN), SB=5
        self.assertEqual(derive_position(1, bb, active), "BB")
        self.assertEqual(derive_position(2, bb, active), "UTG")
        self.assertEqual(derive_position(3, bb, active), "MP")
        self.assertEqual(derive_position(4, bb, active), "BTN")
        self.assertEqual(derive_position(5, bb, active), "SB")

    def test_heads_up(self):
        active = [3, 5]
        bb = 5
        self.assertEqual(derive_position(5, bb, active), "BB")
        self.assertEqual(derive_position(3, bb, active), "BTN")

    def test_wrap_around(self):
        # BB on seat 6, hero on seat 1 → UTG (next clockwise after BB)
        active = [1, 2, 3, 4, 5, 6]
        bb = 6
        self.assertEqual(derive_position(6, bb, active), "BB")
        self.assertEqual(derive_position(1, bb, active), "UTG")
        self.assertEqual(derive_position(2, bb, active), "MP")

    def test_hero_not_at_table(self):
        self.assertEqual(derive_position(99, 1, [1, 2, 3]), "MP")  # safe default


class TestDeriveBlindsFromDealer(unittest.TestCase):
    """
    Pure-function tests for the dealer→blinds derivation that snapshot()
    falls back to when game.game_alldata isn't in the live event stream.
    """

    def test_six_handed_dealer_one(self):
        sb, bb = derive_blinds_from_dealer(1, [1, 2, 3, 4, 5, 6])
        self.assertEqual((sb, bb), (2, 3))

    def test_six_handed_dealer_six_wraps(self):
        sb, bb = derive_blinds_from_dealer(6, [1, 2, 3, 4, 5, 6])
        self.assertEqual((sb, bb), (1, 2))

    def test_six_handed_dealer_five(self):
        sb, bb = derive_blinds_from_dealer(5, [1, 2, 3, 4, 5, 6])
        self.assertEqual((sb, bb), (6, 1))

    def test_four_handed_with_gaps(self):
        # Live failure mode: 4 active out of 6 seats, dealer rotating
        active = [1, 2, 3, 5]
        # dealer=1 → SB=2, BB=3
        self.assertEqual(derive_blinds_from_dealer(1, active), (2, 3))
        # dealer=2 → SB=3, BB=5 (skip empty 4)
        self.assertEqual(derive_blinds_from_dealer(2, active), (3, 5))
        # dealer=3 → SB=5, BB=1 (wrap, skip empty 4 and 6)
        self.assertEqual(derive_blinds_from_dealer(3, active), (5, 1))
        # dealer=5 → SB=1, BB=2
        self.assertEqual(derive_blinds_from_dealer(5, active), (1, 2))

    def test_dead_button(self):
        # Dealer button parked at an empty seat (player left)
        sb, bb = derive_blinds_from_dealer(1, [2, 3, 4, 5, 6])
        self.assertEqual((sb, bb), (2, 3))

    def test_heads_up_dealer_is_sb(self):
        # In HU the dealer posts the small blind
        sb, bb = derive_blinds_from_dealer(1, [1, 2])
        self.assertEqual((sb, bb), (1, 2))
        sb, bb = derive_blinds_from_dealer(2, [1, 2])
        self.assertEqual((sb, bb), (2, 1))

    def test_too_few_seats(self):
        self.assertEqual(derive_blinds_from_dealer(1, [1]), (None, None))
        self.assertEqual(derive_blinds_from_dealer(1, []), (None, None))

    def test_no_dealer(self):
        self.assertEqual(derive_blinds_from_dealer(None, [1, 2, 3]), (None, None))


class TestPositionDerivationFromLiveEvents(unittest.TestCase):
    """
    Regression for the live position bug: ``game.game_alldata`` only fires
    once per session in CoinPoker's live stream, so without dealer-derived
    blinds, hero's ``position`` defaulted to "MP" on every hand after the
    first. This test reproduces the live event sequence (no game_alldata,
    just pre_hand_start_info + seatInfo) and asserts position rotates as
    the dealer button moves.
    """

    def _wrap(self, cmd, bean):
        return {"cmd_bean": {"Cmd": cmd, "BeanData": json.dumps(bean), "RoomName": "T"}}

    def _start_hand(self, builder, hand_id, dealer_seat, active_seats, hero_seat):
        builder.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": hand_id, "dealerSeatId": dealer_seat,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        builder.ingest(self._wrap("game.seatInfo", {
            "gameHandId": hand_id,
            "seatResponseDataList": [
                {
                    "seatId": s,
                    "userId": (HERO_USER_ID if s == hero_seat else 100 + s),
                    "userName": ("hero" if s == hero_seat else f"v{s}"),
                    "userChips": 10000.0,
                    "betAmout": 0.0,
                    "isPlaying": True,
                }
                for s in active_seats
            ],
        }))
        builder.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "KING"}],
        }))

    def test_four_handed_position_rotates(self):
        """
        Hero at seat 5, 4-handed table [1,2,3,5], dealer rotates 1→2→3→5.
        Hero's position should walk through all four labels.

        _POSITION_NAMES[4] = ["BB", "UTG", "BTN", "SB"], indexed clockwise
        from BB. Walking ordered seats [1,2,3,5] clockwise from BB:
            dealer=1 → SB=2, BB=3 → UTG=5, BTN=1 → hero(5)=UTG
            dealer=2 → SB=3, BB=5 → UTG=1, BTN=2 → hero(5)=BB
            dealer=3 → SB=5, BB=1 → UTG=2, BTN=3 → hero(5)=SB
            dealer=5 → SB=1, BB=2 → UTG=3, BTN=5 → hero(5)=BTN
        """
        b = CoinPokerStateBuilder(HERO_USER_ID)
        active = [1, 2, 3, 5]
        self._start_hand(b, "H1", dealer_seat=1, active_seats=active, hero_seat=5)
        self.assertEqual(b.snapshot()["position"], "UTG")
        self._start_hand(b, "H2", dealer_seat=2, active_seats=active, hero_seat=5)
        self.assertEqual(b.snapshot()["position"], "BB")
        self._start_hand(b, "H3", dealer_seat=3, active_seats=active, hero_seat=5)
        self.assertEqual(b.snapshot()["position"], "SB")
        self._start_hand(b, "H4", dealer_seat=5, active_seats=active, hero_seat=5)
        self.assertEqual(b.snapshot()["position"], "BTN")

    def test_six_handed_no_alldata(self):
        b = CoinPokerStateBuilder(HERO_USER_ID)
        active = [1, 2, 3, 4, 5, 6]
        # _POSITION_NAMES[6] = ["BB", "UTG", "MP", "CO", "BTN", "SB"]
        # dealer=1: SB=2, BB=3 → hero(5) at offset (3→5 = 2) → MP
        self._start_hand(b, "H1", dealer_seat=1, active_seats=active, hero_seat=5)
        self.assertEqual(b.snapshot()["position"], "MP")
        # dealer=4: SB=5, BB=6 → hero(5) at offset (6→5 = 5 wrap) → SB
        self._start_hand(b, "H2", dealer_seat=4, active_seats=active, hero_seat=5)
        self.assertEqual(b.snapshot()["position"], "SB")

    def test_bb_seat_resets_between_hands(self):
        """
        Defensive: even if game_alldata DID set bb_seat for hand 1, when
        the next hand starts (pre_hand_start_info reset), bb_seat must
        clear so the new hand re-derives from the new dealer position.
        """
        b = CoinPokerStateBuilder(HERO_USER_ID)
        active = [1, 2, 3, 4, 5, 6]
        # Hand 1 with explicit bb_seat
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H1", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": s, "userId": (HERO_USER_ID if s == 5 else 100 + s),
                 "userName": ("hero" if s == 5 else f"v{s}"),
                 "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True}
                for s in active
            ],
        }))
        b.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 2, "bigBlindSeatId": 3,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))
        self.assertEqual(b.bb_seat, 3)
        # Hand 2 — pre_hand_start_info should clear bb_seat so it's
        # re-derived from the NEW dealer (no game_alldata this hand)
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H2", "dealerSeatId": 2,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        self.assertIsNone(b.bb_seat)
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H2",
            "seatResponseDataList": [
                {"seatId": s, "userId": (HERO_USER_ID if s == 5 else 100 + s),
                 "userName": ("hero" if s == 5 else f"v{s}"),
                 "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True}
                for s in active
            ],
        }))
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "KING"}],
        }))
        # dealer=2 → SB=3, BB=4 → hero(5) offset (4→5=1) → UTG
        self.assertEqual(b.snapshot()["position"], "UTG")


# ── snapshot smoke tests ──────────────────────────────────────────────────────

class TestBuilderEmpty(unittest.TestCase):
    def test_no_state_returns_none(self):
        b = CoinPokerStateBuilder(HERO_USER_ID)
        self.assertIsNone(b.snapshot())

    def test_ignores_unknown_cmd(self):
        b = CoinPokerStateBuilder(HERO_USER_ID)
        b.ingest({"cmd_bean": {"Cmd": "game.unknown_xyz", "BeanData": "{}"}})
        self.assertIsNone(b.snapshot())

    def test_ignores_malformed_bean(self):
        b = CoinPokerStateBuilder(HERO_USER_ID)
        b.ingest({"cmd_bean": {"Cmd": "game.game_alldata", "BeanData": "not json"}})
        self.assertIsNone(b.snapshot())


# ── live fixture replay ───────────────────────────────────────────────────────

class TestFixtureReplay(unittest.TestCase):
    """
    Walk every frame in the captured session and check invariants.
    These verify the adapter never crashes and converges to sane states.
    """

    def setUp(self):
        self.frames = load_fixture()
        self.builder = CoinPokerStateBuilder(HERO_USER_ID)

    def test_replay_does_not_crash(self):
        for f in self.frames:
            self.builder.ingest(f)

    def test_hero_seat_identified(self):
        # Hero must be recognized at least once during the session.
        seen = False
        for f in self.frames:
            self.builder.ingest(f)
            if self.builder.hero_seat is not None:
                seen = True
                break
        self.assertTrue(seen, "hero seat never identified from fixture")

    def test_hole_cards_captured(self):
        # We expect at least one game.hole_cards in the fixture, and it
        # must produce a 2-card hero hand.
        hand_ids_with_cards: set[str] = set()
        for f in self.frames:
            self.builder.ingest(f)
            if len(self.builder.hero_cards) == 2 and self.builder.hand_id:
                hand_ids_with_cards.add(self.builder.hand_id)
        self.assertGreater(len(hand_ids_with_cards), 0,
                           "no hero hole cards in fixture")

    def test_board_progression(self):
        # At some point in the fixture the board should reach a length of
        # 3+ (flop dealt). We don't require turn/river — depends on which
        # hands made it that far.
        max_board = 0
        for f in self.frames:
            self.builder.ingest(f)
            max_board = max(max_board, len(self.builder.board))
        self.assertGreaterEqual(max_board, 3,
                                "board never progressed past preflop in fixture")

    def test_phase_transitions(self):
        # Phase should reach FLOP at least once.
        phases_seen: set[str] = set()
        for f in self.frames:
            self.builder.ingest(f)
            phases_seen.add(self.builder.phase)
        self.assertIn("PREFLOP", phases_seen)
        self.assertIn("FLOP", phases_seen)

    def test_snapshot_shape(self):
        # After replay, the snapshot must have all required keys with
        # the right types — this is the contract with AdvisorStateMachine.
        for f in self.frames:
            self.builder.ingest(f)
        snap = self.builder.snapshot()
        self.assertIsNotNone(snap, "no snapshot after full replay")
        required = [
            ("hero_cards",    list),
            ("board_cards",   list),
            ("hand_id",       str),
            ("facing_bet",    bool),
            ("call_amount",   int),
            ("phase",         str),
            ("num_opponents", int),
            ("pot",           int),
            ("hero_stack",    int),
            ("position",      str),
            ("bets",          list),
            ("hero_seat",     int),
            ("players",       list),
            ("hero_turn",     bool),
        ]
        for key, typ in required:
            self.assertIn(key, snap, f"missing key {key}")
            self.assertIsInstance(snap[key], typ, f"{key} not {typ.__name__}")
        self.assertIn(snap["position"], {"UTG", "EP", "MP", "CO", "BTN", "SB", "BB"})
        self.assertIn(snap["phase"], {"PREFLOP", "FLOP", "TURN", "RIVER"})

    def test_pot_resets_on_new_hand(self):
        # When the hand_id changes, the pot must reset to a value smaller
        # than the previous hand's peak (typically the new hand's antes /
        # blinds). Pot is NOT strictly monotonic mid-hand because uncalled
        # all-in chips can be returned via game.return_chips at hand end —
        # legitimate poker logic, not an adapter bug.
        cur_hand = None
        peak_pot = 0
        boundary_drops = 0
        for f in self.frames:
            self.builder.ingest(f)
            if self.builder.hand_id != cur_hand:
                if cur_hand is not None and self.builder.pot < peak_pot:
                    boundary_drops += 1
                cur_hand = self.builder.hand_id
                peak_pot = self.builder.pot
            else:
                peak_pot = max(peak_pot, self.builder.pot)
        # We should have crossed at least one hand boundary AND at least
        # one of those boundaries should have dropped the pot below the
        # previous hand's peak.
        self.assertGreater(boundary_drops, 0,
                           "pot never reset across a hand boundary")


class TestTurnTracking(unittest.TestCase):
    """
    Regression for the AcQh bouncing-rec bug. After hero acts, the
    snapshot must report ``hero_turn=False`` until the server's next
    ``game.user_turn`` arrives, even though the action sequence is still
    in PREFLOP. Without this, the runner re-invokes the advisor with a
    facing-flip-to-False state and emits a bogus open-raise size.
    """

    def _wrap(self, cmd, bean):
        return {"cmd_bean": {"Cmd": cmd, "BeanData": json.dumps(bean), "RoomName": "T"}}

    def _seed_six_handed(self, builder, hero_seat=5):
        builder.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H1", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 16.0,
        }))
        builder.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": s, "userId": (HERO_USER_ID if s == hero_seat else 100 + s),
                 "userName": ("hero" if s == hero_seat else f"v{s}"),
                 "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True}
                for s in range(1, 7)
            ],
        }))
        builder.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 2, "bigBlindSeatId": 3,
                "smallBlind": 50.0, "bigBlind": 100.0, "ante": 16.0,
                "whoseTurnSeatId": 4,
            },
        }))

    def test_hero_turn_clears_after_hero_acts(self):
        b = CoinPokerStateBuilder(HERO_USER_ID)
        self._seed_six_handed(b)
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "QUEEN"}],
        }))
        # Server says it's hero's turn (seat 5)
        b.ingest(self._wrap("game.user_turn", {
            "whoseTurn": "hero", "roundMaxBet": 100.0,
        }))
        snap = b.snapshot()
        self.assertTrue(snap["hero_turn"])
        # Hero acts: raises to 250
        b.ingest(self._wrap("game.seat", {
            "seatId": 5, "userName": "hero",
            "userChips": 9750.0, "betAmout": 250.0,
            "newCaption": "Raise",
        }))
        snap = b.snapshot()
        self.assertFalse(snap["hero_turn"],
                         "hero_turn should clear after hero raises")

    def test_hero_turn_clears_after_villain_acts(self):
        # Same mechanism — any seat acting passes the action.
        b = CoinPokerStateBuilder(HERO_USER_ID)
        self._seed_six_handed(b)
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "DIAMONDS", "value": "EIGHT"},
                          {"suit": "DIAMONDS", "value": "SEVEN"}],
        }))
        b.ingest(self._wrap("game.user_turn", {
            "whoseTurn": "v6", "roundMaxBet": 100.0,
        }))
        # whose_turn_seat is now seat 6 (villain), not hero
        self.assertFalse(b.snapshot()["hero_turn"])
        # Villain raises
        b.ingest(self._wrap("game.seat", {
            "seatId": 6, "userName": "v6",
            "userChips": 9750.0, "betAmout": 250.0,
            "newCaption": "Raise",
        }))
        # whose_turn_seat should clear; next user_turn will set it
        snap = b.snapshot()
        self.assertFalse(snap["hero_turn"])
        self.assertIsNone(b.whose_turn_seat)
        self.assertTrue(snap["facing_bet"])
        self.assertEqual(snap["call_amount"], 25000)  # 250 chips

    def test_acqh_3bet_bounce_does_not_recur(self):
        """
        Replays the exact event order from live hand 2467310103
        (AcQh in SB facing a 3-bet → 4-bet) and asserts that hero_turn
        is True for *exactly* the three real decision points, not the
        intermediate frames where hero just acted.
        """
        b = CoinPokerStateBuilder(HERO_USER_ID)
        # 6-handed table, hero seat 5, BB seat 3
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H1", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": 2, "userId": 102, "userName": "v2", "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 3, "userId": 103, "userName": "v3", "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 4, "userId": 104, "userName": "v4", "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 5, "userId": HERO_USER_ID, "userName": "hero", "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 6, "userId": 106, "userName": "v6", "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
            ],
        }))
        b.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 2, "bigBlindSeatId": 3,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "QUEEN"}],
        }))

        # Helper to record hero_turn after each ingest
        hero_turn_history: list[tuple[str, bool, bool, int]] = []
        def step(cmd, bean, label):
            b.ingest(self._wrap(cmd, bean))
            snap = b.snapshot()
            if snap and snap["hero_cards"]:
                hero_turn_history.append((label, snap["hero_turn"],
                                          snap["facing_bet"], snap["call_amount"]))

        # Hero turn 1 — open spot, no raise yet (autoBB so already 100 in)
        # Set hero's bet to 100 to mimic having posted BB
        b.ingest(self._wrap("game.seat", {
            "seatId": 5, "userName": "hero", "userChips": 9900.0,
            "betAmout": 100.0, "newCaption": "AutoBB",
        }))
        step("game.user_turn", {"whoseTurn": "hero", "roundMaxBet": 100.0}, "hero_turn1")

        # Hero acts: checks (still 100 invested) → action passes
        step("game.seat", {
            "seatId": 5, "userName": "hero", "userChips": 9900.0,
            "betAmout": 100.0, "newCaption": "Check",
        }, "after_hero_check")

        # Villain (seat 6) raises to 2460
        step("game.seat", {
            "seatId": 6, "userName": "v6", "userChips": 7540.0,
            "betAmout": 2460.0, "newCaption": "Raise",
        }, "v6_raises")
        step("game.user_turn", {"whoseTurn": "v2", "roundMaxBet": 0.0}, "v2_to_act")

        # v2 folds, v3 folds
        step("game.seat", {"seatId": 2, "userName": "v2", "betAmout": 50.0, "newCaption": "Fold"}, "v2_folds")
        step("game.seat", {"seatId": 3, "userName": "v3", "betAmout": 100.0, "newCaption": "Fold"}, "v3_folds")

        # Hero turn 2 — facing the 2460 raise
        step("game.user_turn", {"whoseTurn": "hero", "roundMaxBet": 2460.0}, "hero_turn2")

        # Hero 4-bets to 4820
        step("game.seat", {
            "seatId": 5, "userName": "hero", "userChips": 5180.0,
            "betAmout": 4820.0, "newCaption": "Raise",
        }, "after_hero_4bet")

        # v6 5-bets to 7180
        step("game.user_turn", {"whoseTurn": "v6", "roundMaxBet": 0.0}, "v6_to_act")
        step("game.seat", {
            "seatId": 6, "userName": "v6", "userChips": 2820.0,
            "betAmout": 7180.0, "newCaption": "Raise",
        }, "v6_5bets")

        # Hero turn 3 — facing the 7180 raise
        step("game.user_turn", {"whoseTurn": "hero", "roundMaxBet": 7180.0}, "hero_turn3")

        # Verify: hero_turn=True at exactly the three "hero_turnN" labels
        # and False everywhere else. THIS is the regression — before the
        # fix, "after_hero_check" and "after_hero_4bet" leaked hero_turn=True.
        true_labels = [lbl for lbl, ht, _, _ in hero_turn_history if ht]
        self.assertEqual(
            true_labels, ["hero_turn1", "hero_turn2", "hero_turn3"],
            f"hero_turn fired at wrong frames: {true_labels}",
        )

        # And at hero_turn2 / hero_turn3, facing_bet must be True with the
        # correct call amount.
        h2 = next(e for e in hero_turn_history if e[0] == "hero_turn2")
        h3 = next(e for e in hero_turn_history if e[0] == "hero_turn3")
        # hero_bet=100 (BB), max bet 2460 → call 2360 chips → 236000 cents
        self.assertTrue(h2[2])
        self.assertEqual(h2[3], 236000)
        # hero_bet=4820, max bet 7180 → call 2360 chips → 236000 cents
        self.assertTrue(h3[2])
        self.assertEqual(h3[3], 236000)

    def test_user_turn_zero_rmb_does_not_clobber_known_max(self):
        # Server quirk: ``roundMaxBet=0.0`` arrives in user_turn events
        # even when there's a real bet to call. The adapter must keep
        # the value it learned from seat updates.
        b = CoinPokerStateBuilder(HERO_USER_ID)
        self._seed_six_handed(b)
        b.ingest(self._wrap("game.seat", {
            "seatId": 6, "userName": "v6", "userChips": 9750.0,
            "betAmout": 250.0, "newCaption": "Raise",
        }))
        self.assertEqual(b.round_max_bet, 25000)
        # Spurious zero
        b.ingest(self._wrap("game.user_turn", {
            "whoseTurn": "v2", "roundMaxBet": 0.0,
        }))
        self.assertEqual(b.round_max_bet, 25000,
                         "spurious user_turn rmb=0 must not clobber known max")
        # Legit positive value updates as usual
        b.ingest(self._wrap("game.user_turn", {
            "whoseTurn": "hero", "roundMaxBet": 250.0,
        }))
        self.assertEqual(b.round_max_bet, 25000)


class TestSyntheticFlow(unittest.TestCase):
    """End-to-end synthetic frames so we don't depend solely on the fixture."""

    def _wrap(self, cmd: str, bean: dict) -> dict:
        return {
            "cmd_bean": {
                "Cmd": cmd,
                "BeanData": json.dumps(bean),
                "RoomName": "TEST",
            },
            "room_name": "TEST",
        }

    def test_preflop_bb_with_hole_cards(self):
        b = CoinPokerStateBuilder(hero_user_id=42)

        # New hand starts
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H1",
            "dealerSeatId": 4,
            "bbAmount": 100.0,
            "sbAmount": 50.0,
            "anteAmount": 0.0,
        }))
        # Seat info: 6-max, hero is the BB on seat 1
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H1",
            "isGameStarted": True,
            "seatResponseDataList": [
                {"seatId": 1, "userId": 42, "userName": "hero",   "userChips": 100.0, "betAmout": 1.0,  "isPlaying": True,  "lastAction": ""},
                {"seatId": 2, "userId": 11, "userName": "villA",  "userChips": 100.0, "betAmout": 0.0,  "isPlaying": True,  "lastAction": ""},
                {"seatId": 3, "userId": 12, "userName": "villB",  "userChips": 100.0, "betAmout": 0.0,  "isPlaying": True,  "lastAction": ""},
                {"seatId": 4, "userId": 13, "userName": "villC",  "userChips": 100.0, "betAmout": 0.0,  "isPlaying": True,  "lastAction": ""},
                {"seatId": 5, "userId": 14, "userName": "villD",  "userChips": 100.0, "betAmout": 0.5,  "isPlaying": True,  "lastAction": ""},
                {"seatId": 6, "userId": 15, "userName": "villE",  "userChips": 100.0, "betAmout": 0.0,  "isPlaying": True,  "lastAction": ""},
            ],
        }))
        # game_alldata sets blind seats
        b.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 4, "smallBlindSeatId": 5, "bigBlindSeatId": 1,
                "smallBlind": 50.0, "bigBlind": 100.0, "ante": 0.0,
                "whoseTurnSeatId": 2,
                "dealerCards": {"FLOP": None, "TURN": None, "RIVER": None},
            },
            "seatInfoRsponseData": {"seatResponseDataList": []},
            "potInfoResponseData": {"totalPotAmount": 1.5, "roundName": "PREFLOP"},
        }))
        # Hero hole cards
        b.ingest(self._wrap("game.hole_cards", {
            "gameHandId": "H1",
            "holeCards": [{"suit": "HEARTS", "value": "ACE"},
                          {"suit": "SPADES", "value": "KING"}],
        }))

        snap = b.snapshot()
        self.assertEqual(snap["hand_id"], "H1")
        self.assertEqual(snap["hero_cards"], ["Ah", "Ks"])
        self.assertEqual(snap["hero_seat"], 1)
        self.assertEqual(snap["position"], "BB")
        self.assertEqual(snap["phase"], "PREFLOP")
        self.assertEqual(snap["num_opponents"], 5)
        # Pot was 1.5 chips → 150 cents
        self.assertEqual(snap["pot"], 150)
        # Hero bet 1.0 (BB), max other bet 0.5 (SB) → not facing
        self.assertFalse(snap["facing_bet"])
        self.assertEqual(snap["call_amount"], 0)

    def test_flop_dealer_cards_advances_phase(self):
        b = CoinPokerStateBuilder(hero_user_id=42)
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H2", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H2",
            "seatResponseDataList": [
                {"seatId": 1, "userId": 42, "userName": "hero",  "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 2, "userId": 11, "userName": "vill",  "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
            ],
        }))
        b.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H2",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 1, "bigBlindSeatId": 2,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "HEARTS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "KING"}],
        }))
        b.ingest(self._wrap("game.dealer_cards", {
            "gameHandId": "H2",
            "dealerCards": {
                "FLOP": [
                    {"suit": "HEARTS", "value": "QUEEN"},
                    {"suit": "HEARTS", "value": "JACK"},
                    {"suit": "DIAMONDS", "value": "TWO"},
                ],
                "TURN": None, "RIVER": None,
            },
        }))
        b.ingest(self._wrap("game.potInfo", {"totalPotAmount": 3.0, "roundName": "FLOP"}))

        snap = b.snapshot()
        self.assertEqual(snap["phase"], "FLOP")
        self.assertEqual(snap["board_cards"], ["Qh", "Jh", "2d"])
        self.assertEqual(snap["pot"], 300)

    def test_facing_a_raise_call_amount(self):
        b = CoinPokerStateBuilder(hero_user_id=42)
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H3", "dealerSeatId": 3,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H3",
            "seatResponseDataList": [
                {"seatId": 1, "userId": 42, "userName": "hero",  "userChips": 100.0, "betAmout": 1.0,  "isPlaying": True},
                {"seatId": 2, "userId": 11, "userName": "raiser","userChips": 100.0, "betAmout": 3.5,  "isPlaying": True},
                {"seatId": 3, "userId": 12, "userName": "btn",   "userChips": 100.0, "betAmout": 0.5,  "isPlaying": True},
            ],
        }))
        b.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H3",
            "gameInitResponseData": {
                "dealerSeatId": 3, "smallBlindSeatId": 3, "bigBlindSeatId": 1,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "JACK"},
                          {"suit": "CLUBS", "value": "JACK"}],  # JJ
        }))
        # Server says round max bet is 3.5
        b.ingest(self._wrap("game.user_turn", {
            "whoseTurn": "hero", "roundMaxBet": 3.5, "totalPot": 5.0,
        }))

        snap = b.snapshot()
        self.assertTrue(snap["facing_bet"])
        # Hero has 1.0 in already, max bet 3.5 → call 2.5 chips → 250 cents
        self.assertEqual(snap["call_amount"], 250)
        self.assertTrue(snap["hero_turn"])
        self.assertEqual(snap["position"], "BB")

    def test_reset_clears_hero_cards(self):
        b = CoinPokerStateBuilder(hero_user_id=42)
        b.ingest(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H4", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0,
        }))
        b.ingest(self._wrap("game.seatInfo", {
            "gameHandId": "H4",
            "seatResponseDataList": [
                {"seatId": 1, "userId": 42, "userName": "hero", "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 2, "userId": 11, "userName": "v",    "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
            ],
        }))
        b.ingest(self._wrap("game.game_alldata", {
            "gameHandId": "H4",
            "gameInitResponseData": {"dealerSeatId": 1, "smallBlindSeatId": 1, "bigBlindSeatId": 2,
                                     "smallBlind": 50.0, "bigBlind": 100.0},
        }))
        b.ingest(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "DIAMONDS", "value": "TEN"},
                          {"suit": "DIAMONDS", "value": "NINE"}],
        }))
        self.assertEqual(b.hero_cards, ["Td", "9d"])
        b.ingest(self._wrap("game.reset_data", {"gameHandId": "H4"}))
        self.assertEqual(b.hero_cards, [])
        self.assertEqual(b.board, [])
        self.assertEqual(b.phase, "PREFLOP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
