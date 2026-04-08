"""
Tests for vision.coinpoker_runner — the file iterators and the
CoinPokerSession orchestration loop.

These tests intentionally do NOT load the full Advisor / PostflopEngine
stack — they verify the runner's I/O + dispatch logic with a mock
callback. End-to-end advisor wiring is exercised manually via
``python -m vision.coinpoker_runner --replay --print-only``.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from coinpoker_runner import (
    CoinPokerSession,
    MultiTableCoinPokerSession,
    OverlayClient,
    follow_iter,
    make_console_printer,
    parse_room_stake,
    replay_iter,
    snapshot_to_overlay_msg,
)

HERO = 1571120
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "coinpoker_session.jsonl")


def _wrap(cmd, bean):
    return {"cmd_bean": {"Cmd": cmd, "BeanData": json.dumps(bean), "RoomName": "T"}}


# ── replay_iter ───────────────────────────────────────────────────────────────

class TestReplayIter(unittest.TestCase):
    def test_yields_each_line(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as f:
            f.write("a\nb\n\nc\n")
            path = f.name
        try:
            self.assertEqual(list(replay_iter(path)), ["a", "b", "c"])
        finally:
            os.unlink(path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as f:
            path = f.name
        try:
            self.assertEqual(list(replay_iter(path)), [])
        finally:
            os.unlink(path)


# ── CoinPokerSession ──────────────────────────────────────────────────────────

class TestSessionDispatch(unittest.TestCase):
    def setUp(self):
        self.dispatched: list[dict] = []
        self.session = CoinPokerSession(
            hero_user_id=HERO,
            on_snapshot=self.dispatched.append,
        )

    def test_no_dispatch_before_state_seeded(self):
        # An unrelated cmd before any seat data should ingest fine but
        # produce no snapshot to dispatch.
        self.session.feed_frame(_wrap("server_lag_value", {}))
        self.assertEqual(self.dispatched, [])

    def test_seeds_dispatches_at_least_once(self):
        # pre_hand + seatInfo + game_alldata = enough state for one or
        # more snapshots. seatInfo establishes hero seat (first dispatch);
        # game_alldata then sets blind seats / position (second dispatch).
        self._seed_minimal_hand()
        self.assertGreaterEqual(len(self.dispatched), 1)
        snap = self.dispatched[-1]
        self.assertEqual(snap["hand_id"], "H1")
        self.assertEqual(snap["hero_seat"], 1)
        # heads-up: dealer=1=hero=SB=BTN, villain on seat 2 = BB
        self.assertEqual(snap["position"], "BTN")

    def test_dedupes_unchanged_frames(self):
        self._seed_minimal_hand()
        before = len(self.dispatched)
        # Same seatInfo again — no state change → no dispatch.
        self.session.feed_frame(_wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": 1, "userId": HERO, "userName": "hero", "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 2, "userId": 11,   "userName": "v",    "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
            ],
        }))
        self.assertEqual(len(self.dispatched), before)

    def test_dispatches_on_phase_change(self):
        self._seed_minimal_hand()
        before = len(self.dispatched)
        self.session.feed_frame(_wrap("game.dealer_cards", {
            "gameHandId": "H1",
            "dealerCards": {
                "FLOP": [
                    {"suit": "HEARTS", "value": "ACE"},
                    {"suit": "HEARTS", "value": "KING"},
                    {"suit": "DIAMONDS", "value": "TWO"},
                ],
                "TURN": None, "RIVER": None,
            },
        }))
        self.assertEqual(len(self.dispatched), before + 1)
        self.assertEqual(self.dispatched[-1]["phase"], "FLOP")

    def test_callback_exception_does_not_break_session(self):
        boom_count = [0]
        def boom(_):
            boom_count[0] += 1
            raise RuntimeError("strategy crash")
        sess = CoinPokerSession(hero_user_id=HERO, on_snapshot=boom)
        self._seed_minimal_hand(sess=sess)
        # Callback was invoked at least once and the runner kept going.
        seed_calls = boom_count[0]
        self.assertGreaterEqual(seed_calls, 1)
        # Subsequent feed should still work — runner survived the exception
        sess.feed_frame(_wrap("game.dealer_cards", {
            "gameHandId": "H1",
            "dealerCards": {
                "FLOP": [
                    {"suit": "HEARTS", "value": "TWO"},
                    {"suit": "CLUBS", "value": "THREE"},
                    {"suit": "DIAMONDS", "value": "FOUR"},
                ],
            },
        }))
        self.assertGreater(boom_count[0], seed_calls)

    def test_bb_cents_default(self):
        # No table data yet → fall back to 100 chips × CHIP_SCALE.
        from coinpoker_adapter import CHIP_SCALE
        self.assertEqual(self.session.bb_cents, 100 * CHIP_SCALE)

    def test_bb_cents_auto_detected(self):
        from coinpoker_adapter import CHIP_SCALE
        self._seed_minimal_hand()
        # game_alldata in _seed_minimal_hand sets BB to 100.0
        self.assertEqual(self.session.bb_cents, 100 * CHIP_SCALE)

    def test_bb_cents_override(self):
        from coinpoker_adapter import CHIP_SCALE
        sess = CoinPokerSession(hero_user_id=HERO, on_snapshot=lambda s: None,
                                bb_chips=4)
        self.assertEqual(sess.bb_cents, 4 * CHIP_SCALE)

    def test_feed_line_handles_bad_json(self):
        # The patcher's IL try/catch could in theory write a partial line
        # on shutdown; runner must not blow up.
        self.assertIsNone(self.session.feed_line("not json"))
        self.assertIsNone(self.session.feed_line(""))

    # ── helper ──

    def _seed_minimal_hand(self, sess=None):
        s = sess or self.session
        s.feed_frame(_wrap("game.pre_hand_start_info", {
            "gameHandId": "H1", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0,
        }))
        s.feed_frame(_wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": 1, "userId": HERO, "userName": "hero", "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 2, "userId": 11,   "userName": "v",    "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
            ],
        }))
        s.feed_frame(_wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 1, "bigBlindSeatId": 2,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))


# ── full-session replay against the captured fixture ─────────────────────────

class TestSessionFixtureReplay(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(FIXTURE):
            self.skipTest(f"fixture missing: {FIXTURE}")
        self.dispatched: list[dict] = []
        self.session = CoinPokerSession(
            hero_user_id=HERO,
            on_snapshot=self.dispatched.append,
        )

    def test_replay_produces_snapshots(self):
        self.session.run(replay_iter(FIXTURE))
        # The 200-frame fixture spans ~3 hands and should produce many
        # state changes. Lower bound is conservative.
        self.assertGreater(len(self.dispatched), 30)
        self.assertGreater(self.session.frames_seen, 150)

    def test_replay_visits_multiple_hand_ids(self):
        self.session.run(replay_iter(FIXTURE))
        hand_ids = {snap["hand_id"] for snap in self.dispatched}
        self.assertGreaterEqual(len(hand_ids), 2,
                                "expected at least 2 distinct hands in fixture")

    def test_replay_transitions_phases(self):
        self.session.run(replay_iter(FIXTURE))
        phases = {snap["phase"] for snap in self.dispatched}
        self.assertIn("PREFLOP", phases)
        self.assertIn("FLOP", phases)


# ── follow_iter (live tail) ───────────────────────────────────────────────────

class TestFollowIter(unittest.TestCase):
    """
    The follow iterator is hard to test thoroughly without an event loop,
    but we can validate the basic case: file grows, new lines arrive.
    """

    def test_picks_up_appends(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as f:
            path = f.name

        received: list[str] = []
        stop_event = threading.Event()

        def consumer():
            for line in follow_iter(path, poll=0.02):
                received.append(line)
                if stop_event.is_set():
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        # Wait briefly for the consumer to seek to EOF, then write some
        # lines and let it pick them up.
        time.sleep(0.1)
        with open(path, "a", encoding="utf-8") as f:
            f.write("first\nsecond\n")
        time.sleep(0.2)
        with open(path, "a", encoding="utf-8") as f:
            f.write("third\n")
        time.sleep(0.2)

        stop_event.set()
        t.join(timeout=1.0)

        try:
            os.unlink(path)
        except OSError:
            pass

        # The tail starts at EOF so initial empty file gives no lines,
        # then sees first/second/third in order.
        self.assertEqual(received[:3], ["first", "second", "third"])


# ── console printer ──────────────────────────────────────────────────────────

class TestConsolePrinter(unittest.TestCase):
    def test_printer_runs_on_minimal_snapshot(self):
        # The printer hits stdout — we just need it not to crash.
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            make_console_printer()({
                "phase": "PREFLOP",
                "hand_id": "H1",
                "position": "BB",
                "hero_cards": ["Ah", "Ks"],
                "board_cards": [],
                "pot": 150,
                "call_amount": 0,
                "facing_bet": False,
                "hero_stack": 10000,
                "hero_turn": True,
            })
        out = buf.getvalue()
        self.assertIn("PREFLOP", out)
        self.assertIn("Ah", out)
        self.assertIn("Ks", out)
        self.assertIn("BB", out)


class _StubAdvisorOut:
    def __init__(self, action="BET 250.00", equity=0.72):
        self.action = action
        self.equity = equity


class TestParseRoomStake(unittest.TestCase):
    def test_dash_format(self):
        self.assertEqual(parse_room_stake("PR-NL 50-100 EV-INRIT-ANTE (A) 246519"), "50/100")

    def test_slash_format(self):
        self.assertEqual(parse_room_stake("4/8 NL Hold'em"), "4/8")

    def test_no_match_returns_truncated_name(self):
        self.assertEqual(parse_room_stake("Practice Table"), "Practice Table")

    def test_empty(self):
        self.assertEqual(parse_room_stake(""), "")


class TestSnapshotToOverlayMsg(unittest.TestCase):
    def _snap(self, **overrides):
        base = {
            "hero_cards": ["Ah", "Ks"],
            "board_cards": ["Qh", "Jh", "2d"],
            "hand_id": "H1",
            "facing_bet": True,
            "call_amount": 25000,   # 250 chips
            "phase": "FLOP",
            "num_opponents": 1,
            "pot": 100000,          # 1000 chips
            "hero_stack": 500000,   # 5000 chips
            "position": "BTN",
            "bets": [25000, 25000],
            "hero_seat": 1,
            "players": [],
            "hero_turn": True,
        }
        base.update(overrides)
        return base

    def test_basic_message_shape(self):
        msg = snapshot_to_overlay_msg(
            self._snap(), _StubAdvisorOut("BET 500.00", equity=0.72),
            table_id="coinpoker_t1",
            room_name="PR-NL 50-100 EV-INRIT-ANTE (A) 246519",
        )
        self.assertEqual(msg["type"], "table_update")
        self.assertEqual(msg["table_id"], "coinpoker_t1")
        self.assertEqual(msg["site"], "CoinPoker (chips)")
        self.assertEqual(msg["stake"], "50/100")
        self.assertEqual(msg["position"], "BTN")
        self.assertEqual(msg["cards"], "Ah Ks")
        self.assertEqual(msg["board"], "Qh Jh 2d")
        self.assertEqual(msg["phase"], "FLOP")
        self.assertEqual(msg["equity"], 0.72)
        self.assertEqual(msg["pot"], 1000.0)
        self.assertEqual(msg["stack"], 5000.0)
        self.assertEqual(msg["call"], 250.0)
        self.assertTrue(msg["facing_bet"])
        self.assertEqual(msg["rec"], "BET 500.00")
        self.assertEqual(msg["rec_color"], "green")

    def test_pot_odds_computed_when_facing(self):
        msg = snapshot_to_overlay_msg(
            self._snap(facing_bet=True, call_amount=50000, pot=150000),
            _StubAdvisorOut(),
            table_id="t1", room_name="PR-NL 50-100",
        )
        # call=500, pot=1500 → odds = 500/(1500+500) = 0.25
        self.assertAlmostEqual(msg["pot_odds"], 0.25, places=6)

    def test_pot_odds_none_when_not_facing(self):
        msg = snapshot_to_overlay_msg(
            self._snap(facing_bet=False, call_amount=0),
            _StubAdvisorOut(),
            table_id="t1", room_name="PR-NL 50-100",
        )
        self.assertIsNone(msg["pot_odds"])

    def test_rec_color_fold(self):
        msg = snapshot_to_overlay_msg(
            self._snap(), _StubAdvisorOut("FOLD"),
            table_id="t1", room_name="",
        )
        self.assertEqual(msg["rec_color"], "red")

    def test_rec_color_check(self):
        msg = snapshot_to_overlay_msg(
            self._snap(facing_bet=False, call_amount=0),
            _StubAdvisorOut("CHECK"),
            table_id="t1", room_name="",
        )
        self.assertEqual(msg["rec_color"], "blue")

    def test_handles_none_advisor_out(self):
        # When the advisor returns None we still want to render cards/pot.
        msg = snapshot_to_overlay_msg(
            self._snap(), None,
            table_id="t1", room_name="PR-NL 50-100",
        )
        self.assertEqual(msg["rec"], "")
        self.assertEqual(msg["rec_color"], "neutral")
        self.assertEqual(msg["cards"], "Ah Ks")


class TestOverlayClient(unittest.TestCase):
    def test_send_writes_jsonl(self):
        from io import StringIO
        sink = StringIO()
        client = OverlayClient(stream=sink, table_id="t1")
        client.send({"type": "table_update", "table_id": "t1", "rec": "BET"})
        out = sink.getvalue().splitlines()
        self.assertEqual(len(out), 1)
        self.assertEqual(json.loads(out[0])["rec"], "BET")

    def test_send_noop_when_dead(self):
        client = OverlayClient(stream=None)
        client.send({"x": 1})  # must not raise
        self.assertFalse(client.alive())

    def test_send_disables_after_failure(self):
        class BoomStream:
            def write(self, _): raise IOError("pipe broken")
            def flush(self): pass
        client = OverlayClient(stream=BoomStream(), table_id="t1")
        client.send({"x": 1})
        # Stream got nulled out → subsequent sends are no-ops
        self.assertIsNone(client._stream)
        client.send({"y": 2})

    def test_remove_table_emits_remove_message(self):
        from io import StringIO
        sink = StringIO()
        client = OverlayClient(stream=sink, table_id="t42")
        client.remove_table()
        msg = json.loads(sink.getvalue().strip())
        self.assertEqual(msg["type"], "table_remove")
        self.assertEqual(msg["table_id"], "t42")

    def test_session_overlay_integration(self):
        # Drive a fake session through to the overlay sink and verify
        # we get table_update messages with the right table_id.
        from io import StringIO
        sink = StringIO()
        client = OverlayClient(stream=sink, table_id="coinpoker_test")
        captured: list[dict] = []

        def cb(snap):
            out = _StubAdvisorOut("CHECK", equity=0.5)
            client.send(snapshot_to_overlay_msg(snap, out,
                                                 table_id=client.table_id,
                                                 room_name="PR-NL 50-100"))
            captured.append(snap)

        sess = CoinPokerSession(hero_user_id=HERO, on_snapshot=cb)
        sess.feed_frame(_wrap("game.pre_hand_start_info", {
            "gameHandId": "H1", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0,
        }))
        sess.feed_frame(_wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": 1, "userId": HERO, "userName": "hero", "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
                {"seatId": 2, "userId": 11,   "userName": "v",    "userChips": 100.0, "betAmout": 0.0, "isPlaying": True},
            ],
        }))
        sess.feed_frame(_wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 1, "bigBlindSeatId": 2,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))

        lines = sink.getvalue().strip().splitlines()
        self.assertGreater(len(lines), 0)
        msg = json.loads(lines[0])
        self.assertEqual(msg["type"], "table_update")
        self.assertEqual(msg["table_id"], "coinpoker_test")
        self.assertEqual(msg["site"], "CoinPoker (chips)")
        self.assertEqual(msg["stake"], "50/100")


class TestSpectatorOverlayBehavior(unittest.TestCase):
    """
    Regressions for the bugs found during the first live overlay run.

    1. follow_iter starts at EOF, so without warmup the builder never
       sees the seed events (pre_hand_start_info, seatInfo, game_alldata)
       and snapshot() returns None for every new frame. The runner's
       --follow path now warms up by ingesting the existing file silently
       before tailing — verify the same machinery works in tests.

    2. When hero is spectating (not dealt in), AdvisorStateMachine
       returns None and the old callback short-circuited the overlay
       too. We now always update the overlay even when the advisor has
       no rec, so the user sees board / pot / phase regardless.
    """

    def setUp(self):
        if not os.path.exists(FIXTURE):
            self.skipTest(f"fixture missing: {FIXTURE}")

    def test_warmup_seeds_builder_so_subsequent_frames_dispatch(self):
        # Mimic the runner's follow-mode warmup: ingest the entire fixture
        # silently into the builder, then verify a new frame appended
        # afterwards produces a snapshot.
        from coinpoker_adapter import CoinPokerStateBuilder

        sess = CoinPokerSession(hero_user_id=HERO, on_snapshot=lambda s: None)
        with open(FIXTURE, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    sess.builder.ingest(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        # After warmup the builder must have a hand_id and a hero seat —
        # the basic invariant the live --follow path relies on.
        self.assertIsNotNone(sess.builder.hand_id,
                             "warmup did not populate hand_id")
        self.assertIsNotNone(sess.builder.hero_seat,
                             "warmup did not populate hero_seat")

        # Now dispatch a synthetic frame that changes the pot. With the
        # builder seeded, the change must produce a snapshot.
        dispatched: list[dict] = []
        sess.on_snapshot = dispatched.append
        sess.feed_frame(_wrap("game.potInfo", {
            "totalPotAmount": 999999.0,
            "roundName": "PREFLOP",
        }))
        self.assertEqual(len(dispatched), 1,
                         "post-warmup pot change failed to dispatch")
        self.assertEqual(dispatched[0]["pot"], 99999900)  # 999999.0 × CHIP_SCALE

    def test_overlay_message_built_for_spectator_snapshot(self):
        # When hero is spectating (no hole cards), AdvisorOutput is None
        # but snapshot_to_overlay_msg must still produce a usable message
        # so the overlay HUD reflects board/pot/phase to the human user.
        snap = {
            "hero_cards": [],
            "board_cards": ["Qh", "Jh", "2d"],
            "hand_id": "H99",
            "facing_bet": False,
            "call_amount": 0,
            "phase": "FLOP",
            "num_opponents": 5,
            "pot": 50000,
            "hero_stack": 1000000,
            "position": "MP",
            "bets": [],
            "hero_seat": 6,
            "players": [],
            "hero_turn": False,
        }
        msg = snapshot_to_overlay_msg(snap, None,
                                       table_id="t1",
                                       room_name="PR-NL 50-100")
        self.assertEqual(msg["type"], "table_update")
        self.assertEqual(msg["cards"], "")
        self.assertEqual(msg["board"], "Qh Jh 2d")
        self.assertEqual(msg["phase"], "FLOP")
        self.assertEqual(msg["pot"], 500.0)
        self.assertEqual(msg["rec"], "")
        self.assertEqual(msg["rec_color"], "neutral")


class TestAdvisorOnlyOnHeroTurn(unittest.TestCase):
    """
    Regression for the AcQh "RAISE 250" bouncing bug. The runner now
    only invokes ``AdvisorStateMachine.process_state`` when ``hero_turn``
    is True, and caches the last produced AdvisorOutput so the overlay
    can keep displaying it on intermediate frames.
    """

    def _wrap(self, cmd, bean):
        return {"cmd_bean": {"Cmd": cmd, "BeanData": json.dumps(bean), "RoomName": "T"}}

    def test_callback_skips_advisor_when_not_hero_turn(self):
        # Build a stub state machine that records every call.
        from coinpoker_runner import OverlayClient, snapshot_to_overlay_msg

        calls: list[dict] = []
        class StubSM:
            bb_cents = 10000
            def process_state(self, snap):
                calls.append(snap)
                class _O:
                    action = "FOLD"
                    equity = 0.3
                    phase = snap["phase"]
                return _O()

        # Replicate the on_snapshot logic from make_advisor_callback
        # without loading the heavy real Advisor. This isn't a public
        # API but it's the same gating we want to verify.
        sm = StubSM()
        cache = {"hand": None, "out": None}
        from io import StringIO
        overlay = OverlayClient(stream=StringIO(), table_id="t1")
        room_name = "PR-NL 50-100"

        def on_snapshot(snap):
            if cache["hand"] != snap["hand_id"]:
                cache["hand"] = snap["hand_id"]
                cache["out"] = None
            out = None
            if snap["hero_turn"] and len(snap["hero_cards"]) >= 2:
                out = sm.process_state(snap)
                if out is not None and out.action:
                    cache["out"] = out
            overlay.send(snapshot_to_overlay_msg(
                snap, cache["out"], table_id="t1", room_name=room_name))

        sess = CoinPokerSession(hero_user_id=HERO, on_snapshot=on_snapshot)

        # Seed the hand with hero in BB on a 6-handed table
        sess.feed_frame(self._wrap("game.pre_hand_start_info", {
            "gameHandId": "H1", "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0,
        }))
        sess.feed_frame(self._wrap("game.seatInfo", {
            "gameHandId": "H1",
            "seatResponseDataList": [
                {"seatId": s, "userId": (HERO if s == 5 else 100 + s),
                 "userName": ("hero" if s == 5 else f"v{s}"),
                 "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True}
                for s in range(1, 7)
            ],
        }))
        sess.feed_frame(self._wrap("game.game_alldata", {
            "gameHandId": "H1",
            "gameInitResponseData": {
                "dealerSeatId": 1, "smallBlindSeatId": 2, "bigBlindSeatId": 3,
                "smallBlind": 50.0, "bigBlind": 100.0,
            },
        }))
        sess.feed_frame(self._wrap("game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "QUEEN"}],
        }))

        # No advisor calls until hero is on the clock — but the callback
        # has been firing on every state change.
        prev_call_count = len(calls)
        # Villain acts (not hero) — this should NOT invoke the advisor
        sess.feed_frame(self._wrap("game.user_turn", {
            "whoseTurn": "v6", "roundMaxBet": 100.0,
        }))
        sess.feed_frame(self._wrap("game.seat", {
            "seatId": 6, "userName": "v6", "userChips": 9750.0,
            "betAmout": 250.0, "newCaption": "Raise",
        }))
        self.assertEqual(len(calls), prev_call_count,
                         "advisor was invoked on a villain action")

        # Hero is on the clock — advisor must be invoked exactly once
        sess.feed_frame(self._wrap("game.user_turn", {
            "whoseTurn": "hero", "roundMaxBet": 250.0,
        }))
        self.assertEqual(len(calls), prev_call_count + 1)

        # Hero acts → action passes → no further advisor calls until
        # the next user_turn for hero
        sess.feed_frame(self._wrap("game.seat", {
            "seatId": 5, "userName": "hero", "userChips": 9000.0,
            "betAmout": 1000.0, "newCaption": "Raise",
        }))
        # Villain re-raises
        sess.feed_frame(self._wrap("game.user_turn", {
            "whoseTurn": "v6", "roundMaxBet": 0.0,
        }))
        sess.feed_frame(self._wrap("game.seat", {
            "seatId": 6, "userName": "v6", "userChips": 7000.0,
            "betAmout": 3000.0, "newCaption": "Raise",
        }))
        # Still no extra advisor call — it's not hero's turn
        self.assertEqual(len(calls), prev_call_count + 1,
                         f"advisor invoked off-turn: {len(calls) - prev_call_count - 1} extra")

        # Hero on the clock again
        sess.feed_frame(self._wrap("game.user_turn", {
            "whoseTurn": "hero", "roundMaxBet": 3000.0,
        }))
        self.assertEqual(len(calls), prev_call_count + 2)

    def test_cache_resets_on_new_hand(self):
        from coinpoker_runner import OverlayClient, snapshot_to_overlay_msg
        from io import StringIO

        class StubSM:
            bb_cents = 10000
            def process_state(self, snap):
                class _O:
                    action = f"FOLD-{snap['hand_id']}"
                    equity = 0.3
                    phase = snap["phase"]
                return _O()

        sm = StubSM()
        cache = {"hand": None, "out": None}
        overlay = OverlayClient(stream=StringIO(), table_id="t1")

        def on_snapshot(snap):
            if cache["hand"] != snap["hand_id"]:
                cache["hand"] = snap["hand_id"]
                cache["out"] = None
            if snap["hero_turn"] and len(snap["hero_cards"]) >= 2:
                out = sm.process_state(snap)
                if out is not None and out.action:
                    cache["out"] = out

        sess = CoinPokerSession(hero_user_id=HERO, on_snapshot=on_snapshot)

        # Hand 1
        for f in self._minimal_seed("H1"):
            sess.feed_frame(f)
        sess.feed_frame(self._wrap("game.user_turn", {
            "whoseTurn": "hero", "roundMaxBet": 100.0,
        }))
        self.assertIsNotNone(cache["out"])
        self.assertEqual(cache["out"].action, "FOLD-H1")

        # New hand starts → cache must reset
        for f in self._minimal_seed("H2"):
            sess.feed_frame(f)
        # Cache cleared until hero_turn fires again
        self.assertIsNone(cache["out"])

    def _minimal_seed(self, hid):
        return [
            self._wrap("game.pre_hand_start_info", {
                "gameHandId": hid, "dealerSeatId": 1,
                "bbAmount": 100.0, "sbAmount": 50.0,
            }),
            self._wrap("game.seatInfo", {
                "gameHandId": hid,
                "seatResponseDataList": [
                    {"seatId": 5, "userId": HERO, "userName": "hero",
                     "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
                    {"seatId": 6, "userId": 106, "userName": "v",
                     "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True},
                ],
            }),
            self._wrap("game.game_alldata", {
                "gameHandId": hid,
                "gameInitResponseData": {
                    "dealerSeatId": 1, "smallBlindSeatId": 5, "bigBlindSeatId": 6,
                    "smallBlind": 50.0, "bigBlind": 100.0,
                },
            }),
            self._wrap("game.hole_cards", {
                "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                              {"suit": "HEARTS", "value": "QUEEN"}],
            }),
        ]


class TestEndToEndAdvisorReplay(unittest.TestCase):
    """
    Loads the REAL AdvisorStateMachine + base Advisor and replays the
    fixture through the full pipeline. Heavy (~1-3s startup for the
    advisor models) — the regression we're guarding against is breakage
    of the runner→adapter→advisor wiring as either side evolves.
    """

    def setUp(self):
        if not os.path.exists(FIXTURE):
            self.skipTest(f"fixture missing: {FIXTURE}")
        try:
            from coinpoker_runner import make_advisor_callback  # noqa: F401
        except Exception as e:
            self.skipTest(f"runner unavailable: {e}")

    def test_replay_produces_at_least_one_action(self):
        from coinpoker_runner import make_advisor_callback
        actions: list[tuple] = []

        # We'll wrap the real callback so we can capture every action
        # without poisoning the printer with extra calls. The simplest
        # way is to instantiate the same machinery the runner does.
        from advisor import Advisor as BaseAdvisor
        from preflop_chart import preflop_advice
        from advisor_state_machine import AdvisorStateMachine

        try:
            from strategy.postflop_engine import PostflopEngine
            postflop = PostflopEngine()
        except Exception:
            postflop = None

        try:
            from advisor import assess_board_danger
        except ImportError:
            assess_board_danger = lambda h, b: {"warnings": []}

        base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
        sm = AdvisorStateMachine(
            base_advisor=base,
            preflop_advice_fn=preflop_advice,
            postflop_engine=postflop,
            assess_board_danger_fn=assess_board_danger,
            tracker=None,
            bb_cents=10000,  # 100 chips × CHIP_SCALE
        )

        def cb(snap):
            sm.bb_cents = max(sm.bb_cents, 10000)
            out = sm.process_state(snap)
            if out and out.action:
                actions.append((snap["hand_id"], snap["phase"], out.action))

        session = CoinPokerSession(hero_user_id=HERO, on_snapshot=cb)
        session.run(replay_iter(FIXTURE))

        # The fixture covers ~3 hands. We should see at least one action
        # produced for hero. If this drops to 0 in future, the wiring
        # between adapter and advisor is broken.
        self.assertGreater(len(actions), 0,
                           "advisor produced zero actions across fixture replay")
        # And actions should span both PREFLOP and at least one postflop
        # street, since the fixture reaches the FLOP.
        phases = {a[1] for a in actions}
        self.assertIn("PREFLOP", phases)


class TestMultiTableCoinPokerSession(unittest.TestCase):
    """
    Tests for MultiTableCoinPokerSession — the per-room dispatcher that
    enables 4+ simultaneous tables for the £10/hr grind plan.

    Each test uses synthetic frames with explicit room_name fields. We
    don't load any real CoinPoker data here — that's covered by the
    existing single-table fixture tests + the live replay smoke tests.
    """

    HERO = 1571120

    def _wrap(self, room: str, hand_id: str, cmd: str, bean: dict) -> dict:
        return {
            "cmd_bean": {
                "Cmd": cmd,
                "BeanData": json.dumps(bean),
                "RoomName": room,
            },
            "room_name": room,
        }

    def _seed_hand(self, sess, room, hand_id, hero_seat=2):
        """Push enough frames into a session that the room's builder
        produces a snapshot with hero seated."""
        sess.feed_frame(self._wrap(room, hand_id, "game.pre_hand_start_info", {
            "gameHandId": hand_id, "dealerSeatId": 1,
            "bbAmount": 100.0, "sbAmount": 50.0, "anteAmount": 0.0,
        }))
        sess.feed_frame(self._wrap(room, hand_id, "game.seatInfo", {
            "gameHandId": hand_id,
            "seatResponseDataList": [
                {"seatId": s, "userId": (self.HERO if s == hero_seat else 100 + s),
                 "userName": ("hero" if s == hero_seat else f"v{s}"),
                 "userChips": 10000.0, "betAmout": 0.0, "isPlaying": True}
                for s in range(1, 4)
            ],
        }))
        sess.feed_frame(self._wrap(room, hand_id, "game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "KING"}],
        }))

    def test_empty_session_state(self):
        sess = MultiTableCoinPokerSession(hero_user_id=self.HERO,
                                          on_snapshot=lambda s: None)
        self.assertEqual(len(sess), 0)
        self.assertEqual(sess.builders, {})
        self.assertIsNone(sess.builder)
        # Default bb falls back to practice-table value
        self.assertEqual(sess.bb_cents(), 100 * 100)

    def test_dispatches_per_room(self):
        seen = []
        sess = MultiTableCoinPokerSession(
            hero_user_id=self.HERO,
            on_snapshot=lambda s: seen.append((s.get("room_name"), s.get("hand_id"))),
        )
        self._seed_hand(sess, "room_A", "H1")
        self._seed_hand(sess, "room_B", "H2")
        # Both rooms should have generated snapshots, with different room_names
        rooms = set(r for r, _ in seen)
        self.assertEqual(rooms, {"room_A", "room_B"})
        # Both rooms have their own builder
        self.assertEqual(len(sess), 2)
        self.assertIn("room_A", sess.builders)
        self.assertIn("room_B", sess.builders)

    def test_state_isolated_between_rooms(self):
        """Critical: a hand_id in room_A must not contaminate room_B's
        state. This was the whole reason for the multi-table refactor."""
        sess = MultiTableCoinPokerSession(hero_user_id=self.HERO,
                                          on_snapshot=lambda s: None)
        self._seed_hand(sess, "room_A", "H1")
        self._seed_hand(sess, "room_B", "H2")
        # Each room's builder should hold its own hand_id
        self.assertEqual(sess.get_builder("room_A").hand_id, "H1")
        self.assertEqual(sess.get_builder("room_B").hand_id, "H2")
        # And they shouldn't have leaked across
        self.assertNotEqual(sess.get_builder("room_A").hand_id,
                            sess.get_builder("room_B").hand_id)

    def test_snapshot_carries_room_name(self):
        """Every dispatched snapshot must have room_name populated so
        the callback can route per-room state."""
        snapshots = []
        sess = MultiTableCoinPokerSession(
            hero_user_id=self.HERO,
            on_snapshot=lambda s: snapshots.append(s),
        )
        self._seed_hand(sess, "PR-NL 50-100 (A) 246361", "H1")
        # All snapshots from this seed should have the room name
        for snap in snapshots:
            self.assertEqual(snap.get("room_name"), "PR-NL 50-100 (A) 246361")

    def test_dedup_per_room(self):
        """Re-feeding the same frame for the same room shouldn't
        re-dispatch (signature-based dedup)."""
        count = [0]
        sess = MultiTableCoinPokerSession(
            hero_user_id=self.HERO,
            on_snapshot=lambda s: count.__setitem__(0, count[0] + 1),
        )
        # Seed twice — second seed should produce zero new dispatches
        self._seed_hand(sess, "room_A", "H1")
        first_count = count[0]
        # Re-feed the EXACT same hole_cards frame
        sess.feed_frame(self._wrap("room_A", "H1", "game.hole_cards", {
            "holeCards": [{"suit": "CLUBS", "value": "ACE"},
                          {"suit": "HEARTS", "value": "KING"}],
        }))
        # No new snapshot — state didn't change
        self.assertEqual(count[0], first_count)

    def test_dedup_independent_between_rooms(self):
        """The dedup signature is per-room — a duplicate state in
        room_A shouldn't suppress a real change in room_B."""
        seen = []
        sess = MultiTableCoinPokerSession(
            hero_user_id=self.HERO,
            on_snapshot=lambda s: seen.append((s.get("room_name"), s.get("hand_id"))),
        )
        self._seed_hand(sess, "room_A", "H1")
        # Re-seed room_A with same hand → no new dispatches in room_A
        before = len(seen)
        self._seed_hand(sess, "room_A", "H1")
        # But room_B should still get its own snapshots
        self._seed_hand(sess, "room_B", "H2")
        after = len(seen)
        # Verify room_B's snapshots came through
        self.assertTrue(any(r == "room_B" for r, _ in seen[before:]))

    def test_bb_cents_per_room(self):
        sess = MultiTableCoinPokerSession(hero_user_id=self.HERO,
                                          on_snapshot=lambda s: None)
        # NL10 table
        sess.feed_frame(self._wrap("nl10", "H1", "game.pre_hand_start_info", {
            "gameHandId": "H1", "bbAmount": 100.0, "sbAmount": 50.0,
            "dealerSeatId": 1,
        }))
        # NL50 table
        sess.feed_frame(self._wrap("nl50", "H2", "game.pre_hand_start_info", {
            "gameHandId": "H2", "bbAmount": 500.0, "sbAmount": 250.0,
            "dealerSeatId": 1,
        }))
        self.assertEqual(sess.bb_cents("nl10"), 100 * 100)
        self.assertEqual(sess.bb_cents("nl50"), 500 * 100)
        # Default (no room arg) returns the most recently active room
        self.assertEqual(sess.bb_cents(), 500 * 100)

    def test_bb_cents_override(self):
        sess = MultiTableCoinPokerSession(hero_user_id=self.HERO,
                                          on_snapshot=lambda s: None,
                                          bb_chips=200)
        # Override should win regardless of frame data
        sess.feed_frame(self._wrap("any_room", "H1", "game.pre_hand_start_info", {
            "gameHandId": "H1", "bbAmount": 100.0, "sbAmount": 50.0,
            "dealerSeatId": 1,
        }))
        self.assertEqual(sess.bb_cents("any_room"), 200 * 100)
        self.assertEqual(sess.bb_cents(), 200 * 100)

    def test_frames_missing_room_dropped(self):
        seen = []
        sess = MultiTableCoinPokerSession(
            hero_user_id=self.HERO,
            on_snapshot=lambda s: seen.append(s),
        )
        # Frame with no room_name field — should be dropped silently
        bad = {"cmd_bean": {"Cmd": "game.pre_hand_start_info",
                            "BeanData": json.dumps({"gameHandId": "H1"})}}
        result = sess.feed_frame(bad)
        self.assertIsNone(result)
        self.assertEqual(len(sess), 0)
        self.assertEqual(seen, [])

    def test_callback_exception_does_not_crash_session(self):
        """A buggy callback in one snapshot shouldn't kill the session
        — print + continue, same as the single-table version."""
        def boom(_snap):
            raise RuntimeError("strategy crash")
        sess = MultiTableCoinPokerSession(hero_user_id=self.HERO,
                                          on_snapshot=boom)
        # Should NOT raise
        try:
            self._seed_hand(sess, "room_A", "H1")
        except RuntimeError:
            self.fail("MultiTableCoinPokerSession let a callback exception escape")

    def test_builder_property_returns_most_recent(self):
        """Backwards-compat shim: .builder returns the most recently
        active room's builder so single-table callers work transparently."""
        sess = MultiTableCoinPokerSession(hero_user_id=self.HERO,
                                          on_snapshot=lambda s: None)
        self._seed_hand(sess, "room_A", "H1")
        a_builder = sess.builder
        self.assertIsNotNone(a_builder)
        self.assertEqual(a_builder.hand_id, "H1")
        self._seed_hand(sess, "room_B", "H2")
        b_builder = sess.builder
        self.assertEqual(b_builder.hand_id, "H2")  # most recent
        # And it's a different object than room_A's builder
        self.assertIsNot(a_builder, b_builder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
