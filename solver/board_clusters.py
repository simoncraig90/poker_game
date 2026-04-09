"""
Flop texture clustering for precomputed solver solutions.

Classifies all 22,100 possible flops into ~150 clusters based on:
1. High card rank bucket (A-high, K-high, Q-high, ..., 7-high)
2. Pairing (unpaired, paired, trips)
3. Suit pattern (rainbow, two-tone, monotone)
4. Connectivity (disconnected, gutshot-connected, OESD-connected, very-connected)

Two flops in the same cluster have similar GTO strategies, so one solver
solution covers the entire cluster.

Each cluster is a tuple: (high_bucket, pair_type, suit_type, connectivity)
"""

from itertools import combinations

RANKS = "AKQJT98765432"
RANK_VAL = {r: 14 - i for i, r in enumerate(RANKS)}  # A=14, K=13, ..., 2=2
SUITS = "cdhs"


def rank_val(card: str) -> int:
    return RANK_VAL[card[0]]


def suit(card: str) -> str:
    return card[1]


def classify_flop(cards: tuple[str, str, str]) -> tuple:
    """Classify a 3-card flop into a cluster key.

    Returns (high_bucket, pair_type, suit_type, connectivity).
    """
    ranks = sorted([rank_val(c) for c in cards], reverse=True)
    suits = [suit(c) for c in cards]

    # High card bucket
    high = ranks[0]
    if high == 14:
        high_bucket = "A"
    elif high == 13:
        high_bucket = "K"
    elif high == 12:
        high_bucket = "Q"
    elif high == 11:
        high_bucket = "J"
    elif high == 10:
        high_bucket = "T"
    elif high >= 8:
        high_bucket = "98"  # merge 9-high and 8-high
    else:
        high_bucket = "7-"  # 7-high and below

    # Pairing
    if ranks[0] == ranks[1] == ranks[2]:
        pair_type = "trips"
    elif ranks[0] == ranks[1] or ranks[1] == ranks[2]:
        pair_type = "paired"
    else:
        pair_type = "unpaired"

    # Suit pattern
    unique_suits = len(set(suits))
    if unique_suits == 1:
        suit_type = "mono"
    elif unique_suits == 2:
        suit_type = "tt"  # two-tone
    else:
        suit_type = "rainbow"

    # Connectivity (for unpaired boards only; paired boards have reduced connectivity)
    if pair_type != "unpaired":
        connectivity = "n/a"
    else:
        gaps = sorted([abs(ranks[i] - ranks[j]) for i, j in [(0,1), (1,2), (0,2)]])
        # gaps[0] = smallest gap, gaps[2] = largest gap
        min_gap = gaps[0]
        mid_gap = gaps[1]
        max_gap = gaps[2]  # = gaps[0] + gaps[1] since sorted unique ranks

        # Very connected: all 3 cards within 4 ranks (e.g., JT9, T87, 987)
        if max_gap <= 4:
            connectivity = "very"
        # OESD-connected: at least two cards within 2 ranks
        elif min_gap <= 2 and mid_gap <= 3:
            connectivity = "oesd"
        # Gutshot-connected: at least two cards within 3 ranks
        elif min_gap <= 3:
            connectivity = "gut"
        else:
            connectivity = "dry"

    return (high_bucket, pair_type, suit_type, connectivity)


def generate_all_flops():
    """Generate all 22,100 possible 3-card flops."""
    deck = [f"{r}{s}" for r in RANKS for s in SUITS]
    return list(combinations(deck, 3))


def build_cluster_map():
    """Build the full cluster map: cluster_key -> list of representative flops."""
    all_flops = generate_all_flops()
    clusters = {}
    for flop in all_flops:
        key = classify_flop(flop)
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(flop)
    return clusters


def pick_representative(flops: list[tuple]) -> tuple:
    """Pick a canonical representative flop from a cluster.
    Prefer rainbow or two-tone in specific suits for consistency."""
    # Pick the first alphabetically sorted flop
    return min(flops, key=lambda f: (f[0], f[1], f[2]))


if __name__ == "__main__":
    clusters = build_cluster_map()
    print(f"Total clusters: {len(clusters)}")
    print(f"Total flops: {sum(len(v) for v in clusters.values())}")
    print()

    # Summary by dimension
    highs = {}
    pairs = {}
    suits_d = {}
    conns = {}
    for key, flops in clusters.items():
        h, p, s, c = key
        highs[h] = highs.get(h, 0) + len(flops)
        pairs[p] = pairs.get(p, 0) + len(flops)
        suits_d[s] = suits_d.get(s, 0) + len(flops)
        conns[c] = conns.get(c, 0) + len(flops)

    print("By high card:", {k: v for k, v in sorted(highs.items())})
    print("By pairing:", pairs)
    print("By suit:", suits_d)
    print("By connectivity:", conns)
    print()

    # Show all cluster keys with sizes
    print(f"{'Cluster':<45} {'Flops':>6}  {'Rep':>15}")
    print("-" * 72)
    for key in sorted(clusters.keys()):
        rep = pick_representative(clusters[key])
        rep_str = " ".join(rep)
        print(f"{str(key):<45} {len(clusters[key]):>6}  {rep_str:>15}")
