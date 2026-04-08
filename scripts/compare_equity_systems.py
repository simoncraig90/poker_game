"""
Compare NN equity vs heuristic equity vs reality on the leak hands.

For each losing hand from tonight's sessions, compute equity 3 ways:
1. The NN (now wired into postflop_engine)
2. The old heuristic (the fallback path)
3. Actual outcome (won/lost)

Reports MAE and the worst disagreements.
"""

import os, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from strategy.equity_nn import equity_nn

# The OLD heuristic (saved before we wired in NN)
def heuristic_strength(hero_strs, board_strs):
    RANK_MAP = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'T':10,'J':11,'Q':12,'K':13,'A':14}
    SUIT_MAP = {'c':1,'d':2,'h':3,'s':4}
    def parse(s): return {'rank': RANK_MAP[s[0].upper()], 'suit': SUIT_MAP[s[1].lower()]}
    cards = [parse(c) for c in hero_strs]
    board = [parse(c) for c in board_strs]
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
    if not board:
        return min(1.0, pf)
    board_ranks = [c['rank'] for c in board]
    bc = {}
    for r in board_ranks:
        bc[r] = bc.get(r, 0) + 1
    hr = [r1, r2]
    hit1 = bc.get(r1, 0) > 0
    hit2 = bc.get(r2, 0) > 0
    has_hit = pair or hit1 or hit2
    post = pf if has_hit else pf * 0.4
    if pair:
        if bc.get(r1, 0) >= 2: post += 0.95
        elif bc.get(r1, 0) == 1:
            post += 0.70
            if any(cnt >= 2 for rk, cnt in bc.items() if rk != r1): post += 0.15
        elif r1 > max(board_ranks, default=0):
            post += 0.30
        else:
            overcards = sum(1 for br in board_ranks if br > r1)
            if overcards >= 3: post -= 0.10
            elif overcards >= 2: post += 0.05
            else: post += 0.15
    else:
        if hit1 and hit2: post += 0.55
        elif hit1: post += 0.25
        elif hit2: post += 0.20
    all_suits = [c['suit'] for c in cards + board]
    sc = {}
    for s in all_suits: sc[s] = sc.get(s, 0) + 1
    ms = max(sc.values())
    if ms >= 5: post += 0.30
    elif ms == 4: post += 0.10
    all_ranks = sorted(set(hr + board_ranks))
    mc = cur = 1
    for i in range(1, len(all_ranks)):
        if all_ranks[i] == all_ranks[i-1] + 1:
            cur += 1; mc = max(mc, cur)
        else: cur = 1
    if mc >= 5: post += 0.25
    elif mc == 4: post += 0.08
    return min(1.0, post)


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "vision/data/session_*.jsonl")))
    losing_hands = []
    for f in files:
        for line in open(f):
            try:
                h = json.loads(line)
                if h.get('profit_cents', 0) < 0:
                    losing_hands.append(h)
            except: pass

    print(f"Comparing equity systems on {len(losing_hands)} losing hands")
    print()

    total_nn_err = 0
    total_heur_err = 0
    n = 0
    big_diffs = []

    for h in losing_hands:
        hero = h.get('hero', [])
        if len(hero) < 2: continue
        for s in h.get('streets', []):
            board = s.get('board', [])
            if len(board) < 3: continue  # postflop only
            advisor_eq = s.get('rec_equity', 0)

            try:
                nn_eq = equity_nn(hero, board)
            except: nn_eq = None
            try:
                heur_eq = heuristic_strength(hero, board)
            except: heur_eq = None

            if nn_eq is None or heur_eq is None: continue

            # The advisor's recorded equity is what was used at the time
            nn_err = abs(nn_eq - advisor_eq)
            heur_err = abs(heur_eq - advisor_eq)
            total_nn_err += nn_err
            total_heur_err += heur_err
            n += 1

            diff = abs(nn_eq - heur_eq)
            if diff > 0.20:
                big_diffs.append({
                    'hero': hero, 'board': board, 'phase': s.get('phase'),
                    'advisor_eq': advisor_eq, 'nn': nn_eq, 'heuristic': heur_eq,
                    'diff': diff,
                })

    if n > 0:
        print(f"Comparing {n} postflop spots from losing hands:")
        print(f"  NN vs advisor recorded eq:        MAE={total_nn_err/n:.3f}")
        print(f"  Heuristic vs advisor recorded eq: MAE={total_heur_err/n:.3f}")
        print()
        print(f"Top {min(15, len(big_diffs))} spots where NN and Heuristic disagree by >20%:")
        big_diffs.sort(key=lambda x: -x['diff'])
        for d in big_diffs[:15]:
            print(f"  {' '.join(d['hero']):6s} on {' '.join(d['board']):20s} {d['phase']:6s}  "
                  f"NN={d['nn']:.2f} Heur={d['heuristic']:.2f} (advisor recorded {d['advisor_eq']:.2f})")


if __name__ == "__main__":
    main()
