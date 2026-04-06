"""
CFR strategy adapter for the WebSocket advisor.

Maps the WS game state (hero cards, board, bets, position) to a CFR info set key,
looks up the trained mixed strategy, and returns the recommended action with probabilities.

Uses the 50-bucket 6-max CFR strategy (1.3M info sets, 138MB).
"""

import json
import os
import math

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGY_PATH = os.path.join(VISION_DIR, "models", "cfr_strategy_50bucket.json")
NUM_BUCKETS = 50

# Rank mapping
RANK_MAP = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
            '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
SUIT_MAP = {'c': 1, 'd': 2, 'h': 3, 's': 4}


class CFRAdapter:
    """Adapts WS game state to CFR info set lookups."""

    def __init__(self, strategy_path=None):
        path = strategy_path or STRATEGY_PATH
        if not os.path.exists(path):
            # Try the 10-bucket strategy as fallback
            path = os.path.join(VISION_DIR, "models", "cfr_strategy.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No CFR strategy found at {path}")

        print(f"[CFR] Loading strategy from {os.path.basename(path)}...")
        with open(path) as f:
            self.strategy = json.load(f)
        print(f"[CFR] Loaded {len(self.strategy)} info sets")

    def _parse_card(self, card_str):
        """Parse 'Ah' to {'rank': 14, 'suit': 3}."""
        r = card_str[0].upper()
        s = card_str[1].lower()
        return {'rank': RANK_MAP.get(r, 0), 'suit': SUIT_MAP.get(s, 0)}

    def _eval_preflop_strength(self, cards):
        """Evaluate preflop hand strength as 0..1."""
        c1, c2 = cards
        r1, r2 = c1['rank'], c2['rank']
        suited = c1['suit'] == c2['suit']
        pair = r1 == r2
        high = max(r1, r2)
        gap = abs(r1 - r2)

        if pair:
            pf = 0.5 + (r1 / 14) * 0.5
        else:
            pf = (high / 14) * 0.4
            if suited: pf += 0.08
            if gap <= 1: pf += 0.06
            if gap <= 3: pf += 0.03
            if r1 >= 10 and r2 >= 10: pf += 0.15
            if high == 14: pf += 0.1

        return min(1.0, pf)

    def _eval_postflop_strength(self, cards, board):
        """Evaluate postflop hand strength as 0..1."""
        pf = self._eval_preflop_strength(cards)

        board_ranks = [c['rank'] for c in board]
        hero_ranks = [c['rank'] for c in cards]
        all_suits = [c['suit'] for c in cards + board]

        # Board hits
        bonus = 0
        # Pair on board
        for r in hero_ranks:
            if r in board_ranks:
                bonus += 0.25
                if r == max(board_ranks):
                    bonus += 0.1  # top pair
                break

        # Two pair
        if hero_ranks[0] in board_ranks and hero_ranks[1] in board_ranks:
            bonus += 0.35

        # Trips
        for r in hero_ranks:
            if board_ranks.count(r) >= 2:
                bonus += 0.45

        # Flush draw
        for s in set(c['suit'] for c in cards):
            flush_count = sum(1 for c in board if c['suit'] == s) + 1
            if flush_count >= 4:
                bonus += 0.15
            if flush_count >= 5:
                bonus += 0.35

        # Straight potential
        all_ranks = sorted(set(hero_ranks + board_ranks))
        for i in range(len(all_ranks) - 3):
            if all_ranks[i+3] - all_ranks[i] <= 4:
                bonus += 0.08

        return min(1.0, pf * 0.3 + bonus + 0.2)

    def _strength_to_bucket(self, strength):
        """Map 0..1 strength to bucket index."""
        return min(NUM_BUCKETS - 1, int(strength * NUM_BUCKETS))

    def get_action(self, hero_cards, board_cards, position, facing_bet, call_amount,
                   pot, hero_stack, phase="PREFLOP"):
        """
        Get CFR recommended action.

        Args:
            hero_cards: ['Ah', 'Ks'] format
            board_cards: ['Th', '4d', '9s'] format
            position: 'BTN', 'SB', 'BB', 'UTG', 'MP', 'CO'
            facing_bet: bool
            call_amount: int (cents)
            pot: int (cents)
            hero_stack: int (cents)
            phase: 'PREFLOP', 'FLOP', 'TURN', 'RIVER'

        Returns:
            dict with 'action', 'amount', 'probs', 'info_key'
        """
        cards = [self._parse_card(c) for c in hero_cards]
        board = [self._parse_card(c) for c in board_cards]

        # Hand strength and bucket
        if phase == "PREFLOP":
            strength = self._eval_preflop_strength(cards)
        else:
            strength = self._eval_postflop_strength(cards, board)

        bucket = self._strength_to_bucket(strength)

        # Position adjustment (IP plays wider)
        is_ip = position in ('BTN', 'CO')
        if phase == "PREFLOP":
            if is_ip:
                bucket = min(NUM_BUCKETS - 1, bucket + 5)
            elif position == 'UTG':
                bucket = max(0, bucket - 3)

        pos_str = "IP" if is_ip else "OOP"

        # Stack bucket — strategy mostly trained with s0
        bb = 4  # 0.04 BB in cents
        bbs = hero_stack / max(bb, 1)
        stack_bucket = 0  # Use s0 for all (most coverage in trained strategy)

        # Action history — simplified mapping since we don't track full action sequence
        # Preflop: empty (open), c (limp), rh (raise), rhrh (3bet)
        # Postflop: add "-" separator, then k (check), bh (bet half), bp (bet pot)
        if phase == "PREFLOP":
            if facing_bet:
                action_history = "rh"
            elif is_ip:
                action_history = ""
            else:
                action_history = "c"
        else:
            # Build preflop prefix based on what likely happened
            pf_prefix = "rpc" if is_ip else "rpc"  # raised pot, called
            if phase == "FLOP":
                if facing_bet:
                    action_history = f"{pf_prefix}-bh"
                else:
                    action_history = f"{pf_prefix}-k"
            elif phase == "TURN":
                if facing_bet:
                    action_history = f"{pf_prefix}-bpc-bh"
                else:
                    action_history = f"{pf_prefix}-bpc-k"
            elif phase == "RIVER":
                if facing_bet:
                    action_history = f"{pf_prefix}-bpc-bpc-bh"
                else:
                    action_history = f"{pf_prefix}-bpc-bpc-k"

        # Build info set key (try multiple formats)
        keys_to_try = [
            f"{phase}:{bucket}:s{stack_bucket}:{pos_str}:{action_history}",
            f"{phase}:{bucket}:s{stack_bucket}:{action_history}",
            f"{phase}:{bucket}:{action_history}",
            f"{bucket}:{action_history}",
        ]

        strategy = None
        used_key = None
        for key in keys_to_try:
            if key in self.strategy:
                strategy = self.strategy[key]
                used_key = key
                break

        # Fuzzy matching: try nearby buckets ±1, ±2, ±3
        if not strategy:
            for delta in [1, -1, 2, -2, 3, -3]:
                fuzzy_bucket = bucket + delta
                if fuzzy_bucket < 0 or fuzzy_bucket >= NUM_BUCKETS:
                    continue
                fuzzy_keys = [
                    f"{phase}:{fuzzy_bucket}:s{stack_bucket}:{pos_str}:{action_history}",
                    f"{phase}:{fuzzy_bucket}:s{stack_bucket}:{action_history}",
                    f"{phase}:{fuzzy_bucket}:{action_history}",
                ]
                for key in fuzzy_keys:
                    if key in self.strategy:
                        strategy = self.strategy[key]
                        used_key = f"~{key}"  # ~ prefix = fuzzy match
                        break
                if strategy:
                    break

        # Try alternate action histories if still no match
        if not strategy:
            alt_histories = []
            if phase == "PREFLOP":
                alt_histories = ["rh", "rp", "c", ""]
            elif phase == "FLOP":
                alt_histories = ["rpc-bh", "rhc-bh", "rpc-k", "rhc-k", "rpc-bp", "rhc-bp"]
            elif phase == "TURN":
                alt_histories = ["rpc-bpc-bh", "rpc-bpc-k", "rpc-kk-bh", "rpc-kk-k"]
            elif phase == "RIVER":
                alt_histories = ["rpc-bpc-bpc-bh", "rpc-bpc-bpc-k", "rpc-bpc-kk-bh"]

            for alt_hist in alt_histories:
                if alt_hist == action_history:
                    continue
                for b_delta in [0, 1, -1, 2, -2]:
                    b = bucket + b_delta
                    if b < 0 or b >= NUM_BUCKETS:
                        continue
                    key = f"{phase}:{b}:s{stack_bucket}:{pos_str}:{alt_hist}"
                    if key in self.strategy:
                        strategy = self.strategy[key]
                        used_key = f"~~{key}"  # ~~ = double fuzzy
                        break
                if strategy:
                    break

        if not strategy:
            return None

        # Extract probabilities
        fold_p = strategy.get('FOLD', 0)
        check_p = strategy.get('CHECK', 0)
        call_p = strategy.get('CALL', 0)
        raise_p = (strategy.get('RAISE_HALF', 0) + strategy.get('RAISE_POT', 0) +
                   strategy.get('RAISE_ALLIN', 0) + strategy.get('BET_HALF', 0) +
                   strategy.get('BET_POT', 0) + strategy.get('BET_ALLIN', 0))

        total = fold_p + check_p + call_p + raise_p
        if total <= 0:
            return None

        # Normalize
        fold_p /= total
        check_p /= total
        call_p /= total
        raise_p /= total

        # Determine recommended action (highest probability)
        probs = {'fold': fold_p, 'check': check_p, 'call': call_p, 'raise': raise_p}

        if facing_bet:
            # Can't check when facing a bet
            options = {'fold': fold_p, 'call': call_p, 'raise': raise_p}
        else:
            # Not facing bet — can check or bet/raise
            options = {'check': check_p + fold_p, 'raise': raise_p}
            if position == 'BB' and phase == 'PREFLOP':
                options = {'check': check_p + fold_p, 'raise': raise_p}

        best_action = max(options, key=options.get)
        best_prob = options[best_action]

        # Bet sizing from CFR sub-actions
        amount = None
        if best_action == 'raise':
            half_p = strategy.get('RAISE_HALF', 0) + strategy.get('BET_HALF', 0)
            pot_p = strategy.get('RAISE_POT', 0) + strategy.get('BET_POT', 0)
            allin_p = strategy.get('RAISE_ALLIN', 0) + strategy.get('BET_ALLIN', 0)
            total_r = half_p + pot_p + allin_p
            if total_r > 0:
                if allin_p / total_r > 0.5:
                    amount = hero_stack
                elif pot_p / total_r > half_p / total_r:
                    amount = min(max(pot, bb * 3), hero_stack)
                else:
                    amount = min(max(int(pot * 0.5), bb * 3), hero_stack)

            if facing_bet and amount:
                amount = max(amount, call_amount * 3)  # min 3x raise
            elif not facing_bet and amount:
                amount = max(amount, bb * 3)  # min 3BB open

            if amount:
                amount = min(amount, hero_stack)

        elif best_action == 'call':
            amount = call_amount

        return {
            'action': best_action.upper(),
            'amount': amount,
            'probs': probs,
            'info_key': used_key,
            'strength': strength,
            'bucket': bucket,
        }


if __name__ == "__main__":
    cfr = CFRAdapter()

    # Test some hands
    tests = [
        (['Ah', 'As'], [], 'BTN', False, 0, 6, 1000, 'PREFLOP'),  # AA BTN open
        (['7h', '2c'], [], 'UTG', False, 0, 6, 1000, 'PREFLOP'),  # 72o UTG
        (['Kh', 'Qh'], ['Th', '4d', '9s'], 'BTN', True, 20, 40, 800, 'FLOP'),  # KQh flush draw
        (['Ah', 'Kh'], ['Ah', '5c', '3d'], 'CO', True, 15, 30, 900, 'FLOP'),  # TPTK
    ]

    for hero, board, pos, facing, call_amt, pot, stack, phase in tests:
        result = cfr.get_action(hero, board, pos, facing, call_amt, pot, stack, phase)
        hero_str = ' '.join(hero)
        board_str = ' '.join(board) if board else '(preflop)'
        if result:
            print(f"{hero_str} | {board_str} | {pos} facing={facing}")
            print(f"  -> {result['action']} (f:{result['probs']['fold']:.0%} "
                  f"c:{result['probs']['call']:.0%} r:{result['probs']['raise']:.0%})")
            print(f"  key={result['info_key']} bucket={result['bucket']}")
        else:
            print(f"{hero_str} | {board_str} | {pos} -> NO CFR DATA")
        print()
