"""
Infer per-player actions from WS state diffs.

The WS gives us state snapshots (bets per seat, seat states, pot, phase)
but not discrete events. This module compares consecutive states and
emits a list of (player, action_type, amount) tuples for each transition.

Used by:
- CollusionDetector (track who raised whom)
- BotDetector (track timing per player)
- OpponentTracker (track aggression patterns)
"""

import time


class WSActionInferrer:
    """Stateful action inference from WS state snapshots.

    Usage:
        inf = WSActionInferrer()
        for state in ws_stream:
            actions = inf.update(state)
            # actions = [('Alice', 'raise', 12), ('Bob', 'fold', 0), ...]
    """

    def __init__(self):
        self._prev_state = None
        self._current_hand_id = None
        self._current_phase = None
        self._max_bet_seen = 0  # max bet this street
        self._raise_count = 0   # number of raises this street

    def reset_hand(self, hand_id):
        self._current_hand_id = hand_id
        self._current_phase = None
        self._max_bet_seen = 0
        self._raise_count = 0

    def reset_street(self, phase):
        self._current_phase = phase
        self._max_bet_seen = 0
        self._raise_count = 0

    def update(self, state):
        """
        Compare new state to prev state, return list of inferred actions.

        Returns: list of (player, action_type, amount) tuples
        """
        actions = []
        new_hand_id = state.get('hand_id')
        new_phase = state.get('phase')
        players = state.get('players', [])
        bets = state.get('bets', [])
        seats = state.get('seat_states', [])  # may not be present

        # New hand resets all tracking, no actions to infer
        if new_hand_id != self._current_hand_id:
            self.reset_hand(new_hand_id)
            self._current_phase = new_phase
            self._prev_state = state
            return actions

        # First state for this hand: just record, no diff possible
        if not self._prev_state:
            self._current_phase = new_phase
            self._prev_state = state
            return actions

        # New street: reset bet tracking but DON'T return early —
        # state diffs may still contain meaningful actions in this update
        if new_phase != self._current_phase:
            self._current_phase = new_phase
            self._max_bet_seen = 0
            self._raise_count = 0
            self._prev_state = state
            return actions

        # Compare bets per seat
        prev_bets = self._prev_state.get('bets', [])
        prev_seats = self._prev_state.get('seat_states', [])

        for i, name in enumerate(players):
            if not name or i >= len(bets):
                continue
            new_bet = bets[i]
            old_bet = prev_bets[i] if i < len(prev_bets) else 0
            new_seat = seats[i] if i < len(seats) else None
            old_seat = prev_seats[i] if i < len(prev_seats) else None

            # Detect fold (seat state 3 = folded in unibet)
            if new_seat == 3 and old_seat != 3:
                actions.append((name, 'fold', 0))
                continue

            # Detect bet/raise/call
            if new_bet > old_bet:
                bet_delta = new_bet - old_bet
                if new_bet > self._max_bet_seen:
                    # This is a raise (or first bet of the street)
                    if self._max_bet_seen == 0:
                        action_type = 'bet' if new_phase != 'PREFLOP' else 'raise'
                    else:
                        action_type = 'raise' if self._raise_count == 0 else '3bet' if self._raise_count == 1 else '4bet'
                    self._max_bet_seen = new_bet
                    self._raise_count += 1
                    actions.append((name, action_type, new_bet))
                elif new_bet == self._max_bet_seen:
                    # Calling the existing max bet
                    actions.append((name, 'call', new_bet))
                # else: weird partial bet — ignore

        self._prev_state = state
        return actions

    def get_seated_players(self, state):
        """Helper: list of seated player names from state."""
        players = state.get('players', [])
        return [p for p in players if p]
