"""
Winrate tracker for CoinPoker grind sessions.

Walks `coinpoker_frames.jsonl` and produces a per-session report:
  - Total hands hero was dealt in
  - Total profit (chip cents)
  - bb/100 winrate
  - Per-stake breakdown (NL10 vs NL25 vs practice etc.)
  - Session windows (groups of hands within N minutes of each other)

This is the first tool that lets the user see "am I actually winning?"
across a multi-day grind, instead of guessing from gut feel after each
session. It's deliberately read-only and offline — point it at the
frame log any time to get a fresh winrate estimate.

Usage:
    python tools/winrate.py
    python tools/winrate.py --since "2026-04-09"     # only hands after a date
    python tools/winrate.py --since "1h"             # last hour
    python tools/winrate.py --since "session"        # since the most recent
                                                       # 30-min gap (current session)
    python tools/winrate.py --by-stake               # break down per stake
    python tools/winrate.py --by-room                # break down per table
    python tools/winrate.py --hands                  # print every hand
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Optional


DEFAULT_FRAMES = r"C:\Users\Simon\coinpoker_frames.jsonl"
DEFAULT_HERO_ID = 1571120
SESSION_GAP_SECONDS = 30 * 60  # 30 minutes between hands = new "session"


def parse_since(spec: str) -> Optional[float]:
    """
    Parse a --since spec into a unix timestamp threshold.
    Supports:
      - "session" → most-recent-session boundary (handled separately)
      - ISO date "YYYY-MM-DD"
      - Relative "1h", "30m", "2d"
      - Unix epoch number
    Returns None for "session" (caller computes).
    """
    if not spec or spec == "session":
        return None
    spec = spec.strip()
    # Relative: 1h, 30m, 2d
    m = re.match(r"^(\d+)([smhd])$", spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        secs = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return time.time() - n * secs
    # ISO date
    try:
        return time.mktime(time.strptime(spec, "%Y-%m-%d"))
    except ValueError:
        pass
    # Unix epoch
    try:
        return float(spec)
    except ValueError:
        pass
    raise ValueError(f"unrecognized --since: {spec!r}")


def parse_room_stake(room_name: str) -> str:
    """
    Extract a short stake label from a room name. Handles decimal stakes
    like '0.05-0.10' (real money) AND integer stakes like '50-100'
    (practice tables). The decimal-aware regex matches '0.05' as one
    number, not '05'.
    """
    if not room_name:
        return "unknown"
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)", room_name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return room_name[:20]


def walk_hands(path: str, hero_id: int):
    """
    Yield one dict per hand hero participated in. Each dict has:
        hand_id, ts, room_name, stake, hero_cards (list or None),
        start_chips, end_chips, profit (end - start), hero_left (bool).

    A "hand" starts at game.pre_hand_start_info and ends at the next
    one (or end of file). Hero participation is detected from any
    seatInfo containing user_id == hero_id.

    Hands where hero LEFT the table mid-hand (game.leave_Seat) are
    SKIPPED — the chips don't disappear, they go back to the wallet,
    but the frame log records currentChips=0 which would otherwise
    show as a "loss" of the entire stack. The 2026-04-08 +17 BB session
    initially looked like -10 EUR because of this artifact.
    """
    current = None  # dict for the in-progress hand
    hero_seat = None  # tracked across the hand so leave_Seat can match

    def emit(h):
        if not h:
            return None
        if h.get("hero_left"):
            return None  # hero stood up — chips returned to wallet, not lost
        if h["start_chips"] is None or h["end_chips"] is None:
            return None
        h["profit"] = h["end_chips"] - h["start_chips"]
        return h

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            cb = frame.get("cmd_bean") or {}
            cmd = cb.get("Cmd")
            bd = cb.get("BeanData", "")
            try:
                bean = json.loads(bd) if isinstance(bd, str) and bd else (
                    bd if isinstance(bd, dict) else {})
            except json.JSONDecodeError:
                bean = {}
            room = frame.get("room_name", "") or cb.get("RoomName", "")

            if cmd == "game.pre_hand_start_info":
                # Finalize previous hand if hero participated
                done = emit(current)
                if done:
                    yield done
                hand_id = bean.get("gameHandId")
                if hand_id is None:
                    current = None
                    continue
                ts_ms = bean.get("initTimeStamp", 0)
                try:
                    ts = int(ts_ms) / 1000.0 if ts_ms else 0.0
                except (TypeError, ValueError):
                    ts = 0.0
                current = {
                    "hand_id": str(hand_id),
                    "ts": ts,
                    "room_name": room,
                    "stake": parse_room_stake(room),
                    "hero_cards": None,
                    "start_chips": None,
                    "end_chips": None,
                    "hero_left": False,
                }
                hero_seat = None
                continue

            if not current:
                continue

            if cmd == "game.seatInfo":
                seats = bean.get("seatResponseDataList", []) or []
                for s in seats:
                    if s.get("userId") == hero_id:
                        # Track hero's seat so we can match leave_Seat events
                        if hero_seat is None:
                            hero_seat = s.get("seatId")
                        chips = s.get("userChips")
                        if chips is not None:
                            chips_int = int(round(float(chips) * 100))
                            if current["start_chips"] is None:
                                current["start_chips"] = chips_int
                            # Don't overwrite end_chips with 0 if hero already
                            # left the table — the 0 is a wallet-return artifact
                            if not current["hero_left"]:
                                current["end_chips"] = chips_int
            elif cmd == "game.leave_Seat":
                # Hero stood up. Mark the hand so emit() skips it.
                if bean.get("seatId") == hero_seat and bean.get("isSeatLeft"):
                    current["hero_left"] = True
            elif cmd == "game.hole_cards":
                cards = bean.get("holeCards", [])
                if cards and len(cards) == 2:
                    rmap = {"TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
                            "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9",
                            "TEN": "T", "JACK": "J", "QUEEN": "Q", "KING": "K", "ACE": "A"}
                    smap = {"CLUBS": "c", "DIAMONDS": "d",
                            "HEARTS": "h", "SPADES": "s"}
                    current["hero_cards"] = [
                        rmap.get(c.get("value"), "?") + smap.get(c.get("suit"), "?")
                        for c in cards
                    ]
            elif cmd == "game.transaction_winnings":
                cc = bean.get("currentChips")
                if cc is not None and not current["hero_left"]:
                    # Same wallet-return guard as in seatInfo
                    try:
                        current["end_chips"] = int(round(float(cc) * 100))
                    except (TypeError, ValueError):
                        pass

    # Final hand at EOF
    done = emit(current)
    if done:
        yield done


def find_session_boundary(hands: list, gap_seconds: float = SESSION_GAP_SECONDS) -> int:
    """
    Find the index of the first hand of the most recent session.
    A "session" boundary is any gap of >gap_seconds between consecutive
    hands. Returns the index of the first hand AFTER the most recent gap.
    Returns 0 if no gap found (entire log is one session).
    """
    if not hands:
        return 0
    last_ts = hands[-1]["ts"] or 0
    if last_ts == 0:
        return 0
    boundary_idx = 0
    for i in range(len(hands) - 1, 0, -1):
        prev_ts = hands[i - 1]["ts"] or 0
        cur_ts = hands[i]["ts"] or 0
        if prev_ts > 0 and cur_ts > 0 and (cur_ts - prev_ts) > gap_seconds:
            boundary_idx = i
            break
    return boundary_idx


def stake_to_bb_cents(stake: str) -> Optional[int]:
    """
    Map a parsed stake string back to BB in chip cents. Examples:
      "0.05-0.10" → 10  (NL10)
      "0.25-0.50" → 50  (NL50)
      "50-100"    → 10000  (practice table)
    """
    m = re.match(r"^(\S+?)\s*[-/]\s*(\S+)$", stake)
    if not m:
        return None
    try:
        bb = float(m.group(2))
    except ValueError:
        return None
    # If BB looks like dollars (small float), convert to cents
    if bb < 10:
        return int(round(bb * 100))
    # Already in chip units (practice tables use 100 chips for BB)
    return int(round(bb * 100))


def is_practice_stake(stake: str) -> bool:
    """
    Heuristic: a stake row is "practice chips" if its BB exceeds the
    largest plausible real-money BB ($100 = NL20000). Practice tables
    use 100 chips for BB; real-money tables use values like 0.10.
    """
    bb = stake_to_bb_cents(stake)
    if bb is None:
        return False
    return bb > 1000  # > $10.00 BB → must be practice chips


def report(hands: list, label: str = "all"):
    """Print a winrate report for a list of hand dicts."""
    if not hands:
        print(f"  [{label}] no hands")
        return

    # Group by stake so bb/100 is meaningful
    by_stake = defaultdict(list)
    for h in hands:
        by_stake[h["stake"]].append(h)

    print(f"  [{label}] {len(hands)} hands across {len(by_stake)} stake(s)")

    real_total_cents = 0
    practice_total_chips = 0
    for stake, stake_hands in sorted(by_stake.items()):
        profit_cents = sum(h["profit"] for h in stake_hands)
        bb_cents = stake_to_bb_cents(stake)
        practice = is_practice_stake(stake)
        if bb_cents and bb_cents > 0:
            bb_won = profit_cents / bb_cents
            bb_per_100 = (bb_won / len(stake_hands)) * 100
            bb_str = f"{bb_won:+.1f} BB ({bb_per_100:+.1f} bb/100)"
        else:
            bb_str = "(BB scale unknown)"
        prefix = "[practice]" if practice else "          "
        print(f"    {prefix} {stake:>12s}: {len(stake_hands):>4d} hands  "
              f"profit={profit_cents/100:+9.2f}  {bb_str}")
        if practice:
            practice_total_chips += profit_cents
        else:
            real_total_cents += profit_cents

    if real_total_cents != 0 or practice_total_chips == 0:
        print(f"  REAL MONEY TOTAL:  {real_total_cents/100:+9.2f}")
    if practice_total_chips != 0:
        print(f"  PRACTICE TOTAL:    {practice_total_chips/100:+9.2f} "
              f"(practice chips, NOT dollars)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=DEFAULT_FRAMES,
                    help="Frame log path (default: %(default)s)")
    ap.add_argument("--hero-id", type=int, default=DEFAULT_HERO_ID)
    ap.add_argument("--since", default=None,
                    help="Filter: 'session' (last 30-min gap), "
                         "ISO date 'YYYY-MM-DD', relative '1h'/'30m'/'2d', "
                         "or unix epoch")
    ap.add_argument("--by-stake", action="store_true",
                    help="Break down per stake (default behavior — kept for clarity)")
    ap.add_argument("--by-room", action="store_true",
                    help="Break down per room (separate report per table)")
    ap.add_argument("--hands", action="store_true",
                    help="Print every hand (verbose)")
    args = ap.parse_args()

    if not os.path.exists(args.frames):
        print(f"FATAL: frame log not found: {args.frames}", file=sys.stderr)
        return 2

    print(f"[winrate] reading {args.frames}")
    print(f"[winrate] hero_id = {args.hero_id}")
    print()

    all_hands = list(walk_hands(args.frames, args.hero_id))
    print(f"[winrate] total hands hero participated in: {len(all_hands)}")

    # Apply --since filter
    if args.since == "session":
        idx = find_session_boundary(all_hands)
        filtered = all_hands[idx:]
        print(f"[winrate] --since session: {len(filtered)} hands "
              f"(after most-recent 30-min gap)")
    elif args.since:
        try:
            cutoff = parse_since(args.since)
        except ValueError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 2
        filtered = [h for h in all_hands if h["ts"] >= cutoff]
        print(f"[winrate] --since {args.since}: {len(filtered)} hands")
    else:
        filtered = all_hands

    print()
    print("=" * 70)
    if args.by_room:
        by_room = defaultdict(list)
        for h in filtered:
            by_room[h["room_name"]].append(h)
        for room, hs in sorted(by_room.items(), key=lambda x: -len(x[1])):
            label = (room or "(no room)")[:50]
            report(hs, label=label)
            print()
    else:
        report(filtered, label="filtered")
    print("=" * 70)

    if args.hands:
        print()
        print("Per-hand detail:")
        for h in filtered:
            cards = " ".join(h["hero_cards"]) if h["hero_cards"] else "??"
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(h["ts"])) if h["ts"] else "?"
            print(f"  {ts_str}  {h['stake']:>10s}  hand={h['hand_id']:>10s}  "
                  f"{cards:5s}  profit={h['profit']/100:+7.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
