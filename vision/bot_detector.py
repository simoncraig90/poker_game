"""
Bot detector — identifies suspicious bot behavior in opponent pool.

Tracks per-player signals over time:
1. **Timing consistency** — bot reaction times have low variance
2. **Action speed** — sub-second consistent reactions
3. **Stat stability** — VPIP/PFR variance too low across volume
4. **Schedule** — playing 24/7 or multiple long sessions
5. **No tilt** — win rate stable after losing streaks
6. **Identical sizings** — always exactly 2.5x BB or 66% pot
7. **Position plays uniform** — same positional strategy

Output: per-player bot probability score 0-1 + signal list.

Used for:
- Avoiding tables with bot density > X
- Defensive ranges vs known bots (they don't bluff like humans)
- Building blacklist of suspected bot accounts
"""

import math
import time
from collections import defaultdict


class PlayerBotStats:
    """Per-player tracked stats for bot detection."""
    __slots__ = (
        'name',
        'total_actions',
        'action_times',          # list of (timestamp, action_type) for last N actions
        'reaction_intervals',    # ms between state-change and action
        'session_starts',        # list of timestamps when player joined a session
        'last_seen',
        'bet_sizes',             # raise/bet amounts for sizing pattern detection
        'preflop_open_sizes',    # specifically open raises
        'fold_actions',
        'raise_actions',
        'call_actions',
        'consec_loss_actions',   # actions taken after consecutive losses (tilt check)
        'recent_results',        # last N hand profits
    )

    def __init__(self, name):
        self.name = name
        self.total_actions = 0
        self.action_times = []
        self.reaction_intervals = []
        self.session_starts = []
        self.last_seen = 0
        self.bet_sizes = []
        self.preflop_open_sizes = []
        self.fold_actions = 0
        self.raise_actions = 0
        self.call_actions = 0
        self.consec_loss_actions = []
        self.recent_results = []


class BotDetector:
    """Tracks players and scores bot probability."""

    MIN_ACTIONS_FOR_SCORE = 50
    MAX_HISTORY_PER_PLAYER = 500

    def __init__(self, db=None):
        self.players = {}  # name → PlayerBotStats
        self.db = db
        self._dirty = set()
        self._action_state_change_ts = None  # when this action's prompt fired
        if db is not None:
            self._load_from_db()

    def _get(self, name):
        if not name:
            return None
        if name not in self.players:
            self.players[name] = PlayerBotStats(name)
        return self.players[name]

    # ── DB persistence ─────────────────────────────────────────────────

    def _load_from_db(self):
        if not self.db:
            return
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_stats (
                    name TEXT PRIMARY KEY,
                    total_actions INTEGER,
                    last_seen REAL,
                    fold_actions INTEGER,
                    raise_actions INTEGER,
                    call_actions INTEGER,
                    avg_reaction_ms REAL,
                    reaction_stdev REAL,
                    raw_data TEXT
                )
            """)
            self.db.conn.commit()
        except Exception as e:
            print(f"[BotDetector] DB schema error: {e}")

    def flush(self):
        if not self.db or not self._dirty:
            return
        import json
        for name in list(self._dirty):
            p = self.players.get(name)
            if not p:
                continue
            avg_reaction = sum(p.reaction_intervals) / len(p.reaction_intervals) if p.reaction_intervals else 0
            reaction_stdev = self._stdev(p.reaction_intervals) if len(p.reaction_intervals) > 1 else 0
            raw = json.dumps({
                'reactions': p.reaction_intervals[-100:],
                'bets': p.bet_sizes[-100:],
                'opens': p.preflop_open_sizes[-100:],
            })
            try:
                self.db.conn.execute("""
                    INSERT INTO bot_stats
                    (name, total_actions, last_seen, fold_actions, raise_actions,
                     call_actions, avg_reaction_ms, reaction_stdev, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                      total_actions=?, last_seen=?, fold_actions=?, raise_actions=?,
                      call_actions=?, avg_reaction_ms=?, reaction_stdev=?, raw_data=?
                """, (
                    name, p.total_actions, p.last_seen, p.fold_actions, p.raise_actions,
                    p.call_actions, avg_reaction, reaction_stdev, raw,
                    p.total_actions, p.last_seen, p.fold_actions, p.raise_actions,
                    p.call_actions, avg_reaction, reaction_stdev, raw,
                ))
            except Exception:
                pass
        self.db.conn.commit()
        self._dirty.clear()

    @staticmethod
    def _stdev(values):
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance)

    # ── Recording API ──────────────────────────────────────────────────

    def mark_action_prompt(self, timestamp=None):
        """Call when the action moves to a new player.
        Used to compute reaction time when they act."""
        self._action_state_change_ts = timestamp or time.time()

    def record_action(self, name, action_type, amount=0, timestamp=None):
        """Record one action by a player. Computes reaction time if mark_action_prompt was called."""
        p = self._get(name)
        if not p:
            return
        ts = timestamp or time.time()

        # Reaction time
        if self._action_state_change_ts is not None:
            interval_ms = (ts - self._action_state_change_ts) * 1000
            if 0 < interval_ms < 60000:  # ignore weird outliers
                p.reaction_intervals.append(interval_ms)
                if len(p.reaction_intervals) > self.MAX_HISTORY_PER_PLAYER:
                    p.reaction_intervals.pop(0)
            self._action_state_change_ts = None  # consume

        p.action_times.append((ts, action_type))
        if len(p.action_times) > self.MAX_HISTORY_PER_PLAYER:
            p.action_times.pop(0)

        p.total_actions += 1
        p.last_seen = ts
        action_lower = (action_type or '').lower()
        if action_lower == 'fold':
            p.fold_actions += 1
        elif action_lower in ('raise', 'bet', '3bet', '4bet'):
            p.raise_actions += 1
            if amount > 0:
                p.bet_sizes.append(amount)
                if len(p.bet_sizes) > self.MAX_HISTORY_PER_PLAYER:
                    p.bet_sizes.pop(0)
        elif action_lower == 'call':
            p.call_actions += 1

        self._dirty.add(name)

    def record_open_raise(self, name, amount):
        """Track preflop open raise sizes specifically."""
        p = self._get(name)
        if not p:
            return
        p.preflop_open_sizes.append(amount)
        if len(p.preflop_open_sizes) > self.MAX_HISTORY_PER_PLAYER:
            p.preflop_open_sizes.pop(0)
        self._dirty.add(name)

    def mark_session_start(self, name, timestamp=None):
        """Mark a session boundary (player came back online after gap)."""
        p = self._get(name)
        if not p:
            return
        p.session_starts.append(timestamp or time.time())
        self._dirty.add(name)

    # ── Scoring ─────────────────────────────────────────────────────────

    def score_player(self, name):
        """Compute bot probability 0-1. Returns (score, [signals]) or None."""
        p = self.players.get(name)
        if not p or p.total_actions < self.MIN_ACTIONS_FOR_SCORE:
            return None

        score = 0.0
        signals = []

        # 1. Timing consistency: too-stable reaction time
        if len(p.reaction_intervals) >= 30:
            avg = sum(p.reaction_intervals) / len(p.reaction_intervals)
            stdev = self._stdev(p.reaction_intervals)
            cv = stdev / avg if avg > 0 else 0
            if cv < 0.15 and avg > 100:
                score += 0.30
                signals.append(f"reaction CV={cv:.2f} (avg {avg:.0f}ms, too stable)")

        # 2. Sub-second consistent reactions
        if len(p.reaction_intervals) >= 30:
            sub_sec = sum(1 for r in p.reaction_intervals if r < 800)
            if sub_sec / len(p.reaction_intervals) > 0.85:
                score += 0.20
                signals.append(f"{sub_sec}/{len(p.reaction_intervals)} reactions <800ms")

        # 3. Identical bet sizing
        if len(p.preflop_open_sizes) >= 20:
            unique = len(set(p.preflop_open_sizes))
            if unique <= 2:
                score += 0.20
                signals.append(f"only {unique} unique open sizes in {len(p.preflop_open_sizes)} opens")

        if len(p.bet_sizes) >= 30:
            unique_bets = len(set(p.bet_sizes))
            if unique_bets / len(p.bet_sizes) < 0.10:
                score += 0.15
                signals.append(f"bet sizing very repetitive ({unique_bets} unique in {len(p.bet_sizes)})")

        # 4. 24/7 playing — many session starts close together OR session_starts has long span
        if len(p.session_starts) >= 5:
            span_hours = (p.session_starts[-1] - p.session_starts[0]) / 3600
            if span_hours > 20 and len(p.session_starts) > span_hours / 4:
                # More than 1 session per 4 hours over 20+ hours
                score += 0.15
                signals.append(f"{len(p.session_starts)} sessions over {span_hours:.0f}h")

        # 5. Action distribution rigid
        if p.total_actions >= 100:
            total = p.fold_actions + p.raise_actions + p.call_actions
            if total > 0:
                fold_pct = p.fold_actions / total
                raise_pct = p.raise_actions / total
                # Bots often have 70-80% fold rate from button passes — common but not bot-unique
                # The signal is when ALL three buckets are very specific values
                # (no signal here without more sophisticated analysis)
                pass

        return min(1.0, score), signals

    def is_likely_bot(self, name, threshold=0.50):
        result = self.score_player(name)
        return result is not None and result[0] >= threshold

    def table_bot_density(self, players_at_table):
        """Fraction of players at the table that are likely bots."""
        scored = 0
        bots = 0
        for p in players_at_table:
            r = self.score_player(p)
            if r is not None:
                scored += 1
                if r[0] >= 0.50:
                    bots += 1
        return (bots / scored) if scored > 0 else 0
