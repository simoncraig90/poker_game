"""
Leak detection: analyze all session JSONLs to find spots where we lose money.

Groups losing hands by spot type and identifies patterns:
- Position (UTG/MP/CO/BTN/SB/BB)
- Phase reached (preflop/flop/turn/river)
- Action sequence (check-call, bet-call, raise-call, etc)
- Hand category (premium/medium/marginal)
- Board texture (dry/wet/paired)

Output: ranked leak categories by total loss.
"""

import os
import sys
import json
import glob
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hand_category(hero):
    """Classify hero hand into a strength bucket."""
    if not hero or len(hero) < 2:
        return 'unknown'
    rank_map = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
                '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
    r1 = rank_map.get(hero[0][0].upper(), 5)
    r2 = rank_map.get(hero[1][0].upper(), 5)
    suited = hero[0][1] == hero[1][1]

    if r1 == r2:
        if r1 >= 10: return 'premium_pair'
        if r1 >= 7: return 'medium_pair'
        return 'small_pair'

    high, low = max(r1, r2), min(r1, r2)
    if high == 14:  # Ace
        if low >= 10: return 'premium_ace'
        if suited: return 'suited_ace'
        if low >= 7: return 'mid_ace_offsuit'
        return 'weak_ace'

    if high >= 12 and low >= 10:
        return 'broadway'
    if suited and abs(r1 - r2) <= 2 and low >= 5:
        return 'suited_connector'
    if suited:
        return 'suited_other'
    if abs(r1 - r2) == 1 and low >= 5:
        return 'connector'
    return 'trash'


def board_texture(board):
    """Classify board into a category."""
    if not board or len(board) < 3:
        return 'preflop'
    suits = [c[1] for c in board if len(c) >= 2]
    ranks = [c[0] for c in board if len(c) >= 2]

    flush_draw = max((suits.count(s) for s in set(suits)), default=0) >= 3
    paired = len(ranks) != len(set(ranks))

    rank_map = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
                '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
    rank_vals = sorted([rank_map.get(r, 5) for r in ranks])
    high_card = max(rank_vals) if rank_vals else 0
    connected = len(rank_vals) >= 3 and (rank_vals[-1] - rank_vals[0]) <= 4

    if paired and flush_draw:
        return 'paired_flush'
    if paired:
        return 'paired'
    if flush_draw and connected:
        return 'wet_connected'
    if flush_draw:
        return 'flush_draw'
    if connected:
        return 'connected'
    if high_card >= 13:
        return 'high_dry'
    return 'dry'


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "vision/data/session_*.jsonl")))
    print(f"Analyzing {len(files)} session files...")

    all_hands = []
    for f in files:
        for line in open(f):
            try:
                all_hands.append(json.loads(line))
            except Exception:
                pass

    print(f"Total hands: {len(all_hands)}")
    print()

    # Group losing hands by leak signature
    leaks_by_position = defaultdict(lambda: {'count': 0, 'total_loss_cents': 0, 'hands': []})
    leaks_by_category = defaultdict(lambda: {'count': 0, 'total_loss_cents': 0, 'hands': []})
    leaks_by_phase = defaultdict(lambda: {'count': 0, 'total_loss_cents': 0, 'hands': []})
    leaks_by_combo = defaultdict(lambda: {'count': 0, 'total_loss_cents': 0, 'hands': []})

    total_won = 0
    total_lost = 0
    total_hands = 0
    losing_hands = 0

    for h in all_hands:
        profit = h.get('profit_cents', 0)
        if profit == 0:
            continue  # skip null hands
        total_hands += 1
        if profit > 0:
            total_won += profit
        else:
            total_lost += abs(profit)
            losing_hands += 1
            hero = h.get('hero', [])
            pos = h.get('position', '?')
            cat = hand_category(hero)
            streets = h.get('streets', [])
            last_phase = streets[-1].get('phase', 'PREFLOP') if streets else 'PREFLOP'
            last_board = streets[-1].get('board', []) if streets else []
            texture = board_texture(last_board)
            combo = f"{pos}_{cat}_{last_phase}"

            leaks_by_position[pos]['count'] += 1
            leaks_by_position[pos]['total_loss_cents'] += abs(profit)

            leaks_by_category[cat]['count'] += 1
            leaks_by_category[cat]['total_loss_cents'] += abs(profit)

            leaks_by_phase[last_phase]['count'] += 1
            leaks_by_phase[last_phase]['total_loss_cents'] += abs(profit)

            leaks_by_combo[combo]['count'] += 1
            leaks_by_combo[combo]['total_loss_cents'] += abs(profit)
            leaks_by_combo[combo]['hands'].append({
                'hero': hero, 'pos': pos, 'profit_cents': profit,
                'phase': last_phase, 'texture': texture
            })

    print(f"Total hands with profit/loss: {total_hands}")
    print(f"Won: +{total_won/100:.2f} EUR over {total_hands - losing_hands} hands")
    print(f"Lost: -{total_lost/100:.2f} EUR over {losing_hands} hands")
    print(f"Net: {(total_won - total_lost)/100:+.2f} EUR")
    print()

    print("=" * 60)
    print("LEAKS BY POSITION (top losses)")
    print("=" * 60)
    for pos, d in sorted(leaks_by_position.items(), key=lambda x: -x[1]['total_loss_cents'])[:8]:
        print(f"  {pos:6s} {d['count']:4d} hands  -{d['total_loss_cents']/100:6.2f} EUR  avg -{d['total_loss_cents']/d['count']/100:.3f}")

    print()
    print("=" * 60)
    print("LEAKS BY HAND CATEGORY (top losses)")
    print("=" * 60)
    for cat, d in sorted(leaks_by_category.items(), key=lambda x: -x[1]['total_loss_cents'])[:10]:
        print(f"  {cat:20s} {d['count']:4d} hands  -{d['total_loss_cents']/100:6.2f} EUR  avg -{d['total_loss_cents']/d['count']/100:.3f}")

    print()
    print("=" * 60)
    print("LEAKS BY PHASE REACHED (top losses)")
    print("=" * 60)
    for phase, d in sorted(leaks_by_phase.items(), key=lambda x: -x[1]['total_loss_cents'])[:5]:
        print(f"  {phase:10s} {d['count']:4d} hands  -{d['total_loss_cents']/100:6.2f} EUR  avg -{d['total_loss_cents']/d['count']/100:.3f}")

    print()
    print("=" * 60)
    print("BIGGEST INDIVIDUAL LOSING SPOTS (combo)")
    print("=" * 60)
    for combo, d in sorted(leaks_by_combo.items(), key=lambda x: -x[1]['total_loss_cents'])[:10]:
        print(f"  {combo:35s} {d['count']:3d}h  -{d['total_loss_cents']/100:6.2f} EUR")
        for h in d['hands'][:3]:
            print(f"    {' '.join(h['hero']):6s} {h['phase']:8s} {h['texture']:15s} -{abs(h['profit_cents'])/100:.2f}")

    # Top 10 biggest individual losses
    print()
    print("=" * 60)
    print("TOP 10 BIGGEST INDIVIDUAL LOSSES")
    print("=" * 60)
    losing = [h for h in all_hands if h.get('profit_cents', 0) < 0]
    losing.sort(key=lambda h: h.get('profit_cents', 0))
    for h in losing[:10]:
        hero = ' '.join(h.get('hero', []))
        pos = h.get('position', '?')
        profit = h.get('profit_cents', 0)
        streets = h.get('streets', [])
        last_phase = streets[-1].get('phase', '?') if streets else '?'
        actions = '/'.join(s.get('rec_action', '?').split()[0] for s in streets)
        print(f"  {hero:6s} {pos:4s} {last_phase:8s} {profit/100:+.2f}  {actions}")


if __name__ == "__main__":
    main()
