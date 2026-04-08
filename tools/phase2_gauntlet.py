"""
Phase 2 click adapter dry-run gauntlet.

Watches the live frame stream produced by the patched PBClient.dll and,
on every detected hero turn, writes a fake action via CoinPokerClicker.
Then watches the inject log to confirm the matching ``PENDING ...`` line
appears within a short timeout.

Reports stats per detected hero turn:
  - requests issued
  - requests rejected (paused / stale / queue collision)
  - inject log entries seen
  - per-request round-trip latency
  - drops (issued but never logged)

Stops after a configurable number of successful round-trips (default 50)
or on Ctrl+C, whichever comes first. Designed to validate the click
adapter's reliability before any real-money click in Phase 3.

Usage:
    python tools/phase2_gauntlet.py [--target-rounds 50] [--action FOLD]

The script does NOT require the runner to be running — it tails the
frame log directly. Run it alongside a CoinPoker session where you (or
the auto-clicker) sit at a practice table.
"""

import argparse
import json
import os
import sys
import time

VISION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vision")
sys.path.insert(0, VISION)

from coinpoker_adapter import CoinPokerStateBuilder  # noqa: E402
from coinpoker_clicker import CoinPokerClicker  # noqa: E402

DEFAULT_FRAME_LOG = r"C:\Users\Simon\coinpoker_frames.jsonl"
DEFAULT_HERO_ID = 1571120  # precious0864449

# Heuristic: each round-trip should land in the inject log within
# ROUND_TRIP_TIMEOUT_S. The IL only fires on incoming game events, so
# during quiet periods (between hands, or hero sitting out) the file
# can wait several seconds before being consumed. Use a generous
# timeout to avoid false-positive drops during normal idle periods.
ROUND_TRIP_TIMEOUT_S = 5.0


def follow_log(path: str, poll: float = 0.05):
    """Generator yielding new lines from a growing log file."""
    f = open(path, "r", encoding="utf-8", errors="replace")
    try:
        f.seek(0, 2)
        leftover = ""
        while True:
            chunk = f.read()
            if not chunk:
                time.sleep(poll)
                continue
            data = leftover + chunk
            lines = data.split("\n")
            leftover = lines[-1]
            for line in lines[:-1]:
                if line:
                    yield line
    finally:
        f.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Phase 2 click adapter gauntlet")
    p.add_argument("--frame-log", default=DEFAULT_FRAME_LOG)
    p.add_argument("--hero-id", type=int, default=DEFAULT_HERO_ID)
    p.add_argument("--target-rounds", type=int, default=50,
                   help="Stop after this many successful round-trips (default: 50)")
    p.add_argument("--action", default="FOLD",
                   choices=["FOLD", "CHECK", "CALL", "RAISE", "ALLIN"],
                   help="Synthetic action to write each round (default: FOLD)")
    p.add_argument("--size", type=float, default=None,
                   help="Optional size for RAISE/BET (chip units)")
    p.add_argument("--mode", default="hero-turn",
                   choices=["hero-turn", "periodic"],
                   help="hero-turn: fire on each detected hero turn; "
                        "periodic: fire at fixed rate regardless of game state. "
                        "Use periodic when hero is sitting out (default: hero-turn)")
    p.add_argument("--period-ms", type=int, default=200,
                   help="Inter-request delay for --mode=periodic (default: 200ms)")
    p.add_argument("--ignore-staleness", action="store_true",
                   help="Skip the hand-id staleness check (needed for periodic "
                        "mode when the live hand keeps changing)")
    args = p.parse_args(argv)

    if not os.path.exists(args.frame_log):
        print(f"frame log not found: {args.frame_log}", file=sys.stderr)
        return 2

    builder = CoinPokerStateBuilder(args.hero_id)

    # Wire the staleness check to the live builder state, unless told
    # to ignore it. Periodic mode fires regardless of hand state so the
    # check would reject most requests.
    provider = None if args.ignore_staleness else (lambda: builder.hand_id)
    clicker = CoinPokerClicker(
        default_paused=False,
        current_hand_provider=provider,
    )
    clicker.resume()  # explicit — gauntlet is active

    # Stats
    rounds_issued = 0
    rounds_logged = 0
    drops = 0
    latencies: list[float] = []
    pending: dict[str, float] = {}  # request marker -> timestamp

    # Warmup the builder from the existing log
    print(f"warming up builder from {args.frame_log} ...")
    n = 0
    with open(args.frame_log, "r", encoding="utf-8") as wf:
        for raw in wf:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                builder.ingest(json.loads(raw))
                n += 1
            except json.JSONDecodeError:
                continue
    print(f"warmup ingested {n} frames; "
          f"hand={builder.hand_id} hero_seat={builder.hero_seat}")

    inject_log_path = clicker.inject_log_path
    inject_offset = 0
    if os.path.exists(inject_log_path):
        inject_offset = os.path.getsize(inject_log_path)
    print(f"watching frames from EOF, inject log offset {inject_offset}")
    print(f"target rounds: {args.target_rounds}, action: {args.action}\n")

    def fire(hand_id_for_request):
        nonlocal rounds_issued
        marker = f"GAUNTLET_{rounds_issued + 1}_{hand_id_for_request}"
        ok = clicker.request_action(
            args.action, hand_id=hand_id_for_request,
            size=args.size, reason=marker)
        if ok:
            rounds_issued += 1
            pending[marker] = time.time()
            print(f"  -> #{rounds_issued:3d} hand={hand_id_for_request} "
                  f"action={args.action}  marker={marker}")
        else:
            print(f"  x blocked: hand={hand_id_for_request} "
                  f"sent={clicker.requests_sent} "
                  f"blocked={clicker.requests_blocked} "
                  f"stale={clicker.requests_stale}")
        return ok

    def drain_inject_log(offset):
        nonlocal rounds_logged
        new_lines, off = clicker.tail_inject_log(offset)
        for line in new_lines:
            matched = None
            for m in pending:
                if m in line:
                    matched = m
                    break
            if matched:
                rt = time.time() - pending.pop(matched)
                latencies.append(rt)
                rounds_logged += 1
                print(f"  <- #{rounds_logged:3d} {matched}  rt={rt*1000:.0f}ms")
        return off

    def check_drops():
        nonlocal drops
        now = time.time()
        for m in list(pending):
            if now - pending[m] > ROUND_TRIP_TIMEOUT_S:
                drops += 1
                print(f"  x DROP {m}  (>{ROUND_TRIP_TIMEOUT_S}s no log entry)")
                del pending[m]

    last_hero_turn = False
    start_time = time.time()
    try:
        if args.mode == "periodic":
            # Fire requests at a fixed rate, draining the inject log
            # between each. Doesn't depend on hero turns.
            print("[periodic mode] firing one request every "
                  f"{args.period_ms}ms (target {args.target_rounds})\n")
            next_fire = time.time()
            while rounds_logged < args.target_rounds:
                # Pull any new frames so the staleness check has a fresh
                # hand_id (cheap; we only ingest, don't snapshot).
                try:
                    pass  # builder is updated by warmup; periodic mode
                          # doesn't strictly need live frames if --ignore-staleness
                except Exception:
                    pass
                now = time.time()
                if now >= next_fire and len(pending) < 8:
                    fire(builder.hand_id or "GAUNTLET_NOHAND")
                    next_fire = now + args.period_ms / 1000.0
                inject_offset = drain_inject_log(inject_offset)
                check_drops()
                time.sleep(0.01)
        else:
            # hero-turn mode (original): fire on each fresh hero turn edge
            print("[hero-turn mode] firing on each detected hero turn\n")
            for raw in follow_log(args.frame_log):
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                builder.ingest(frame)
                snap = builder.snapshot()
                if snap is None:
                    continue
                if snap["hero_turn"] and not last_hero_turn and snap["hero_cards"]:
                    fire(snap["hand_id"])
                last_hero_turn = snap["hero_turn"]
                inject_offset = drain_inject_log(inject_offset)
                check_drops()
                if rounds_logged >= args.target_rounds:
                    break
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        clicker.pause()

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("PHASE 2 GAUNTLET REPORT")
    print("=" * 60)
    print(f"  elapsed:           {elapsed:.1f}s")
    print(f"  hero turns issued: {rounds_issued}")
    print(f"  log entries seen:  {rounds_logged}")
    print(f"  drops:             {drops}")
    print(f"  blocked (queue):   {clicker.requests_blocked}")
    print(f"  blocked (stale):   {clicker.requests_stale}")
    if latencies:
        latencies.sort()
        print(f"  latency p50:       {latencies[len(latencies)//2]*1000:.0f}ms")
        print(f"  latency p95:       {latencies[int(len(latencies)*0.95)]*1000:.0f}ms")
        print(f"  latency max:       {max(latencies)*1000:.0f}ms")
    print()
    if rounds_logged >= args.target_rounds and drops == 0:
        print("[PASS] PASS — Phase 2 round-trip is reliable")
        return 0
    elif drops > 0:
        print(f"[FAIL] FAIL — {drops} drop(s) — investigate before Phase 3")
        return 1
    else:
        print(f"[WARN] INCOMPLETE — only {rounds_logged}/{args.target_rounds} round-trips")
        return 1


if __name__ == "__main__":
    sys.exit(main())
