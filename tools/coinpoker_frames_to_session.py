"""
Convert a CoinPoker frame log into a Unibet-format session JSONL.

CoinPoker `coinpoker_frames.jsonl` is the raw cmd_bean event stream from
the patched PBClient.dll. The Unibet `vision/data/session_*.jsonl` format
is one JSON record per played hand, with `hero`, `position`, `streets[]`,
and `profit_cents`. This converter:

  1. Walks the frame log via CoinPokerStateBuilder
  2. Runs the production AdvisorStateMachine on each snapshot
  3. Pipes results into SessionLogger, which writes the per-hand JSONL

The output file lands in `vision/data/` so `scripts/replay_whatif.py`
picks it up automatically alongside the existing Unibet session files.

Why this exists: tonight's lesson — passing unit tests is not enough,
we need replay validation against actual hands. `replay_whatif.py` is
the existing tool for that, but it only reads the Unibet format. Rather
than rewriting the replay tool, we convert at the data layer.

Usage:
    python tools/coinpoker_frames_to_session.py
    python tools/coinpoker_frames_to_session.py --frames C:/path/to/frames.jsonl
    python tools/coinpoker_frames_to_session.py --hero-id 1571120 --bb-chips 100
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

DEFAULT_FRAMES = r"C:\Users\Simon\coinpoker_frames.jsonl"
DEFAULT_HERO_ID = 1571120  # precious0864449


def _build_advisor():
    """Build the production AdvisorStateMachine. Same wiring as the runner."""
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
    sm = AdvisorStateMachine(
        base_advisor=base,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        assess_board_danger_fn=assess_board_danger,
        tracker=None,
        bb_cents=4,  # session.bb_cents will get auto-updated as we see hands
    )
    return sm


def _adapt_advisor_output_to_logger_rec(out) -> dict:
    """SessionLogger.update expects a recommendation dict with `action`,
    `equity`, optional `cfr_probs`. AdvisorOutput is an object — pull
    the fields it has."""
    if out is None:
        return {}
    return {
        "action": getattr(out, "action", "") or "",
        "equity": getattr(out, "equity", 0.0) or 0.0,
    }


def _frames(path: str):
    """Yield parsed JSON frames from a JSONL file."""
    import json
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=DEFAULT_FRAMES,
                    help="Input frame log (default: %(default)s)")
    ap.add_argument("--hero-id", type=int, default=DEFAULT_HERO_ID,
                    help="CoinPoker userId for hero (default: %(default)s)")
    ap.add_argument("--bb-chips", type=int, default=None,
                    help="Override big-blind chip amount in raw chips "
                         "(otherwise auto-detect from pre_hand_start_info)")
    ap.add_argument("--output-dir", default=None,
                    help="Where to write session_*.jsonl (default: vision/data/)")
    ap.add_argument("--output-name", default=None,
                    help="Override the auto-generated filename (e.g. session_coinpoker_test.jsonl). "
                         "If supplied, must NOT include the directory.")
    args = ap.parse_args()

    if not os.path.exists(args.frames):
        print(f"FATAL: frame log not found: {args.frames}", file=sys.stderr)
        return 2

    from coinpoker_adapter import CoinPokerStateBuilder, CHIP_SCALE
    from session_logger import SessionLogger

    builder = CoinPokerStateBuilder(args.hero_id)
    sm = _build_advisor()
    logger = SessionLogger(log_dir=args.output_dir)

    # Override the auto-generated filename if requested
    if args.output_name:
        log_dir = os.path.dirname(logger.log_path)
        logger.log_path = os.path.join(log_dir, args.output_name)
        # Wipe any prior content of the override file so we start fresh
        try:
            open(logger.log_path, "w").close()
        except Exception:
            pass

    print(f"[converter] frames:    {args.frames}")
    print(f"[converter] hero_id:   {args.hero_id}")
    print(f"[converter] output:    {logger.log_path}")
    print(f"[converter] processing ...")

    last_signature: Optional[tuple] = None
    snapshots_processed = 0
    advisor_calls = 0

    for frame in _frames(path=args.frames):
        builder.ingest(frame)
        snap = builder.snapshot()
        if snap is None:
            continue

        # Drop duplicate consecutive snapshots (same state) to avoid
        # spamming the logger with no-op updates
        sig = (
            snap.get("hand_id"),
            tuple(snap.get("hero_cards") or []),
            tuple(snap.get("board_cards") or []),
            snap.get("phase"),
            snap.get("call_amount"),
            snap.get("hero_stack"),
        )
        if sig == last_signature:
            continue
        last_signature = sig
        snapshots_processed += 1

        # Refresh BB scale from the live builder
        if args.bb_chips:
            sm.bb_cents = args.bb_chips * CHIP_SCALE
        elif builder.bb_amount > 0:
            sm.bb_cents = builder.bb_amount

        # Run the advisor only when we have hero cards (otherwise
        # process_state will return early and SessionLogger handles the
        # waiting state correctly).
        rec = {}
        if len(snap.get("hero_cards", [])) >= 2:
            try:
                out = sm.process_state(snap)
            except Exception as e:
                out = None
                if advisor_calls < 5:
                    print(f"  [advisor error] {type(e).__name__}: {e}")
            if out is not None:
                rec = _adapt_advisor_output_to_logger_rec(out)
                advisor_calls += 1

        # Drive the logger
        try:
            logger.update(snap, recommendation=rec if rec else None)
        except Exception as e:
            print(f"  [logger error] {type(e).__name__}: {e}")

    # Finalize any open hand
    if logger.current_hand:
        logger._write_hand()

    summary = logger.get_session_summary()
    print()
    print("=" * 60)
    print(f"[converter] done")
    print(f"  snapshots processed: {snapshots_processed}")
    print(f"  advisor calls:       {advisor_calls}")
    print(f"  hands logged:        {summary['hands']}")
    print(f"  output:              {summary['log_file']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
