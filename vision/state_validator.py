"""Snapshot validation before the advisor sees it.

Three severity levels:
  ok     — pass to advisor, no extra log
  warn   — pass to advisor, log anomaly
  unsafe — block advisor, overlay shows "STATE?"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

_CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")
_VALID_PHASES = {"PREFLOP", "FLOP", "TURN", "RIVER"}
_VALID_POSITIONS = {"UTG", "MP", "HJ", "CO", "BTN", "SB", "BB"}
_PHASE_BOARD_LEN = {"PREFLOP": 0, "FLOP": 3, "TURN": 4, "RIVER": 5}


@dataclass
class ValidationResult:
    status: str  # "ok", "warn", "unsafe"
    checks_failed: List[str] = field(default_factory=list)


def validate_snapshot(snap: dict) -> ValidationResult:
    """Validate a CoinPoker snapshot dict.

    Returns ValidationResult with status and any failed check names.
    """
    failed: list[str] = []
    unsafe = False

    # ── required fields (unsafe if missing) ──────────────────────────

    hero_cards = snap.get("hero_cards") or []
    if not isinstance(hero_cards, list) or len(hero_cards) != 2:
        failed.append("hero_cards_missing")
        unsafe = True
    elif not all(_CARD_RE.match(c) for c in hero_cards):
        failed.append("hero_cards_invalid")
        unsafe = True

    board_cards = snap.get("board_cards") or []
    if not isinstance(board_cards, list) or len(board_cards) not in (0, 3, 4, 5):
        failed.append("board_cards_invalid_length")
        unsafe = True
    elif board_cards and not all(_CARD_RE.match(c) for c in board_cards):
        failed.append("board_cards_invalid_format")
        unsafe = True

    hand_id = snap.get("hand_id")
    if not hand_id or str(hand_id) == "0":
        failed.append("hand_id_missing")
        unsafe = True

    phase = snap.get("phase", "")
    if phase not in _VALID_PHASES:
        failed.append("phase_invalid")
        unsafe = True

    hero_turn = snap.get("hero_turn")
    if not isinstance(hero_turn, bool):
        failed.append("hero_turn_not_bool")
        unsafe = True

    position = snap.get("position", "")
    if position not in _VALID_POSITIONS:
        failed.append("position_invalid")
        unsafe = True

    # If any unsafe check failed, return immediately.
    if unsafe:
        return ValidationResult(status="unsafe", checks_failed=failed)

    # ── sanity checks (warn, still pass to advisor) ──────────────────

    pot = snap.get("pot", 0) or 0
    if phase != "PREFLOP" and pot <= 0:
        failed.append("pot_zero_postflop")

    hero_stack = snap.get("hero_stack", 0) or 0
    if hero_stack <= 0:
        failed.append("hero_stack_zero")

    call_amount = snap.get("call_amount", 0) or 0
    if call_amount < 0:
        failed.append("call_amount_negative")

    # Board length vs phase
    expected_board = _PHASE_BOARD_LEN.get(phase, -1)
    if expected_board >= 0 and len(board_cards) != expected_board:
        failed.append("board_phase_mismatch")

    # Duplicate cards
    all_cards = list(hero_cards) + list(board_cards)
    if len(all_cards) != len(set(all_cards)):
        failed.append("duplicate_cards")

    if failed:
        return ValidationResult(status="warn", checks_failed=failed)
    return ValidationResult(status="ok", checks_failed=[])
