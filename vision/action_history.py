"""
Per-hand ordered action history.

Replaces the empty-string `flop_action_history` placeholder in the SM
with a real per-street, per-player log of every villain action with
sizing. Required by:

  - Range narrowing (Phase 2): a villain's range conditions on the
    sequence of actions they took up to the current decision point.
  - Replay harness (Phase 1): when re-running an advisor against a
    captured hand, the harness needs the actual action sequence so it
    can ask the advisor "what would you do at THIS decision point,
    given everything that happened up to now."
  - Today's danger filters (legacy): some filters condition on whether
    hero already aggressed a street. The SM has a separate
    aggressed_phases set that this module subsumes.

Detection model
---------------

CoinPoker snapshots only show *current state*. To recover individual
actions we diff consecutive snapshots within the same hand. Two
signals are tracked per seat:

  1. ``bet`` increases  → CALL / BET / RAISE / ALLIN
     (classification by comparing prev round_max and current round_max)
  2. ``last_action`` becomes "Fold" or "Check" while bet unchanged
     → FOLD / CHECK

Street transitions reset per-seat bet tracking (chips committed roll
into the pot at the end of each street). Hand transitions reset
everything.

Limitations
-----------

  - If multiple players act between two snapshots, ordering within
    that batch is approximate (we emit them in seat order). At micro
    stakes snapshot frequency is high enough that this is rare.
  - Blinds appear as bet increases on the first preflop snapshot;
    they are tagged ``POST`` instead of BET/RAISE.
  - Straddles, posts-out-of-turn, and other exotica are folded into
    POST. Good enough for 6-max cash.

The module is intentionally pure: no IO, no globals, no shared state.
Each `ActionHistory` tracks one table; multi-table runners create N
instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Action type constants ─────────────────────────────────────────────
ACTION_POST = "POST"      # SB / BB / posted dead blind
ACTION_FOLD = "FOLD"
ACTION_CHECK = "CHECK"
ACTION_CALL = "CALL"
ACTION_BET = "BET"        # first voluntary chip in on a street
ACTION_RAISE = "RAISE"    # increase to an existing bet
ACTION_ALLIN = "ALLIN"    # all chips in (any source)

AGGRESSIVE_ACTIONS = frozenset({ACTION_BET, ACTION_RAISE, ACTION_ALLIN})


@dataclass
class Action:
    """One discrete poker action by one player on one street."""
    hand_id: str
    street: str          # PREFLOP / FLOP / TURN / RIVER
    seat: int
    user_id: Optional[int]
    name: str
    action: str          # see ACTION_* constants above
    amount: int          # delta in scaled chip cents (0 for fold/check)
    total_bet: int       # cumulative bet on this street after this action
    pot_before: int      # pot size at the moment of this action
    sequence_index: int  # 0, 1, 2, ... ordering within the hand


class ActionHistory:
    """
    Per-hand action log built by diffing consecutive snapshots.

    Usage:
        history = ActionHistory()
        for snap in snapshot_stream:
            new_actions = history.update(snap)  # returns delta
            ...
        flop = history.actions_on_street("FLOP")
        agg = history.last_aggressor("TURN")
        v_acts = history.villain_actions(hero_seat=5)
    """

    def __init__(self) -> None:
        self.hand_id: Optional[str] = None
        self.actions: list[Action] = []
        # Per-seat snapshot of (bet, last_action, stack) at the previous
        # update. Used to compute deltas. Reset on hand change.
        self._prev_seat_state: dict[int, dict] = {}
        self._prev_phase: Optional[str] = None
        self._sequence_index: int = 0
        # Whether we've seen the first preflop snapshot yet for this
        # hand — used to tag blinds as POST instead of BET/RAISE.
        self._seen_first_preflop: bool = False

    # ── lifecycle ────────────────────────────────────────────────────

    def reset(self) -> None:
        self.hand_id = None
        self.actions = []
        self._prev_seat_state = {}
        self._prev_phase = None
        self._sequence_index = 0
        self._seen_first_preflop = False

    # ── ingest ───────────────────────────────────────────────────────

    def update(self, snapshot: dict) -> list[Action]:
        """
        Process one snapshot and emit any newly-detected actions.

        Returns the list of actions added by this update (possibly
        empty). Side effect: appends to self.actions.
        """
        hand_id = snapshot.get("hand_id")
        if hand_id != self.hand_id:
            self.reset()
            self.hand_id = hand_id

        phase = snapshot.get("phase", "") or ""

        # Street transition: chip commits roll into the pot, per-seat
        # bet counter resets to 0 for the next street.
        if phase != self._prev_phase:
            for seat_state in self._prev_seat_state.values():
                seat_state["bet"] = 0
                seat_state["last_action"] = ""
            self._prev_phase = phase

        players = snapshot.get("players") or []
        pot = snapshot.get("pot", 0) or 0

        # Compute prev round-max BEFORE applying this snapshot's deltas.
        # Used to classify a bet increase as CALL vs BET vs RAISE.
        prev_round_max = max(
            (s.get("bet", 0) or 0 for s in self._prev_seat_state.values()),
            default=0,
        )

        new_actions: list[Action] = []

        # Iterate seats in seat order so multiple actions in the same
        # snapshot are appended in a deterministic order. Real action
        # order is approximate when more than one seat acted.
        sorted_players = sorted(
            players,
            key=lambda p: p.get("seat", 0) if p.get("seat") is not None else 0,
        )

        for p in sorted_players:
            seat = p.get("seat")
            if seat is None:
                continue
            cur_bet = p.get("bet", 0) or 0
            cur_action_raw = (p.get("last_action") or "").strip()
            cur_stack = p.get("stack", 0) or 0

            prev = self._prev_seat_state.get(
                seat,
                {"bet": 0, "last_action": "", "stack": cur_stack},
            )
            prev_bet = prev["bet"]
            prev_action = prev["last_action"]

            emitted: Optional[Action] = None

            # ── bet increase → CALL / BET / RAISE / ALLIN / POST ──
            if cur_bet > prev_bet:
                delta = cur_bet - prev_bet
                # Going all-in iff player has 0 chips left (or this delta
                # equals their previous stack).
                is_allin = (cur_stack == 0) or (delta >= prev["stack"])

                # First-preflop blinds get tagged POST not BET/RAISE.
                # Heuristic: PREFLOP, first snapshot we ever process for
                # the hand, the player's last_action is empty (they
                # haven't acted yet — just posted).
                is_first_pf = (
                    phase == "PREFLOP"
                    and not self._seen_first_preflop
                    and not cur_action_raw
                )

                if is_first_pf:
                    act = ACTION_POST
                elif is_allin:
                    act = ACTION_ALLIN
                elif prev_round_max == 0:
                    # No prior bet on this street — this player is the
                    # first voluntary chip in.
                    act = ACTION_BET
                elif cur_bet <= prev_round_max:
                    # Their total matches (or undershoots if short stack)
                    # the existing round bet → CALL.
                    act = ACTION_CALL
                else:
                    # Their total exceeds the prior round max → RAISE.
                    act = ACTION_RAISE

                emitted = Action(
                    hand_id=hand_id or "",
                    street=phase,
                    seat=seat,
                    user_id=p.get("user_id"),
                    name=p.get("name") or "",
                    action=act,
                    amount=delta,
                    total_bet=cur_bet,
                    pot_before=pot,
                    sequence_index=self._sequence_index,
                )

            # ── last_action becomes Fold (no bet change) → FOLD ──
            elif cur_action_raw.lower() == "fold" and prev_action.lower() != "fold":
                emitted = Action(
                    hand_id=hand_id or "",
                    street=phase,
                    seat=seat,
                    user_id=p.get("user_id"),
                    name=p.get("name") or "",
                    action=ACTION_FOLD,
                    amount=0,
                    total_bet=cur_bet,
                    pot_before=pot,
                    sequence_index=self._sequence_index,
                )

            # ── last_action becomes Check (no bet change) → CHECK ──
            elif cur_action_raw.lower() == "check" and prev_action.lower() != "check":
                emitted = Action(
                    hand_id=hand_id or "",
                    street=phase,
                    seat=seat,
                    user_id=p.get("user_id"),
                    name=p.get("name") or "",
                    action=ACTION_CHECK,
                    amount=0,
                    total_bet=cur_bet,
                    pot_before=pot,
                    sequence_index=self._sequence_index,
                )

            if emitted is not None:
                new_actions.append(emitted)
                self._sequence_index += 1

            # Update prev state for next snapshot's diff
            self._prev_seat_state[seat] = {
                "bet": cur_bet,
                "last_action": cur_action_raw,
                "stack": cur_stack,
            }

        if phase == "PREFLOP":
            self._seen_first_preflop = True

        self.actions.extend(new_actions)
        return new_actions

    # ── queries ──────────────────────────────────────────────────────

    def actions_on_street(self, street: str) -> list[Action]:
        return [a for a in self.actions if a.street == street]

    def villain_actions(self, hero_seat: int,
                        street: Optional[str] = None) -> list[Action]:
        pool = self.actions if street is None else self.actions_on_street(street)
        return [a for a in pool if a.seat != hero_seat]

    def last_aggressor(self, street: Optional[str] = None) -> Optional[Action]:
        """Most recent BET / RAISE / ALLIN action, optionally on a specific street."""
        pool = self.actions if street is None else self.actions_on_street(street)
        for a in reversed(pool):
            if a.action in AGGRESSIVE_ACTIONS:
                return a
        return None

    def num_aggressors(self, street: str) -> int:
        """Count distinct seats that took an aggressive action on a street."""
        seats = set()
        for a in self.actions_on_street(street):
            if a.action in AGGRESSIVE_ACTIONS:
                seats.add(a.seat)
        return len(seats)

    def hero_aggressed(self, hero_seat: int, street: str) -> bool:
        """True if hero took at least one aggressive action on this street."""
        for a in self.actions_on_street(street):
            if a.seat == hero_seat and a.action in AGGRESSIVE_ACTIONS:
                return True
        return False

    def to_compact_string(self, hero_seat: int) -> str:
        """
        Compact one-line summary used for the action_history input to
        the postflop CFR engine. Format: "B-C-r150-c150" style where
        each token is one action with hero/villain prefix.

        Hero actions get capital prefix, villain actions lowercase.
        """
        out = []
        for a in self.actions:
            prefix = "H" if a.seat == hero_seat else "v"
            tag = a.action[0]  # F/C/B/R/A/P
            if a.action in (ACTION_BET, ACTION_RAISE, ACTION_ALLIN, ACTION_CALL):
                out.append(f"{prefix}{tag}{a.amount}")
            else:
                out.append(f"{prefix}{tag}")
        return "-".join(out)
