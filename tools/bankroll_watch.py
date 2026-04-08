"""
Bankroll discipline watchdog.

Tails the CoinPoker frame log and watches hero's stack across the
current session. Triggers a warning (and a hard "STOP NOW" alert) when
the user's session drawdown crosses configured thresholds.

The grind plan is "2 hours/day, climb stakes" — but variance can hand
you a -3 buy-in session in 30 hands. Without an external stop-loss,
the temptation to "chase one more hand" can wipe out a week of grind
profit. This tool exists to be the external voice that says "stop
playing right now."

It does NOT close tables or click anything. It just prints big visible
warnings to the console. The user is responsible for actually standing
up.

Defaults match the kanban / memory rules:
  --warn-bi 2     soft warning at -2 buy-ins from session start
  --stop-bi 3     hard stop at -3 buy-ins (matches the "stop after first
                  buy-in" rule the user committed to earlier in the
                  project for the bounded validation runs)
  --max-bi 5      kill-switch maximum: -5 buy-ins is total session ruin

Usage:
    python tools/bankroll_watch.py
    python tools/bankroll_watch.py --warn-bi 1 --stop-bi 2  # tighter
    python tools/bankroll_watch.py --no-bell                # silent text-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional


DEFAULT_FRAMES = r"C:\Users\Simon\coinpoker_frames.jsonl"
DEFAULT_HERO_ID = 1571120
SESSION_GAP_SECONDS = 30 * 60  # 30 minutes between hands = new session


def follow_frames(path: str, poll_secs: float = 0.3):
    """Tail a JSONL file. Yields each parsed frame as it appears."""
    while not os.path.exists(path):
        time.sleep(1)
    f = open(path, "r", encoding="utf-8")
    try:
        f.seek(0, 2)  # start at EOF
        leftover = ""
        while True:
            chunk = f.read()
            if not chunk:
                try:
                    if os.path.getsize(path) < f.tell():
                        f.close()
                        f = open(path, "r", encoding="utf-8")
                        leftover = ""
                except OSError:
                    pass
                time.sleep(poll_secs)
                continue
            data = leftover + chunk
            lines = data.split("\n")
            leftover = lines[-1]
            for line in lines[:-1]:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    finally:
        try:
            f.close()
        except Exception:
            pass


def find_session_start_chips(path: str, hero_id: int) -> Optional[int]:
    """
    Walk the frame log to find hero's chip count at the START of the
    most recent session (= first hand after the most-recent 30-min gap).
    Returns chip count in scaled cents, or None if not found.
    """
    hands = []  # list of (ts_seconds, hero_chips_at_start)
    current_hand = None
    current_start = None
    last_hero_chips_in_hand = None

    if not os.path.exists(path):
        return None

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
                bean = json.loads(bd) if isinstance(bd, str) and bd else {}
            except json.JSONDecodeError:
                bean = {}

            if cmd == "game.pre_hand_start_info":
                # Finalize previous hand (need start_chips for this hand)
                if current_hand is not None and current_start is not None:
                    hands.append((current_hand, current_start))
                current_hand = None
                current_start = None
                ts_ms = bean.get("initTimeStamp", 0)
                try:
                    current_hand = int(ts_ms) / 1000.0 if ts_ms else 0.0
                except (TypeError, ValueError):
                    current_hand = 0.0
                continue

            if current_hand is None:
                continue

            if cmd == "game.seatInfo":
                for s in bean.get("seatResponseDataList", []) or []:
                    if s.get("userId") == hero_id:
                        chips = s.get("userChips")
                        if chips is not None and current_start is None:
                            current_start = int(round(float(chips) * 100))

    # Final hand at EOF
    if current_hand is not None and current_start is not None:
        hands.append((current_hand, current_start))

    if not hands:
        return None

    # Find the first hand after the most-recent 30-min gap
    boundary_idx = 0
    for i in range(len(hands) - 1, 0, -1):
        prev_ts = hands[i - 1][0]
        cur_ts = hands[i][0]
        if prev_ts > 0 and cur_ts > 0 and (cur_ts - prev_ts) > SESSION_GAP_SECONDS:
            boundary_idx = i
            break

    return hands[boundary_idx][1]


def alert(msg: str, ring_bell: bool = True) -> None:
    """Print a high-visibility console alert."""
    bar = "!" * 70
    print(file=sys.stderr)
    print(bar, file=sys.stderr)
    print(f"!! {msg}", file=sys.stderr)
    print(bar, file=sys.stderr)
    if ring_bell:
        # ASCII bell — most terminals beep
        sys.stderr.write("\a")
        sys.stderr.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=DEFAULT_FRAMES,
                    help="Frame log path (default: %(default)s)")
    ap.add_argument("--hero-id", type=int, default=DEFAULT_HERO_ID)
    ap.add_argument("--bi-cents", type=int, default=1000,
                    help="One buy-in in chip cents (default: 1000 = $10 = NL10 BI)")
    ap.add_argument("--warn-bi", type=float, default=2.0,
                    help="Soft warning threshold in buy-ins (default: 2.0)")
    ap.add_argument("--stop-bi", type=float, default=3.0,
                    help="Hard stop threshold in buy-ins (default: 3.0)")
    ap.add_argument("--max-bi", type=float, default=5.0,
                    help="Kill-switch threshold in buy-ins (default: 5.0)")
    ap.add_argument("--no-bell", action="store_true",
                    help="Silent text-only alerts (default: ring the bell)")
    ap.add_argument("--start-chips", type=int, default=None,
                    help="Override session start chips (in cents). "
                         "Default: auto-detect from frame log.")
    args = ap.parse_args()

    if not os.path.exists(args.frames):
        print(f"FATAL: frame log not found: {args.frames}", file=sys.stderr)
        return 2

    # Detect session start
    if args.start_chips is not None:
        session_start = args.start_chips
        print(f"[bankroll] session start chips (override): "
              f"{session_start/100:.2f}")
    else:
        session_start = find_session_start_chips(args.frames, args.hero_id)
        if session_start is None:
            print("[bankroll] could not find session start in frame log; "
                  "waiting for first hand")
        else:
            print(f"[bankroll] auto-detected session start: "
                  f"{session_start/100:.2f}")

    print(f"[bankroll] thresholds: "
          f"warn at -{args.warn_bi} BI, "
          f"STOP at -{args.stop_bi} BI, "
          f"MAX at -{args.max_bi} BI "
          f"(1 BI = {args.bi_cents/100:.2f})")
    print(f"[bankroll] tailing {args.frames}")
    print()

    warned = False
    stopped = False
    maxed = False
    last_chips = session_start

    try:
        for frame in follow_frames(args.frames):
            cb = frame.get("cmd_bean") or {}
            cmd = cb.get("Cmd")
            bd = cb.get("BeanData", "")
            try:
                bean = json.loads(bd) if isinstance(bd, str) and bd else {}
            except json.JSONDecodeError:
                continue

            chips = None
            if cmd == "game.seatInfo":
                for s in bean.get("seatResponseDataList", []) or []:
                    if s.get("userId") == args.hero_id:
                        c = s.get("userChips")
                        if c is not None:
                            chips = int(round(float(c) * 100))
                            break
            elif cmd == "game.transaction_winnings":
                c = bean.get("currentChips")
                if c is not None:
                    chips = int(round(float(c) * 100))

            if chips is None:
                continue

            # Lock in session start at first sighting if we don't have one
            if session_start is None:
                session_start = chips
                last_chips = chips
                print(f"[bankroll] session start locked at "
                      f"{session_start/100:.2f}")
                continue

            # Don't track wallet-zero artifacts (hero stood up between sessions)
            if chips == 0 and last_chips and last_chips > session_start * 0.5:
                # Sudden drop to zero is suspicious — likely a stand-up
                continue

            last_chips = chips
            delta_cents = chips - session_start
            delta_bi = delta_cents / args.bi_cents

            if delta_bi <= -args.max_bi and not maxed:
                maxed = True
                alert(f"MAX DRAWDOWN: -{abs(delta_bi):.1f} BI "
                      f"({delta_cents/100:+.2f}) — STOP PLAYING NOW. "
                      f"Session start was {session_start/100:.2f}, "
                      f"current is {chips/100:.2f}.",
                      ring_bell=not args.no_bell)
            elif delta_bi <= -args.stop_bi and not stopped:
                stopped = True
                alert(f"STOP-LOSS: -{abs(delta_bi):.1f} BI "
                      f"({delta_cents/100:+.2f}). "
                      f"Stand up from all tables now. "
                      f"Re-evaluate before next session.",
                      ring_bell=not args.no_bell)
            elif delta_bi <= -args.warn_bi and not warned:
                warned = True
                alert(f"WARN: -{abs(delta_bi):.1f} BI "
                      f"({delta_cents/100:+.2f}). "
                      f"Tighten focus or take a break.",
                      ring_bell=not args.no_bell)

            # Periodically (every chip change) print the current state
            print(f"[bankroll] chips={chips/100:>7.2f}  "
                  f"delta={delta_cents/100:+7.2f}  "
                  f"({delta_bi:+.2f} BI)")
    except KeyboardInterrupt:
        print()
        print("[bankroll] stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
