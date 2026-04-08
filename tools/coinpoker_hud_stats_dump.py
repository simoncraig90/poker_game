"""
Parse the JSONL output of `tools/coinpoker_stats_sniffer.py` and dump
the per-player stats CoinPoker fetches from
`/pbshots/v2/stats/cash?user_id=...`.

This is the ground-truth opponent stats endpoint discovered 2026-04-08.
CoinPoker maintains these on the server side for every player and
serves them to the client when it builds the HUD popup. The sniffer
captures the responses; this tool flattens them into a per-player
table you can compare against your own OpponentTracker.

Schema returned by the API (one entry per user_id):
    user_id, version, timestamp, ratios[{
        mini_game_type, vpip, pfr, 3bet, cbet, check_raise,
        fold_to_3bet, fold_to_cbet, steal, wsd, wtsd, allin,
        fta, fold
    }]

Usage:
    python tools/coinpoker_hud_stats_dump.py
    python tools/coinpoker_hud_stats_dump.py --user 1571120
    python tools/coinpoker_hud_stats_dump.py --by-name "precious0864449"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Optional

DEFAULT_INPUT = r"C:\Users\Simon\coinpoker_hud_stats.jsonl"


def load_responses(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"sniffer output not found: {path}\n"
                                f"Run tools/coinpoker_stats_sniffer.py first "
                                f"and hover an opponent in CoinPoker.")
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def flatten_to_player_rows(responses: list[dict]) -> dict[int, dict]:
    """
    Walk every captured response, extract per-player stats blocks, and
    keep the most recent (by timestamp) per user_id.
    """
    latest: dict[int, dict] = {}
    for r in responses:
        body = r.get("body_json")
        if not body or not isinstance(body, dict):
            continue
        if body.get("status") != "success":
            continue
        data = (body.get("response") or {}).get("data") or []
        for entry in data:
            uid = entry.get("user_id")
            if uid is None:
                continue
            ratios_list = entry.get("ratios") or []
            if not ratios_list:
                continue
            r0 = ratios_list[0]
            ts = entry.get("timestamp", 0)
            if uid not in latest or latest[uid].get("timestamp", 0) < ts:
                latest[uid] = {
                    "user_id": uid,
                    "timestamp": ts,
                    "version": entry.get("version", ""),
                    **r0,
                }
    return latest


STAT_COLUMNS = [
    ("vpip", "VPIP"),
    ("pfr", "PFR"),
    ("3bet", "3B"),
    ("cbet", "CB"),
    ("fold_to_cbet", "F2CB"),
    ("fold_to_3bet", "F23B"),
    ("check_raise", "CR"),
    ("steal", "Steal"),
    ("wtsd", "WTSD"),
    ("wsd", "W$SD"),
    ("fold", "Fold%"),
]


def classify_from_stats(s: dict) -> str:
    """
    Classify a player into FISH/NIT/TAG/LAG/WHALE based on the same
    rules our OpponentTracker uses, so we can compare apples to apples.
    Returns 'UNKNOWN' if VPIP/PFR aren't available.
    """
    vpip = s.get("vpip")
    pfr = s.get("pfr")
    if vpip is None or pfr is None:
        return "UNKNOWN"
    if vpip > 0.50:
        return "WHALE" if vpip > 0.70 else "FISH"
    if vpip < 0.15:
        return "NIT"
    if pfr > 0.20 and (s.get("3bet", 0) > 0.10):
        return "LAG"
    if pfr > 0.15:
        return "TAG"
    return "FISH"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT,
                    help="Sniffer output JSONL (default: %(default)s)")
    ap.add_argument("--user", type=int, default=None,
                    help="Filter to a specific user_id")
    ap.add_argument("--limit", type=int, default=20,
                    help="Max rows to print (default: %(default)s)")
    args = ap.parse_args()

    try:
        responses = load_responses(args.input)
    except FileNotFoundError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    print(f"[hud-dump] loaded {len(responses)} sniffer responses from {args.input}")
    rows = flatten_to_player_rows(responses)
    print(f"[hud-dump] {len(rows)} unique players in dataset")
    print()

    if args.user:
        rows = {k: v for k, v in rows.items() if k == args.user}
        if not rows:
            print(f"no data for user_id={args.user}")
            return 1

    sorted_rows = sorted(rows.values(), key=lambda r: -r.get("timestamp", 0))[:args.limit]

    header_cells = [f"{lbl:>6s}" for _, lbl in STAT_COLUMNS]
    print(f"  {'user_id':>10s}  {'class':>6s}  " + "  ".join(header_cells))
    print("  " + "-" * (10 + 6 + 4 + len(STAT_COLUMNS) * 8))
    for r in sorted_rows:
        cls = classify_from_stats(r)
        cells = []
        for key, _ in STAT_COLUMNS:
            v = r.get(key)
            if v is None:
                cells.append(f"{'—':>6s}")
            else:
                cells.append(f"{v*100:>5.1f}%")
        print(f"  {r['user_id']:>10d}  {cls:>6s}  " + "  ".join(cells))

    return 0


if __name__ == "__main__":
    sys.exit(main())
