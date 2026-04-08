"""
Replay-vs-real-outcomes harness — Phase 1 of the rebuild.

The validation gate Simon's memory has been asking for. Walks the
captured frame corpus (`coinpoker_frames.jsonl`), drives the SAME
session/builder/advisor stack the live runner uses, and records every
hero decision point with:

  - The advisor's recommendation
  - The pot/call/stacks at the moment of decision
  - The hero's actual subsequent action (from the next snapshot)
  - The full hand outcome (delta in chips at hand end)

Aggregates into a `ReplayReport` with per-hand records and overall
chip / BB-per-100 totals. This is the *baseline* harness — Phase 1
exit criterion is simply that it reproduces actual session results
within ±2 BB/100 when run against the captured corpus.

Counterfactual ("what would the advisor have made?") scoring is
explicitly NOT in the MVP. That comes after the range-aware equity
model lands in Phase 2 — only then can we score hypothetical lines
with any honesty. For now we record disagreements as observations
and let the user audit them by hand.

Design notes:

  - Pure Python, no IO except the input frame stream. The caller
    decides whether to pass an open file handle, an iterable of
    pre-parsed lines, or a list (for tests).
  - Uses the live `MultiTableCoinPokerSession` so this harness is
    guaranteed to see snapshots in exactly the same shape the live
    runner does. If the harness disagrees with live, the harness is
    wrong.
  - Multi-room aware. The frame log interleaves rooms; we maintain
    per-room hand state.
  - Fail-soft on per-snapshot errors: a buggy SM call records the
    exception and skips the decision, never crashes the harness.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

from coinpoker_runner import MultiTableCoinPokerSession  # noqa: E402
from coinpoker_adapter import CHIP_SCALE  # noqa: E402


# ── records ───────────────────────────────────────────────────────────


@dataclass
class Decision:
    """One hero decision point during a hand."""
    hand_id: str
    room: str
    phase: str
    pot: int
    hero_stack: int
    call_amount: int
    facing_bet: bool
    hero_cards: list
    board: list
    position: str
    advisor_action: str
    advisor_source: str
    advisor_equity: float
    error: Optional[str] = None  # populated if SM raised


@dataclass
class HandRecord:
    """One completed hand with all hero decision points captured.

    Stack accounting model
    ----------------------

    A hand's chip_delta is computed from two boundary snapshots:

      - ``starting_stack``: hero_stack from the FIRST snapshot of this
        hand. Represents "stack going into this hand", with whatever
        blinds may already have been posted included.

      - ``ending_stack``: hero_stack from the FIRST snapshot of the
        NEXT hand at the same room. Represents "stack going into the
        next hand", which is the same vantage point as starting_stack
        — both points are taken from "the first snapshot of a hand."
        Using a fixed vantage point eliminates the within-hand
        ambiguity (mid-hand snapshots can show partial bets, partial
        payouts, etc).

    For the very last hand of the corpus, there is no successor hand
    to take the ending_stack from. We fall back to the last seen
    snapshot's hero_stack — which is approximate but the best
    available data.

    A flag ``ending_finalized`` indicates whether ending_stack was
    set from a real next-hand boundary (True) or from the
    last-snapshot fallback (False). The harness's BB/100 calculation
    can optionally exclude un-finalized hands to keep aggregates
    clean.
    """
    hand_id: str
    room: str
    starting_stack: int
    ending_stack: int
    bb_cents: int            # the BB scale for this room (chips per BB)
    decisions: list[Decision] = field(default_factory=list)
    ending_finalized: bool = False

    @property
    def chip_delta(self) -> int:
        return self.ending_stack - self.starting_stack

    @property
    def bb_delta(self) -> float:
        if self.bb_cents <= 0:
            return 0.0
        return self.chip_delta / self.bb_cents


@dataclass
class ReplayReport:
    """Aggregate result across all hands processed by the harness."""
    hands: list[HandRecord] = field(default_factory=list)
    sm_errors: int = 0
    runtime_seconds: float = 0.0
    frames_processed: int = 0

    @property
    def total_hands(self) -> int:
        return len(self.hands)

    @property
    def total_decisions(self) -> int:
        return sum(len(h.decisions) for h in self.hands)

    @property
    def total_chip_delta(self) -> int:
        return sum(h.chip_delta for h in self.hands)

    @property
    def total_bb_delta(self) -> float:
        return sum(h.bb_delta for h in self.hands)

    def bb_per_100(self) -> float:
        """BB/100 hands across the corpus. Returns 0 if no hands processed."""
        if not self.hands:
            return 0.0
        return (self.total_bb_delta / self.total_hands) * 100.0

    def summary(self) -> str:
        """One-paragraph human-readable summary."""
        return (
            f"ReplayReport: hands={self.total_hands} "
            f"decisions={self.total_decisions} "
            f"chip_delta={self.total_chip_delta} "
            f"bb_delta={self.total_bb_delta:+.2f} "
            f"bb/100={self.bb_per_100():+.2f} "
            f"sm_errors={self.sm_errors} "
            f"frames={self.frames_processed} "
            f"runtime={self.runtime_seconds:.1f}s"
        )

    def per_room_breakdown(self) -> dict[str, dict]:
        """
        Group hands by room and return per-room aggregate stats.
        Critical for the corpus, which mixes practice tables (huge
        chip values, no real EV) and real-money tables (the only ones
        that matter for the Phase 1 BB/100 baseline).
        """
        rooms: dict[str, dict] = {}
        for h in self.hands:
            r = rooms.setdefault(h.room or "(default)", {
                "hands": 0, "decisions": 0, "chip_delta": 0,
                "bb_delta": 0.0, "bb_cents": h.bb_cents,
            })
            r["hands"] += 1
            r["decisions"] += len(h.decisions)
            r["chip_delta"] += h.chip_delta
            r["bb_delta"] += h.bb_delta
        for r in rooms.values():
            r["bb_per_100"] = (
                (r["bb_delta"] / r["hands"]) * 100.0 if r["hands"] else 0.0
            )
        return rooms

    def filter_real_money(self) -> "ReplayReport":
        """
        Return a new report containing only hands from real-money rooms.
        Heuristic: real-money rooms have bb_cents <= 1000 (NL10 and below
        in scaled units). Practice tables use 10000 (100 chips × CHIP_SCALE).
        """
        new = ReplayReport()
        new.runtime_seconds = self.runtime_seconds
        new.frames_processed = self.frames_processed
        new.sm_errors = self.sm_errors
        new.hands = [h for h in self.hands if 0 < h.bb_cents <= 1000]
        return new

    def last_n_hands(self, n: int) -> "ReplayReport":
        """
        Return a new report containing only the most recent N hands
        across all rooms (preserves frame order). Used to scope to a
        single session — pass approximately the number of hands the
        session played.
        """
        if n <= 0 or n >= len(self.hands):
            new = ReplayReport()
            new.runtime_seconds = self.runtime_seconds
            new.frames_processed = self.frames_processed
            new.sm_errors = self.sm_errors
            new.hands = list(self.hands)
            return new
        new = ReplayReport()
        new.runtime_seconds = self.runtime_seconds
        new.frames_processed = self.frames_processed
        new.sm_errors = self.sm_errors
        new.hands = list(self.hands)[-n:]
        return new

    def finalized_only(self) -> "ReplayReport":
        """
        Return a new report containing only hands whose ending_stack
        was set from a real next-hand boundary. Excludes the last
        hand of the corpus (whose ending stack is the
        last-snapshot fallback) and any hands cut by mid-stream EOF.
        """
        new = ReplayReport()
        new.runtime_seconds = self.runtime_seconds
        new.frames_processed = self.frames_processed
        new.sm_errors = self.sm_errors
        new.hands = [h for h in self.hands if h.ending_finalized]
        return new


# ── harness ───────────────────────────────────────────────────────────


class ReplayHarness:
    """
    Walk a frame corpus and produce a `ReplayReport`.

    Args:
        hero_user_id: CoinPoker user_id to treat as hero.
        advisor_factory: callable taking ``bb_cents`` (int) and returning
            an object with a ``process_state(snapshot) -> AdvisorOutput``
            method. The factory is called once per room. If None, the
            harness records decisions but skips the advisor call (useful
            for sanity-checking the corpus walker itself).

    Use:
        h = ReplayHarness(hero_user_id=12345, advisor_factory=make_sm)
        report = h.run_path("/path/to/coinpoker_frames.jsonl")
        print(report.summary())
    """

    def __init__(self,
                 hero_user_id: int,
                 advisor_factory: Optional[Callable[[int], Any]] = None):
        self.hero_user_id = int(hero_user_id)
        self.advisor_factory = advisor_factory
        # Per-room state
        self._advisors: dict[str, Any] = {}
        self._hand_state: dict[str, Optional[HandRecord]] = {}
        self._report = ReplayReport()
        self._session: Optional[MultiTableCoinPokerSession] = None

    # ── public entry points ──────────────────────────────────────────

    def run_path(self, frame_log_path: str) -> ReplayReport:
        """Open a JSONL file and run the harness against it."""
        with open(frame_log_path, "r", encoding="utf-8", errors="replace") as f:
            return self.run_lines(f)

    def run_lines(self, lines: Iterable[str]) -> ReplayReport:
        """Run against an iterable of JSONL strings (one frame per line)."""
        self._reset()
        t0 = time.time()
        for line in lines:
            self._session.feed_line(line if isinstance(line, str) else json.dumps(line))
        # Finalize any hand still in flight at EOF
        for room, hr in list(self._hand_state.items()):
            if hr is not None:
                self._finalize(room)
        self._report.runtime_seconds = time.time() - t0
        self._report.frames_processed = self._session.frames_seen
        return self._report

    def run_frames(self, frames: Iterable[dict]) -> ReplayReport:
        """Run against pre-parsed frame dicts (used by tests)."""
        return self.run_lines(json.dumps(f) for f in frames)

    # ── internals ────────────────────────────────────────────────────

    def _reset(self):
        self._advisors = {}
        self._hand_state = {}
        self._last_seen_stack: dict[str, int] = {}
        self._report = ReplayReport()
        self._session = MultiTableCoinPokerSession(
            hero_user_id=self.hero_user_id,
            on_snapshot=self._handle_snapshot,
        )

    def _get_advisor(self, room: str) -> Optional[Any]:
        if self.advisor_factory is None:
            return None
        if room not in self._advisors:
            try:
                bb = self._session.bb_cents(room) if self._session else 100 * CHIP_SCALE
                self._advisors[room] = self.advisor_factory(bb)
            except Exception:
                traceback.print_exc()
                self._advisors[room] = None
        return self._advisors.get(room)

    def _handle_snapshot(self, snap: dict):
        room = snap.get("room_name", "") or ""
        hand_id = snap.get("hand_id")
        if hand_id is None:
            return

        # Hand transition: finalize previous hand for this room, start new.
        # The new hand's starting hero_stack is also the previous hand's
        # *ending* hero_stack — both are captured from "first snapshot of
        # a hand" so the boundary is symmetric and within-hand ambiguity
        # (partial bets, payouts not yet settled) doesn't pollute the delta.
        prev = self._hand_state.get(room)
        starting_stack = int(snap.get("hero_stack", 0) or 0)
        if prev is None or prev.hand_id != hand_id:
            if prev is not None:
                # Finalize previous hand using THIS snapshot's stack as
                # the ending — that's "stack between hands" measured at
                # the start of the next hand.
                prev.ending_stack = starting_stack
                prev.ending_finalized = True
                self._finalize(room)
            self._hand_state[room] = HandRecord(
                hand_id=str(hand_id),
                room=room,
                starting_stack=starting_stack,
                ending_stack=starting_stack,
                bb_cents=0,  # filled in at finalize
                ending_finalized=False,
            )

        hr = self._hand_state[room]
        # Track the most recent within-hand stack as a fallback ending
        # value, used only if this hand is the LAST one in the corpus
        # (no successor hand to provide the proper boundary).
        hero_stack = snap.get("hero_stack")
        if hero_stack is not None:
            self._last_seen_stack[room] = int(hero_stack)

        # Decision point: hero_turn AND hero has cards
        if not snap.get("hero_turn"):
            return
        hero_cards = snap.get("hero_cards") or []
        if len(hero_cards) < 2:
            return

        sm = self._get_advisor(room)
        if sm is None:
            # Skip but still record we saw a decision point
            hr.decisions.append(Decision(
                hand_id=str(hand_id),
                room=room,
                phase=snap.get("phase", "") or "",
                pot=int(snap.get("pot", 0) or 0),
                hero_stack=int(snap.get("hero_stack", 0) or 0),
                call_amount=int(snap.get("call_amount", 0) or 0),
                facing_bet=bool(snap.get("facing_bet")),
                hero_cards=list(hero_cards),
                board=list(snap.get("board_cards") or []),
                position=snap.get("position", "") or "",
                advisor_action="",
                advisor_source="(no advisor)",
                advisor_equity=0.0,
            ))
            return

        try:
            out = sm.process_state(snap)
        except Exception as e:
            self._report.sm_errors += 1
            hr.decisions.append(Decision(
                hand_id=str(hand_id),
                room=room,
                phase=snap.get("phase", "") or "",
                pot=int(snap.get("pot", 0) or 0),
                hero_stack=int(snap.get("hero_stack", 0) or 0),
                call_amount=int(snap.get("call_amount", 0) or 0),
                facing_bet=bool(snap.get("facing_bet")),
                hero_cards=list(hero_cards),
                board=list(snap.get("board_cards") or []),
                position=snap.get("position", "") or "",
                advisor_action="",
                advisor_source=f"ERROR:{type(e).__name__}",
                advisor_equity=0.0,
                error=f"{type(e).__name__}: {e}",
            ))
            return

        if out is None:
            return

        hr.decisions.append(Decision(
            hand_id=str(hand_id),
            room=room,
            phase=getattr(out, "phase", "") or snap.get("phase", "") or "",
            pot=int(snap.get("pot", 0) or 0),
            hero_stack=int(snap.get("hero_stack", 0) or 0),
            call_amount=int(snap.get("call_amount", 0) or 0),
            facing_bet=bool(snap.get("facing_bet")),
            hero_cards=list(hero_cards),
            board=list(snap.get("board_cards") or []),
            position=snap.get("position", "") or "",
            advisor_action=getattr(out, "action", "") or "",
            advisor_source=getattr(out, "source", "") or "",
            advisor_equity=float(getattr(out, "equity", 0.0) or 0.0),
        ))

    def _finalize(self, room: str):
        hr = self._hand_state.get(room)
        if hr is None:
            return
        # Resolve bb_cents NOW that the room's builder has had time to
        # learn the real BB from the hand's frame stream.
        try:
            bb = (self._session.bb_cents(room)
                  if self._session else 100 * CHIP_SCALE)
        except Exception:
            bb = 100 * CHIP_SCALE
        hr.bb_cents = int(bb) if bb > 0 else 100 * CHIP_SCALE
        # If ending_stack hasn't been finalized by a next-hand boundary
        # (this is the last hand of the corpus, or the corpus was cut
        # mid-stream), fall back to the last seen within-hand stack.
        # Approximate but the best we can do.
        if not hr.ending_finalized:
            fallback = self._last_seen_stack.get(room)
            if fallback is not None:
                hr.ending_stack = fallback
        self._report.hands.append(hr)
        self._hand_state[room] = None


# ── CLI ───────────────────────────────────────────────────────────────


def _make_default_advisor_factory():
    """
    Build the standard live advisor stack (BaseAdvisor + PostflopEngine
    + preflop chart + AdvisorStateMachine). Used by the CLI; lazy-loads
    so unit tests don't pay the import cost.
    """
    from advisor import Advisor as BaseAdvisor
    from preflop_chart import preflop_advice
    from advisor_state_machine import AdvisorStateMachine
    try:
        from strategy.postflop_engine import PostflopEngine
        postflop = PostflopEngine()
    except Exception:
        postflop = None
    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)

    def factory(bb_cents: int):
        return AdvisorStateMachine(
            base_advisor=base,
            preflop_advice_fn=preflop_advice,
            postflop_engine=postflop,
            tracker=None,
            bb_cents=bb_cents,
        )
    return factory


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Replay harness — walks captured frames")
    p.add_argument("--file", default=r"C:\Users\Simon\coinpoker_frames.jsonl",
                   help="Frame log path")
    # Default matches the live runner's HERO_USER_ID_DEFAULT in
    # coinpoker_runner.py (precious0864449 — hero on the practice table).
    # Override with --hero-id if running against a different account.
    p.add_argument("--hero-id", type=int, default=1571120,
                   help="Hero CoinPoker user_id")
    p.add_argument("--no-advisor", action="store_true",
                   help="Skip the advisor call (corpus-walker sanity check)")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after processing N frames (0 = all)")
    p.add_argument("--real-money-only", action="store_true",
                   help="Filter out practice-table hands (bb_cents > 1000)")
    p.add_argument("--last-hands", type=int, default=0,
                   help="Slice to most recent N hands (session scoping)")
    p.add_argument("--finalized-only", action="store_true",
                   help="Exclude hands whose ending stack came from EOF fallback")
    p.add_argument("--by-room", action="store_true",
                   help="Print per-room breakdown")
    args = p.parse_args(argv)

    factory = None if args.no_advisor else _make_default_advisor_factory()
    harness = ReplayHarness(hero_user_id=args.hero_id, advisor_factory=factory)

    if args.limit > 0:
        # Stream-with-limit
        def lines_iter():
            with open(args.file, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= args.limit:
                        break
                    yield line
        report = harness.run_lines(lines_iter())
    else:
        report = harness.run_path(args.file)

    print(report.summary())
    if args.finalized_only:
        report = report.finalized_only()
        print("FINALIZED ONLY:  " + report.summary())
    if args.real_money_only:
        report = report.filter_real_money()
        print("REAL-MONEY ONLY: " + report.summary())
    if args.last_hands > 0:
        report = report.last_n_hands(args.last_hands)
        print(f"LAST {args.last_hands} HANDS:  " + report.summary())
    if args.by_room:
        rooms = report.per_room_breakdown()
        print(f"\nPer-room breakdown ({len(rooms)} rooms):")
        for name, r in sorted(rooms.items(), key=lambda x: -x[1]["hands"]):
            display = name if len(name) <= 50 else name[:47] + "..."
            print(f"  {display:50}  hands={r['hands']:4}  "
                  f"bb_cents={r['bb_cents']:6}  "
                  f"bb_delta={r['bb_delta']:+9.2f}  "
                  f"bb/100={r['bb_per_100']:+8.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
