"""
Range vs hero equity calculator — Phase 2 deliverable #5.

The structural fix the entire rebuild has been pointing toward.
Replaces the hand-vs-random equity model with hand-vs-actual-range
equity, computed against the combo set produced by the range
narrowing engine.

Algorithm
---------

Given:
  - hero_hand: 2 card strings ["Ah", "Ks"]
  - villain_combos: list of (c1, c2) tuples (already filtered for
    blockers by range_narrow.narrow_villain_range)
  - board: 0-5 visible board cards

Compute hero's expected equity by Monte Carlo simulation:

  for `samples` iterations:
    1. Pick a random villain combo from villain_combos
    2. Deal random cards to fill the board to 5
    3. Compare hero's best 5 vs villain's best 5
    4. Score 1.0 win, 0.5 tie, 0.0 loss
  return mean

Special cases:
  - If board has 5 cards already, no dealing needed; just enumerate
    all villain combos and average
  - If villain_combos is empty, return 0.5 (no information → coin flip)
  - If villain_combos has very few combos (<= 50), use exhaustive
    enumeration over villain combos × random board completions
    instead of Monte Carlo for stability

Output is hero's equity as a float in [0.0, 1.0].
"""

from __future__ import annotations

import random
from typing import Iterable, Optional

from hand_eval import evaluate, compare
from hand_combos import ALL_CARDS


def _build_dead_set(hero_hand: list, board: list,
                    villain_combo: tuple) -> set:
    """All cards currently 'in play' (hero + board + this villain combo)."""
    return set(hero_hand) | set(board) | set(villain_combo)


def _deck_minus(dead: set) -> list:
    """The remaining deck after removing dead cards. List for random.sample."""
    return [c for c in ALL_CARDS if c not in dead]


def hero_equity_vs_combo(hero_hand: list,
                         villain_combo: tuple,
                         board: list,
                         samples: Optional[int] = None,
                         rng: Optional[random.Random] = None) -> float:
    """
    Hero equity (in [0,1]) against ONE specific villain combo on
    the given board. Used as a building block by the range version.

    If board has 5 cards: deterministic showdown, samples ignored.
    Otherwise: Monte Carlo over `samples` board completions, default 200.

    Returns 0.5 if hero/villain conflict (cards overlap) — caller is
    expected to filter blockers, but we defend.
    """
    if rng is None:
        rng = random.Random(0)
    if samples is None:
        samples = 200

    hero_set = set(hero_hand)
    villain_set = set(villain_combo)
    board_set = set(board)
    if hero_set & villain_set or hero_set & board_set or villain_set & board_set:
        return 0.5

    cards_to_deal = 5 - len(board)
    if cards_to_deal == 0:
        # Showdown directly
        h_score = evaluate(list(hero_hand) + list(board))
        v_score = evaluate(list(villain_combo) + list(board))
        cmp = compare(h_score, v_score)
        return 1.0 if cmp > 0 else (0.0 if cmp < 0 else 0.5)

    dead = hero_set | villain_set | board_set
    deck = _deck_minus(dead)

    wins = 0.0
    for _ in range(samples):
        # Random board completion
        runout = rng.sample(deck, cards_to_deal)
        full_board = list(board) + runout
        h_score = evaluate(list(hero_hand) + full_board)
        v_score = evaluate(list(villain_combo) + full_board)
        cmp = compare(h_score, v_score)
        if cmp > 0:
            wins += 1.0
        elif cmp == 0:
            wins += 0.5
    return wins / samples


def hero_equity_vs_range(hero_hand: list,
                         villain_combos: list,
                         board: list,
                         samples_per_combo: Optional[int] = None,
                         rng: Optional[random.Random] = None) -> float:
    """
    Hero equity (in [0,1]) against a list of villain combos on the
    given board. Each combo is treated as equally likely (no per-combo
    weighting in v0; range_model.py's combo expansion already
    represents weighting via combo counts).

    If villain_combos is empty, returns 0.5.

    Implementation: weighted Monte Carlo. For small combo counts
    (<= 50) we run a fixed number of samples per combo for stability;
    for larger sets we sample combos proportionally to budget.
    """
    if not villain_combos:
        return 0.5

    if rng is None:
        rng = random.Random(0)

    n_combos = len(villain_combos)

    # Adaptive sample budget — more combos → fewer samples per combo
    if samples_per_combo is None:
        if n_combos <= 50:
            samples_per_combo = 100
        elif n_combos <= 200:
            samples_per_combo = 50
        else:
            samples_per_combo = 20

    cards_to_deal = 5 - len(board)
    hero_set = set(hero_hand)
    board_set = set(board)

    total = 0.0
    valid_combos = 0
    for combo in villain_combos:
        v_set = set(combo)
        if v_set & hero_set or v_set & board_set:
            continue
        valid_combos += 1
        if cards_to_deal == 0:
            # Direct showdown
            h_score = evaluate(list(hero_hand) + list(board))
            v_score = evaluate(list(combo) + list(board))
            cmp = compare(h_score, v_score)
            total += 1.0 if cmp > 0 else (0.0 if cmp < 0 else 0.5)
            continue

        dead = hero_set | v_set | board_set
        deck = _deck_minus(dead)
        for _ in range(samples_per_combo):
            runout = rng.sample(deck, cards_to_deal)
            full_board = list(board) + runout
            h_score = evaluate(list(hero_hand) + full_board)
            v_score = evaluate(list(combo) + full_board)
            cmp = compare(h_score, v_score)
            if cmp > 0:
                total += 1.0
            elif cmp == 0:
                total += 0.5
        # Average across the samples for THIS combo, then we'll average
        # across combos at the end. But total accumulates raw wins, so
        # we need to divide by total samples seen.

    if valid_combos == 0:
        return 0.5

    if cards_to_deal == 0:
        return total / valid_combos
    return total / (valid_combos * samples_per_combo)


def hero_equity_vs_multiway(hero_hand: list,
                            villain_ranges: list,
                            board: list,
                            samples: Optional[int] = None,
                            rng: Optional[random.Random] = None) -> float:
    """
    Hero's equity in a multiway pot against N independent villain
    ranges.

    Args:
        hero_hand: 2 card strings
        villain_ranges: list where each element is itself a list of
            (card1, card2) combos representing one villain's range.
            Already pre-filtered for blockers via remove_blockers.
            len(villain_ranges) == number of opponents at showdown.
        board: 0..5 visible board cards
        samples: Monte Carlo trial budget (default 300 for 2 villains,
            200 for 3, 150 for 4+, scaled down for combinatorial cost)

    Algorithm:
        for each trial:
            for each villain i in order:
                sample a combo from villain_ranges[i] that doesn't
                conflict with hero hand, board, or previously-sampled
                villain combos (rejection sampling — small ranges
                may need many retries)
            sample remaining board completion
            evaluate hero vs each villain
            hero "wins" the trial when hero beats all villains
            chop fractionally on ties

    Returns hero equity in [0, 1]. Returns 0.5 for impossible
    situations (no valid combo assignments after many retries).

    Performance note: this is more expensive than the 2-player case
    because each trial samples N combos with rejection and runs N+1
    evaluations. v0 budgets are conservative; production tuning will
    cache hand strength scores per combo.
    """
    if not villain_ranges:
        return 0.5
    if len(villain_ranges) == 1:
        # Heads-up — delegate to the 2-player path for speed
        return hero_equity_vs_range(hero_hand, villain_ranges[0], board,
                                     samples_per_combo=samples, rng=rng)

    if rng is None:
        rng = random.Random(0)

    n_villains = len(villain_ranges)
    if samples is None:
        if n_villains == 2:
            samples = 300
        elif n_villains == 3:
            samples = 200
        else:
            samples = 150

    cards_to_deal = 5 - len(board)
    hero_set = set(hero_hand)
    board_set = set(board)
    base_dead = hero_set | board_set

    wins = 0.0
    valid_trials = 0
    for _ in range(samples):
        # Sample one combo per villain without card collisions
        used_villain_cards: set = set()
        chosen_combos: list = []
        ok = True
        for v_range in villain_ranges:
            # Up to ~30 retries, then give up on this trial
            chosen = None
            for _try in range(30):
                combo = rng.choice(v_range)
                cs = set(combo)
                if cs & base_dead or cs & used_villain_cards:
                    continue
                chosen = combo
                used_villain_cards |= cs
                break
            if chosen is None:
                ok = False
                break
            chosen_combos.append(chosen)
        if not ok:
            continue

        # Deal remaining board cards from the shrunken deck
        deck_dead = base_dead | used_villain_cards
        deck = [c for c in ALL_CARDS if c not in deck_dead]
        if cards_to_deal > 0:
            try:
                runout = rng.sample(deck, cards_to_deal)
            except ValueError:
                continue
            full_board = list(board) + runout
        else:
            full_board = list(board)

        valid_trials += 1

        # Evaluate hero
        hero_score = evaluate(list(hero_hand) + full_board)

        # Evaluate each villain
        villain_scores = [
            evaluate(list(combo) + full_board) for combo in chosen_combos
        ]

        # Hero wins the trial if their hand beats EVERY villain
        max_villain = max(villain_scores)
        if hero_score > max_villain:
            wins += 1.0
        elif hero_score == max_villain:
            # Chop — count by fraction of winners
            winners_at_top = 1 + sum(1 for vs in villain_scores if vs == max_villain)
            wins += 1.0 / winners_at_top
        # else: hero loses, no contribution

    if valid_trials == 0:
        return 0.5
    return wins / valid_trials
