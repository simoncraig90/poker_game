"""
Loader for CoinPoker's server-side HUD stats.

CoinPoker's lobby fetches per-player stats from a REST endpoint
(`https://nxtgenapi.thecloudinfra.com/pbshots/v2/stats/cash?user_id=...`).
The endpoint returns the same VPIP/PFR/3-bet/etc. numbers that drive
CoinPoker's built-in HUD popup. `tools/coinpoker_stats_sniffer.py`
captures these responses to a JSONL file; this module reads that file
and exposes per-player stats by ``user_id``.

This is the SERVER-SIDE GROUND TRUTH for opponent profiling. It's
meant to be used ALONGSIDE `OpponentTracker` (which accumulates stats
from observed actions during a session) so we can compare classifications
and validate or replace our tracker's output.

Design notes:
  - Loader is read-only on the JSONL file. The sniffer writes; we read.
  - Lazy reload: file mtime is checked on every lookup. If the file has
    grown (sniffer captured new data), we re-parse. No background thread.
  - Multiple captures of the same user_id keep the LATEST by timestamp,
    so the loader always reflects the freshest server state.
  - Returns None for unknown users (graceful fallback). Caller is
    expected to fall through to OpponentTracker or another source.
  - Classification uses the SAME thresholds as `OpponentTracker._classify`
    so direct comparison is meaningful (apples to apples, not apples to
    oranges).

Usage:
    loader = CoinPokerHudLoader()
    stats = loader.get_stats(user_id=523576)
    # stats is a dict with vpip, pfr, 3bet, etc., or None
    cls = loader.classify(user_id=523576)
    # cls is one of FISH/NIT/TAG/LAG/WHALE/UNKNOWN
"""
from __future__ import annotations

import json
import os
from typing import Optional


DEFAULT_HUD_PATH = r"C:\Users\Simon\coinpoker_hud_stats.jsonl"


class CoinPokerHudLoader:
    """
    Reads CoinPoker HUD stats sniffed from the /v2/stats/cash endpoint.

    The loader is intentionally simple: it re-reads the JSONL file when
    its mtime changes (so a long-running session picks up new captures
    as the sniffer logs them) and keeps the most recent stats per
    user_id by the API's `timestamp` field.
    """

    def __init__(self, path: str = DEFAULT_HUD_PATH):
        self._path = path
        self._mtime = 0.0
        self._stats_by_user: dict[int, dict] = {}

    @property
    def path(self) -> str:
        return self._path

    def __len__(self) -> int:
        self._maybe_reload()
        return len(self._stats_by_user)

    def known_user_ids(self) -> list[int]:
        self._maybe_reload()
        return sorted(self._stats_by_user.keys())

    # ── reload ────────────────────────────────────────────────────────────

    def _maybe_reload(self) -> None:
        """Check the file's mtime; if changed (or first call), re-parse."""
        if not os.path.exists(self._path):
            self._stats_by_user = {}
            self._mtime = 0.0
            return
        try:
            current_mtime = os.path.getmtime(self._path)
        except OSError:
            return
        if current_mtime == self._mtime:
            return
        self._mtime = current_mtime
        self._reparse()

    def _reparse(self) -> None:
        """Walk the JSONL file from scratch and rebuild the stats map."""
        latest: dict[int, dict] = {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    body = record.get("body_json")
                    if not isinstance(body, dict):
                        continue
                    if body.get("status") != "success":
                        continue
                    data = (body.get("response") or {}).get("data") or []
                    for entry in data:
                        uid = entry.get("user_id")
                        if uid is None:
                            continue
                        ratios = entry.get("ratios") or []
                        if not ratios:
                            continue
                        ts = entry.get("timestamp", 0)
                        # Keep only the freshest entry per user
                        existing = latest.get(uid)
                        if existing and existing.get("_timestamp", 0) >= ts:
                            continue
                        merged = dict(ratios[0])
                        merged["_timestamp"] = ts
                        merged["_version"] = entry.get("version", "")
                        latest[uid] = merged
        except OSError:
            return
        self._stats_by_user = latest

    # ── lookup ────────────────────────────────────────────────────────────

    def get_stats(self, user_id) -> Optional[dict]:
        """
        Get the latest captured stats for a user_id, or None if unknown.

        The returned dict contains all fields from the API:
            vpip, pfr, 3bet, cbet, check_raise, fold_to_3bet, fold_to_cbet,
            steal, wsd, wtsd, allin, fta, fold,
            mini_game_type, _timestamp, _version
        """
        if user_id is None:
            return None
        self._maybe_reload()
        try:
            return self._stats_by_user.get(int(user_id))
        except (TypeError, ValueError):
            return None

    def classify(self, user_id) -> str:
        """
        Classify a player into FISH/NIT/TAG/LAG/WHALE based on the same
        thresholds OpponentTracker uses, so direct comparison is fair.

        Returns 'UNKNOWN' if no stats are available for this user_id.
        """
        s = self.get_stats(user_id)
        if not s:
            return "UNKNOWN"
        vpip = s.get("vpip")
        pfr = s.get("pfr")
        if vpip is None or pfr is None:
            return "UNKNOWN"
        # Mirror OpponentTracker._classify but with the HUD's continuous
        # ratios (already 0-1 floats, not normalized counts).
        if vpip > 0.50:
            return "WHALE" if vpip > 0.70 else "FISH"
        if vpip < 0.15:
            return "NIT"
        # The HUD doesn't directly expose AF; we use 3-bet frequency as
        # the proxy for aggression — high 3-bet + high PFR = LAG.
        if pfr > 0.20 and s.get("3bet", 0) > 0.10:
            return "LAG"
        if pfr > 0.15:
            return "TAG"
        return "FISH"
