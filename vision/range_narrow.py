"""
Range narrowing engine — Phase 2 deliverable #3.

Consumes the per-hand action sequence from `vision/action_history.py`
and the starting range tables from `vision/range_model.py` to produce
a narrowed combo-level range for any villain at any decision point.

v0 scope (this file)
--------------------

Preflop narrowing only. Postflop the range stays as whatever combos
entered the flop (after blocker removal for the board). v0 does not
further narrow on postflop actions because that requires per-combo
hand evaluation against the board, which is in scope for Phase 2's
equity calculator (next module). The preflop-only narrowing alone
shrinks the average combo count from ~1326 to ~30-200, which is the
single biggest equity-accuracy improvement we get from this module.

Narrowing model
---------------

Walk the per-hand action history. For the target villain, classify
their preflop "role" by the first aggressive action they took:

  - OPEN_RAISER:    first chips in beyond blinds, no one had raised yet
  - THREE_BET:      raised over an existing open
  - FOUR_BET:       raised over an existing 3-bet
  - COLD_CALL:      called an open (no prior aggression of their own)
  - BB_DEFEND:      called from BB, no prior aggression
  - LIMP:           voluntarily put in a chip without raising
  - SQUEEZE:        raised after at least one open and one call
  - UNKNOWN:        couldn't determine role from history

The role + villain class + position selects which starting range to
use from `range_model.py`. Then:

  1. Look up the base range for (class, role, position)
  2. Expand to combo level via `hand_combos.expand_range`
  3. Apply blockers (hero hole cards + visible board)
  4. Return the surviving combos

Output is a list of (card1, card2) tuples — the combos villain could
plausibly be holding given everything observed up to this point.
"""

from __future__ import annotations

from typing import Optional

from action_history import (
    ActionHistory,
    Action,
    ACTION_POST,
    ACTION_FOLD,
    ACTION_CHECK,
    ACTION_CALL,
    ACTION_BET,
    ACTION_RAISE,
    ACTION_ALLIN,
    AGGRESSIVE_ACTIONS,
)
from range_model import (
    get_starting_range,
    get_continuing_range,
    normalize_class,
    CLASS_NIT, CLASS_TAG, CLASS_LAG, CLASS_FISH, CLASS_UNKNOWN,
    POS_UTG, POS_MP, POS_CO, POS_BTN, POS_SB, POS_BB,
    ACTION_OPEN, ACTION_3BET, ACTION_4BET, ACTION_CALL as ACTION_CALL_PREFLOP,
    ACTION_LIMP,
)
from hand_combos import remove_blockers, expand_range
from hand_eval import evaluate


# ── role constants ────────────────────────────────────────────────────

ROLE_OPEN_RAISER = "OPEN_RAISER"
ROLE_THREE_BET = "THREE_BET"
ROLE_FOUR_BET = "FOUR_BET"
ROLE_COLD_CALL = "COLD_CALL"
ROLE_BB_DEFEND = "BB_DEFEND"
ROLE_LIMP = "LIMP"
ROLE_SQUEEZE = "SQUEEZE"
ROLE_UNKNOWN = "UNKNOWN"


def classify_villain_role(history: ActionHistory,
                          villain_seat: int,
                          villain_position: str) -> str:
    """
    Walk the preflop action history and classify the villain's role.

    The role is determined by villain's LAST voluntary action — if a
    villain opened then 4-bet, their RANGE is the 4-bet range, not
    the original open range. We classify by the highest aggression
    level reached in the sequence.

    Algorithm:
      1. Find villain's last voluntary action (skipping POST blinds)
      2. Count how many raises occurred BEFORE that action
         (including villain's own earlier raises — the index in
         the chronological raise sequence is what determines the
         3-bet/4-bet/etc level)
      3. Map the (last_action_type, prior_raise_count, callers) tuple
         to a ROLE_* constant

    Examples:
      - Last action is RAISE, 0 prior raises → OPEN_RAISER (raise #1)
      - Last action is RAISE, 1 prior raise → THREE_BET (raise #2)
        unless there was also a caller in between → SQUEEZE
      - Last action is RAISE, 2+ prior raises → FOUR_BET (raise #3+)
      - Last action is CALL, 0 prior raises → LIMP
      - Last action is CALL, 1+ prior raises → COLD_CALL (BB_DEFEND if BB)
      - Last action is FOLD → UNKNOWN (no postflop range needed)
      - No voluntary action observed → UNKNOWN
    """
    actions = history.actions_on_street("PREFLOP")

    # Villain's voluntary actions (excluding POST/blinds)
    villain_voluntary = [
        a for a in actions
        if a.seat == villain_seat and a.action != ACTION_POST
    ]
    if not villain_voluntary:
        return ROLE_UNKNOWN

    last = villain_voluntary[-1]

    # Count chronological raises strictly BEFORE villain's last action.
    # Includes villain's OWN earlier raises (level = sequence index).
    raises_before = 0
    callers_before = 0
    for a in actions:
        if a is last:
            break
        if a.action == ACTION_POST:
            continue
        if a.action in (ACTION_RAISE, ACTION_BET, ACTION_ALLIN):
            raises_before += 1
        elif a.action == ACTION_CALL:
            callers_before += 1

    if last.action in (ACTION_RAISE, ACTION_ALLIN, ACTION_BET):
        if raises_before == 0:
            return ROLE_OPEN_RAISER
        if raises_before == 1:
            # 3-bet level. Squeeze if there was also a caller before us.
            if callers_before >= 1:
                return ROLE_SQUEEZE
            return ROLE_THREE_BET
        return ROLE_FOUR_BET  # 2+ prior raises = 4-bet or higher

    if last.action == ACTION_CALL:
        if raises_before == 0:
            return ROLE_LIMP
        if villain_position == POS_BB:
            return ROLE_BB_DEFEND
        return ROLE_COLD_CALL

    # FOLD or any other terminal action
    return ROLE_UNKNOWN


def role_to_starting_action(role: str) -> str:
    """
    Map a classified villain role to the corresponding action key
    used by `range_model.get_starting_range`.

    SQUEEZE collapses to 3BET range for v0 (squeeze is a special
    3-bet shape; v0 doesn't have a separate squeeze range yet).
    BB_DEFEND collapses to CALL.
    """
    if role == ROLE_OPEN_RAISER:
        return ACTION_OPEN
    if role == ROLE_THREE_BET:
        return ACTION_3BET
    if role == ROLE_SQUEEZE:
        return ACTION_3BET  # v0 collapse
    if role == ROLE_FOUR_BET:
        return ACTION_4BET
    if role == ROLE_COLD_CALL:
        return ACTION_CALL_PREFLOP
    if role == ROLE_BB_DEFEND:
        return ACTION_CALL_PREFLOP
    if role == ROLE_LIMP:
        return ACTION_LIMP
    return ""  # UNKNOWN — no specific range


def narrow_villain_range(history: ActionHistory,
                         villain_seat: int,
                         villain_position: str,
                         villain_class: str,
                         hero_cards: list,
                         board_cards: list,
                         apply_postflop: bool = True) -> list[tuple[str, str]]:
    """
    Top-level entry point. Returns a list of (card1, card2) combos that
    villain could plausibly be holding given everything observed.

    Algorithm:
      1. Classify villain's role from preflop action history
      2. Look up the starting range for (class, role, position)
      3. Fall back to continuing range if role-specific is empty
      4. Expand to combo level
      5. Remove combos blocked by hero cards or board cards
      6. If apply_postflop=True (default), walk villain's postflop
         actions and apply per-street strength filters via
         narrow_postflop. Each street narrows further based on
         what action villain took at THAT street's board state.

    Args:
        apply_postflop: when False, returns the preflop-only narrowed
            range (used by tests that want to verify the preflop
            stage in isolation, and by performance-sensitive callers).

    Returns an empty list only on input errors (no class, no position)
    or when narrowing eliminated all combos.
    """
    cls = normalize_class(villain_class)
    if not villain_position:
        return []

    role = classify_villain_role(history, villain_seat, villain_position)
    range_action = role_to_starting_action(role)

    if range_action:
        keys = get_starting_range(cls, villain_position, range_action)
    else:
        keys = set()

    if not keys:
        keys = get_continuing_range(cls, villain_position)

    # Apply blockers from hero hole cards + visible board
    dead = list(hero_cards) + list(board_cards)
    combos = remove_blockers(keys, dead)

    # Postflop narrowing — walk villain's per-street actions and
    # filter by hand strength implied by each action.
    if apply_postflop and len(board_cards) >= 3:
        combos = narrow_postflop(combos, history, villain_seat, board_cards)

    return combos


def _filter_combos_by_strength(combos: list,
                               board: list,
                               keep_top_pct: float) -> list:
    """
    Sort combos by hand strength on the given board and keep the top
    `keep_top_pct` fraction (by hand_eval tuple, descending).

    Used by postflop narrowing — when villain takes an action that
    reveals strength, we keep only the combos at or above the implied
    strength percentile.

    Edge cases:
      - keep_top_pct >= 1.0 → all combos returned (no narrowing)
      - keep_top_pct <= 0.0 → empty list
      - empty input → empty list
      - len(combos) == 1 → returns the one combo (always keeps at least 1)
    """
    if not combos or keep_top_pct <= 0.0:
        return []
    if keep_top_pct >= 1.0:
        return list(combos)
    if len(combos) <= 1:
        return list(combos)

    scored = []
    for combo in combos:
        try:
            score = evaluate(list(combo) + list(board))
        except Exception:
            # Combo invalid (shouldn't happen — caller filtered blockers)
            continue
        scored.append((score, combo))
    if not scored:
        return []
    scored.sort(reverse=True)  # strongest first

    keep_n = max(1, int(round(len(scored) * keep_top_pct)))
    return [combo for _, combo in scored[:keep_n]]


# Per-action retention percentages used by postflop narrowing.
# Tuned to be conservative — too-tight narrowing biases equity wrong
# the other way. Numbers are "fraction of combos kept" by strength rank.
_POSTFLOP_KEEP = {
    ACTION_FOLD:  0.00,  # gone
    ACTION_CHECK: 1.00,  # no info — could be anything
    ACTION_CALL:  0.65,  # medium and up — drops the worst third
    ACTION_BET:   0.50,  # value-bets and bluffs from the top half
    ACTION_RAISE: 0.30,  # mostly value at micros
    ACTION_ALLIN: 0.20,  # very polarized — top of range or pure bluff
}

# Check-raise is much more polarized than a regular raise. The "I
# checked, opponent bet, now I raise" line at micros is almost always
# value (sets, two pair, top pair + good kicker on dynamic boards).
# Pure bluff check-raises are rare; semi-bluff check-raises are more
# common but still narrow. Conservative tuning at v0.
_CHECK_RAISE_KEEP = 0.15


def narrow_postflop(combos: list,
                    history: ActionHistory,
                    villain_seat: int,
                    final_board: list) -> list:
    """
    Walk villain's postflop actions and apply per-action strength
    filters to narrow the combo set street by street.

    For each street the villain acted on:
      - Look up villain's actions on that street (in order)
      - For each action, filter combos by `_POSTFLOP_KEEP[action]`
        applied to combos' hand strength on the BOARD AT THAT STREET
        (the prefix of `final_board` for that street)

    The board prefix matters: a combo's hand strength on the flop
    might be PAIR but on the river it might be TWO_PAIR after another
    matching card lands. We narrow against villain's perception at
    the time they took the action.

    v0 quirks (deferred to refinement):
      - Treats CHECK as no-info (real solvers know what kinds of hands
        check). Conservative.
      - Doesn't model bet sizing — a pot-sized bet narrows more than
        a small block bet. v0 uses one threshold for all sizings.
      - Doesn't model multi-street consistency (a player can't have a
        flush on the turn if the third heart was the river card).
        That falls out naturally from the per-street board prefix.
    """
    if not combos:
        return []
    work = list(combos)
    for street in ("FLOP", "TURN", "RIVER"):
        if street == "FLOP":
            board_at_street = final_board[:3]
        elif street == "TURN":
            board_at_street = final_board[:4]
        else:
            board_at_street = final_board[:5]
        if len(board_at_street) < 3:
            break  # haven't reached this street yet

        street_actions = [
            a for a in history.actions_on_street(street)
            if a.seat == villain_seat
        ]
        prev_villain_action_on_street = None
        for a in street_actions:
            keep_pct = _POSTFLOP_KEEP.get(a.action, 1.0)
            # Check-raise detection: villain's PREVIOUS action this
            # street was a CHECK and now they're betting/raising. Much
            # narrower than a regular raise.
            if (a.action in (ACTION_BET, ACTION_RAISE)
                    and prev_villain_action_on_street == ACTION_CHECK):
                keep_pct = _CHECK_RAISE_KEEP
            work = _filter_combos_by_strength(work, board_at_street, keep_pct)
            prev_villain_action_on_street = a.action
            if not work:
                return []
    return work


def villain_combo_count(history: ActionHistory,
                        villain_seat: int,
                        villain_position: str,
                        villain_class: str,
                        hero_cards: list,
                        board_cards: list) -> int:
    """Convenience: just the count of combos in villain's narrowed range."""
    return len(narrow_villain_range(
        history, villain_seat, villain_position, villain_class,
        hero_cards, board_cards
    ))
