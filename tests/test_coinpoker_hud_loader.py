"""
Tests for vision.coinpoker_hud_loader.

Builds synthetic JSONL files in a temp directory and verifies the
loader correctly parses them, prefers the freshest record per user_id,
reloads on mtime change, and classifies player types using the same
thresholds as OpponentTracker.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vision"))

from coinpoker_hud_loader import CoinPokerHudLoader


def _wrap_player(user_id, ratios, timestamp=None):
    """Build a single player record matching the API response shape."""
    return {
        "url": f"https://nxtgenapi.thecloudinfra.com/pbshots/v2/stats/cash?user_id={user_id}",
        "method": "GET",
        "status": 200,
        "ts": time.time(),
        "body_json": {
            "status": "success",
            "api_version": "1.0.0",
            "api_code": 1,
            "response": {
                "data": [
                    {
                        "user_id": user_id,
                        "version": "v1.0.0",
                        "timestamp": timestamp or int(time.time()),
                        "ratios": [{"mini_game_type": 1, **ratios}],
                    }
                ]
            },
        },
    }


def _wrap_batch(players_dict, timestamp=None):
    """Build a multi-player record (the typical CoinPoker batched response)."""
    base_ts = timestamp or int(time.time())
    data = []
    for uid, ratios in players_dict.items():
        data.append({
            "user_id": uid,
            "version": "v1.0.0",
            "timestamp": base_ts,
            "ratios": [{"mini_game_type": 1, **ratios}],
        })
    return {
        "url": "https://nxtgenapi.thecloudinfra.com/pbshots/v2/stats/cash",
        "method": "GET",
        "status": 200,
        "ts": time.time(),
        "body_json": {
            "status": "success",
            "response": {"data": data},
        },
    }


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestEmptyAndMissing(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        loader = CoinPokerHudLoader(path="C:/nonexistent_path_for_test.jsonl")
        self.assertEqual(len(loader), 0)
        self.assertIsNone(loader.get_stats(123))
        self.assertEqual(loader.classify(123), "UNKNOWN")

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write("")
            tmp = f.name
        try:
            loader = CoinPokerHudLoader(path=tmp)
            self.assertEqual(len(loader), 0)
            self.assertIsNone(loader.get_stats(523576))
        finally:
            os.unlink(tmp)


class TestSinglePlayerLookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        _write_jsonl(self.tmp, [
            _wrap_player(523576, {
                "vpip": 0.2313, "pfr": 0.1928, "3bet": 0.1056, "cbet": 0.571,
                "check_raise": 0.1013, "fold_to_3bet": 0.4623, "fold_to_cbet": 0.2897,
                "steal": 0.3632, "wsd": 0.5849, "wtsd": 0.3508,
                "allin": 0.0021, "fta": 0.5565, "fold": 0.7805,
            }, timestamp=1775616044),
        ])
        self.loader = CoinPokerHudLoader(path=self.tmp)

    def tearDown(self):
        os.unlink(self.tmp)

    def test_get_stats_returns_full_dict(self):
        s = self.loader.get_stats(523576)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["vpip"], 0.2313)
        self.assertAlmostEqual(s["pfr"], 0.1928)
        self.assertAlmostEqual(s["3bet"], 0.1056)
        self.assertEqual(s["_timestamp"], 1775616044)

    def test_unknown_user_returns_none(self):
        self.assertIsNone(self.loader.get_stats(999999))
        self.assertEqual(self.loader.classify(999999), "UNKNOWN")

    def test_known_user_classified_as_tag(self):
        # vpip 23%, pfr 19%, 3bet 10.5% — TAG range
        self.assertEqual(self.loader.classify(523576), "TAG")

    def test_string_user_id_accepted(self):
        # The runner might pass user_id as a string in some paths.
        s = self.loader.get_stats("523576")
        self.assertIsNotNone(s)

    def test_none_user_id_safe(self):
        self.assertIsNone(self.loader.get_stats(None))
        self.assertEqual(self.loader.classify(None), "UNKNOWN")


class TestClassificationThresholds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        # One record per archetype
        _write_jsonl(self.tmp, [
            _wrap_batch({
                # NIT: vpip < 15%
                100001: {"vpip": 0.10, "pfr": 0.08, "3bet": 0.04},
                # TAG: vpip 18-28, pfr 15-22
                100002: {"vpip": 0.22, "pfr": 0.18, "3bet": 0.07},
                # LAG: vpip 28-40, pfr > 20, 3bet > 10
                100003: {"vpip": 0.32, "pfr": 0.25, "3bet": 0.13},
                # FISH: vpip > 50 but < 70
                100004: {"vpip": 0.55, "pfr": 0.20, "3bet": 0.05},
                # WHALE: vpip > 70
                100005: {"vpip": 0.75, "pfr": 0.30, "3bet": 0.05},
                # FISH-passive: vpip mid, pfr low (treated as fish, not nit)
                100006: {"vpip": 0.20, "pfr": 0.08, "3bet": 0.02},
            }),
        ])
        self.loader = CoinPokerHudLoader(path=self.tmp)

    def tearDown(self):
        os.unlink(self.tmp)

    def test_nit(self):
        self.assertEqual(self.loader.classify(100001), "NIT")

    def test_tag(self):
        self.assertEqual(self.loader.classify(100002), "TAG")

    def test_lag(self):
        self.assertEqual(self.loader.classify(100003), "LAG")

    def test_fish(self):
        self.assertEqual(self.loader.classify(100004), "FISH")

    def test_whale(self):
        self.assertEqual(self.loader.classify(100005), "WHALE")

    def test_passive_mid_vpip_is_fish(self):
        # Mid-VPIP + low PFR = passive caller, classified as fish
        self.assertEqual(self.loader.classify(100006), "FISH")


class TestFreshnessAndReload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name

    def tearDown(self):
        os.unlink(self.tmp)

    def test_keeps_latest_timestamp_per_user(self):
        # Two records for the same user, second is fresher
        _write_jsonl(self.tmp, [
            _wrap_player(523576, {"vpip": 0.10, "pfr": 0.05}, timestamp=1000),
            _wrap_player(523576, {"vpip": 0.40, "pfr": 0.30}, timestamp=2000),
        ])
        loader = CoinPokerHudLoader(path=self.tmp)
        s = loader.get_stats(523576)
        self.assertAlmostEqual(s["vpip"], 0.40)  # the fresher one
        self.assertEqual(s["_timestamp"], 2000)

    def test_keeps_latest_even_when_second_is_older(self):
        # Out-of-order: fresher record appears first in the file
        _write_jsonl(self.tmp, [
            _wrap_player(523576, {"vpip": 0.40, "pfr": 0.30}, timestamp=2000),
            _wrap_player(523576, {"vpip": 0.10, "pfr": 0.05}, timestamp=1000),
        ])
        loader = CoinPokerHudLoader(path=self.tmp)
        s = loader.get_stats(523576)
        self.assertAlmostEqual(s["vpip"], 0.40)
        self.assertEqual(s["_timestamp"], 2000)

    def test_reload_picks_up_new_data(self):
        _write_jsonl(self.tmp, [
            _wrap_player(523576, {"vpip": 0.10, "pfr": 0.05}, timestamp=1000),
        ])
        loader = CoinPokerHudLoader(path=self.tmp)
        self.assertEqual(loader.classify(523576), "NIT")
        # Now another player gets sniffed; loader should pick it up.
        # Sleep briefly so mtime ticks (FAT mtime resolution is 2s on
        # some systems but Python uses ns precision on NTFS).
        time.sleep(0.05)
        _write_jsonl(self.tmp, [
            _wrap_player(523576, {"vpip": 0.10, "pfr": 0.05}, timestamp=1000),
            _wrap_player(637530, {"vpip": 0.32, "pfr": 0.19, "3bet": 0.09}, timestamp=2000),
        ])
        # Force the reload by accessing
        s = loader.get_stats(637530)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["vpip"], 0.32)
        self.assertEqual(loader.classify(637530), "TAG")

    def test_known_user_ids_returns_sorted(self):
        _write_jsonl(self.tmp, [
            _wrap_batch({
                300: {"vpip": 0.2, "pfr": 0.15},
                100: {"vpip": 0.2, "pfr": 0.15},
                200: {"vpip": 0.2, "pfr": 0.15},
            }),
        ])
        loader = CoinPokerHudLoader(path=self.tmp)
        self.assertEqual(loader.known_user_ids(), [100, 200, 300])


class TestMalformedInput(unittest.TestCase):
    def test_malformed_json_lines_skipped(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("not json at all\n")
                f.write(json.dumps(_wrap_player(
                    523576, {"vpip": 0.22, "pfr": 0.18}
                )) + "\n")
                f.write("{another bad line\n")
            loader = CoinPokerHudLoader(path=tmp)
            self.assertEqual(loader.classify(523576), "TAG")
            self.assertEqual(len(loader), 1)
        finally:
            os.unlink(tmp)

    def test_failed_response_skipped(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        try:
            _write_jsonl(tmp, [
                {
                    "url": "x", "method": "GET", "status": 500,
                    "body_json": {"status": "error", "message": "rate limited"},
                },
                _wrap_player(523576, {"vpip": 0.22, "pfr": 0.18}),
            ])
            loader = CoinPokerHudLoader(path=tmp)
            self.assertEqual(len(loader), 1)
            self.assertEqual(loader.classify(523576), "TAG")
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
