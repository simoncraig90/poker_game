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

        # Action-history accumulator (v0 of equity-vs-action-range).
        # Per-hand list of detected villain actions, populated by diffing
        # `players[*].last_action` between successive snapshots. Used by
        # `_equity_discount_from_action_history` to discount equity when
        # the action sequence narrows villain's range. Resets on new hand.
        self.action_history = []
        self.action_history_hand = None
        self._prev_villain_actions = {}  # seat -> last_action string

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

        # Update action-history accumulator on every snapshot, BEFORE the
        # waiting-for-cards short-circuit. This way villain actions during
        # blinds posting / pre-deal still get tracked, and by the time hero
        # has cards we already have history for them.
        self._ingest_snapshot_for_action_history(state)

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

        # ── Action-history discount (v0 of equity-vs-action-range) ──
        # Compose with the bet-ratio discount above. Only fires when
        # facing aggression AND there's accumulated villain action history
        # this hand. The multiplier is < 1.0 only when villain has shown
        # specific aggression patterns (recent raise, multiple raises).
        # See _equity_discount_from_action_history for the math.
        if facing and pot_cents > 0:
            history_mult = self._equity_discount_from_action_history()
            if history_mult < 1.0:
                adjusted_eq *= history_mult

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
            # Default unknown villains to NIT at micro stakes. Replay
            # validation against 733 captured hands (2026-04-08) showed
            # `nit_assume` was the only +EV variant tested (+EUR 0.22)
            # vs the production baseline. Micros (NL2-NL10) are
            # population-tighter than the UNKNOWN profile assumes —
            # treating unknowns as nits avoids overcalling river bets.
            # Threshold: bb_cents <= 1000 (i.e. NL10 and below).
            if opp_type == 'UNKNOWN' and self.bb_cents <= 1000:
                opp_type = 'NIT'
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
    # Action history accumulator (v0 of equity-vs-action-range)
    # ─────────────────────────────────────────────────────────────────────
    #
    # Why this exists: the equity model computes hero vs RANDOM hand.
    # In real spots, villain's actions narrow their range — a check-call
    # check-raise sequence on a wet board doesn't have random hands in
    # it, it has draws and made hands. The equity model doesn't see this
    # narrowing, so it overestimates hero's chances against an action-
    # narrowed range.
    #
    # The proper fix is range-vs-range equity computation (P0 in the
    # kanban). This v0 is a stand-in: we accumulate detected villain
    # actions per hand and compute a multiplier on equity that scales
    # with how much aggression villain has shown. Compounds with the
    # existing bet-ratio discount in `_process_postflop`.
    #
    # Detection works by diffing successive snapshots: if a player's
    # `last_action` field changes, that's a new action. This is robust
    # to multiple snapshots arriving for the same actual decision (no
    # double-counting) and to seeing a player for the first time mid-
    # hand (treats their current last_action as their first detected).

    def _ingest_snapshot_for_action_history(self, state):
        """Update self.action_history from the current snapshot. Resets
        on new hand. Idempotent on duplicate snapshots within a hand."""
        hand_id = state.get('hand_id')
        if hand_id != self.action_history_hand:
            self.action_history_hand = hand_id
            self.action_history = []
            self._prev_villain_actions = {}

        hero_seat = state.get('hero_seat')
        phase = state.get('phase', '?')

        for p in state.get('players', []) or []:
            if not isinstance(p, dict):
                continue  # Unibet name-list format — skip
            seat = p.get('seat')
            if seat is None or seat == hero_seat:
                continue
            la = (p.get('last_action') or '').strip()
            if not la:
                continue
            prev = self._prev_villain_actions.get(seat, '')
            if la == prev:
                continue  # not a new action

            # Classify the action — only aggressive/passive actions count
            la_upper = la.upper()
            if 'RAISE' in la_upper or 'ALLIN' in la_upper or 'ALL-IN' in la_upper or 'ALL IN' in la_upper:
                action_type = 'RAISE'
            elif 'BET' in la_upper:
                action_type = 'BET'
            elif 'CALL' in la_upper:
                action_type = 'CALL'
            elif 'CHECK' in la_upper:
                action_type = 'CHECK'
            elif 'FOLD' in la_upper:
                action_type = 'FOLD'
            else:
                action_type = None  # ignore "Inuse", "Sitout", "Ante", "SB", "BB", etc.

            if action_type in ('RAISE', 'BET', 'CALL', 'CHECK'):
                self.action_history.append({
                    'phase': phase,
                    'seat': seat,
                    'action': action_type,
                })
            self._prev_villain_actions[seat] = la

    def _equity_discount_from_action_history(self):
        """
        Compute a multiplier on hero's equity based on the action history
        accumulated this hand. Conservative — only ever <= 1.0, never
        inflates. Floor at 0.30 to avoid catastrophically discounting
        when hero might still have a strong hand.

        Multiplier components (compose multiplicatively):
          - last action is RAISE on RIVER:  × 0.65
          - last action is RAISE on TURN:   × 0.75
          - last action is RAISE on FLOP:   × 0.85
          - villain has raised >= 2 times this hand: × 0.85
          - villain has raised >= 3 times this hand: × 0.85 (compounds)

        These thresholds were chosen so that a single c-bet on the flop
        produces no discount (just BET, not RAISE), but a flop bet
        followed by a turn check-raise produces ~0.64 (= 0.75 × 0.85)
        — significant but not catastrophic.

        IMPORTANT 2026-04-08 finding: A/B comparison against tonight's
        CoinPoker dataset (3026 snapshots, 102 hero-turn decisions)
        showed this v0 produces ZERO action-category divergences vs
        the same code with the discount disabled. The multiplier fires
        on 16% of hero turns but doesn't push any decision across a
        threshold because:
          (a) The existing bet-ratio discount in `_process_postflop`
              already moves equity in the same direction.
          (b) The danger filters already catch the catastrophic
              river-raise spots before they reach the postflop engine.
          (c) Most postflop hero turns are either folds-anyway or
              strong-hand calls that don't approach the boundary.

        The v0 is plumbed correctly and provides defense-in-depth that
        WILL activate on future hands with multi-raise patterns the
        danger filters don't cover. But on tonight's data it's a no-op
        in terms of recommended actions. The proper fix (range-vs-range
        equity computation, kanban P0) is still required for spots
        that are common but not catastrophic — top-pair calldowns,
        marginal-made-hand calls, etc.
        """
        if not self.action_history:
            return 1.0

        multiplier = 1.0
        last = self.action_history[-1]

        if last['action'] == 'RAISE':
            if last['phase'] == 'RIVER':
                multiplier *= 0.65
            elif last['phase'] == 'TURN':
                multiplier *= 0.75
            elif last['phase'] == 'FLOP':
                multiplier *= 0.85
        elif last['action'] == 'BET' and last['phase'] == 'RIVER':
            # River bets are commitment signals — even without a raise,
            # a villain who bets the river has a hand they think can
            # win at showdown. Mild discount to push hero toward folding
            # marginal one-pair hands.
            multiplier *= 0.90

        raise_count = sum(1 for a in self.action_history if a['action'] == 'RAISE')
        if raise_count >= 2:
            multiplier *= 0.85
        if raise_count >= 3:
            multiplier *= 0.85

        # If villain has been the AGGRESSOR on multiple streets in a
        # row (bet flop, bet turn, bet river — the "barreling" pattern)
        # apply an additional discount even without raises. This catches
        # the leak where the equity model treats every street's bet as
        # independent and doesn't update on the cumulative aggression.
        bet_or_raise_streets = set(
            a['phase'] for a in self.action_history
            if a['action'] in ('BET', 'RAISE')
        )
        if len(bet_or_raise_streets) >= 3:  # bet on 3 different streets
            multiplier *= 0.85

        return max(0.30, multiplier)

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
    def _board_has_3card_flush(cls, board):
        """
        True if 3+ board cards share a suit (a flush is reachable with just
        2 cards of the right suit in villain's hand). Weaker danger signal
        than _board_has_4card_flush, so callers should pair with a higher
        bet-ratio threshold.
        """
        if len(board) < 3:
            return False
        suits = {}
        for c in board:
            s = cls._card_suit(c)
            if s:
                suits[s] = suits.get(s, 0) + 1
        return any(v >= 3 for v in suits.values())

    @classmethod
    def _board_is_paired(cls, board):
        """True if any rank appears 2+ times on the board."""
        if len(board) < 2:
            return False
        ranks = [cls._card_rank(c) for c in board if cls._card_rank(c)]
        return len(set(ranks)) < len(ranks)

    @classmethod
    def _hero_has_flush(cls, hero, board):
        """
        True if hero participates in a 5+ same-suit combo across hole +
        board. Pure board-flush (4-flush + hero blank) does not count
        because hero doesn't actually have the flush in hand.
        """
        if len(hero) != 2:
            return False
        suits = {}
        for c in list(hero) + list(board):
            s = cls._card_suit(c)
            if s:
                suits[s] = suits.get(s, 0) + 1
        for suit, count in suits.items():
            if count >= 5:
                hero_has_suit = any(cls._card_suit(c) == suit for c in hero)
                if hero_has_suit:
                    return True
        return False

    # Hand class constants for _evaluate_hand_class. Ordered ascending
    # so callers can compare with `<` / `>=`.
    HAND_HIGH_CARD = 0
    HAND_PAIR = 1
    HAND_TWO_PAIR = 2
    HAND_TRIPS = 3
    HAND_STRAIGHT = 4
    HAND_FLUSH = 5
    HAND_FULL_HOUSE = 6
    HAND_QUADS = 7
    HAND_STRAIGHT_FLUSH = 8

    HAND_CLASS_NAMES = {
        HAND_HIGH_CARD: "high-card",
        HAND_PAIR: "pair",
        HAND_TWO_PAIR: "two-pair",
        HAND_TRIPS: "trips",
        HAND_STRAIGHT: "straight",
        HAND_FLUSH: "flush",
        HAND_FULL_HOUSE: "full-house",
        HAND_QUADS: "quads",
        HAND_STRAIGHT_FLUSH: "straight-flush",
    }

    @classmethod
    def _evaluate_hand_class(cls, hero, board):
        """
        Classify hero's best 5-card hand from hole+board into one of:
        HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE,
        QUADS, STRAIGHT_FLUSH.

        Returns one of the HAND_* integer constants. Higher = stronger.

        IMPORTANT: this evaluates HERO's best hand, not the board alone
        and not the nut-relative ranking. PAIR returns true for both
        "hero matches a board card" (top pair etc) AND "pocket pair"
        (overpair etc). The danger filters that use this should still
        check `_hero_has_overpair` separately if they need that
        distinction.

        Returns HAND_HIGH_CARD if cards are missing or unparseable.
        """
        if len(hero) != 2:
            return cls.HAND_HIGH_CARD
        all_cards = list(hero) + list(board)
        ranks = [cls._card_rank(c) for c in all_cards]
        suits = [cls._card_suit(c) for c in all_cards]
        if any(r is None for r in ranks) or any(s is None for s in suits):
            return cls.HAND_HIGH_CARD
        if len(all_cards) < 5:
            # Not enough cards for a 5-card hand class — return best
            # available shape (pair / two pair / trips / quads from
            # rank counts). No straight/flush possible with <5 cards.
            from collections import Counter
            rank_counts = Counter(ranks)
            counts = sorted(rank_counts.values(), reverse=True)
            if counts[0] == 4:
                return cls.HAND_QUADS
            if counts[0] == 3 and len(counts) > 1 and counts[1] >= 2:
                return cls.HAND_FULL_HOUSE
            if counts[0] == 3:
                return cls.HAND_TRIPS
            if counts[0] == 2 and len(counts) > 1 and counts[1] == 2:
                return cls.HAND_TWO_PAIR
            if counts[0] == 2:
                return cls.HAND_PAIR
            return cls.HAND_HIGH_CARD

        # 5+ cards: check straight/flush/straight flush + n-of-a-kind
        from collections import Counter
        rank_counts = Counter(ranks)
        suit_counts = Counter(suits)

        # Flush detection
        flush_suit = None
        for s, count in suit_counts.items():
            if count >= 5:
                flush_suit = s
                break
        is_flush = flush_suit is not None

        # Straight detection (handles wheel A-2-3-4-5)
        unique_ranks = sorted(set(ranks))
        is_straight = False
        straight_high = 0
        for i in range(len(unique_ranks) - 4):
            window = unique_ranks[i:i+5]
            if window[4] - window[0] == 4 and len(set(window)) == 5:
                is_straight = True
                straight_high = window[4]
        # Wheel: A-2-3-4-5 → ranks 14,2,3,4,5
        if not is_straight and 14 in unique_ranks:
            wheel = [r if r != 14 else 1 for r in unique_ranks]
            wheel = sorted(set(wheel))
            for i in range(len(wheel) - 4):
                w = wheel[i:i+5]
                if w[4] - w[0] == 4 and len(set(w)) == 5:
                    is_straight = True
                    straight_high = max(straight_high, w[4])

        # Straight flush: do we have 5 cards of one suit that also form a straight?
        if is_flush and is_straight:
            flush_ranks = sorted(set(
                ranks[i] for i in range(len(ranks)) if suits[i] == flush_suit
            ))
            sf_found = False
            for i in range(len(flush_ranks) - 4):
                w = flush_ranks[i:i+5]
                if w[4] - w[0] == 4 and len(set(w)) == 5:
                    sf_found = True
                    break
            # Wheel straight flush
            if not sf_found and 14 in flush_ranks:
                wheel_fr = sorted(set(1 if r == 14 else r for r in flush_ranks))
                for i in range(len(wheel_fr) - 4):
                    w = wheel_fr[i:i+5]
                    if w[4] - w[0] == 4 and len(set(w)) == 5:
                        sf_found = True
                        break
            if sf_found:
                return cls.HAND_STRAIGHT_FLUSH

        # n-of-a-kind
        counts = sorted(rank_counts.values(), reverse=True)
        if counts[0] == 4:
            return cls.HAND_QUADS
        if counts[0] == 3 and len(counts) > 1 and counts[1] >= 2:
            return cls.HAND_FULL_HOUSE
        if is_flush:
            return cls.HAND_FLUSH
        if is_straight:
            return cls.HAND_STRAIGHT
        if counts[0] == 3:
            return cls.HAND_TRIPS
        if counts[0] == 2 and len(counts) > 1 and counts[1] == 2:
            return cls.HAND_TWO_PAIR
        if counts[0] == 2:
            return cls.HAND_PAIR
        return cls.HAND_HIGH_CARD

    @classmethod
    def _hero_can_have_boat(cls, hero, board):
        """
        True if hero's hand class could include trips or full house given
        the visible board.

        Used to gate flush-fold filters: if hero might have a boat or
        better, the filter should NOT fold the recommendation. Three
        ways to have trips+ at this point:
          (a) Pocket pair → set on the flop, full house if board pairs
              another rank later.
          (b) Hero card matches a paired board rank → trips, possibly
              full house if hero pair+matching board.
          (c) Hero card matches a tripled board rank → quads.
        """
        if len(hero) != 2:
            return False
        r1 = cls._card_rank(hero[0])
        r2 = cls._card_rank(hero[1])
        if r1 is None or r2 is None:
            return False
        if r1 == r2:
            return True  # pocket pair → at least a set if board has matching
        rank_counts = {}
        for c in board:
            r = cls._card_rank(c)
            if r is not None:
                rank_counts[r] = rank_counts.get(r, 0) + 1
        for hr in (r1, r2):
            if rank_counts.get(hr, 0) >= 2:
                return True  # trips or boat via matched paired board
        return False

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
        # Only fires on TURN/RIVER — flops can't have a 4-card straight.
        if (phase in ("TURN", "RIVER")
                and self._hero_has_overpair(hero, board)
                and self._board_has_4card_straight(board)
                and bet_ratio >= 0.20):
            return ("FOLD", f"overpair on 4-straight board facing {bet_ratio:.0%}-pot")

        # ── Filter 2: Overpair on 4-flush board facing aggression ──
        # No specific seed hand yet, but the same equity-vs-action-range
        # gap applies. KK on a 4-flush facing a raise has worse equity
        # than the model thinks. Conservative same-shape filter.
        # Fires only on TURN/RIVER (a flop can't have 4 of one suit).
        if (phase in ("TURN", "RIVER")
                and self._hero_has_overpair(hero, board)
                and self._board_has_4card_flush(board)
                and bet_ratio >= 0.20):
            return ("FOLD", f"overpair on 4-flush board facing {bet_ratio:.0%}-pot")

        # ── Filter 5: One-pair-or-less on COORDINATED river facing big bet ──
        # Catches the "top-pair-good-kicker calldown on a scary board"
        # leak class. On the river there are no more draws, and a single
        # pair vs an action-narrowed range that's bet big on a coordinated
        # board is way behind the equity model's hand-vs-random estimate.
        #
        # Conservative — requires ALL of:
        #   - phase is RIVER (no draws to consider)
        #   - hero has at most a pair (not two pair, trips, straight, etc)
        #   - facing >= 75% pot bet (clear value sizing)
        #   - board has 4-card straight OR 3+ card flush (coordinated)
        #
        # The coordination requirement avoids false-positiving on TPTK
        # facing a value bet on a dry board (e.g. QcAc on 8-7-Q-T-2 in
        # hand 2379771919, which we verified is a +EV call). Only fires
        # when the board itself screams "many hands beat one pair."
        if (phase == "RIVER"
                and bet_ratio >= 0.75
                and self._evaluate_hand_class(hero, board) <= self.HAND_PAIR
                and (self._board_has_4card_straight(board)
                     or self._board_has_3card_flush(board))):
            return ("FOLD",
                    f"one-pair-or-less on coordinated river facing {bet_ratio:.0%}-pot")

        # ── Filter 4: Flush on a paired board with no boat possibility ──
        # Seed hand 2379447781 (Unibet replay test, 2026-04-08): QcJc BTN
        # on Kc Th Td 3c 8c. Hero has K-high flush (own QcJc + board's
        # Kc 3c 8c = 5 clubs, K high). Board is paired (Th Td). Villain
        # raised river to 281 cents into a 129-cent pot — a 218% pot bet.
        # That action sequence + paired-board texture + 2x pot raise =
        # villain's range is heavily weighted toward boats and trips.
        # Hero has neither a T nor a pocket pair, so hero CANNOT have a
        # boat. Best case is just a flush, which loses to every full
        # house in villain's range. Equity model overestimates because
        # it doesn't condition on the action.
        #
        # Filter only fires when:
        #   - Hero actually has a flush (not blank on a 4-flush board)
        #   - Board is paired (boats are reachable for villain)
        #   - Hero CANNOT have a boat (so we don't fold a boat by mistake)
        #   - Facing meaningful sizing (>= 30% pot)
        if (phase in ("TURN", "RIVER")
                and self._hero_has_flush(hero, board)
                and self._board_is_paired(board)
                and not self._hero_can_have_boat(hero, board)
                and bet_ratio >= 0.30):
            return ("FOLD",
                    f"flush on paired board, no boat possibility, "
                    f"facing {bet_ratio:.0%}-pot")

        # ── Filter 3: Overpair on 3-flush board facing LARGE aggression ──
        # Seed hand 2379414698 (Unibet replay test, 2026-04-08): KK on
        # 9h 6h 2h flop with hero holding Kh (1 blocker), facing a
        # 9x-pot bet. Equity model said ~70% (KK vs random); in reality
        # villain's huge overbet on a flush-completing board narrows
        # their range to flushes/sets where KK has 10-15%.
        #
        # 3-flush is a WEAKER danger signal than 4-flush — flush is only
        # made if villain has 2 cards of that suit (~5% of random
        # hands). Threshold compensates by requiring much bigger sizing:
        #   - flop:        >= 150% pot (clear overbet, screams "I have it")
        #   - turn/river:  >= 50% pot (more committed action)
        # Won't fire on normal c-bets at any street.
        if (self._hero_has_overpair(hero, board)
                and self._board_has_3card_flush(board)
                and not self._board_has_4card_flush(board)):  # caught above
            min_ratio = 1.50 if phase == "FLOP" else 0.50
            if bet_ratio >= min_ratio:
                return ("FOLD",
                        f"overpair on 3-flush board ({phase.lower()}) "
                        f"facing {bet_ratio:.0%}-pot")

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
