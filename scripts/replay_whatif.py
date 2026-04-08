"""
What-if replay simulator.

Replays captured hands through multiple strategy variants and compares
hypothetical actions vs the baseline (what we actually did).

For each spot where variants disagree:
- Estimate EV impact based on actual hand outcome
- Aggregate per variant
- Report which variant would have made/saved money

Usage:
    python scripts/replay_whatif.py
    python scripts/replay_whatif.py --variants tight loose nn-only
"""

import os
import sys
import json
import glob
import argparse
import copy
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from advisor_state_machine import AdvisorStateMachine
from preflop_chart import preflop_advice, EP_RAISE, MP_RAISE, CO_RAISE, BTN_RAISE, SB_RAISE, BB_CALL, BB_3BET
from strategy.postflop_engine import PostflopEngine, OPPONENT_ADJUSTMENTS


# ─────────────────────────────────────────────────────────────────────
# Variant definitions — each is a callable that returns an AdvisorStateMachine
# with different settings.
# ─────────────────────────────────────────────────────────────────────

class FakeBaseAdvisor:
    """Minimal base advisor that just returns equity from a heuristic."""
    def __init__(self):
        from strategy.equity_nn import equity_nn
        self._equity_nn = equity_nn

    def _get_recommendation(self, state):
        hero = state["hero_cards"]
        board = state["board_cards"]
        if len(board) == 0:
            phase = "PREFLOP"
        elif len(board) == 3:
            phase = "FLOP"
        elif len(board) == 4:
            phase = "TURN"
        else:
            phase = "RIVER"

        eq = 0.5
        if len(hero) >= 2:
            try:
                nn_eq = self._equity_nn(hero, board)
                if nn_eq is not None:
                    eq = nn_eq
            except Exception:
                pass

        return {
            "phase": phase,
            "equity": eq,
            "danger": {"warnings": [], "danger": 0, "suppress_raise": False},
            "category": "REPLAY",
            "preflop": {"action": "FOLD", "hand_key": "??", "in_range": False, "note": ""},
            "action_probs": {},
            "recommended": "",
            "rec_prob": 0,
            "nn_equity": eq,
            "bucket": int(eq * 50),
            "fallback": False,
        }


def variant_baseline():
    """Current production strategy."""
    return AdvisorStateMachine(
        base_advisor=FakeBaseAdvisor(),
        preflop_advice_fn=preflop_advice,
        postflop_engine=PostflopEngine(),
        bb_cents=4,
    )


def variant_tighter_river():
    """Even tighter river thresholds (value=0.80, call=0.55)."""
    engine = PostflopEngine()
    # Monkey-patch the thresholds inside the engine
    original = engine._turn_river_decision
    def patched(equity, facing_bet, call_amount, pot, stack, bb, phase, opponent_type):
        # Override OPPONENT_ADJUSTMENTS just for this call
        adj = OPPONENT_ADJUSTMENTS.get(opponent_type, OPPONENT_ADJUSTMENTS['UNKNOWN']).copy()
        if phase == "RIVER":
            adj['value_delta'] = adj.get('value_delta', 0) + 0.08
            adj['call_delta'] = adj.get('call_delta', 0) + 0.10
        # Temporarily swap and call
        old = OPPONENT_ADJUSTMENTS.get(opponent_type)
        OPPONENT_ADJUSTMENTS[opponent_type] = adj
        try:
            return original(equity, facing_bet, call_amount, pot, stack, bb, phase, opponent_type)
        finally:
            if old is not None:
                OPPONENT_ADJUSTMENTS[opponent_type] = old
    engine._turn_river_decision = patched
    return AdvisorStateMachine(
        base_advisor=FakeBaseAdvisor(),
        preflop_advice_fn=preflop_advice,
        postflop_engine=engine,
        bb_cents=4,
    )


def variant_looser_bb_defense():
    """Wider BB defending range — add A2s-A4s back, K7s-K8s, more offsuit broadways."""
    extra = {"A4s", "A3s", "A2s", "K7s", "K8s", "K9s", "Q8s", "J8s", "T8s", "T9o", "J9o", "QTo", "KTo", "ATo", "K5s", "K6s"}
    original_advice = preflop_advice

    def looser_advice(c1, c2, position, facing_raise=False):
        result = original_advice(c1, c2, position, facing_raise=facing_raise)
        if position == "BB" and facing_raise and result["action"] == "FOLD":
            hand_key = result.get("hand_key", "")
            if hand_key in extra:
                return {"action": "CALL", "hand_key": hand_key, "in_range": True, "note": "loose defend"}
        return result

    return AdvisorStateMachine(
        base_advisor=FakeBaseAdvisor(),
        preflop_advice_fn=looser_advice,
        postflop_engine=PostflopEngine(),
        bb_cents=4,
    )


def variant_nit_assumption():
    """Assume all opponents are NITs (treat their bets as strong)."""
    base_engine = PostflopEngine()
    original_get = base_engine.get_action

    def patched_get(*args, **kwargs):
        kwargs['opponent_type'] = 'NIT'
        return original_get(*args, **kwargs)

    base_engine.get_action = patched_get
    return AdvisorStateMachine(
        base_advisor=FakeBaseAdvisor(),
        preflop_advice_fn=preflop_advice,
        postflop_engine=base_engine,
        bb_cents=4,
    )


def variant_fish_assumption():
    """Assume all opponents are FISH (call light, bluff little)."""
    base_engine = PostflopEngine()
    original_get = base_engine.get_action

    def patched_get(*args, **kwargs):
        kwargs['opponent_type'] = 'FISH'
        return original_get(*args, **kwargs)

    base_engine.get_action = patched_get
    return AdvisorStateMachine(
        base_advisor=FakeBaseAdvisor(),
        preflop_advice_fn=preflop_advice,
        postflop_engine=base_engine,
        bb_cents=4,
    )


VARIANTS = {
    "baseline":      variant_baseline,
    "tighter_river": variant_tighter_river,
    "looser_bb":     variant_looser_bb_defense,
    "nit_assume":    variant_nit_assumption,
    "fish_assume":   variant_fish_assumption,
}


# ─────────────────────────────────────────────────────────────────────
# Hand replay
# ─────────────────────────────────────────────────────────────────────

def hand_to_states(hand):
    """Convert a session JSONL hand record into a list of state dicts."""
    states = []
    hero = hand.get("hero", [])
    if len(hero) < 2:
        return states
    pos = hand.get("position", "MP")
    hand_id = f"replay_{hand.get('hand_id', 0)}"
    starting_stack = hand.get("starting_stack", 1000)

    for i, street in enumerate(hand.get("streets", [])):
        states.append({
            "hero_cards": hero,
            "board_cards": street.get("board", []),
            "hand_id": f"{hand_id}_{i}",
            "facing_bet": street.get("facing_bet", False),
            "call_amount": street.get("call_amount", 0),
            "pot": street.get("pot", 0),
            "num_opponents": 5,
            "position": pos,
            "hero_stack": street.get("stack", starting_stack),
            "phase": street.get("phase", "PREFLOP"),
            "bets": [0] * 6,
            "players": ["V1", "Hero", "V2", "V3", "V4", "V5"],
            "hero_seat": 1,
        })
    return states


# Cache state machine instances per variant (avoid reloading models per hand)
_sm_cache = {}

def _get_sm(factory):
    if factory not in _sm_cache:
        _sm_cache[factory] = factory()
    return _sm_cache[factory]


def replay_hand_through_variant(hand, sm_factory):
    """Replay one hand through a variant. Returns list of recommendations."""
    sm = _get_sm(sm_factory)
    # Reset state machine for new hand (clear hand_id memory)
    sm.prev_hand_id = None
    recs = []
    for state in hand_to_states(hand):
        try:
            out = sm.process_state(state)
            if out and out.action:
                recs.append({
                    "phase": out.phase,
                    "action": out.action,
                    "facing": out.facing_bet,
                    "call": out.call_amount,
                    "equity": out.equity,
                })
        except Exception as e:
            recs.append({"phase": "ERROR", "action": str(e), "facing": False, "call": 0, "equity": 0})
    return recs


# ─────────────────────────────────────────────────────────────────────
# EV estimation
# ─────────────────────────────────────────────────────────────────────

def categorize_action(action_str):
    """Bucket an action string into FOLD/CHECK/CALL/RAISE."""
    a = action_str.upper()
    if "FOLD" in a:
        return "FOLD"
    if "RAISE" in a:
        return "RAISE"
    if "BET" in a:
        return "BET"
    if "CALL" in a:
        return "CALL"
    return "CHECK"


def estimate_ev_diff(hand, baseline_recs, variant_recs):
    """
    Estimate EV difference for a variant vs baseline on a single hand.

    Heuristic:
    - If variant FOLDS where baseline CALLS/RAISES and hand was a LOSS → variant SAVES the call/bet
    - If variant FOLDS where baseline CALLS/RAISES and hand was a WIN → variant LOSES the win
    - If variant RAISES where baseline CALLS → assume variant captures more on wins, loses more on losses
    - If actions match: 0 EV diff

    Returns: estimated cents diff (positive = variant better)
    """
    actual_profit = hand.get("profit_cents", 0)
    is_win = actual_profit > 0
    is_loss = actual_profit < 0

    diff = 0
    streets = hand.get("streets", [])
    n = min(len(baseline_recs), len(variant_recs), len(streets))

    for i in range(n):
        b = categorize_action(baseline_recs[i]["action"])
        v = categorize_action(variant_recs[i]["action"])
        if b == v:
            continue

        street = streets[i]
        call = street.get("call_amount", 0)
        pot = street.get("pot", 0)

        # Variant FOLD when baseline CALLED/RAISED
        if v == "FOLD" and b in ("CALL", "RAISE", "BET"):
            if is_loss:
                # We saved the call (didn't lose more chips)
                diff += call
            elif is_win:
                # We missed winning the pot
                diff -= pot
            # If we already won, folding loses the future win
            return diff

        # Variant CALLED when baseline FOLDED
        if v in ("CALL", "RAISE") and b == "FOLD":
            if is_win:
                # We would have won
                diff += pot * 0.5  # rough estimate
            elif is_loss:
                # We would have lost more
                diff -= call

        # Variant raised more than baseline → hard to estimate without sim
        # skip for now

    return diff


# ─────────────────────────────────────────────────────────────────────
# Main analysis
# ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS.keys()),
                        help="Which variants to test")
    parser.add_argument("--limit", type=int, default=None, help="Limit hands tested")
    parser.add_argument("--verbose", action="store_true", help="Print divergent hands")
    args = parser.parse_args()

    # Load all hands
    files = sorted(glob.glob(os.path.join(ROOT, "vision/data/session_*.jsonl")))
    all_hands = []
    for f in files:
        for line in open(f):
            try:
                h = json.loads(line)
                if h.get("hero") and len(h.get("hero", [])) >= 2:
                    all_hands.append(h)
            except Exception:
                pass

    if args.limit:
        all_hands = all_hands[:args.limit]

    print(f"Loaded {len(all_hands)} hands from {len(files)} session files")
    print(f"Testing variants: {args.variants}")
    print()

    # Run baseline first
    print("Computing baseline recommendations...")
    baseline_recs_per_hand = {}
    for h in all_hands:
        hand_id = h.get("hand_id")
        baseline_recs_per_hand[hand_id] = replay_hand_through_variant(h, variant_baseline)

    # For each variant, compute diffs
    results = {}
    for variant_name in args.variants:
        if variant_name == "baseline":
            continue
        if variant_name not in VARIANTS:
            print(f"Unknown variant: {variant_name}")
            continue

        print(f"\nTesting variant: {variant_name}")
        factory = VARIANTS[variant_name]

        total_diff = 0
        divergent_hands = 0
        diff_actions = defaultdict(int)
        diff_examples = []

        for h in all_hands:
            hand_id = h.get("hand_id")
            baseline_recs = baseline_recs_per_hand[hand_id]
            variant_recs = replay_hand_through_variant(h, factory)

            # Detect divergence
            div = False
            for i in range(min(len(baseline_recs), len(variant_recs))):
                b = categorize_action(baseline_recs[i]["action"])
                v = categorize_action(variant_recs[i]["action"])
                if b != v:
                    div = True
                    diff_actions[f"{b}->{v}"] += 1
                    if len(diff_examples) < 5 and args.verbose:
                        diff_examples.append({
                            "hero": h.get("hero"),
                            "phase": baseline_recs[i]["phase"],
                            "baseline": baseline_recs[i]["action"],
                            "variant": variant_recs[i]["action"],
                            "profit": h.get("profit_cents", 0) / 100.0,
                        })
                    break

            if div:
                divergent_hands += 1
                ev_diff = estimate_ev_diff(h, baseline_recs, variant_recs)
                total_diff += ev_diff

        results[variant_name] = {
            "divergent_hands": divergent_hands,
            "total_hands": len(all_hands),
            "estimated_ev_cents": total_diff,
            "diff_actions": dict(diff_actions),
            "examples": diff_examples,
        }

        print(f"  Divergent hands: {divergent_hands}/{len(all_hands)} ({divergent_hands/len(all_hands)*100:.1f}%)")
        print(f"  Estimated EV diff: {total_diff/100:+.2f} EUR")
        print(f"  Action substitutions:")
        for k, v in sorted(diff_actions.items(), key=lambda x: -x[1])[:8]:
            print(f"    {k}: {v}")
        if args.verbose:
            print(f"  Examples:")
            for ex in diff_examples:
                print(f"    {' '.join(ex['hero']):6s} {ex['phase']:8s} baseline={ex['baseline']:20s} variant={ex['variant']:20s} actual_profit={ex['profit']:+.2f}")

    print()
    print("=" * 60)
    print("RANKING")
    print("=" * 60)
    ranked = sorted(results.items(), key=lambda x: -x[1]["estimated_ev_cents"])
    for name, r in ranked:
        sign = "+" if r["estimated_ev_cents"] >= 0 else ""
        print(f"  {name:20s} {sign}€{r['estimated_ev_cents']/100:.2f}  ({r['divergent_hands']} divergent)")

    print()
    print("Note: EV estimates are rough heuristics, not real simulations.")
    print("Use directionally to find candidate strategy improvements.")


if __name__ == "__main__":
    main()
