"""
Repair the opponents table in vision/data/hands.db.

The OpponentTracker had a bb_amt bug (hardcoded `bb_amt = 4` for Unibet
NL2 cents) that meant CoinPoker bets in scaled chip units always
satisfied `bet > bb_amt`, marking EVERY player as VPIP every hand.
Cross-session HandDB persistence compounded the corruption — by the
time it was diagnosed, 64/100 rows had `vpip > hands_seen` (impossible)
and the rest were inflated to match. Diagnosis: 2026-04-09 via
side-by-side comparison against CoinPoker's HUD ground truth endpoint.

There's no clean way to RECOVER the truth from corrupted action
counts — the source data (hand histories) doesn't include enough to
reconstruct exactly when each player voluntarily put chips in vs
posted blinds. So this tool NUKES the action-derived columns and
relies on:
  - The HUD-preferred classification path in
    `_CoinPokerTrackerAdapter.classify_villain` (HUD ground truth wins
    when available)
  - The nit_assume default in
    `AdvisorStateMachine._process_postflop` (UNKNOWN -> NIT at micros)
  - Future re-accumulation of correct stats now that the bb_amt bug
    is fixed

Columns reset to 0:
  vpip, pfr, postflop_bets, postflop_calls, postflop_folds

Columns reset to 'UNKNOWN':
  classification

Columns PRESERVED (not touched by the bug):
  name, hands_seen (incremented once per new hand_id, dedup works)
  went_to_showdown, won_at_showdown (not in the buggy code path)
  last_seen

Safety:
  - Default mode is dry-run. Prints what would change but does NOT
    write to the DB. Use --apply to actually modify.
  - --apply ALWAYS creates a backup first at hands.db.backup_<TIMESTAMP>.
  - You can restore by copying the backup back to hands.db.
  - The runner reads opponents into memory at startup, so restart
    `vision/coinpoker_runner.py` after running this tool with --apply.

Usage:
    python tools/repair_opponent_db.py            # dry-run, prints plan
    python tools/repair_opponent_db.py --apply    # creates backup + nukes
    python tools/repair_opponent_db.py --apply --no-backup  # explicit, dangerous
    python tools/repair_opponent_db.py --restore-backup hands.db.backup_1775612345
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from typing import Optional


DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vision", "data", "hands.db",
)


def diagnose(db_path: str) -> dict:
    """Read the opponents table and report counts of corrupted rows."""
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM opponents").fetchone()[0]
        impossible = conn.execute(
            "SELECT COUNT(*) FROM opponents "
            "WHERE vpip > hands_seen AND hands_seen > 0"
        ).fetchone()[0]
        zero_hands = conn.execute(
            "SELECT COUNT(*) FROM opponents WHERE hands_seen = 0"
        ).fetchone()[0]
        sane = total - impossible - zero_hands
        # By classification
        by_class = {}
        for row in conn.execute(
            "SELECT classification, COUNT(*) FROM opponents GROUP BY classification"
        ).fetchall():
            by_class[row[0]] = row[1]
        # Total accumulated stats (so we know the magnitude of the wipe)
        sums = conn.execute(
            "SELECT SUM(vpip), SUM(pfr), SUM(hands_seen), "
            "SUM(postflop_bets), SUM(postflop_calls), SUM(postflop_folds) "
            "FROM opponents"
        ).fetchone()
        return {
            "total": total,
            "impossible": impossible,
            "zero_hands": zero_hands,
            "sane": sane,
            "by_class": by_class,
            "sum_vpip": sums[0] or 0,
            "sum_pfr": sums[1] or 0,
            "sum_hands": sums[2] or 0,
            "sum_postflop_bets": sums[3] or 0,
            "sum_postflop_calls": sums[4] or 0,
            "sum_postflop_folds": sums[5] or 0,
        }
    finally:
        conn.close()


def print_diagnosis(d: dict) -> None:
    print(f"  Total opponents:           {d['total']}")
    print(f"    impossible (vpip>hands): {d['impossible']}")
    print(f"    zero hands:              {d['zero_hands']}")
    print(f"    'sane' (vpip<=hands):    {d['sane']}")
    print(f"  Sum stats across all rows:")
    print(f"    hands_seen:              {d['sum_hands']:,}")
    print(f"    vpip increments:         {d['sum_vpip']:,}")
    print(f"    pfr increments:          {d['sum_pfr']:,}")
    print(f"    postflop bets:           {d['sum_postflop_bets']:,}")
    print(f"    postflop calls:          {d['sum_postflop_calls']:,}")
    print(f"    postflop folds:          {d['sum_postflop_folds']:,}")
    print(f"  Classification distribution:")
    for k, v in sorted(d['by_class'].items(), key=lambda x: -x[1]):
        print(f"    {k:<8s}: {v}")


def make_backup(db_path: str) -> str:
    """Copy db_path to db_path.backup_<unix_ts>. Returns the backup path."""
    ts = int(time.time())
    backup_path = f"{db_path}.backup_{ts}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def apply_repair(db_path: str) -> None:
    """Reset corrupted columns. Caller must have backed up first."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE opponents SET "
            "vpip = 0, pfr = 0, "
            "postflop_bets = 0, postflop_calls = 0, postflop_folds = 0, "
            "classification = 'UNKNOWN'"
        )
        conn.commit()
    finally:
        conn.close()


def restore_backup(db_path: str, backup_path: str) -> None:
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"backup not found: {backup_path}")
    # Don't trash an existing live DB without acknowledgement — make
    # a safety copy first
    if os.path.exists(db_path):
        safety = f"{db_path}.pre_restore_{int(time.time())}"
        shutil.copy2(db_path, safety)
        print(f"  saved current db to {safety} before restoring")
    shutil.copy2(backup_path, db_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB,
                    help="Path to hands.db (default: %(default)s)")
    ap.add_argument("--apply", action="store_true",
                    help="Actually modify the DB (default is dry-run)")
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip the safety backup. Dangerous; only use if "
                         "you have your own backup elsewhere.")
    ap.add_argument("--restore-backup", default=None,
                    help="Restore from this backup path and exit. Mutually "
                         "exclusive with --apply.")
    args = ap.parse_args()

    if args.restore_backup:
        if args.apply:
            print("ERROR: --restore-backup is mutually exclusive with --apply",
                  file=sys.stderr)
            return 2
        try:
            restore_backup(args.db, args.restore_backup)
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(f"  restored {args.db} from {args.restore_backup}")
        return 0

    if not os.path.exists(args.db):
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    print(f"  db: {args.db}")
    print()
    print("BEFORE:")
    before = diagnose(args.db)
    print_diagnosis(before)
    print()

    if not args.apply:
        print("DRY-RUN: no changes made.")
        print()
        print("To apply: python tools/repair_opponent_db.py --apply")
        print("This will:")
        print("  1. Back up hands.db to hands.db.backup_<timestamp>")
        print("  2. Reset vpip/pfr/postflop_bets/postflop_calls/postflop_folds = 0")
        print("  3. Reset classification = 'UNKNOWN'")
        print("  4. Preserve name, hands_seen, went_to_showdown, won_at_showdown, last_seen")
        return 0

    # --apply path
    if not args.no_backup:
        backup_path = make_backup(args.db)
        print(f"  backup: {backup_path}")
        print()

    apply_repair(args.db)

    print("AFTER:")
    after = diagnose(args.db)
    print_diagnosis(after)
    print()
    print(f"  Repaired {before['impossible']} impossible rows + reset "
          f"{before['total']} classifications.")
    print(f"  Restart vision/coinpoker_runner.py to pick up the cleaned stats.")
    if not args.no_backup:
        print(f"  Restore with: python tools/repair_opponent_db.py "
              f"--restore-backup {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
