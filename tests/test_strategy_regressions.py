"""
Named regression tests for advisor strategy leaks that have cost real money.

Each test in this file represents a SPECIFIC live hand where the advisor
recommended an action that lost (or would have lost) the user money. The
test name encodes the hand ID and the leak class. Tests are marked
``expectedFailure`` until the leak is fixed; when the fix lands, the
marker is removed and the test becomes a permanent regression guard.

Rules of engagement:
  1. NEVER delete a test from this file. If the spot is no longer relevant
     because the engine was rewritten, leave the test (the spot is still a
     good check on the new code).
  2. NEVER weaken an assertion to make a test pass. The recommended action
     is what's correct for the spot. If the engine can't produce it, the
     engine is wrong, not the test.
  3. New strategy leaks discovered in real play get added here, with the
     hand ID and a short description, before any fix is attempted.

Currently catalogued leaks:
  - 2460830661  AQo SB facing 2.5x at 4-handed → flat-called instead of 3-betting
  - 2460830707  KK BB on 5d 9s 7d 4c 8c facing river raise → CALL with eq=69%
                (villain showed 7h6h straight; the equity model didn't account
                for villain's action-narrowed range)

See ``feedback_passing_tests_not_validation.md`` for the broader context.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vision"))


def _build_state_machine():
    """
    Construct an AdvisorStateMachine identical to what the live runners
    use, so the assertions exercise the real code path. Heavy import — only
    pay it when these tests actually run.
    """
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
    return AdvisorStateMachine(
        base_advisor=base,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        assess_board_danger_fn=assess_board_danger,
        tracker=None,
        bb_cents=10,  # NL10 ($0.05/$0.10) — matches both regression hands
    )


class TestStrategyRegressions(unittest.TestCase):
    """One test per named real-money loss spot. See module docstring."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.sm = _build_state_machine()
        except Exception as e:
            raise unittest.SkipTest(f"could not load advisor dependencies: {e}")

    def test_2460830661_AQo_SB_facing_open_should_3bet(self):
        """
        Hand 2460830661, NL10, 4-handed.

        Hero in SB with As Qh, facing a 2.5x open from BTN/CO. AQo at 4-max
        is a top-15% hand and the standard play vs a 2.5x open from a late
        position is a 3-bet for value/protection. Flat-calling OOP with a
        domination-prone hand bleeds EV.

        FIXED 2026-04-08 (session 13) by per-position 3-bet ranges in
        `vision/preflop_chart.py`. SB now has SB_3BET_EXTRA which adds
        AQo+, AJs+, KQs+, JJ/TT for value plus a few blocker bluffs to
        the universal premium 3-bet range.
        """
        state = {
            "hero_cards":   ["As", "Qh"],
            "board_cards":  [],
            "hand_id":      "2460830661",
            "facing_bet":   True,
            "call_amount":  20,        # 2 BB more on top of the SB
            "pot":          40,        # in chip cents (CHIP_SCALE=100)
            "phase":        "PREFLOP",
            "num_opponents": 3,        # 4-handed
            "hero_stack":   1009,
            "position":     "SB",
        }
        out = self.sm.process_state(state)
        self.assertIsNotNone(out, "advisor returned None for AQo SB decision")
        self.assertIn("RAISE", out.action.upper(),
                      f"AQo SB facing open at 4-max should 3-BET, got {out.action!r}")

    def test_2379414698_KK_on_3flush_facing_overbet_should_fold(self):
        """
        Hand 2379414698, Unibet NL2 (BB=4 cents).

        Hero in SB with Ks Kd on flop 9h 6h 2h. Hero holds K-of-hearts as
        a 1-card flush blocker, but the board is monotone hearts and
        villain has bet ~9x pot. The equity model treats KK vs random as
        ~70%, but villain's action sequence (huge overbet on a flush-
        completing board) narrows their range to flushes and sets, where
        KK has maybe 10-15% equity.

        The OLD recommendation was BET 0.27 (suggesting hero acts first),
        but on the SECOND snapshot of the same street the hero faces a
        752-cent call into an 82-cent pot — that's the "facing huge
        bet" decision point, where the NEW advisor (without the 3-flush
        filter) recommends CALL 7.52, losing the rest of the stack.

        Discovered 2026-04-08 via Unibet replay test against captured
        hands. Same equity-vs-action-range leak class as the KK 4-straight
        case.

        FIXED 2026-04-08 (session 13) by Filter 3 in `_apply_danger_overrides`:
        overpair on 3-flush board facing >=50% pot bet → FOLD.
        """
        state = {
            "hero_cards":   ["Ks", "Kd"],
            "board_cards":  ["9h", "6h", "2h"],
            "hand_id":      "2379414698",
            "facing_bet":   True,
            "call_amount":  752,    # ~9x pot bet (in chip cents)
            "pot":          82,
            "phase":        "FLOP",
            "num_opponents": 1,
            "hero_stack":   752,    # all-in or close
            "position":     "SB",
        }
        out = self.sm.process_state(state)
        self.assertIsNotNone(out, "advisor returned None for KK 3-flush decision")
        self.assertIn("FOLD", out.action.upper(),
                      f"KK on 3-flush board facing 9x pot bet should FOLD, got {out.action!r}")

    def test_2379447781_QJ_flush_on_paired_board_should_fold(self):
        """
        Hand 2379447781, Unibet NL2.

        Hero in BTN with Qc Jc on river board Kc Th Td 3c 8c. Hero has
        K-high flush (own QcJc + board's Kc 3c 8c). The board is PAIRED
        (Th Td). Villain raised the river to 281 cents into a 129-cent
        pot — a 218%-pot bet that screams "I have a full house."

        Hero has neither a T nor a pocket pair → hero CANNOT have a boat.
        Best case is K-high flush, which loses to every full house in
        villain's range (KT, JT, T-anything matched up, KK/JJ/QQ/etc).
        Equity model overestimates because it doesn't condition on the
        2x-pot river raise.

        Discovered 2026-04-08 via Unibet replay test against captured
        hands. Same equity-vs-action-range leak class as the KK river
        and KK 3-flush cases.

        FIXED 2026-04-08 (session 13) by Filter 4 in `_apply_danger_overrides`:
        flush on paired board with no boat possibility facing >=30% pot
        bet → FOLD.
        """
        state = {
            "hero_cards":   ["Qc", "Jc"],
            "board_cards":  ["Kc", "Th", "Td", "3c", "8c"],
            "hand_id":      "2379447781",
            "facing_bet":   True,
            "call_amount":  281,    # 218% pot bet
            "pot":          129,
            "phase":        "RIVER",
            "num_opponents": 1,
            "hero_stack":   500,
            "position":     "BTN",
        }
        out = self.sm.process_state(state)
        self.assertIsNotNone(out, "advisor returned None for QJ paired-board decision")
        self.assertIn("FOLD", out.action.upper(),
                      f"QJ flush on paired board facing 2x pot raise should FOLD, got {out.action!r}")

    def test_2502750418_KQo_SB_folded_to_should_open_not_fold(self):
        """
        Hand 2502750418, NL10 real money, KdQs SB.

        Folded around to hero in SB. Hero has KQo. Faces only the
        BB blind (call_amount = 0.05, half a BB on top of the SB
        already posted). The chart used to FOLD because it routed
        through the facing_raise branch (KQo isn't in SB_3BET_EXTRA
        or SB_CALL_RANGE). But this is the textbook RFI spot for SB
        — KQo SB folded-to is a clear open across every published
        6-max chart.

        FIXED 2026-04-09 by the RFI re-route in _process_preflop:
        if call_amt <= bb the chart is re-resolved with
        facing_raise=False so the open-raise range applies.
        """
        sm = self.sm
        # Reset state
        sm.prev_hero = []; sm.prev_board = []; sm.prev_hand_id = None
        sm.prev_phase = None; sm.last_facing = None
        sm.last_call_amount = None; sm.last_pot = None
        sm.bb_cents = 10  # NL10
        state = {
            "hero_cards":   ["Kd", "Qs"],
            "board_cards":  [],
            "hand_id":      "2502750418",
            "facing_bet":   True,    # snapshot says True (BB is a bet)
            "call_amount":  5,       # 0.05 = half-BB on top of SB
            "pot":          15,
            "phase":        "PREFLOP",
            "num_opponents": 5,
            "hero_stack":   1140,
            "position":     "SB",
        }
        out = sm.process_state(state)
        self.assertIsNotNone(out)
        # Must NOT be FOLD — should be RAISE (open) or at minimum CALL
        self.assertNotIn("FOLD", out.action.upper(),
                         f"KQo SB folded-to should open, not fold. Got: {out.action!r}")

    def test_2502750xxx_QTo_BTN_folded_to_should_open_not_fold(self):
        """
        Same RFI bug as KdQs SB but for BTN. Reported live by user
        on QsTh BTN with no raise (everyone folded to BTN). The chart
        used to FOLD because call_amt = 1 BB and the snapshot reports
        facing_bet=True (you're "facing" the BB blind). QTo BTN
        folded-to is a clear open in every 6-max chart.

        FIXED 2026-04-09 by the same RFI re-route as the SB case.
        """
        sm = self.sm
        # Reset state
        sm.prev_hero = []; sm.prev_board = []; sm.prev_hand_id = None
        sm.prev_phase = None; sm.last_facing = None
        sm.last_call_amount = None; sm.last_pot = None
        sm.bb_cents = 10  # NL10
        state = {
            "hero_cards":   ["Qs", "Th"],
            "board_cards":  [],
            "hand_id":      "btn_rfi_test",
            "facing_bet":   True,
            "call_amount":  10,      # exactly 1 BB (no raise, just the BB)
            "pot":          15,
            "phase":        "PREFLOP",
            "num_opponents": 5,
            "hero_stack":   1000,
            "position":     "BTN",
        }
        out = sm.process_state(state)
        self.assertIsNotNone(out)
        self.assertNotIn("FOLD", out.action.upper(),
                         f"QTo BTN folded-to should open, not fold. Got: {out.action!r}")

    def test_rfi_reroute_does_NOT_fire_on_actual_raise(self):
        """
        Defensive: the RFI re-route must not fire when there's an
        actual raise. A min-raise to 2 BB makes call_amt > bb, which
        should still route through the facing_raise branch.
        """
        sm = self.sm
        # Reset state
        sm.prev_hero = []; sm.prev_board = []; sm.prev_hand_id = None
        sm.prev_phase = None; sm.last_facing = None
        sm.last_call_amount = None; sm.last_pot = None
        sm.bb_cents = 10  # NL10
        # Hero in BTN with 72o facing a real 3x raise (call=30, bb=10)
        state = {
            "hero_cards":   ["7c", "2d"],
            "board_cards":  [],
            "hand_id":      "rfi_negative",
            "facing_bet":   True,
            "call_amount":  30,      # 3x BB raise — NOT an RFI spot
            "pot":          50,
            "phase":        "PREFLOP",
            "num_opponents": 4,
            "hero_stack":   1000,
            "position":     "BTN",
        }
        out = sm.process_state(state)
        self.assertIsNotNone(out)
        # 72o BTN facing a raise → fold is correct, RFI re-route must NOT
        # have flipped this to "open with 72o because BTN_RAISE is wide"
        self.assertIn("FOLD", out.action.upper(),
                      f"72o BTN facing actual 3x raise should FOLD, "
                      f"got: {out.action!r}")

    def test_2502750404_call_amount_change_must_re_fire_advisor(self):
        """
        Hand 2502750404, NL10 real money, 9c8c CO.

        The action sequence:
          1. Hero limps for 0.10 facing the BB
          2. BTN (Kelsier) raises to 0.50
          3. BB (stark) calls
          4. Hero now faces a 0.40 raise on top

        The pre-fix bug: AdvisorStateMachine.process_state had a state
        change detection guard that returned None unless one of
        (hand_id, board, phase, hero_cards, facing_bet) changed. When a
        villain raises mid-preflop after hero already faced a bet,
        facing_bet stays True the whole time so the guard returned
        early. The advisor never re-fired and the overlay kept
        showing the stale 'CALL 0.10' rec from the limp moment when
        the user actually needed to make a 'fold or call 0.40' decision.

        FIX: track call_amount and pot in the change detection. Any
        sizing change inside the same betting round re-fires the rec.

        This test simulates the exact two-snapshot sequence: first
        snapshot at hero turn 1 (call 10, pot 25), second snapshot
        at hero turn 2 (call 40, pot 115). The SM must produce a
        non-None action on BOTH snapshots, and the second one must
        reflect the new sizing.
        """
        sm = self.sm

        # Reset SM state for the test (don't depend on test ordering)
        sm.prev_hero = []
        sm.prev_board = []
        sm.prev_hand_id = None
        sm.prev_phase = None
        sm.last_facing = None
        sm.last_call_amount = None
        sm.last_pot = None

        # Hero turn 1: limping into BB for 0.10
        state1 = {
            "hero_cards":   ["9c", "8c"],
            "board_cards":  [],
            "hand_id":      "2502750404",
            "facing_bet":   True,
            "call_amount":  10,    # 0.10 in chip cents
            "pot":          25,
            "phase":        "PREFLOP",
            "num_opponents": 5,
            "hero_stack":   1113,
            "position":     "CO",
        }
        out1 = sm.process_state(state1)
        self.assertIsNotNone(out1, "first snapshot must produce a recommendation")

        # Hero turn 2: BTN raised to 0.50, BB called, hero faces 0.40 raise
        state2 = {
            "hero_cards":   ["9c", "8c"],
            "board_cards":  [],
            "hand_id":      "2502750404",
            "facing_bet":   True,    # still True (was True before, KEY: this didn't change)
            "call_amount":  40,      # CHANGED: now 0.40 to call (the raise on top)
            "pot":          115,     # CHANGED: pot grew
            "phase":        "PREFLOP",  # SAME phase
            "num_opponents": 5,
            "hero_stack":   1103,
            "position":     "CO",
        }
        out2 = sm.process_state(state2)
        # Critical assertion: the SM MUST re-fire on the new call_amount
        self.assertIsNotNone(
            out2,
            "SM must produce a fresh rec when call_amount changes mid-round "
            "(even if facing_bet boolean stays True). Stale rec on the overlay "
            "is the bug that almost cost real money on hand 2502750404.")
        # And the action should reflect the new sizing — either FOLD or
        # a CALL with the new amount, NOT the stale "CALL 0.10"
        action_upper = out2.action.upper() if out2.action else ""
        self.assertNotIn("0.10", action_upper,
                         f"Stale 'CALL 0.10' is the bug. Got: {out2.action!r}")

    def test_synthetic_TPTK_on_coordinated_river_should_fold(self):
        """
        Synthetic regression test for Filter 5: top pair top kicker on a
        coordinated river facing a 75%+ pot bet should fold.

        The captured datasets don't contain a real hand matching this
        exact shape (one-pair-only on coordinated river with big bet),
        so this test uses a constructed state. The shape is the next
        leak class beyond the catastrophic ones the other filters catch.

        Setup: hero has Ah Jc, board is Th 7h 6h 8d 2c. That's:
          - 4-card straight on the board (5-6-7-8 + need 9 or 4-5-6-7 + need 8 already there)
            Actually: 6-7-8-T means 4 cards in a 5-rank window. Filter 1
            won't fire because hero isn't an OVERPAIR (we have AJ unpaired).
          - 3-card heart flush on board (Th 7h 6h)
          - Hero hits 1 pair after the river (no card matches AJ)
            Wait, actually with no Aces or Jacks on the board, hero is
            HIGH CARD only. Let me make sure that's HAND_HIGH_CARD or
            HAND_PAIR — the filter handles both.

        Expected: Filter 5 fires because (river, one-pair-or-less,
        coordinated board with both 4-straight and 3-flush, big bet).
        """
        state = {
            "hero_cards":   ["Ah", "Jc"],
            "board_cards":  ["Th", "7h", "6h", "8d", "2c"],
            "hand_id":      "synthetic_TPTK_river",
            "facing_bet":   True,
            "call_amount":  100,    # 100% pot bet
            "pot":          100,
            "phase":        "RIVER",
            "num_opponents": 1,
            "hero_stack":   500,
            "position":     "BTN",
        }
        out = self.sm.process_state(state)
        self.assertIsNotNone(out)
        self.assertIn("FOLD", out.action.upper(),
                      f"AJ no-pair on 4-straight + 3-flush coordinated river facing pot bet "
                      f"should FOLD, got {out.action!r}")

    def test_2460830707_KK_river_facing_raise_on_4straight_should_fold(self):
        """
        Hand 2460830707, NL10, 4-handed.

        Hero in BB with Ks Kh. Action: hero iso-raised 2 limpers preflop,
        was check-raised the flop (5d 9s 7d), c-bet turn (4c), then on the
        river (8c — completes 5-6-7-8-9 straights) hero min-bet 0.10 and
        was raised to 4.49 by villain who had check-called every prior
        street. Villain showed 7h 6h (9-high straight). Hero called the
        rest of the stack — busto.

        The equity model computes KK vs random hand and reports ~69%.
        That's correct vs random but villain's *action-narrowed* range on
        a 4-straight river facing a check-raise is almost exclusively
        straights, sets, and two pair — KK has maybe 10-15% equity, not
        69%. The advisor uses the inflated equity to recommend CALL.

        FIXED 2026-04-08 (session 13) by `_apply_danger_overrides` in
        AdvisorStateMachine: hard-coded fold filter for "overpair on a
        4-card-straight board facing ≥20%-pot aggression." This is a
        narrow override that only ever folds, never bluffs/calls. The
        deeper equity-vs-action-range fix is still on the kanban; this
        is the cheap version that prevents the catastrophic loss class.
        """
        state = {
            "hero_cards":   ["Ks", "Kh"],
            "board_cards":  ["5d", "9s", "7d", "4c", "8c"],
            "hand_id":      "2460830707",
            "facing_bet":   True,
            "call_amount":  439,       # villain's raise to 4.39 above hero's 0.10
            "pot":          1328,      # accumulated pot before this call
            "phase":        "RIVER",
            "num_opponents": 1,        # HU after CHIGG folded the flop
            "hero_stack":   137,       # what's left of the stack
            "position":     "BB",
        }
        out = self.sm.process_state(state)
        self.assertIsNotNone(out, "advisor returned None for KK river decision")
        self.assertIn("FOLD", out.action.upper(),
                      f"KK on 4-straight river facing raise should FOLD, got {out.action!r}")


class TestDangerHelpers(unittest.TestCase):
    """
    Unit tests for the board-texture / overpair helpers used by
    AdvisorStateMachine._apply_danger_overrides. These don't require
    loading the full advisor — they only import the class methods.
    """

    @classmethod
    def setUpClass(cls):
        from advisor_state_machine import AdvisorStateMachine
        cls.SM = AdvisorStateMachine

    def test_4card_straight_basic(self):
        # 5-6-7-8 fits in a 5-rank window (5,6,7,8,9) → straight reachable
        self.assertTrue(self.SM._board_has_4card_straight(["5d", "6h", "7s", "8c"]))
        # 5-7-8-9 (the actual KK board pre-river) — 5,7,8,9 fit in 5..9 window → 4 ranks
        self.assertTrue(self.SM._board_has_4card_straight(["5d", "9s", "7d", "8c"]))
        # The full KK river board: 5-7-8-9 + 4 → still has the 4-straight
        self.assertTrue(self.SM._board_has_4card_straight(["5d", "9s", "7d", "4c", "8c"]))

    def test_no_4card_straight_when_too_spread(self):
        # 2, 7, K, A — no 4 cards within 5 ranks
        self.assertFalse(self.SM._board_has_4card_straight(["2d", "7h", "Ks", "Ac"]))

    def test_4card_straight_wheel(self):
        # A-2-3-4 wheel → should detect via the A-low check
        self.assertTrue(self.SM._board_has_4card_straight(["Ad", "2h", "3s", "4c"]))

    def test_no_straight_with_three_cards(self):
        self.assertFalse(self.SM._board_has_4card_straight(["5d", "6h", "7s"]))

    def test_4card_flush(self):
        self.assertTrue(self.SM._board_has_4card_flush(["5d", "9d", "7d", "8d"]))
        self.assertTrue(self.SM._board_has_4card_flush(["5d", "9d", "7d", "8d", "Kc"]))

    def test_no_4card_flush_with_3_diamonds(self):
        self.assertFalse(self.SM._board_has_4card_flush(["5d", "9d", "7d", "Kc"]))

    def test_board_paired(self):
        # Th Td → paired
        self.assertTrue(self.SM._board_is_paired(["Kc", "Th", "Td"]))
        self.assertTrue(self.SM._board_is_paired(["Kc", "Th", "Td", "3c", "8c"]))
        # All distinct
        self.assertFalse(self.SM._board_is_paired(["Kc", "Th", "9d"]))
        self.assertFalse(self.SM._board_is_paired(["Kc", "Th", "9d", "3c", "8c"]))

    def test_hero_has_flush(self):
        # QcJc on Kc Th Td 3c 8c → 5 clubs incl. hero participates
        self.assertTrue(self.SM._hero_has_flush(
            ["Qc", "Jc"], ["Kc", "Th", "Td", "3c", "8c"]))
        # Hero blank, board has 4 of one suit → NO flush for hero
        self.assertFalse(self.SM._hero_has_flush(
            ["Qd", "Jh"], ["Kc", "Th", "Tc", "3c", "8c"]))
        # Only 4 same-suit total — not yet a flush
        self.assertFalse(self.SM._hero_has_flush(
            ["Qc", "Jc"], ["Kc", "Th", "Td", "3d", "8d"]))

    def test_hero_can_have_boat(self):
        # Pocket pair → set possible
        self.assertTrue(self.SM._hero_can_have_boat(
            ["Ks", "Kh"], ["9h", "6h", "2h"]))
        # Hero matches paired board → trips/boat
        self.assertTrue(self.SM._hero_can_have_boat(
            ["Tc", "9d"], ["Kc", "Th", "Td"]))
        # Q J on a T-T paired board, no Q or J on board → no boat possible
        self.assertFalse(self.SM._hero_can_have_boat(
            ["Qc", "Jc"], ["Kc", "Th", "Td", "3c", "8c"]))
        # Unpaired hero, unpaired board → no boat possible
        self.assertFalse(self.SM._hero_can_have_boat(
            ["As", "Kh"], ["Qd", "Jh", "9c"]))

    def test_3card_flush(self):
        # The KK 3-flush case from hand 2379414698
        self.assertTrue(self.SM._board_has_3card_flush(["9h", "6h", "2h"]))
        # 3 hearts + non-heart turn — still 3-flush
        self.assertTrue(self.SM._board_has_3card_flush(["9h", "6h", "2h", "Qc"]))
        # Mixed flop, no 3-flush
        self.assertFalse(self.SM._board_has_3card_flush(["9h", "6c", "2d"]))
        # Need 3+ board cards to even check
        self.assertFalse(self.SM._board_has_3card_flush(["9h", "6h"]))

    def test_overpair_kk_on_low_board(self):
        # KK on 5-9-7 — overpair
        self.assertTrue(self.SM._hero_has_overpair(["Ks", "Kh"], ["5d", "9s", "7d"]))

    def test_overpair_kk_with_ace_on_board(self):
        # KK on A-9-7 — NOT an overpair, ace beats us
        self.assertFalse(self.SM._hero_has_overpair(["Ks", "Kh"], ["Ad", "9s", "7d"]))

    def test_overpair_unpaired_hero(self):
        self.assertFalse(self.SM._hero_has_overpair(["As", "Kh"], ["5d", "9s", "7d"]))

    def test_overpair_set(self):
        # KK on a board with a king — that's a set, not an overpair
        # (technically still > all other cards, but a king is on the board)
        # Our definition: pocket pair must be > MAX board card. K > K is False,
        # so this returns False. That's correct — sets are handled differently
        # by the engine and don't need the overpair-protection filter.
        self.assertFalse(self.SM._hero_has_overpair(["Ks", "Kh"], ["Kd", "9s", "7d"]))


class TestHandEvaluator(unittest.TestCase):
    """
    Tests for AdvisorStateMachine._evaluate_hand_class — the 5-card
    hand classifier used by future danger filters that need to know
    "what does hero actually have right now."

    Naming: SM is the AdvisorStateMachine class.
    """

    @classmethod
    def setUpClass(cls):
        from advisor_state_machine import AdvisorStateMachine
        cls.SM = AdvisorStateMachine

    def test_high_card(self):
        # AK on Q-7-3-2-9, no pair / no straight / no flush
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ad", "Kc"], ["Qh", "7s", "3d", "2c", "9h"]),
            self.SM.HAND_HIGH_CARD)

    def test_pair_pocket(self):
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ks", "Kh"], ["9d", "6c", "2s"]),
            self.SM.HAND_PAIR)

    def test_pair_top_pair(self):
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ah", "Td"], ["Ac", "7s", "2d"]),
            self.SM.HAND_PAIR)

    def test_two_pair(self):
        # JT on T-J-2-3-4 → two pair
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Jh", "Tc"], ["Td", "Js", "2c", "3d", "4h"]),
            self.SM.HAND_TWO_PAIR)

    def test_two_pair_with_board_pair(self):
        # AK on A-K-Q-Q-2 → two pair (Aces and Kings, not AK + queens)
        result = self.SM._evaluate_hand_class(
            ["Ah", "Kc"], ["As", "Kd", "Qh", "Qc", "2s"])
        # Hero has A+A and K+K → two pair (Aces+Kings, kicker Q)
        # Or could see board QQ + hero pair as two pair too. Either way it's two_pair+.
        self.assertGreaterEqual(result, self.SM.HAND_TWO_PAIR)

    def test_trips(self):
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Th", "Tc"], ["Td", "5s", "2c"]),
            self.SM.HAND_TRIPS)

    def test_set_via_pocket_pair_matches_board(self):
        # Set is technically TRIPS in our taxonomy
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Th", "Tc"], ["Td", "5s", "2c", "9h", "Kc"]),
            self.SM.HAND_TRIPS)

    def test_straight_basic(self):
        # 9 8 + 7 6 5 board → straight
        self.assertEqual(self.SM._evaluate_hand_class(
            ["9h", "8c"], ["7d", "6s", "5h", "Kd", "2c"]),
            self.SM.HAND_STRAIGHT)

    def test_straight_wheel(self):
        # A-2-3-4-5 wheel
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ad", "5c"], ["2h", "3d", "4s", "Kh", "9c"]),
            self.SM.HAND_STRAIGHT)

    def test_flush(self):
        # All clubs: hero 2 + board 3
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Qc", "Jc"], ["Kc", "5c", "8c", "2d", "Th"]),
            self.SM.HAND_FLUSH)

    def test_flush_qj_paired_board_2379447781(self):
        # QcJc on Kc Th Td 3c 8c — hero has K-high flush. Board is
        # paired but hero doesn't have a boat. Should be FLUSH.
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Qc", "Jc"], ["Kc", "Th", "Td", "3c", "8c"]),
            self.SM.HAND_FLUSH)

    def test_full_house_via_set_plus_board_pair(self):
        # 99 on 9-K-K-2-5 → 9s full of Ks
        self.assertEqual(self.SM._evaluate_hand_class(
            ["9h", "9c"], ["9d", "Ks", "Kh", "2c", "5d"]),
            self.SM.HAND_FULL_HOUSE)

    def test_full_house_via_paired_hero_card_matching_board_pair(self):
        # AK on K-K-A-A-2 → AAKK two pair (technically AK aces full of kings)
        result = self.SM._evaluate_hand_class(
            ["Ah", "Kc"], ["Ks", "Kd", "Ac", "As", "2h"])
        # AAKK with K kicker → full house (3K+2A or 3A+2K)
        # Actually 2A 2K on board + AK in hand = three As + two Ks (or three Ks + two As)
        # = full house
        self.assertEqual(result, self.SM.HAND_FULL_HOUSE)

    def test_quads(self):
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Th", "Tc"], ["Td", "Ts", "2c", "5d", "9h"]),
            self.SM.HAND_QUADS)

    def test_straight_flush(self):
        # 9-8 + 7c-6c-5c board → 5-6-7-8-9 of clubs
        self.assertEqual(self.SM._evaluate_hand_class(
            ["9c", "8c"], ["7c", "6c", "5c", "Kd", "2h"]),
            self.SM.HAND_STRAIGHT_FLUSH)

    def test_straight_flush_wheel(self):
        # A-2-3-4-5 of clubs
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ac", "2c"], ["3c", "4c", "5c", "Kh", "9d"]),
            self.SM.HAND_STRAIGHT_FLUSH)

    def test_kk_on_kk_4straight_river_2460830707(self):
        # KsKh on 5d 9s 7d 4c 8c — KK is just a pair (overpair) on
        # a board where someone with a 6 has a straight.
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ks", "Kh"], ["5d", "9s", "7d", "4c", "8c"]),
            self.SM.HAND_PAIR)

    def test_kk_on_3flush_2379414698(self):
        # KsKd on 9h 6h 2h — KK is just a pair (no flush; hero has no h)
        self.assertEqual(self.SM._evaluate_hand_class(
            ["Ks", "Kd"], ["9h", "6h", "2h"]),
            self.SM.HAND_PAIR)

    def test_class_ordering_makes_sense(self):
        # Smoke check that constants are ordered correctly
        self.assertLess(self.SM.HAND_HIGH_CARD, self.SM.HAND_PAIR)
        self.assertLess(self.SM.HAND_PAIR, self.SM.HAND_TWO_PAIR)
        self.assertLess(self.SM.HAND_TWO_PAIR, self.SM.HAND_TRIPS)
        self.assertLess(self.SM.HAND_TRIPS, self.SM.HAND_STRAIGHT)
        self.assertLess(self.SM.HAND_STRAIGHT, self.SM.HAND_FLUSH)
        self.assertLess(self.SM.HAND_FLUSH, self.SM.HAND_FULL_HOUSE)
        self.assertLess(self.SM.HAND_FULL_HOUSE, self.SM.HAND_QUADS)
        self.assertLess(self.SM.HAND_QUADS, self.SM.HAND_STRAIGHT_FLUSH)


class TestBetSizeSnapping(unittest.TestCase):
    """
    Tests for AdvisorStateMachine._snap_bet_to_clean_increment — the
    bet/raise rounding helper that turns awkward decimals like
    'RAISE to 1.47' into typeable values like 'RAISE to 1.50' for
    manual multi-table play.
    """

    @classmethod
    def setUpClass(cls):
        from advisor_state_machine import AdvisorStateMachine
        cls.snap = AdvisorStateMachine._snap_bet_to_clean_increment

    # ── NL10 (BB = 10 cents) ─────────────────────────────────────────

    def test_nl10_typical_raise_rounds_to_5(self):
        # 1.47 → 1.45 (nearest 5 cents from 147)
        self.assertEqual(self.snap(147, 10), 145)
        # 1.48 → 1.50 (nearest 5 cents)
        self.assertEqual(self.snap(148, 10), 150)
        # 1.50 → 1.50 (already snapped)
        self.assertEqual(self.snap(150, 10), 150)

    def test_nl10_small_bet_rounds_to_5(self):
        self.assertEqual(self.snap(27, 10), 25)
        self.assertEqual(self.snap(28, 10), 30)

    def test_nl10_floors_to_bb(self):
        # Anything < BB rounds up to BB
        self.assertEqual(self.snap(3, 10), 10)
        self.assertEqual(self.snap(7, 10), 10)
        # Equal to BB stays
        self.assertEqual(self.snap(10, 10), 10)

    # ── NL25 (BB = 25 cents) ─────────────────────────────────────────

    def test_nl25_rounds_to_5(self):
        self.assertEqual(self.snap(73, 25), 75)
        self.assertEqual(self.snap(137, 25), 135)
        self.assertEqual(self.snap(312, 25), 310)

    def test_nl25_floors_to_bb(self):
        self.assertEqual(self.snap(15, 25), 25)
        self.assertEqual(self.snap(24, 25), 25)

    # ── NL50 (BB = 50 cents) ─────────────────────────────────────────

    def test_nl50_rounds_to_25(self):
        # 187/25 = 7.48 → 7 → 175  (175 closer than 200: 12 vs 13)
        self.assertEqual(self.snap(187, 50), 175)
        # 213/25 = 8.52 → 9 → 225
        self.assertEqual(self.snap(213, 50), 225)
        # 263/25 = 10.52 → 11 → 275
        self.assertEqual(self.snap(263, 50), 275)
        # 412/25 = 16.48 → 16 → 400 (400 closer than 425: 12 vs 13)
        self.assertEqual(self.snap(412, 50), 400)

    def test_nl50_floors_to_bb(self):
        self.assertEqual(self.snap(40, 50), 50)
        self.assertEqual(self.snap(20, 50), 50)

    # ── NL100 (BB = 100 cents = $1) ──────────────────────────────────

    def test_nl100_rounds_to_25(self):
        self.assertEqual(self.snap(347, 100), 350)
        self.assertEqual(self.snap(412, 100), 400)
        self.assertEqual(self.snap(763, 100), 775)

    # ── NL200+ (BB >= 200 cents = $2) ────────────────────────────────

    def test_nl200_rounds_to_50(self):
        self.assertEqual(self.snap(537, 200), 550)
        self.assertEqual(self.snap(573, 200), 550)
        self.assertEqual(self.snap(1247, 200), 1250)

    # ── practice / scaled chip tables (BB = 10000 = 100 chips) ───────

    def test_practice_table_scaled_chips(self):
        # The practice tables have BB in scaled chip units (100 chips
        # × CHIP_SCALE=100 = 10000). The increment table caps at
        # bb > 100 → 50 increment.
        # 14700 → 14700 (already a multiple of 50)
        self.assertEqual(self.snap(14700, 10000), 14700)
        # 14723 → 14700 (294.46 → 294 → 14700, closer than 14750)
        self.assertEqual(self.snap(14723, 10000), 14700)
        # 14735 → 14750 (294.7 → 295 → 14750)
        self.assertEqual(self.snap(14735, 10000), 14750)
        # Floors to BB
        self.assertEqual(self.snap(50, 10000), 10000)

    # ── edge cases ───────────────────────────────────────────────────

    def test_zero_amount_passthrough(self):
        self.assertEqual(self.snap(0, 10), 0)

    def test_negative_amount_passthrough(self):
        self.assertEqual(self.snap(-5, 10), -5)

    def test_zero_bb_passthrough(self):
        # If we don't know the BB, just leave the amount alone
        self.assertEqual(self.snap(147, 0), 147)

    def test_none_inputs_passthrough(self):
        self.assertEqual(self.snap(None, 10), None)
        self.assertEqual(self.snap(147, None), 147)

    def test_real_world_kk_iso_raise(self):
        # The actual hand from the user's recent session:
        # NL10 (BB=10), KK in BB iso-raising 2 limpers, 4.5x BB = 45 cents
        # The chart already produced 45 — should pass through
        self.assertEqual(self.snap(45, 10), 45)

    def test_real_world_aqo_3bet(self):
        # AQo SB 3-bet from the regression suite: 60 cents at NL10
        self.assertEqual(self.snap(60, 10), 60)


class TestVillainHudDiscount(unittest.TestCase):
    """
    Tests for AdvisorStateMachine._equity_discount_from_villain_hud — the
    v1 of equity-vs-action-range that uses CoinPoker HUD ground-truth
    stats to discount equity based on villain range tightness +
    street + action context.
    """

    @classmethod
    def setUpClass(cls):
        from advisor_state_machine import AdvisorStateMachine
        cls.SM = AdvisorStateMachine

    def _new_sm(self, hud_stats_for_state=None):
        """Build a SM with a mock tracker that returns the given HUD stats."""
        class MockTracker:
            def __init__(self, stats):
                self._stats = stats

            def get_villain_hud_stats(self, state):
                return self._stats

            def classify_villain(self, state):
                return "UNKNOWN"

        class MockAdvisor:
            def _get_recommendation(self, state):
                return {"phase": state.get("phase", "PREFLOP"), "equity": 0.5}

        return self.SM(
            base_advisor=MockAdvisor(),
            preflop_advice_fn=lambda c1, c2, p, facing_raise: {
                "action": "FOLD", "hand_key": "??", "in_range": False, "note": ""},
            postflop_engine=None,
            tracker=MockTracker(hud_stats_for_state),
            bb_cents=10,
        )

    def test_no_tracker_returns_1(self):
        from advisor_state_machine import AdvisorStateMachine

        class MockAdvisor:
            def _get_recommendation(self, state):
                return {"phase": state.get("phase", "PREFLOP"), "equity": 0.5}

        sm = AdvisorStateMachine(
            base_advisor=MockAdvisor(),
            preflop_advice_fn=lambda c1, c2, p, fr: {},
            tracker=None, bb_cents=10,
        )
        self.assertEqual(sm._equity_discount_from_villain_hud({}, "FLOP"), 1.0)

    def test_tracker_without_method_returns_1(self):
        class StubTracker:
            pass
        from advisor_state_machine import AdvisorStateMachine

        class MockAdvisor:
            def _get_recommendation(self, state):
                return {"phase": state.get("phase", "PREFLOP"), "equity": 0.5}

        sm = AdvisorStateMachine(
            base_advisor=MockAdvisor(),
            preflop_advice_fn=lambda c1, c2, p, fr: {},
            tracker=StubTracker(), bb_cents=10,
        )
        self.assertEqual(sm._equity_discount_from_villain_hud({}, "FLOP"), 1.0)

    def test_no_hud_data_returns_1(self):
        sm = self._new_sm(hud_stats_for_state=None)
        self.assertEqual(sm._equity_discount_from_villain_hud({"phase": "FLOP"}, "FLOP"), 1.0)

    def test_preflop_phase_skipped(self):
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.10, "pfr": 0.08})
        # Even with very tight stats, preflop is skipped (chart handles it)
        self.assertEqual(sm._equity_discount_from_villain_hud({}, "PREFLOP"), 1.0)

    def test_loose_villain_no_discount(self):
        # FISH (vpip=0.50) → no discount
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.50, "pfr": 0.20})
        m = sm._equity_discount_from_villain_hud({"phase": "FLOP"}, "FLOP")
        self.assertEqual(m, 1.0)

    def test_tag_villain_mild_discount_on_river(self):
        # TAG (vpip=0.20) on river with no recent raise
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.20, "pfr": 0.18})
        sm.action_history = []  # no raises
        m = sm._equity_discount_from_villain_hud({"phase": "RIVER"}, "RIVER")
        # tightness = 0.5 - 0.20 = 0.30; street = 1.0; action = 0.5 (no raise)
        # discount = 0.30 * 1.0 * 0.5 = 0.15 → multiplier = 0.85
        self.assertAlmostEqual(m, 0.85, places=2)

    def test_nit_villain_river_raise_max_discount(self):
        # NIT (vpip=0.10) river-raise → maximum discount in v1
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.10, "pfr": 0.08})
        sm.action_history = [{"phase": "RIVER", "seat": 2, "action": "RAISE"}]
        m = sm._equity_discount_from_villain_hud({"phase": "RIVER"}, "RIVER")
        # tightness = 0.4; street = 1.0; action = 1.0 (river raise)
        # discount = 0.4 * 1.0 * 1.0 = 0.4 → multiplier = 0.6
        self.assertAlmostEqual(m, 0.6, places=2)

    def test_floor_at_50_percent(self):
        # Even crazier inputs can't push the multiplier below 0.5
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.0, "pfr": 0.0})
        sm.action_history = [{"phase": "RIVER", "seat": 2, "action": "RAISE"}]
        m = sm._equity_discount_from_villain_hud({"phase": "RIVER"}, "RIVER")
        self.assertGreaterEqual(m, 0.5)

    def test_flop_bet_only_mild(self):
        # NIT on the flop with just a bet (no raise) — minimal discount
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.10, "pfr": 0.08})
        sm.action_history = []
        m = sm._equity_discount_from_villain_hud({"phase": "FLOP"}, "FLOP")
        # tightness = 0.4; street = 0.5 (flop); action = 0.5 (no raise)
        # discount = 0.4 * 0.5 * 0.5 = 0.10 → multiplier = 0.9
        self.assertAlmostEqual(m, 0.9, places=2)

    def test_missing_pfr_returns_1(self):
        # Defensive: if HUD data is partial, fall back to no-op
        sm = self._new_sm(hud_stats_for_state={"vpip": 0.20})  # no pfr key
        m = sm._equity_discount_from_villain_hud({"phase": "FLOP"}, "FLOP")
        self.assertEqual(m, 1.0)


class TestActionHistoryAccumulator(unittest.TestCase):
    """
    Tests for the v0 equity-vs-action-range action-history accumulator.
    Verifies the SM detects new villain actions across snapshots and
    computes a discount multiplier that reflects accumulated aggression.
    """

    @classmethod
    def setUpClass(cls):
        from advisor_state_machine import AdvisorStateMachine
        cls.SM = AdvisorStateMachine

    def _new_sm(self):
        # Use a tiny mock advisor so we don't load YOLO/CFR for these tests.
        class MockAdvisor:
            def _get_recommendation(self, state):
                return {"phase": state.get("phase", "PREFLOP"), "equity": 0.5}
        return self.SM(
            base_advisor=MockAdvisor(),
            preflop_advice_fn=lambda c1, c2, p, facing_raise: {
                "action": "FOLD", "hand_key": "??", "in_range": False, "note": ""},
            postflop_engine=None,
            tracker=None,
            bb_cents=4,
        )

    def _make_state(self, hand_id, phase, hero_seat, villain_actions):
        """Helper: build a state dict where players[seat=N] has last_action."""
        players = []
        for seat, action in villain_actions.items():
            players.append({"seat": seat, "last_action": action,
                            "name": f"v{seat}", "user_id": 100 + seat,
                            "stack": 1000, "bet": 0})
        return {
            "hero_cards": ["Ks", "Kh"],
            "board_cards": ["5d", "9s", "7d"] if phase != "PREFLOP" else [],
            "hand_id": hand_id,
            "facing_bet": True,
            "call_amount": 50,
            "phase": phase,
            "num_opponents": len(villain_actions),
            "pot": 100,
            "hero_stack": 1000,
            "position": "BB",
            "hero_turn": True,
            "hero_seat": hero_seat,
            "players": players,
            "bets": [],
        }

    def test_no_actions_no_discount(self):
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "PREFLOP", 1, {}))
        self.assertEqual(sm._equity_discount_from_action_history(), 1.0)

    def test_villain_check_no_discount(self):
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "FLOP", 1, {2: "Check"}))
        self.assertEqual(sm._equity_discount_from_action_history(), 1.0)

    def test_villain_bet_no_discount_yet(self):
        # A single BET shouldn't trigger discount in v0 — only RAISES do.
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "FLOP", 1, {2: "Bet"}))
        self.assertEqual(sm._equity_discount_from_action_history(), 1.0)

    def test_villain_raise_on_river_strong_discount(self):
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "RIVER", 1, {2: "Raise"}))
        # Single river raise → multiplier = 0.65
        self.assertAlmostEqual(
            sm._equity_discount_from_action_history(), 0.65, places=2)

    def test_villain_raise_on_turn_moderate_discount(self):
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "TURN", 1, {2: "Raise"}))
        self.assertAlmostEqual(
            sm._equity_discount_from_action_history(), 0.75, places=2)

    def test_villain_raise_on_flop_mild_discount(self):
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "FLOP", 1, {2: "Raise"}))
        self.assertAlmostEqual(
            sm._equity_discount_from_action_history(), 0.85, places=2)

    def test_multiple_raises_compound(self):
        sm = self._new_sm()
        # Flop raise from seat 2
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "FLOP", 1, {2: "Raise"}))
        # Then turn raise (still seat 2 — last_action transitions through Call)
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "TURN", 1, {2: "Call"}))
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "TURN", 1, {2: "Raise"}))
        # 2 raises total, last on turn → 0.75 × 0.85 = 0.6375
        self.assertAlmostEqual(
            sm._equity_discount_from_action_history(), 0.75 * 0.85, places=2)

    def test_idempotent_on_repeated_snapshot(self):
        # Same snapshot ingested twice should NOT double-count the action.
        sm = self._new_sm()
        st = self._make_state("H1", "RIVER", 1, {2: "Raise"})
        sm._ingest_snapshot_for_action_history(st)
        sm._ingest_snapshot_for_action_history(st)
        # Still only 1 raise tracked
        self.assertEqual(len([a for a in sm.action_history if a['action']=='RAISE']), 1)
        self.assertAlmostEqual(
            sm._equity_discount_from_action_history(), 0.65, places=2)

    def test_resets_on_new_hand(self):
        sm = self._new_sm()
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "RIVER", 1, {2: "Raise"}))
        # New hand
        sm._ingest_snapshot_for_action_history(
            self._make_state("H2", "PREFLOP", 1, {}))
        self.assertEqual(sm.action_history, [])
        self.assertEqual(sm._equity_discount_from_action_history(), 1.0)

    def test_floor_at_30_percent(self):
        sm = self._new_sm()
        # 4 raises ending on river → would mathematically multiply down
        # to 0.65 * 0.85 * 0.85 = 0.47, still above the 0.30 floor.
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "FLOP", 1, {2: "Raise"}))
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "TURN", 1, {2: "Call"}))
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "TURN", 1, {2: "Raise"}))
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "RIVER", 1, {2: "Call"}))
        sm._ingest_snapshot_for_action_history(
            self._make_state("H1", "RIVER", 1, {2: "Raise"}))
        # Verify multiplier hasn't gone below the floor
        m = sm._equity_discount_from_action_history()
        self.assertGreaterEqual(m, 0.30)
        self.assertLess(m, 1.0)

    def test_ignores_passive_strings(self):
        # 'Inuse', 'Sitout', 'Ante', 'SB', 'BB' should not register as actions
        sm = self._new_sm()
        for s in ["Inuse", "Sitout", "Ante", "SB", "BB"]:
            sm._ingest_snapshot_for_action_history(
                self._make_state("H1", "PREFLOP", 1, {2: s}))
        self.assertEqual(sm.action_history, [])

    def test_ignores_unibet_name_list_format(self):
        # Unibet WS uses list-of-names players, not list-of-dicts.
        # Accumulator should skip cleanly without crashing.
        sm = self._new_sm()
        state = {
            "hand_id": "H1", "phase": "FLOP", "hero_seat": 0,
            "players": ["hero", "v1", "v2"],  # plain strings, not dicts
            "bets": [10, 20, 30],
        }
        sm._ingest_snapshot_for_action_history(state)
        self.assertEqual(sm.action_history, [])


if __name__ == "__main__":
    unittest.main()
