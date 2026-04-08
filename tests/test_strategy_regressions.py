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


if __name__ == "__main__":
    unittest.main()
