"""
Postflop decision engine.

Routes decisions by street:
  - FLOP:  mmap binary CFR lookup
  - TURN:  equity + opponent-adjusted rules
  - RIVER: equity + opponent-adjusted rules

Replaces the ad-hoc threshold logic in advisor_ws.py with a structured
strategy layer.
"""

import os
import math
from .binary_format import MmapStrategy

VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOP_STRATEGY_PATH = os.path.join(VISION_DIR, "models", "flop_cfr_strategy.bin")
FLOP_JSON_PATH = os.path.join(VISION_DIR, "models", "cfr_strategy_flop.json")

# Hand strength evaluation (same heuristic as cfr training)
RANK_MAP = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
            '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
SUIT_MAP = {'c': 1, 'd': 2, 'h': 3, 's': 4}

NUM_BUCKETS = 50


def _parse_card(s):
    return {'rank': RANK_MAP.get(s[0].upper(), 0), 'suit': SUIT_MAP.get(s[1].lower(), 0)}


def _eval_strength(cards, board):
    """Evaluate hand strength 0..1 on flop/turn/river.

    Tries the trained EquityNet first; falls back to heuristic if unavailable.
    """
    # Try the NN
    try:
        from .equity_nn import equity_nn
        nn_eq = equity_nn(cards, board)
        if nn_eq is not None:
            return nn_eq
    except Exception:
        pass

    # Fallback heuristic (kept for safety)
    c1, c2 = cards
    r1, r2 = c1['rank'], c2['rank']
    suited = c1['suit'] == c2['suit']
    pair = r1 == r2
    high = max(r1, r2)
    gap = abs(r1 - r2)

    # Preflop base
    if pair:
        pf = 0.5 + (r1 / 14) * 0.5
    else:
        pf = (high / 14) * 0.4
        if suited: pf += 0.08
        if gap <= 1: pf += 0.06
        if gap <= 3: pf += 0.03
        if r1 >= 10 and r2 >= 10: pf += 0.15
        if high == 14: pf += 0.1

    if not board:
        return min(1.0, pf)

    # Postflop augmentation
    board_ranks = [c['rank'] for c in board]
    board_rank_counts = {}
    for r in board_ranks:
        board_rank_counts[r] = board_rank_counts.get(r, 0) + 1

    hero_ranks = [r1, r2]

    # Check if hero connects with the board at all
    hit1 = board_rank_counts.get(r1, 0) > 0
    hit2 = board_rank_counts.get(r2, 0) > 0
    has_pair_or_better = pair or hit1 or hit2

    # Postflop: start from a lower base if we didn't connect
    # High cards without a pair are just high-card hands (weak)
    if has_pair_or_better:
        post = pf  # keep preflop value as base
    else:
        # No pair, no connection — high cards are mostly worthless postflop
        post = pf * 0.4  # heavily discount unconnected hands

    # Pair/set/trips detection
    if pair:
        if board_rank_counts.get(r1, 0) >= 2:
            post += 0.95  # quads
        elif board_rank_counts.get(r1, 0) == 1:
            post += 0.70  # set
            if any(cnt >= 2 for rk, cnt in board_rank_counts.items() if rk != r1):
                post += 0.15  # full house
        elif r1 > max(board_ranks, default=0):
            post += 0.30  # overpair
        else:
            # Underpair — discount heavily based on how many overcards
            overcards = sum(1 for br in board_ranks if br > r1)
            if overcards >= 3:
                post -= 0.10  # tiny pair on scary board, basically drawing dead
            elif overcards >= 2:
                post += 0.05  # weak underpair
            else:
                post += 0.15  # one overcard, decent pair
    else:
        if hit1 and hit2:
            post += 0.55  # two pair
        elif hit1:
            post += 0.25
        elif hit2:
            post += 0.20

    # Flush
    all_suits = [c['suit'] for c in cards + board]
    suit_counts = {}
    for s in all_suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1
    max_suit = max(suit_counts.values())
    if max_suit >= 5:
        post += 0.30
    elif max_suit == 4:
        post += 0.10

    # Straight
    all_ranks = sorted(set(hero_ranks + board_ranks))
    max_consec = cur = 1
    for i in range(1, len(all_ranks)):
        if all_ranks[i] == all_ranks[i-1] + 1:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 1
    if max_consec >= 5:
        post += 0.25
    elif max_consec == 4:
        post += 0.08

    return min(1.0, post)


def _strength_to_bucket(strength):
    return min(NUM_BUCKETS - 1, int(strength * NUM_BUCKETS))


def _stack_bucket(stack_bb):
    if stack_bb < 30: return 0
    if stack_bb < 80: return 1
    return 2


# ── Opponent adjustments ─────────────────────────────────────────────────

OPPONENT_ADJUSTMENTS = {
    'FISH':    {'value_delta': -0.05, 'bluff_freq': -0.05, 'call_delta': -0.05, 'discount': 0.05},
    'NIT':     {'value_delta': +0.10, 'bluff_freq': +0.15, 'call_delta': +0.10, 'discount': 0.35},
    'TAG':     {'value_delta': 0, 'bluff_freq': 0, 'call_delta': 0, 'discount': 0.20},
    'LAG':     {'value_delta': -0.05, 'bluff_freq': -0.10, 'call_delta': -0.05, 'discount': 0.10},
    'WHALE':   {'value_delta': -0.10, 'bluff_freq': -0.10, 'call_delta': -0.10, 'discount': 0.0},
    'UNKNOWN': {'value_delta': 0, 'bluff_freq': 0, 'call_delta': 0, 'discount': 0.15},
}


class PostflopEngine:
    """Stratified postflop decision engine: CFR on flop, rules on turn/river."""

    def __init__(self, flop_strategy_path=None):
        # Pre-load the equity NN to avoid first-call latency spike
        try:
            from .equity_nn import _get_model
            _get_model()
        except Exception:
            pass

        self._flop_cfr = None
        self._flop_json = None

        # Try binary mmap first, fall back to JSON
        bin_path = flop_strategy_path or FLOP_STRATEGY_PATH
        if os.path.exists(bin_path):
            try:
                self._flop_cfr = MmapStrategy(bin_path)
                print(f"[PostflopEngine] Loaded binary flop CFR: {self._flop_cfr}")
            except Exception as e:
                print(f"[PostflopEngine] Binary load failed: {e}")

        if not self._flop_cfr and os.path.exists(FLOP_JSON_PATH):
            import json
            with open(FLOP_JSON_PATH) as f:
                self._flop_json = json.load(f)
            print(f"[PostflopEngine] Loaded JSON flop CFR: {len(self._flop_json)} entries")

        if not self._flop_cfr and not self._flop_json:
            print("[PostflopEngine] WARNING: No flop strategy found, using rules only")

    def get_action(self, hero_cards, board_cards, position, facing_bet,
                   call_amount, pot, hero_stack, phase, bb=4,
                   opponent_type='UNKNOWN', action_history=None):
        """
        Get postflop action recommendation.

        Note: if call_amount >= hero_stack, opponent is all-in.
        Only valid actions are CALL or FOLD, never RAISE.

        Args:
            hero_cards: ['Ah', 'Ks'] format
            board_cards: ['Th', '4d', '9s', ...] format
            position: 'BTN', 'CO', 'MP', 'EP', 'SB', 'BB'
            facing_bet: bool
            call_amount: int (cents)
            pot: int (cents)
            hero_stack: int (cents)
            phase: 'FLOP', 'TURN', 'RIVER'
            bb: int (cents per BB)
            opponent_type: 'FISH', 'NIT', 'TAG', 'LAG', 'WHALE', 'UNKNOWN'
            action_history: str or None — encoded actions this street

        Returns:
            dict with 'action', 'amount', 'probs', 'source', 'strength'
        """
        cards = [_parse_card(c) for c in hero_cards]
        board = [_parse_card(c) for c in board_cards]
        strength = _eval_strength(cards, board)
        bucket = _strength_to_bucket(strength)
        is_ip = position in ('BTN', 'CO')
        pos_str = "IP" if is_ip else "OOP"
        stack_bb = hero_stack / max(bb, 1)
        sb = _stack_bucket(stack_bb)

        if phase == "FLOP":
            result = self._flop_decision(
                bucket, sb, pos_str, facing_bet, call_amount, pot,
                hero_stack, bb, strength, opponent_type, action_history
            )
        else:
            result = self._turn_river_decision(
                strength, facing_bet, call_amount, pot, hero_stack,
                bb, phase, opponent_type
            )

        # All-in cap: if call_amount >= hero_stack, can only CALL or FOLD
        if result and facing_bet and call_amount >= hero_stack:
            if result['action'] in ('RAISE', 'BET'):
                result['action'] = 'CALL'
                result['amount'] = hero_stack

        # Safety net: never fold with very high equity
        if result and facing_bet and result['action'] == 'FOLD' and strength > 0.85:
            result['action'] = 'CALL'
            result['amount'] = call_amount

        # Action sanity: facing bet = CALL/FOLD/RAISE only, not CHECK
        if result and facing_bet and call_amount > 0:
            if result['action'] == 'CHECK':
                result['action'] = 'FOLD'

        # Action sanity: not facing bet = CHECK/BET only, not CALL
        if result and not facing_bet and call_amount == 0:
            if result['action'] == 'CALL':
                result['action'] = 'CHECK'

        return result

    def _flop_decision(self, bucket, stack_bucket, pos, facing_bet,
                       call_amount, pot, stack, bb, strength,
                       opponent_type, action_history):
        """Flop: use CFR strategy with fallback to rules."""

        # Determine pot class from pot size
        pot_bb = pot / max(bb, 1)
        if pot_bb >= 15:
            pot_class = "3BP"
        elif pot_bb <= 3:
            pot_class = "LP"
        else:
            pot_class = "SRP"

        hist = action_history or ""

        # Try CFR lookup
        cfr_result = None
        matched_key = None

        if self._flop_cfr:
            cfr_result, matched_key = self._flop_cfr.lookup_fuzzy(
                "FLOP", bucket, stack_bucket, pos, pot_class, hist
            )
        elif self._flop_json:
            # JSON fallback
            key = f"FLOP:{bucket}:s{stack_bucket}:{pos}:{pot_class}:{hist}"
            if key in self._flop_json:
                cfr_result = self._flop_json[key]
                matched_key = key
            else:
                # Fuzzy bucket search
                for d in range(1, 6):
                    for delta in (d, -d):
                        b = bucket + delta
                        if b < 0 or b >= NUM_BUCKETS:
                            continue
                        k = f"FLOP:{b}:s{stack_bucket}:{pos}:{pot_class}:{hist}"
                        if k in self._flop_json:
                            cfr_result = self._flop_json[k]
                            matched_key = k
                            break
                    if cfr_result:
                        break

        if cfr_result:
            return self._format_cfr_result(
                cfr_result, matched_key, facing_bet, call_amount, pot, stack,
                bb, strength, bucket, "flop_cfr"
            )

        # Fallback: rules
        return self._turn_river_decision(
            strength, facing_bet, call_amount, pot, stack, bb, "FLOP", opponent_type
        )

    def _format_cfr_result(self, probs, key, facing_bet, call_amount, pot,
                           stack, bb, strength, bucket, source):
        """Convert CFR probability dict to action recommendation."""
        # Aggregate into categories
        fold_p = probs.get('FOLD', 0)
        check_p = probs.get('CHECK', 0)
        call_p = probs.get('CALL', 0)
        bet_p = (probs.get('BET_33', 0) + probs.get('BET_66', 0) +
                 probs.get('BET_POT', 0) + probs.get('BET_ALLIN', 0) +
                 probs.get('BET_HALF', 0))
        raise_p = (probs.get('RAISE_HALF', 0) + probs.get('RAISE_POT', 0) +
                   probs.get('RAISE_ALLIN', 0))
        agg_p = bet_p + raise_p

        total = fold_p + check_p + call_p + agg_p
        if total <= 0:
            return None

        fold_p /= total
        check_p /= total
        call_p /= total
        agg_p /= total

        # Determine action
        if facing_bet:
            options = {'FOLD': fold_p, 'CALL': call_p, 'RAISE': agg_p}
        else:
            options = {'CHECK': check_p + fold_p, 'BET': agg_p}

        action = max(options, key=options.get)

        # Sizing
        amount = None
        if action in ('BET', 'RAISE'):
            # Pick size from sub-probabilities
            b33 = probs.get('BET_33', 0)
            b66 = probs.get('BET_66', 0) + probs.get('BET_HALF', 0)
            bpot = probs.get('BET_POT', 0) + probs.get('RAISE_POT', 0)
            ballin = probs.get('BET_ALLIN', 0) + probs.get('RAISE_ALLIN', 0)
            rhalf = probs.get('RAISE_HALF', 0)

            total_agg = b33 + b66 + bpot + ballin + rhalf
            if total_agg > 0:
                if ballin / total_agg > 0.4:
                    amount = stack
                elif bpot / total_agg > 0.3:
                    amount = min(pot, stack)
                elif b66 / total_agg > 0.3:
                    amount = min(int(pot * 0.66), stack)
                else:
                    amount = min(int(pot * 0.33), stack)

            if facing_bet and amount:
                amount = max(amount, call_amount * 3)
            elif not facing_bet and amount:
                amount = max(amount, bb * 3)
            if amount:
                amount = min(amount, stack)

        elif action == 'CALL':
            amount = call_amount

        return {
            'action': action,
            'amount': amount,
            'probs': {'fold': fold_p, 'check': check_p, 'call': call_p, 'raise': agg_p},
            'source': source,
            'strength': strength,
            'bucket': bucket,
            'info_key': key,
        }

    def _turn_river_decision(self, equity, facing_bet, call_amount, pot,
                             stack, bb, phase, opponent_type):
        """Turn/River: equity + opponent-adjusted rules with delayed c-bet."""
        adj = OPPONENT_ADJUSTMENTS.get(opponent_type, OPPONENT_ADJUSTMENTS['UNKNOWN'])

        # Discount equity when facing bets (opponent range is stronger)
        discount = adj['discount']
        adjusted_eq = equity
        if facing_bet and call_amount > 0 and pot > 0:
            bet_ratio = call_amount / pot
            if bet_ratio > 1.0:
                adjusted_eq = equity * (1 - discount * 1.5)
            elif bet_ratio > 0.66:
                adjusted_eq = equity * (1 - discount)
            elif bet_ratio > 0.33:
                adjusted_eq = equity * (1 - discount * 0.7)
            else:
                adjusted_eq = equity * (1 - discount * 0.4)

        dec_eq = adjusted_eq if facing_bet else equity
        # River requires higher confidence — leak detection showed too many losing bets
        if phase == "RIVER":
            value_thresh = 0.72 + adj['value_delta']  # tighter on river
            call_thresh = 0.45 + adj['call_delta']    # don't call light on river
        else:
            value_thresh = 0.60 + adj['value_delta']
            call_thresh = 0.35 + adj['call_delta']

        # Pot odds
        pot_odds = 0
        if facing_bet and call_amount > 0:
            pot_odds = call_amount / (pot + call_amount)

        if not facing_bet:
            if dec_eq >= value_thresh:
                # Value bet — size based on strength
                if dec_eq > 0.85:
                    bet_size = int(pot * 0.75)
                else:
                    bet_size = int(pot * 0.55)
                bet_size = min(max(bet_size, bb * 2), stack)
                return self._make_result('BET', bet_size, equity, phase)
            elif dec_eq > 0.50:
                # Medium-strong hand: thin value bet 30% of the time
                import random
                if random.random() < 0.30:
                    bet_size = min(int(pot * 0.45), stack)
                    return self._make_result('BET', bet_size, equity, phase)
                return self._make_result('CHECK', None, equity, phase)
            elif dec_eq < 0.25:
                # Weak hand — always check, no bluff
                return self._make_result('CHECK', None, equity, phase)
            else:
                return self._make_result('CHECK', None, equity, phase)
        else:
            # Facing bet — detect traps (sudden aggression from passive opponent)
            if adjusted_eq < equity * 0.70 and call_amount > pot * 0.5:
                # Heavy discount triggered (likely trap) — tighten up
                if dec_eq > 0.80:
                    return self._make_result('CALL', call_amount, equity, phase)
                return self._make_result('FOLD', None, equity, phase)

            is_plus_ev = equity > pot_odds

            if dec_eq > 0.85:
                raise_amt = min(max(call_amount * 3, int(pot * 0.75)), stack)
                return self._make_result('RAISE', raise_amt, equity, phase)
            elif is_plus_ev or dec_eq > call_thresh:
                return self._make_result('CALL', call_amount, equity, phase)
            else:
                return self._make_result('FOLD', None, equity, phase)

    def _make_result(self, action, amount, equity, phase):
        return {
            'action': action,
            'amount': amount,
            'probs': None,
            'source': f'{phase.lower()}_rules',
            'strength': equity,
            'bucket': _strength_to_bucket(equity),
            'info_key': None,
        }
