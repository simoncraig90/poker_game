"""
Opponent tracker for Unibet WebSocket advisor.

Tracks per-player stats within a session:
- VPIP: Voluntarily Put money In Pot (% of hands played)
- PFR: Pre-Flop Raise (% of hands raised preflop)
- AF: Aggression Factor (bets+raises / calls)
- Player type: FISH / NIT / TAG / LAG / WHALE

Uses WS game state updates to track actions per seat.
"""


class OpponentTracker:
    """Track opponent statistics from WebSocket game state.

    Optionally persists to a HandDB for cross-session profiles.
    Pass `db=HandDB()` to enable persistence.
    """

    def __init__(self, db=None):
        self.players = {}  # name -> stats dict
        self._hand_participants = set()  # players in current hand
        self._hand_raisers = set()  # players who raised preflop
        self._hand_id = None
        self.db = db
        self._dirty_players = set()  # names that need DB save
        if db is not None:
            self._load_from_db()

    def _load_from_db(self):
        """Load all known players from DB on startup."""
        try:
            rows = self.db.conn.execute(
                "SELECT name, hands_seen, vpip, pfr, postflop_bets, postflop_calls, "
                "postflop_folds, went_to_showdown, won_at_showdown FROM opponents"
            ).fetchall()
            for row in rows:
                self.players[row[0]] = {
                    'hands': row[1] or 0,
                    'vpip': row[2] or 0,
                    'pfr': row[3] or 0,
                    'bets': row[4] or 0,
                    'calls': row[5] or 0,
                    'folds': row[6] or 0,
                    'showdowns': row[7] or 0,
                    'wins': row[8] or 0,
                }
            print(f"[OpponentTracker] Loaded {len(self.players)} players from DB")
        except Exception as e:
            print(f"[OpponentTracker] Load failed: {e}")

    def _save_dirty_to_db(self):
        """Save players that have been updated this session."""
        if not self.db or not self._dirty_players:
            return
        for name in list(self._dirty_players):
            p = self.players.get(name)
            if not p:
                continue
            stats = self.get_stats(name) or {'type': 'UNKNOWN'}
            try:
                self.db.log_opponent(name, {
                    'hands': p['hands'],
                    'vpip': p['vpip'],
                    'pfr': p['pfr'],
                    'bets': p['bets'],
                    'calls': p['calls'],
                    'folds': p['folds'],
                    'type': stats.get('type', 'UNKNOWN'),
                })
            except Exception:
                pass
        self._dirty_players.clear()

    def flush(self):
        """Force save all dirty players to DB. Call before shutdown."""
        self._save_dirty_to_db()

    def _get_player(self, name):
        if not name or name.strip() == '':
            return None
        if name not in self.players:
            self.players[name] = {
                'hands': 0,
                'vpip': 0,      # hands where they voluntarily put money in
                'pfr': 0,       # hands where they raised preflop
                'bets': 0,      # total bets+raises postflop
                'calls': 0,     # total calls postflop
                'folds': 0,     # total folds
                'showdowns': 0, # went to showdown
                'wins': 0,
            }
        return self.players[name]

    def update(self, state):
        """Update tracker with new game state from WS."""
        hand_id = state.get('hand_id')
        players = state.get('players', [])
        bets = state.get('bets', [])
        phase = state.get('phase', 'WAITING')
        hero_seat = state.get('hero_seat', -1)

        if not hand_id or not players:
            return

        # New hand — record participation
        if hand_id != self._hand_id:
            self._hand_id = hand_id
            self._hand_participants = set()
            self._hand_raisers = set()

            # Save any dirty players from previous hand
            self._save_dirty_to_db()

            # Count hands for all seated players
            for i, name in enumerate(players):
                p = self._get_player(name)
                if p:
                    p['hands'] += 1
                    self._dirty_players.add(name)

        # Track who's putting money in (VPIP)
        if phase == 'PREFLOP' and bets:
            # bb_amt is the threshold for "voluntarily put money in pot"
            # — anything more than the BB (so we don't count blinds-only
            # posts as VPIP). Reads from state['bb_amt'] if present
            # (CoinPoker passes this via _CoinPokerTrackerAdapter so the
            # threshold matches the actual chip scale at the table) and
            # falls back to the Unibet NL2 default (4 = 0.04 EUR) for
            # backwards compatibility.
            #
            # 2026-04-09: this default was the seed of a bad bug —
            # CoinPoker uses scaled chip units (CHIP_SCALE=100), so a
            # NL10 BB is 10000, not 4. Every player's blind post was
            # trivially > 4, marking EVERYONE as VPIP every hand and
            # corrupting the HandDB-persistent stats. Compared against
            # the HUD ground-truth endpoint and the disagreement led
            # back to this single hardcoded constant.
            bb_amt = state.get('bb_amt', 4)
            for i, bet in enumerate(bets):
                if i >= len(players):
                    break
                name = players[i]
                if not name or i == hero_seat:
                    continue
                # VPIP: voluntarily put money in (more than blind)
                if bet > bb_amt and name not in self._hand_participants:
                    self._hand_participants.add(name)
                    p = self._get_player(name)
                    if p:
                        p['vpip'] += 1
                        self._dirty_players.add(name)
                # PFR: raised (more than just calling BB)
                if bet > bb_amt * 2 and name not in self._hand_raisers:
                    self._hand_raisers.add(name)
                    p = self._get_player(name)
                    if p:
                        p['pfr'] += 1
                        self._dirty_players.add(name)

    def get_stats(self, name):
        """Get formatted stats for a player."""
        p = self.players.get(name)
        if not p or p['hands'] < 3:
            return None

        hands = max(p['hands'], 1)
        vpip = p['vpip'] / hands
        pfr = p['pfr'] / hands
        total_actions = p['bets'] + p['calls'] + 1  # +1 to avoid division by zero
        af = p['bets'] / max(p['calls'], 1)

        return {
            'name': name,
            'hands': p['hands'],
            'vpip': vpip,
            'pfr': pfr,
            'af': af,
            'type': self._classify(vpip, pfr, af),
        }

    def _classify(self, vpip, pfr, af):
        """Classify player type."""
        if vpip > 0.50:
            return 'WHALE' if vpip > 0.70 else 'FISH'
        elif vpip < 0.15:
            return 'NIT'
        elif pfr > 0.20 and af > 2:
            return 'LAG'
        elif pfr > 0.15:
            return 'TAG'
        else:
            return 'FISH'

    def classify_villain(self, state):
        """Get opponent type for the current hand's primary villain.
        Prefers the LAST AGGRESSOR (player with the highest current bet),
        falling back to the most-active player with stats.
        Returns 'FISH', 'NIT', 'TAG', 'LAG', 'WHALE', or 'UNKNOWN'."""
        players = state.get('players', [])
        bets = state.get('bets', [])
        hero_seat = state.get('hero_seat', -1)

        # Try the last aggressor (highest bet that isn't hero)
        if bets and len(bets) == len(players):
            best_seat = -1
            best_bet = 0
            for i, b in enumerate(bets):
                if i == hero_seat or not players[i]:
                    continue
                if b > best_bet:
                    best_bet = b
                    best_seat = i
            if best_seat >= 0:
                stats = self.get_stats(players[best_seat])
                if stats:
                    return stats['type']

        # Fallback: any player with enough hands
        for i, name in enumerate(players):
            if i == hero_seat or not name:
                continue
            stats = self.get_stats(name)
            if stats:
                return stats['type']
        return 'UNKNOWN'

    def get_table_summary(self, hero_seat=-1, players=None):
        """Get a one-line summary of table dynamics."""
        if not players:
            return ""

        types = []
        for i, name in enumerate(players):
            if i == hero_seat or not name:
                continue
            stats = self.get_stats(name)
            if stats:
                types.append(stats['type'])

        if not types:
            return ""

        fish_count = sum(1 for t in types if t in ('FISH', 'WHALE'))
        nit_count = sum(1 for t in types if t == 'NIT')

        if fish_count >= 2:
            return "FISHY table"
        elif nit_count >= 2:
            return "TIGHT table"
        else:
            return f"{len(types)} tracked"

    def get_equity_discount(self, name, bet_size, pot):
        """Get equity discount based on opponent profile.

        Tighter players bet with stronger ranges — discount more.
        Looser players bluff more — discount less.
        """
        stats = self.get_stats(name)
        if not stats:
            return 0.20  # default 20% discount

        player_type = stats['type']
        discounts = {
            'NIT': 0.35,    # nit bets = they have it
            'TAG': 0.25,    # solid player, moderate discount
            'LAG': 0.15,    # aggressive, could be bluffing
            'FISH': 0.10,   # fish bet with anything
            'WHALE': 0.05,  # whale = almost no discount
        }
        return discounts.get(player_type, 0.20)
