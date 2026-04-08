"""
Tests for vision.coinpoker_clicker — the Python side of the action
injector. These tests stub the control file and pause flag to temp
locations so they don't touch any real CoinPoker state.

Tests are intentionally pure: they verify the contract of the
control-file writer (validation, atomicity, pause-respecting,
queue-rejection) without ever touching the real injector DLL or the
Unity table process. The Phase 2 round-trip against the actual IL
injection is verified manually with the patched DLL deployed.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from coinpoker_clicker import (
    CoinPokerClicker,
    VALID_ACTIONS,
)


class _TempPaths:
    """Context-manager helper that yields a fresh control file + pause
    flag + inject log inside a temp dir."""
    def __init__(self):
        self.tmpdir = None
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="clicker_test_")
        return (
            os.path.join(self.tmpdir, "pending_action.json"),
            os.path.join(self.tmpdir, ".autoplay_pause"),
            os.path.join(self.tmpdir, "inject.log"),
        )
    def __exit__(self, *exc):
        import shutil
        try:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
        except Exception:
            pass


class TestPauseFlag(unittest.TestCase):
    def test_default_paused_creates_flag(self):
        with _TempPaths() as (ctrl, pause, log):
            self.assertFalse(os.path.exists(pause))
            c = CoinPokerClicker(ctrl, pause, log, default_paused=True)
            self.assertTrue(c.is_paused())
            self.assertTrue(os.path.exists(pause))

    def test_default_unpaused(self):
        with _TempPaths() as (ctrl, pause, log):
            c = CoinPokerClicker(ctrl, pause, log, default_paused=False)
            self.assertFalse(c.is_paused())

    def test_resume_then_pause(self):
        with _TempPaths() as (ctrl, pause, log):
            c = CoinPokerClicker(ctrl, pause, log, default_paused=True)
            c.resume()
            self.assertFalse(c.is_paused())
            c.pause()
            self.assertTrue(c.is_paused())


class TestRequestActionValidation(unittest.TestCase):
    def setUp(self):
        self.ctx = _TempPaths()
        self.ctrl, self.pause, self.log = self.ctx.__enter__()
        self.clicker = CoinPokerClicker(
            self.ctrl, self.pause, self.log, default_paused=False)

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_valid_fold_writes_control_file(self):
        ok = self.clicker.request_action("FOLD", "H1")
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.ctrl))
        with open(self.ctrl, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["action"], "FOLD")
        self.assertEqual(payload["handId"], "H1")
        self.assertIsNone(payload["size"])
        self.assertIn("ts", payload)
        self.assertEqual(self.clicker.requests_sent, 1)

    def test_lowercase_normalized(self):
        ok = self.clicker.request_action("fold", "H2")
        self.assertTrue(ok)
        with open(self.ctrl) as f:
            self.assertEqual(json.load(f)["action"], "FOLD")

    def test_raise_with_size(self):
        ok = self.clicker.request_action("RAISE", "H3", size=250.0,
                                          reason="3.5BB open")
        self.assertTrue(ok)
        with open(self.ctrl) as f:
            payload = json.load(f)
        self.assertEqual(payload["action"], "RAISE")
        self.assertEqual(payload["size"], 250.0)
        self.assertEqual(payload["reason"], "3.5BB open")

    def test_invalid_action_rejected(self):
        ok = self.clicker.request_action("MUCK", "H1")
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.ctrl))
        self.assertEqual(self.clicker.requests_blocked, 1)

    def test_negative_size_rejected(self):
        ok = self.clicker.request_action("RAISE", "H1", size=-50.0)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.ctrl))

    def test_all_valid_actions_accepted(self):
        for action in VALID_ACTIONS:
            # Drain the previous request so we don't trip the queue check
            if os.path.exists(self.ctrl):
                os.unlink(self.ctrl)
            ok = self.clicker.request_action(action, f"H_{action}")
            self.assertTrue(ok, f"action {action} unexpectedly rejected")


class TestRequestActionGuards(unittest.TestCase):
    def setUp(self):
        self.ctx = _TempPaths()
        self.ctrl, self.pause, self.log = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_paused_blocks_request(self):
        c = CoinPokerClicker(self.ctrl, self.pause, self.log, default_paused=True)
        ok = c.request_action("FOLD", "H1")
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.ctrl))
        self.assertEqual(c.requests_blocked, 1)

    def test_pending_action_blocks_new_request(self):
        c = CoinPokerClicker(self.ctrl, self.pause, self.log, default_paused=False)
        self.assertTrue(c.request_action("CALL", "H1"))
        # Second request before the injector consumed the first → drop
        ok = c.request_action("FOLD", "H1")
        self.assertFalse(ok)
        with open(self.ctrl) as f:
            payload = json.load(f)
        self.assertEqual(payload["action"], "CALL")  # original wins
        self.assertEqual(c.requests_blocked, 1)

    def test_request_succeeds_after_injector_consumes(self):
        c = CoinPokerClicker(self.ctrl, self.pause, self.log, default_paused=False)
        self.assertTrue(c.request_action("CALL", "H1"))
        # Simulate the IL injector picking it up
        os.unlink(self.ctrl)
        # Now a new request goes through
        self.assertTrue(c.request_action("RAISE", "H1", size=100.0))


class TestHandIdStaleness(unittest.TestCase):
    """
    Hand-id staleness check: if a current_hand_provider is wired up,
    request_action must reject any request whose hand_id doesn't match
    the live observed hand. Prevents firing an action for a hand that's
    already over.
    """

    def setUp(self):
        self.ctx = _TempPaths()
        self.ctrl, self.pause, self.log = self.ctx.__enter__()
        self.live_hand = "H1"
        self.clicker = CoinPokerClicker(
            self.ctrl, self.pause, self.log,
            default_paused=False,
            current_hand_provider=lambda: self.live_hand,
        )

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_matching_hand_accepted(self):
        ok = self.clicker.request_action("FOLD", hand_id="H1")
        self.assertTrue(ok)
        self.assertEqual(self.clicker.requests_sent, 1)
        self.assertEqual(self.clicker.requests_stale, 0)

    def test_stale_hand_rejected(self):
        ok = self.clicker.request_action("FOLD", hand_id="H99")
        self.assertFalse(ok)
        self.assertEqual(self.clicker.requests_sent, 0)
        self.assertEqual(self.clicker.requests_stale, 1)
        self.assertFalse(os.path.exists(self.ctrl))

    def test_provider_returns_none_rejects(self):
        # If the provider returns None (e.g. no frames seen yet) we
        # treat that as "no live hand" and reject — better safe than sorry.
        self.live_hand = None
        ok = self.clicker.request_action("FOLD", hand_id="H1")
        self.assertFalse(ok)
        self.assertEqual(self.clicker.requests_stale, 1)

    def test_provider_exception_rejects(self):
        def boom():
            raise RuntimeError("frame stream dead")
        c = CoinPokerClicker(
            self.ctrl, self.pause, self.log,
            default_paused=False,
            current_hand_provider=boom,
        )
        ok = c.request_action("FOLD", hand_id="H1")
        self.assertFalse(ok)
        self.assertEqual(c.requests_stale, 1)

    def test_no_provider_skips_check(self):
        # Backwards compat: if no provider is wired, the check is bypassed.
        c = CoinPokerClicker(
            self.ctrl, self.pause, self.log, default_paused=False,
        )
        ok = c.request_action("FOLD", hand_id="anything")
        self.assertTrue(ok)


class TestAtomicWrite(unittest.TestCase):
    def test_no_partial_file_on_disk(self):
        # The control file should never appear in a partial state — we
        # write to a temp file and rename. This is hard to fully prove
        # without a process-level race, but we can at least confirm
        # there's no .tmp file left after a successful write and that
        # the final file is valid JSON.
        with _TempPaths() as (ctrl, pause, log):
            c = CoinPokerClicker(ctrl, pause, log, default_paused=False)
            c.request_action("RAISE", "H1", size=125.5)

            target_dir = os.path.dirname(ctrl)
            leftovers = [f for f in os.listdir(target_dir) if f.startswith(".pending_")]
            self.assertEqual(leftovers, [])

            with open(ctrl) as f:
                payload = json.load(f)  # must be parseable
            self.assertEqual(payload["action"], "RAISE")
            self.assertEqual(payload["size"], 125.5)


class TestTailInjectLog(unittest.TestCase):
    def test_returns_empty_when_log_missing(self):
        with _TempPaths() as (ctrl, pause, log):
            c = CoinPokerClicker(ctrl, pause, log)
            lines, off = c.tail_inject_log(0)
            self.assertEqual(lines, [])
            self.assertEqual(off, 0)

    def test_reads_appended_lines(self):
        with _TempPaths() as (ctrl, pause, log):
            c = CoinPokerClicker(ctrl, pause, log)
            with open(log, "w", encoding="utf-8") as f:
                f.write("line1\nline2\n")
            lines, off = c.tail_inject_log(0)
            self.assertEqual(lines, ["line1", "line2"])
            self.assertGreater(off, 0)
            # Second tail starts at the previous offset
            with open(log, "a", encoding="utf-8") as f:
                f.write("line3\n")
            new_lines, new_off = c.tail_inject_log(off)
            self.assertEqual(new_lines, ["line3"])
            self.assertGreater(new_off, off)


if __name__ == "__main__":
    unittest.main(verbosity=2)
