"""
Phase 1 unit tests for the replay harness.

These cover the harness's bookkeeping (per-room hand transitions, decision
recording, no-advisor mode, advisor errors). They do NOT validate
strategy correctness — that's the harness's *output*, not its job.

The end-to-end "walk the captured corpus" test is separate and
parameterized on the live frame log path.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from replay_harness import ReplayHarness, ReplayReport, HandRecord  # noqa: E402


# ── synthetic frame builder ───────────────────────────────────────────
#
# We bypass the CoinPoker IL frame format and directly inject snapshots
# via a fake session. The harness's contract is "given a stream of
# snapshots, produce a report" — we test that without the cmd_bean
# decoding overhead.

class _FakeSession:
    """Minimal stand-in for MultiTableCoinPokerSession."""
    def __init__(self, snapshots):
        self._snapshots = snapshots
        self.frames_seen = 0

    def feed_line(self, line):
        # Ignore line, dispatch the next snapshot in our queue
        if self._snapshots:
            snap = self._snapshots.pop(0)
            self.frames_seen += 1
            self._on_snapshot(snap)

    def bb_cents(self, room=None):
        return 10  # NL10-style scale: 10 chip cents per BB


def _harness_with_snapshots(snapshots, advisor_factory=None, hero_id=12345):
    """Build a ReplayHarness wired to a fake session with the given snapshots."""
    h = ReplayHarness(hero_user_id=hero_id, advisor_factory=advisor_factory)
    h._reset()
    fake = _FakeSession(snapshots)
    fake._on_snapshot = h._handle_snapshot
    h._session = fake
    # Drive the queue
    while fake._snapshots:
        fake.feed_line("dummy")
    # Finalize
    for room, hr in list(h._hand_state.items()):
        if hr is not None:
            h._finalize(room)
    h._report.frames_processed = fake.frames_seen
    return h._report


def _snap(hand_id, room="r1", phase="PREFLOP", hero_stack=1000,
          hero_turn=False, hero_cards=None, board=None, pot=0,
          call_amount=0, facing_bet=False, position="BTN"):
    # Use is-None checks (not `or`) so callers can pass [] to mean
    # "no cards" without it being silently swapped for the default.
    if hero_cards is None:
        hero_cards = ["Ah", "Ks"]
    if board is None:
        board = []
    return {
        "hand_id": hand_id,
        "room_name": room,
        "phase": phase,
        "hero_stack": hero_stack,
        "hero_turn": hero_turn,
        "hero_cards": hero_cards,
        "board_cards": board,
        "pot": pot,
        "call_amount": call_amount,
        "facing_bet": facing_bet,
        "position": position,
    }


# ── basic recording ───────────────────────────────────────────────────

def test_no_advisor_records_hands_and_decisions():
    """Without an advisor, the harness still records every decision point."""
    snaps = [
        _snap("h1", hero_stack=1000),
        _snap("h1", hero_turn=True, hero_stack=1000),
        _snap("h1", hero_stack=950),  # hero called something, stack down
    ]
    report = _harness_with_snapshots(snaps)
    assert report.total_hands == 1
    assert report.total_decisions == 1
    assert report.hands[0].chip_delta == -50


def test_chip_delta_uses_starting_and_ending_stack():
    """starting_stack from first snap, ending_stack from last."""
    snaps = [
        _snap("h1", hero_stack=2000),
        _snap("h1", hero_stack=1900),
        _snap("h1", hero_stack=1800),
        _snap("h1", hero_stack=1750),
    ]
    report = _harness_with_snapshots(snaps)
    assert report.hands[0].starting_stack == 2000
    assert report.hands[0].ending_stack == 1750
    assert report.hands[0].chip_delta == -250


def test_hand_transitions_create_separate_records():
    """A new hand_id finalizes the previous hand and starts a fresh one."""
    snaps = [
        _snap("h1", hero_stack=1000),
        _snap("h1", hero_stack=900),
        _snap("h2", hero_stack=900),
        _snap("h2", hero_stack=1100),
    ]
    report = _harness_with_snapshots(snaps)
    assert report.total_hands == 2
    assert report.hands[0].hand_id == "h1"
    assert report.hands[0].chip_delta == -100
    assert report.hands[1].hand_id == "h2"
    assert report.hands[1].chip_delta == 200


def test_multi_room_isolation():
    """Two interleaved rooms produce two independent hand records."""
    snaps = [
        _snap("h1", room="r1", hero_stack=1000),
        _snap("h2", room="r2", hero_stack=2000),
        _snap("h1", room="r1", hero_stack=950),
        _snap("h2", room="r2", hero_stack=2200),
    ]
    report = _harness_with_snapshots(snaps)
    assert report.total_hands == 2
    by_room = {h.room: h for h in report.hands}
    assert by_room["r1"].chip_delta == -50
    assert by_room["r2"].chip_delta == 200


def test_decisions_only_recorded_when_hero_turn_and_has_cards():
    """No decision when hero_turn=False or hero_cards is empty."""
    snaps = [
        _snap("h1", hero_turn=False, hero_cards=["Ah", "Ks"]),
        _snap("h1", hero_turn=True, hero_cards=[]),  # spectating
        _snap("h1", hero_turn=True, hero_cards=["Ah", "Ks"]),  # real decision
    ]
    report = _harness_with_snapshots(snaps)
    assert report.total_decisions == 1


# ── advisor integration ──────────────────────────────────────────────

class _StubAdvisor:
    """Minimal advisor that returns a fixed action."""
    def __init__(self, action="FOLD", source="stub", equity=0.42):
        self._action = action
        self._source = source
        self._equity = equity
        self.calls = 0

    def process_state(self, snap):
        self.calls += 1

        class _Out:
            pass
        o = _Out()
        o.action = self._action
        o.source = self._source
        o.equity = self._equity
        o.phase = snap.get("phase", "")
        return o


def test_advisor_recommendation_recorded_on_decision():
    """When an advisor is supplied, its action lands in the Decision record."""
    stub = _StubAdvisor(action="RAISE to 0.30", source="preflop_chart", equity=0.62)
    factory = lambda bb: stub
    snaps = [
        _snap("h1", hero_turn=True, hero_stack=1000),
    ]
    report = _harness_with_snapshots(snaps, advisor_factory=factory)
    assert stub.calls == 1
    d = report.hands[0].decisions[0]
    assert d.advisor_action == "RAISE to 0.30"
    assert d.advisor_source == "preflop_chart"
    assert abs(d.advisor_equity - 0.62) < 1e-9


def test_advisor_error_does_not_crash_harness():
    """A buggy advisor raising mid-decision is captured, not propagated."""
    class _BoomAdvisor:
        def process_state(self, snap):
            raise RuntimeError("kaboom")
    factory = lambda bb: _BoomAdvisor()
    snaps = [_snap("h1", hero_turn=True, hero_stack=1000)]
    report = _harness_with_snapshots(snaps, advisor_factory=factory)
    assert report.sm_errors == 1
    d = report.hands[0].decisions[0]
    assert "RuntimeError" in d.advisor_source
    assert d.error and "kaboom" in d.error


# ── aggregates ───────────────────────────────────────────────────────

def test_bb_per_100_aggregate():
    """BB/100 = (sum of bb deltas / num hands) * 100."""
    # Three hands at NL10 (bb_cents=10): +20 chips, -50 chips, +0 chips
    snaps = [
        _snap("h1", hero_stack=1000),
        _snap("h1", hero_stack=1020),
        _snap("h2", hero_stack=1020),
        _snap("h2", hero_stack=970),
        _snap("h3", hero_stack=970),
        _snap("h3", hero_stack=970),
    ]
    report = _harness_with_snapshots(snaps)
    assert report.total_hands == 3
    assert report.total_chip_delta == -30
    # Total bb_delta: (20 + -50 + 0) / 10 = -3.0
    assert abs(report.total_bb_delta - (-3.0)) < 1e-9
    # bb/100 = -3.0 / 3 * 100 = -100.0
    assert abs(report.bb_per_100() - (-100.0)) < 1e-9


def test_summary_string_renders():
    """summary() doesn't crash and includes the key fields."""
    snaps = [_snap("h1", hero_stack=1000), _snap("h1", hero_stack=1100)]
    report = _harness_with_snapshots(snaps)
    s = report.summary()
    assert "hands=1" in s
    assert "bb/100" in s
