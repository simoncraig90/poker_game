"""
Collusion detector — defensive only.

Tracks suspicion scores between pairs of opponents over time.
Used to:
1. Tighten ranges when a coordinated pair is at the table
2. Auto-leave tables with high collusion scores
3. Build persistent suspect list

Patterns detected:
- Soft play: pair never 3-bets/raises each other across many hands
- Avoidance: pair shows down against each other less often than expected
- Chip flow: chips consistently flow one direction between the pair
- Whipsawing: coordinated raises sandwiching a third player
- Folding-to-each-other: high mutual fold-to-aggression rate
"""

from collections import defaultdict
import math


class PairStats:
    """Stats for one pair of players (order-independent)."""
    __slots__ = (
        'a', 'b',
        'hands_together',           # hands both seated
        'showdowns_together',       # showdowns where both showed
        'a_raised_b_called',        # times A raised, B called (no 3-bet)
        'a_raised_b_folded',
        'a_raised_b_3bet',          # times A raised, B re-raised
        'b_raised_a_called',
        'b_raised_a_folded',
        'b_raised_a_3bet',
        'a_to_b_chips',             # net chip flow A → B
        'whipsaws_around_others',   # times A and B sandwiched another player
    )

    def __init__(self, a, b):
        # Canonical ordering: alphabetical
        if a > b:
            a, b = b, a
        self.a = a
        self.b = b
        self.hands_together = 0
        self.showdowns_together = 0
        self.a_raised_b_called = 0
        self.a_raised_b_folded = 0
        self.a_raised_b_3bet = 0
        self.b_raised_a_called = 0
        self.b_raised_a_folded = 0
        self.b_raised_a_3bet = 0
        self.a_to_b_chips = 0
        self.whipsaws_around_others = 0

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d):
        p = cls(d['a'], d['b'])
        for k in cls.__slots__:
            if k in d and k not in ('a', 'b'):
                setattr(p, k, d[k])
        return p


class CollusionDetector:
    """
    Tracks per-pair coordination stats and produces a 0-1 suspicion score.

    Optionally backed by HandDB for cross-session persistence.
    """

    # Detection thresholds
    MIN_HANDS_FOR_SCORE = 30        # need this many hands together to score
    EXPECTED_3BET_RATE = 0.08       # baseline 3-bet vs 1 raiser at micro stakes
    SUSPECT_THRESHOLD = 0.55        # >this = mark suspect, defensive adjustments
    LEAVE_THRESHOLD = 0.75          # >this = leave the table

    def __init__(self, db=None):
        self.pairs = {}  # (a,b) → PairStats (a < b alphabetically)
        self.db = db
        self._dirty = set()
        self._current_hand_id = None
        self._hand_actions = []  # (player, action_type, amount) for current hand
        self._hand_seated = set()
        if db is not None:
            self._load_from_db()

    def _key(self, a, b):
        return (a, b) if a < b else (b, a)

    def _get_pair(self, a, b):
        if a == b:
            return None
        k = self._key(a, b)
        if k not in self.pairs:
            self.pairs[k] = PairStats(*k)
        return self.pairs[k]

    # ── DB persistence ──────────────────────────────────────────────────

    def _load_from_db(self):
        if not self.db:
            return
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS collusion_pairs (
                    a TEXT, b TEXT,
                    hands_together INTEGER,
                    showdowns_together INTEGER,
                    a_raised_b_called INTEGER,
                    a_raised_b_folded INTEGER,
                    a_raised_b_3bet INTEGER,
                    b_raised_a_called INTEGER,
                    b_raised_a_folded INTEGER,
                    b_raised_a_3bet INTEGER,
                    a_to_b_chips INTEGER,
                    whipsaws_around_others INTEGER,
                    PRIMARY KEY (a, b)
                )
            """)
            self.db.conn.commit()
            rows = self.db.conn.execute("SELECT * FROM collusion_pairs").fetchall()
            for row in rows:
                d = {
                    'a': row[0], 'b': row[1],
                    'hands_together': row[2] or 0,
                    'showdowns_together': row[3] or 0,
                    'a_raised_b_called': row[4] or 0,
                    'a_raised_b_folded': row[5] or 0,
                    'a_raised_b_3bet': row[6] or 0,
                    'b_raised_a_called': row[7] or 0,
                    'b_raised_a_folded': row[8] or 0,
                    'b_raised_a_3bet': row[9] or 0,
                    'a_to_b_chips': row[10] or 0,
                    'whipsaws_around_others': row[11] or 0,
                }
                self.pairs[(d['a'], d['b'])] = PairStats.from_dict(d)
        except Exception as e:
            print(f"[CollusionDetector] DB load failed: {e}")

    def flush(self):
        if not self.db:
            return
        for k in list(self._dirty):
            p = self.pairs.get(k)
            if not p:
                continue
            try:
                d = p.to_dict()
                self.db.conn.execute("""
                    INSERT INTO collusion_pairs
                    (a, b, hands_together, showdowns_together,
                     a_raised_b_called, a_raised_b_folded, a_raised_b_3bet,
                     b_raised_a_called, b_raised_a_folded, b_raised_a_3bet,
                     a_to_b_chips, whipsaws_around_others)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(a, b) DO UPDATE SET
                      hands_together=?, showdowns_together=?,
                      a_raised_b_called=?, a_raised_b_folded=?, a_raised_b_3bet=?,
                      b_raised_a_called=?, b_raised_a_folded=?, b_raised_a_3bet=?,
                      a_to_b_chips=?, whipsaws_around_others=?
                """, (
                    d['a'], d['b'], d['hands_together'], d['showdowns_together'],
                    d['a_raised_b_called'], d['a_raised_b_folded'], d['a_raised_b_3bet'],
                    d['b_raised_a_called'], d['b_raised_a_folded'], d['b_raised_a_3bet'],
                    d['a_to_b_chips'], d['whipsaws_around_others'],
                    d['hands_together'], d['showdowns_together'],
                    d['a_raised_b_called'], d['a_raised_b_folded'], d['a_raised_b_3bet'],
                    d['b_raised_a_called'], d['b_raised_a_folded'], d['b_raised_a_3bet'],
                    d['a_to_b_chips'], d['whipsaws_around_others'],
                ))
            except Exception as e:
                print(f"[CollusionDetector] DB save failed: {e}")
        self.db.conn.commit()
        self._dirty.clear()

    # ── Hand tracking API ──────────────────────────────────────────────

    def hand_started(self, hand_id, players_seated):
        """Call at the start of each hand with the list of seated player names."""
        self._current_hand_id = hand_id
        self._hand_actions = []
        self._hand_seated = set(p for p in players_seated if p)

        # Increment hands_together for every pair
        seated_list = sorted(self._hand_seated)
        for i, a in enumerate(seated_list):
            for b in seated_list[i+1:]:
                p = self._get_pair(a, b)
                if p:
                    p.hands_together += 1
                    self._dirty.add(self._key(a, b))

    def record_action(self, player, action_type, amount=0):
        """
        Record a single action.

        action_type: 'fold', 'check', 'call', 'raise', 'bet', '3bet', '4bet', 'allin', 'showdown'
        """
        if not player or player not in self._hand_seated:
            return
        self._hand_actions.append((player, action_type.lower(), amount))

        # Detect raise→fold patterns (vs each prior actor)
        if action_type.lower() == 'fold':
            # Did anyone raise this hand who was the same actor type?
            for prior_player, prior_action, _ in self._hand_actions[:-1]:
                if prior_action in ('raise', '3bet', '4bet') and prior_player != player:
                    p = self._get_pair(prior_player, player)
                    if p:
                        if prior_player == p.a:
                            p.a_raised_b_folded += 1
                        else:
                            p.b_raised_a_folded += 1
                        self._dirty.add(self._key(prior_player, player))
                    break

        elif action_type.lower() == 'call':
            for prior_player, prior_action, _ in reversed(self._hand_actions[:-1]):
                if prior_action in ('raise', '3bet', '4bet') and prior_player != player:
                    p = self._get_pair(prior_player, player)
                    if p:
                        if prior_player == p.a:
                            p.a_raised_b_called += 1
                        else:
                            p.b_raised_a_called += 1
                        self._dirty.add(self._key(prior_player, player))
                    break

        elif action_type.lower() in ('raise', '3bet', '4bet'):
            # If this raise was a 3-bet (someone raised before), record it
            for prior_player, prior_action, _ in reversed(self._hand_actions[:-1]):
                if prior_action in ('raise', '3bet') and prior_player != player:
                    p = self._get_pair(prior_player, player)
                    if p:
                        if prior_player == p.a:
                            p.a_raised_b_3bet += 1
                        else:
                            p.b_raised_a_3bet += 1
                        self._dirty.add(self._key(prior_player, player))
                    break

    def record_showdown(self, players_shown):
        """Call when a hand reaches showdown."""
        shown = sorted(p for p in players_shown if p in self._hand_seated)
        for i, a in enumerate(shown):
            for b in shown[i+1:]:
                p = self._get_pair(a, b)
                if p:
                    p.showdowns_together += 1
                    self._dirty.add(self._key(a, b))

    def record_chip_flow(self, winner, losers, amount):
        """Record chips flowing from losers to winner."""
        for loser in losers:
            if loser == winner:
                continue
            p = self._get_pair(winner, loser)
            if p:
                # Direction matters: positive = a→b
                if winner == p.a:
                    p.a_to_b_chips -= amount
                else:
                    p.a_to_b_chips += amount
                self._dirty.add(self._key(winner, loser))

    # ── Scoring ─────────────────────────────────────────────────────────

    def score_pair(self, a, b):
        """
        Compute coordination suspicion 0-1 for a pair.

        Returns None if not enough data.
        """
        k = self._key(a, b)
        p = self.pairs.get(k)
        if not p or p.hands_together < self.MIN_HANDS_FOR_SCORE:
            return None

        score = 0.0
        signals = []

        # 1. Soft play vs each other (no 3-betting)
        total_a_raises_seen_by_b = (p.a_raised_b_called + p.a_raised_b_folded + p.a_raised_b_3bet)
        total_b_raises_seen_by_a = (p.b_raised_a_called + p.b_raised_a_folded + p.b_raised_a_3bet)

        if total_a_raises_seen_by_b >= 5:
            ab_3bet_rate = p.a_raised_b_3bet / total_a_raises_seen_by_b
            # Expected at micro-stakes ~5-10%. 0% across 10+ raises is suspicious.
            if ab_3bet_rate < 0.02 and total_a_raises_seen_by_b >= 10:
                score += 0.25
                signals.append(f"B never 3bets A ({total_a_raises_seen_by_b} raises seen)")

        if total_b_raises_seen_by_a >= 5:
            ba_3bet_rate = p.b_raised_a_3bet / total_b_raises_seen_by_a
            if ba_3bet_rate < 0.02 and total_b_raises_seen_by_a >= 10:
                score += 0.25
                signals.append(f"A never 3bets B ({total_b_raises_seen_by_a} raises seen)")

        # 2. High mutual fold rate (avoiding confrontation)
        if total_a_raises_seen_by_b >= 5:
            fold_rate_b = p.a_raised_b_folded / total_a_raises_seen_by_b
            if fold_rate_b > 0.85:
                score += 0.15
                signals.append(f"B folds {fold_rate_b:.0%} to A's raises")

        if total_b_raises_seen_by_a >= 5:
            fold_rate_a = p.b_raised_a_folded / total_b_raises_seen_by_a
            if fold_rate_a > 0.85:
                score += 0.15
                signals.append(f"A folds {fold_rate_a:.0%} to B's raises")

        # 3. Avoidance — fewer showdowns than statistically expected
        # Baseline: at micro stakes, ~15% of hands go to showdown.
        # If two players see <3% mutual showdown, it's suspicious.
        if p.hands_together >= 50:
            sd_rate = p.showdowns_together / p.hands_together
            if sd_rate < 0.02:
                score += 0.15
                signals.append(f"Only {sd_rate:.1%} mutual showdowns ({p.showdowns_together})")

        # 4. Chip flow imbalance (chip dumping)
        # If chips consistently flow one way (>80% of total flow direction)
        # and total amount is significant
        if abs(p.a_to_b_chips) > 200:  # > 2 EUR worth of chips one direction
            flow_ratio = abs(p.a_to_b_chips) / (abs(p.a_to_b_chips) + 1)
            # We don't have absolute chips, just net — but a strong net direction is suspicious
            if abs(p.a_to_b_chips) > p.hands_together * 5:  # > 5 cents per hand net
                score += 0.20
                direction = "B→A" if p.a_to_b_chips > 0 else "A→B"
                signals.append(f"chip flow {direction} {abs(p.a_to_b_chips)/100:.2f}")

        # 5. Whipsawing
        if p.whipsaws_around_others >= 5 and p.hands_together >= 30:
            wr = p.whipsaws_around_others / p.hands_together
            if wr > 0.05:
                score += 0.20
                signals.append(f"{p.whipsaws_around_others} whipsaws on others")

        return min(1.0, score), signals

    def get_table_score(self, players_at_table):
        """
        Get the maximum pair coordination score at a table.

        Returns: (max_score, [(pair, score, signals), ...])
        """
        max_score = 0
        details = []
        players = [p for p in players_at_table if p]
        for i, a in enumerate(players):
            for b in players[i+1:]:
                result = self.score_pair(a, b)
                if result is not None:
                    score, signals = result
                    if score > 0:
                        details.append(((a, b), score, signals))
                        if score > max_score:
                            max_score = score
        return max_score, details

    def is_suspect(self, a, b):
        """True if a pair has crossed the suspect threshold."""
        result = self.score_pair(a, b)
        return result is not None and result[0] >= self.SUSPECT_THRESHOLD

    def should_leave_table(self, players_at_table):
        """True if any pair at the table is above LEAVE_THRESHOLD."""
        max_score, _ = self.get_table_score(players_at_table)
        return max_score >= self.LEAVE_THRESHOLD
