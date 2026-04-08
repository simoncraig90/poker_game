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
                         board_cards: list) -> list[tuple[str, str]]:
    """
    Top-level entry point. Returns a list of (card1, card2) combos that
    villain could plausibly be holding given:

      - Their classification (NIT/TAG/LAG/FISH/UNKNOWN)
      - Their position
      - Their preflop action history (which selects a role)
      - Hero's hole cards (blockers)
      - The visible board (more blockers)

    Algorithm:
      1. Classify villain's role from action history
      2. Look up the starting range for (class, role, position)
      3. If role is UNKNOWN or the starting range is empty, fall
         back to `get_continuing_range(class, position)` — wider
         but still much tighter than 1326-random
      4. Expand to combo level
      5. Remove combos blocked by hero cards or board cards

    Returns an empty list only on input errors (no class, no position).
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

    # Fallback: if the role-specific range is empty (e.g., FISH OPEN
    # from BB which doesn't exist), fall back to the continuing range
    # for the position. Still much tighter than 1326-random.
    if not keys:
        keys = get_continuing_range(cls, villain_position)

    # Apply blockers from hero hole cards + visible board
    dead = list(hero_cards) + list(board_cards)
    return remove_blockers(keys, dead)


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
