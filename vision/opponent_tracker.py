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
    """Track opponent statistics from WebSocket game state."""

    def __init__(self):
        self.players = {}  # name -> stats dict
        self._hand_participants = set()  # players in current hand
        self._hand_raisers = set()  # players who raised preflop
        self._hand_id = None

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

            # Count hands for all seated players
            for i, name in enumerate(players):
                p = self._get_player(name)
                if p:
                    p['hands'] += 1

        # Track who's putting money in (VPIP)
        if phase == 'PREFLOP' and bets:
            bb_amt = 4  # 0.04 cents
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
                # PFR: raised (more than just calling BB)
                if bet > bb_amt * 2 and name not in self._hand_raisers:
                    self._hand_raisers.add(name)
                    p = self._get_player(name)
                    if p:
                        p['pfr'] += 1

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
