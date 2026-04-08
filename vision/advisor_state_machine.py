"""
AdvisorStateMachine — testable core of the poker advisor.

Extracted from the on_state callback in advisor_ws.py.
All state transitions, recommendation logic, and overlay output
are in this class. No subprocess calls, no WS reader, no I/O.

Dependencies are injected via constructor so tests can mock them.
"""

import time


def card_display(card):
    """Pretty format a card: 'Ah' -> 'Ah'"""
    if not card or len(card) < 2:
        return "??"
    return card[0].upper() + card[1].lower()


class AdvisorOutput:
    """Structured output from a single on_state call."""
    __slots__ = (
        "cards_text", "info", "action", "rec_bg", "rec_fg",
        "phase", "equity", "log_line", "should_update_overlay",
        "hand_id", "position", "hero_stack", "board", "pot",
        "facing_bet", "call_amount", "source", "opponent_type",
    )

    def __init__(self):
        self.cards_text = ""
        self.info = ""
        self.action = ""
        self.rec_bg = "#1a1a2e"
        self.rec_fg = "#ffd700"
        self.phase = ""
        self.equity = 0.0
        self.log_line = ""
        self.should_update_overlay = False
        self.hand_id = None
        self.position = "MP"
        self.hero_stack = 0
        self.board = []
        self.pot = 0
        self.facing_bet = False
        self.call_amount = 0
        self.source = ""
        self.opponent_type = ""


class AdvisorStateMachine:
    """
    Core advisor logic as a testable state machine.

    Constructor args are all injectable dependencies:
      - base_advisor: object with _get_recommendation(state) -> dict
      - preflop_advice_fn: (card1, card2, position, facing_raise) -> dict
      - postflop_engine: object with get_action(...) -> dict or None
      - equity_fn: (hero_dicts, board_dicts, num_opp) -> float, or None
      - assess_board_danger_fn: (hero, board) -> dict
      - tracker: OpponentTracker or None
      - bb_cents: big blind in cents (default 4)
    """

    def __init__(self, base_advisor, preflop_advice_fn, postflop_engine=None,
                 equity_fn=None, assess_board_danger_fn=None, tracker=None,
                 bb_cents=4):
        self.base = base_advisor
        self.preflop_advice = preflop_advice_fn
        self.postflop = postflop_engine
        self.equity_fn = equity_fn
        self.assess_board_danger = assess_board_danger_fn or (lambda h, b: {"warnings": []})
        self.tracker = tracker
        self.bb_cents = bb_cents

        # Mutable state
        self.prev_hero = []
        self.prev_board = []
        self.prev_hand_id = None
        self.prev_phase = None
        self.last_facing = None
        self.flop_action_history = ""

        # Session tracking
        self.session_start = time.time()
        self.starting_stack = None
        self.hands_played = 0
        self._last_stack = 0

    def process_state(self, state):
        """
        Process a game state update and return an AdvisorOutput.

        Returns None if no update is needed (no state change, or no hero cards).
        """
        hero = state["hero_cards"]
        board = state["board_cards"]
        hand_id = state["hand_id"]
        facing = state["facing_bet"]
        call_amt = state["call_amount"]
        phase = state["phase"]
        num_opp = state["num_opponents"]
        hero_stack = state.get("hero_stack", 0)
        pos = state.get("position", "MP")

        # Track stack for BB/hr
        if hero_stack > 0 and self.starting_stack is None:
            self.starting_stack = hero_stack

        # New hand detection
        if hand_id != self.prev_hand_id and hand_id is not None:
            self.hands_played += 1
            self._last_stack = hero_stack

        # No hero cards = waiting
        if len(hero) < 2:
            if self.prev_hero:
                out = AdvisorOutput()
                out.cards_text = "Waiting for cards..."
                out.should_update_overlay = True
                self.prev_hero = []
                self.prev_hand_id = hand_id
                return out
            self.prev_hand_id = hand_id
            return None

        # State change detection
        hand_changed = hand_id != self.prev_hand_id
        board_changed = board != self.prev_board
        phase_changed = phase != self.prev_phase
        hero_changed = hero != self.prev_hero
        facing_changed = facing != self.last_facing

        if not (hand_changed or board_changed or phase_changed or hero_changed or facing_changed):
            return None

        # Update tracked state
        self.last_facing = facing
        self.prev_hero = hero[:]
        self.prev_board = board[:]
        self.prev_hand_id = hand_id
        self.prev_phase = phase

        # Format cards
        hero_str = " ".join(card_display(c) for c in hero)
        board_str = " ".join(card_display(c) for c in board)
        cards_text = hero_str + ("  |  " + board_str if board_str else "")

        # BB/hr calculation
        bb_hr_str = self._bb_hr_string(hero_stack, state)

        # Build advisor state for base recommendation engine
        advisor_state = {
            "hero_cards": hero,
            "board_cards": board,
            "hero_turn": True,
            "facing_bet": facing,
            "call_amount": call_amt,
            "pot": state["pot"],
            "num_opponents": num_opp,
            "position": None,
            "position_6max": pos,
        }

        rec = self.base._get_recommendation(advisor_state)
        if not rec:
            return None

        # Override preflop with direct chart
        if rec.get("phase") == "PREFLOP":
            pf_direct = self.preflop_advice(hero[0], hero[1], pos, facing_raise=facing)
            rec["preflop"] = pf_direct

        phase_str = rec.get("phase", "PREFLOP")
        eq = rec.get("equity", 0.5)

        out = AdvisorOutput()
        out.cards_text = cards_text
        out.should_update_overlay = True
        out.phase = phase_str
        out.equity = eq
        out.hand_id = hand_id
        out.position = pos
        out.hero_stack = hero_stack
        out.board = board

        # Capture opponent type so it's available for overlay + logging,
        # not just postflop engine. Tracker may be None or lack the method.
        if self.tracker and hasattr(self.tracker, 'classify_villain'):
            try:
                out.opponent_type = self.tracker.classify_villain(state) or ""
            except Exception:
                out.opponent_type = ""
        out.pot = state["pot"]
        out.facing_bet = facing
        out.call_amount = call_amt

        if phase_str == "PREFLOP":
            self._process_preflop(out, rec, state, hero, pos, facing, call_amt,
                                  hero_str, eq, bb_hr_str)
        else:
            self._process_postflop(out, rec, state, hero, board, pos, facing,
                                   call_amt, hero_str, board_str, eq, bb_hr_str,
                                   phase, num_opp, hand_changed)

        return out

    def _process_preflop(self, out, rec, state, hero, pos, facing, call_amt,
                         hero_str, eq, bb_hr_str):
        """Handle preflop recommendation."""
        pf = rec.get("preflop", {})
        action = pf.get("action", "?")
        bb = self.bb_cents

        # SAFETY NET: never fold when you can check for free.
        # This catches BB option AND any position misdetection where the bot
        # thinks you're UTG/MP/CO but you actually have the check option.
        # Folding for zero cost is *always* strictly worse than checking.
        # Discovered 2026-04-07 after bot recommended FOLD on 7c8d UTG with
        # call=0 facing=False (real position was BB or position misdetected).
        if not facing and call_amt == 0 and action.upper() == "FOLD":
            action = "CHECK"

        # BB with no raise can check (kept for clarity even though above
        # rule already catches it)
        if not facing and action.upper() == "FOLD":
            if pos == "BB":
                action = "CHECK"

        # BB iso-raise vs limpers — premium pairs and AK/AQ should never
        # check the option when there are limpers in. Lost 9.90 EUR on JJ
        # 2026-04-07 because chart returned FOLD -> CHECK for JJ in BB.
        # Detect: BB, not facing a raise, but there's a multiway limped pot.
        if pos == "BB" and not facing and action.upper() == "CHECK":
            rank1, rank2 = hero[0][0].upper(), hero[1][0].upper()
            premium_pair = (rank1 == rank2 and rank1 in ('A', 'K', 'Q', 'J', 'T'))
            premium_unpaired = ({rank1, rank2} == {'A', 'K'}
                                or {rank1, rank2} == {'A', 'Q'})
            # Only iso if there's actually someone to iso (limpers detected via
            # bets array having entries equal to the BB amount, minus our own BB)
            bets = state.get("bets", [])
            limper_count = max(0, len([b for b in bets if b == bb]) - 1)
            if (premium_pair or premium_unpaired) and limper_count >= 1:
                action = "RAISE"

        # Add sizing
        pot_cents = state["pot"]
        if "RAISE" in action.upper():
            if facing and call_amt > 0:
                raise_to = call_amt * 3
                action = f"RAISE to {raise_to/100:.2f}"
            else:
                bets = state.get("bets", [])
                limpers = len([b for b in bets if b == bb]) - 1
                limpers = max(0, limpers)
                raise_size = int(bb * 2.5 + bb * limpers)
                action = f"RAISE to {raise_size/100:.2f}"
        elif "CALL" in action.upper() and call_amt > 0:
            action = f"CALL {call_amt/100:.2f}"

        info = f"Equity: {eq:.0%}  |  {pf.get('hand_key', '')}  {pos}{bb_hr_str}"

        rec_bg = "#1a3a1a" if "RAISE" in action.upper() or "CALL" in action.upper() else "#3a1a1a"
        if action.upper() == "CHECK":
            rec_bg = "#1a1a3a"

        out.action = action
        out.info = info
        out.rec_bg = rec_bg
        out.source = "preflop_chart"
        out.log_line = (f"[{out.phase}] {hero_str} | {pf.get('hand_key','')} {pos} "
                        f"facing={facing} chart={pf.get('action','?')} -> {action}")

    def _process_postflop(self, out, rec, state, hero, board, pos, facing,
                          call_amt, hero_str, board_str, eq, bb_hr_str,
                          phase, num_opp, hand_changed):
        """Handle postflop recommendation."""
        danger = rec.get("danger", {})
        warnings = " ".join(danger.get("warnings", [])) or "clean"
        cat = rec.get("category", "")
        pot_cents = state["pot"]
        hero_stack = state.get("hero_stack", 9999)
        bb = self.bb_cents

        # ── Opponent equity adjustment (MUST come before eq_str) ──
        adjusted_eq = eq
        if facing and call_amt > 0 and pot_cents > 0:
            bet_ratio = call_amt / pot_cents
            if bet_ratio > 1.0:
                adjusted_eq = eq * 0.65
            elif bet_ratio > 0.66:
                adjusted_eq = eq * 0.75
            elif bet_ratio > 0.33:
                adjusted_eq = eq * 0.85
            else:
                adjusted_eq = eq * 0.90

        dec_eq = adjusted_eq if facing else eq

        # ── Pot odds ──
        pot_odds_str = ""
        pot_odds = 0
        if facing and call_amt > 0 and pot_cents > 0:
            pot_odds = call_amt / (pot_cents + call_amt)
            pot_odds_str = f"  |  Pot odds: {pot_odds:.0%}"
            pot_odds_str += " (+EV)" if eq > pot_odds else " (-EV)"

        eq_str = f"Equity: {eq:.0%}"
        if facing and adjusted_eq < eq:
            eq_str += f" (adj: {adjusted_eq:.0%})"

        # ── Try postflop engine (flop CFR + turn/river rules) ──
        postflop_result = None
        if self.postflop and phase in ("FLOP", "TURN", "RIVER"):
            if hand_changed:
                self.flop_action_history = ""
            opp_type = (self.tracker.classify_villain(state)
                        if self.tracker and hasattr(self.tracker, 'classify_villain')
                        else 'UNKNOWN')
            try:
                postflop_result = self.postflop.get_action(
                    hero, board, pos, facing, call_amt,
                    pot_cents, hero_stack, phase, bb=bb,
                    opponent_type=opp_type,
                    action_history=self.flop_action_history if phase == "FLOP" else None,
                )
            except Exception:
                pass

        # ── Determine action ──
        action = ""
        cfr_info = ""
        source = "rules"

        if postflop_result:
            source = postflop_result.get('source', '?')
            p = postflop_result.get('probs')
            pf_action = postflop_result['action']
            pf_amt = postflop_result.get('amount')

            # Safety net: never fold with very high raw equity
            if pf_action == 'FOLD' and eq > 0.85:
                pf_action = 'CALL'
                pf_amt = call_amt

            if pf_action == 'RAISE' and pf_amt:
                action = f"RAISE to {pf_amt/100:.2f}"
            elif pf_action == 'BET' and pf_amt:
                action = f"BET {pf_amt/100:.2f}"
            elif pf_action == 'CALL' and call_amt > 0:
                action = f"CALL {call_amt/100:.2f}"
            elif pf_action == 'CHECK':
                action = "CHECK"
            elif pf_action == 'FOLD':
                action = "FOLD"
            else:
                action = pf_action

            if p:
                cfr_info = f"  [{source} F:{p['fold']:.0%} C:{p['call']:.0%} R:{p['raise']:.0%}]"
            else:
                cfr_info = f"  [{source}]"
        else:
            # Fallback: equity-based rules
            source = "rules"
            action = self._equity_rules_action(
                facing, dec_eq, eq, pot_odds, call_amt, pot_cents,
                hero_stack, danger, bb
            )

        info = f"{eq_str}  |  {cat}  |  {warnings}{pot_odds_str}{bb_hr_str}"

        rec_bg = "#1a3a1a" if any(k in action for k in ("CALL", "RAISE", "BET")) else "#3a1a1a"
        if "CHECK" in action:
            rec_bg = "#1a1a3a"

        # ── Danger-spot overrides ──
        # Hard-coded fold rules for spots where the equity model is known
        # to be miscalibrated. These run AFTER the engine produces an
        # action; they only ever override TO fold, never away from it.
        # See _apply_danger_overrides for the catalogued patterns. Lost
        # 2 buy-ins on 2026-04-08 to spots this filter would have caught.
        override = self._apply_danger_overrides(
            phase=phase, hero=hero, board=board, facing=facing,
            call_amt=call_amt, pot_cents=pot_cents, current_action=action,
        )
        if override:
            new_action, reason = override
            print(f"[danger-override] {phase} {hero_str} on {board_str}: "
                  f"{action!r} -> {new_action!r} ({reason})")
            action = new_action
            source = f"{source}+danger_override"

        out.action = action
        out.info = info
        out.rec_bg = rec_bg
        out.source = source
        adj_str = f" adj:{adjusted_eq:.0%}" if facing and adjusted_eq < eq else ""
        out.log_line = f"[{phase}] {hero_str} | Board: {board_str} | Eq: {eq:.0%}{adj_str} | {action}"

    def _equity_rules_action(self, facing, dec_eq, raw_eq, pot_odds,
                              call_amt, pot_cents, hero_stack, danger, bb):
        """Fallback equity-based action when postflop engine doesn't fire."""
        # Safety net: never fold with very high raw equity
        if facing and raw_eq > 0.85:
            return f"CALL {call_amt/100:.2f}"

        danger_warns = danger.get("warnings", [])
        is_scary = any(w in danger_warns for w in
                       ("STRAIGHT_POSSIBLE", "FLUSH_POSSIBLE", "FLUSH_DRAW", "PAIRED"))
        big_bet = call_amt > pot_cents * 0.5 if pot_cents > 0 else False

        if not facing:
            if dec_eq < 0.5:
                return "CHECK / FOLD"
            elif dec_eq < 0.7:
                return "CHECK / CALL"
            else:
                bet_size = int(pot_cents * 0.66)
                if is_scary and dec_eq < 0.85:
                    bet_size = int(pot_cents * 0.33)
                bet_size = min(bet_size, hero_stack)
                if bet_size >= hero_stack:
                    return "BET ALL-IN"
                return f"BET {bet_size/100:.2f}"
        else:
            is_plus_ev = pot_odds > 0 and raw_eq > pot_odds

            if is_scary and big_bet:
                if dec_eq > 0.90:
                    return f"RAISE to {min(int(call_amt*3), hero_stack)/100:.2f}"
                elif is_plus_ev and dec_eq > 0.40:
                    return f"CALL {call_amt/100:.2f}"
                else:
                    return "FOLD"
            elif is_scary:
                if dec_eq > 0.80:
                    return f"RAISE to {min(int(call_amt*3), hero_stack)/100:.2f}"
                elif is_plus_ev or dec_eq > 0.40:
                    return f"CALL {call_amt/100:.2f}"
                else:
                    return "FOLD"
            else:
                if dec_eq > 0.75:
                    return f"RAISE to {min(int(call_amt*3), hero_stack)/100:.2f}"
                elif is_plus_ev or dec_eq > 0.35:
                    return f"CALL {call_amt/100:.2f}"
                else:
                    return "FOLD"

    # ─────────────────────────────────────────────────────────────────────
    # Danger overrides — hard-coded fold rules for spots where the equity
    # model has known calibration failures. Each pattern represents a real
    # named loss in tests/test_strategy_regressions.py.
    # ─────────────────────────────────────────────────────────────────────

    # Card rank → numeric value for straight detection
    _RANK_VAL = {
        "2":2, "3":3, "4":4, "5":5, "6":6, "7":7, "8":8, "9":9,
        "T":10, "J":11, "Q":12, "K":13, "A":14,
    }

    @classmethod
    def _card_rank(cls, c):
        if not c or len(c) < 2:
            return None
        return cls._RANK_VAL.get(c[0].upper())

    @classmethod
    def _card_suit(cls, c):
        if not c or len(c) < 2:
            return None
        return c[1].lower()

    @classmethod
    def _board_has_4card_straight(cls, board):
        """
        True if 4+ cards on the board fall within a 5-rank window
        (i.e. a straight needs only one more card to complete).
        Handles A-low: A-2-3-4-5 wheel.
        """
        if len(board) < 4:
            return False
        ranks = sorted({cls._card_rank(c) for c in board if cls._card_rank(c)})
        if not ranks:
            return False
        # Standard window check
        for i in range(len(ranks)):
            for j in range(i + 3, len(ranks)):
                if ranks[j] - ranks[i] <= 4:
                    # 4+ ranks fit in a 5-rank window → 4-card straight reachable
                    if j - i + 1 >= 4:
                        return True
        # Wheel check: treat A as 1 and try again
        if 14 in ranks:
            wheel_ranks = sorted({1 if r == 14 else r for r in ranks if r <= 5 or r == 14})
            for i in range(len(wheel_ranks)):
                for j in range(i + 3, len(wheel_ranks)):
                    if wheel_ranks[j] - wheel_ranks[i] <= 4 and j - i + 1 >= 4:
                        return True
        return False

    @classmethod
    def _board_has_4card_flush(cls, board):
        """True if 4+ board cards share a suit."""
        if len(board) < 4:
            return False
        suits = {}
        for c in board:
            s = cls._card_suit(c)
            if s:
                suits[s] = suits.get(s, 0) + 1
        return any(v >= 4 for v in suits.values())

    @classmethod
    def _hero_has_overpair(cls, hero, board):
        """
        True if hero has a pocket pair larger than every board card.
        TT on a 9-high board = overpair. KK on a Q-high board = overpair.
        KK on an A-high board = NOT overpair.
        """
        if len(hero) != 2:
            return False
        r1 = cls._card_rank(hero[0])
        r2 = cls._card_rank(hero[1])
        if r1 is None or r2 is None or r1 != r2:
            return False
        board_ranks = [cls._card_rank(c) for c in board]
        board_ranks = [r for r in board_ranks if r is not None]
        if not board_ranks:
            return False
        return r1 > max(board_ranks)

    def _apply_danger_overrides(self, *, phase, hero, board, facing,
                                call_amt, pot_cents, current_action):
        """
        Run hard-coded fold filters AFTER the engine produces an action.
        Returns (new_action, reason) or None if no override applies.

        Each filter must:
          - Only trigger in narrow, named spots (no broad "fold whenever")
          - Document the seed hand it was added for
          - Only ever turn an action INTO a fold, never away from one

        The cost of false positives is folding too often in some marginal
        spots. The cost of false negatives is busting stacks. We pay the
        false-positive tax knowingly.
        """
        # Don't override an already-folded action
        if not current_action or "FOLD" in current_action.upper():
            return None
        # Only relevant on turn/river — flops can't have a 4-card straight
        if phase not in ("TURN", "RIVER"):
            return None
        # Must be facing aggression with a meaningful sizing
        if not facing or call_amt <= 0 or pot_cents <= 0:
            return None

        bet_ratio = call_amt / pot_cents

        # ── Filter 1: Overpair on 4-straight board facing aggression ──
        # Seed hand 2460830707 (KK on 5d 9s 7d 4c 8c, river call-off).
        # When the board makes a 4-card straight reachable AND we're
        # facing a non-trivial bet/raise, an overpair has terrible
        # equity vs the action-narrowed range — usually 10-20% rather
        # than the equity model's hand-vs-random estimate.
        if (self._hero_has_overpair(hero, board)
                and self._board_has_4card_straight(board)
                and bet_ratio >= 0.20):
            return ("FOLD", f"overpair on 4-straight board facing {bet_ratio:.0%}-pot")

        # ── Filter 2: Overpair on 4-flush board facing aggression ──
        # No specific seed hand yet, but the same equity-vs-action-range
        # gap applies. KK on a 4-flush facing a raise has worse equity
        # than the model thinks. Conservative same-shape filter.
        if (self._hero_has_overpair(hero, board)
                and self._board_has_4card_flush(board)
                and bet_ratio >= 0.20):
            return ("FOLD", f"overpair on 4-flush board facing {bet_ratio:.0%}-pot")

        return None

    def _bb_hr_string(self, hero_stack, state):
        """Format BB/hr tracking string."""
        if self.starting_stack is None or hero_stack <= 0:
            return ""
        elapsed_hrs = max((time.time() - self.session_start) / 3600, 0.01)
        profit_cents = hero_stack - self.starting_stack
        bb_hr = (profit_cents / self.bb_cents) / elapsed_hrs
        table_info = ""
        if self.tracker:
            table_info_raw = self.tracker.get_table_summary(
                state.get("hero_seat", -1), state.get("players", []))
            if table_info_raw:
                table_info = f" | {table_info_raw}"
        return f"  [{bb_hr:+.1f} bb/hr | {self.hands_played}h{table_info}]"
