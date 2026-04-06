"""
Unibet poker advisor using WebSocket game state.
100% accurate card detection via protocol interception.
"""
import os
import sys
import json
import time
import subprocess
import threading

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

from unibet_ws import UnibetWSReader


def card_display(card):
    """Pretty format a card: 'Ah' -> 'Ah'"""
    if not card or len(card) < 2:
        return "??"
    return card[0].upper() + card[1].lower()


def main():
    print("=" * 50)
    print("  UNIBET ADVISOR — WebSocket (100% accurate)")
    print("=" * 50)

    # Load CFR strategy adapter
    from cfr_adapter import CFRAdapter
    try:
        cfr = CFRAdapter()
    except Exception as e:
        print(f"[Advisor] CFR failed: {e}")
        cfr = None

    # Load the base advisor for equity + board danger
    from advisor import Advisor as BaseAdvisor
    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
    print(f"[Advisor] Base advisor loaded")

    # Load preflop chart
    from preflop_chart import preflop_advice
    from opponent_tracker import OpponentTracker
    from session_logger import SessionLogger

    tracker = OpponentTracker()
    logger = SessionLogger()
    print("[Advisor] Opponent tracker + session logger active")

    # Load equity model
    equity_fn = None
    try:
        from train_equity import EquityModel
        import torch
        model_path = os.path.join(VISION_DIR, "models", "equity_model.pt")
        if os.path.exists(model_path):
            eq_model = EquityModel()
            eq_model.load_state_dict(torch.load(model_path, map_location='cpu', weights_only=True))
            eq_model.eval()

            def compute_eq(hero_dicts, board_dicts, num_opp):
                features = []
                for c in hero_dicts:
                    features.extend([c['rank'] / 14.0, c['suit'] / 4.0])
                for c in board_dicts:
                    features.extend([c['rank'] / 14.0, c['suit'] / 4.0])
                while len(features) < 14:
                    features.append(0.0)
                features.append(num_opp / 5.0)
                with torch.no_grad():
                    t = torch.tensor([features], dtype=torch.float32)
                    return float(eq_model(t).item())

            equity_fn = compute_eq
            print("[Advisor] Equity model loaded")
        else:
            print("[Advisor] No equity model file")
    except Exception as e:
        print(f"[Advisor] Equity: {e}")

    # Load board danger
    try:
        from advisor import assess_board_danger
    except ImportError:
        assess_board_danger = lambda h, b: {"warnings": []}

    # Start overlay subprocess
    overlay_script = os.path.join(VISION_DIR, "overlay_process.py")
    overlay = subprocess.Popen(
        [sys.executable, "-u", overlay_script],
        stdin=subprocess.PIPE, text=True
    )
    print(f"[Advisor] Overlay started (PID {overlay.pid})")

    def send_overlay(cards="", info="", rec="", rec_bg="#1a1a2e", rec_fg="#ffd700"):
        if overlay.poll() is None:
            try:
                overlay.stdin.write(json.dumps({
                    "cards": cards, "info": info, "rec": rec,
                    "rec_bg": rec_bg, "rec_fg": rec_fg
                }) + "\n")
                overlay.stdin.flush()
            except Exception:
                pass

    # Start WebSocket reader
    reader = UnibetWSReader()
    prev_hero = []
    prev_board = []
    prev_hand_id = None
    prev_phase = None
    last_facing = None
    pending_update = None  # (state, timestamp) — buffered postflop update

    # BB/hr tracking
    import time as _time
    session_start = _time.time()
    starting_stack = None
    hands_played = 0
    bb_cents = 4  # 0.04 BB

    def on_state(state):
        nonlocal prev_hero, prev_board, prev_hand_id, prev_phase, last_facing
        nonlocal starting_stack, hands_played, pending_update

        hero = state["hero_cards"]
        board = state["board_cards"]
        hand_id = state["hand_id"]
        facing = state["facing_bet"]
        call_amt = state["call_amount"]
        phase = state["phase"]
        num_opp = state["num_opponents"]

        # Track stack for BB/hr
        hero_stack = state.get("hero_stack", 0)
        if hero_stack > 0:
            if starting_stack is None:
                starting_stack = hero_stack
        if hand_id != prev_hand_id and hand_id is not None:
            hands_played += 1

        # Update opponent tracker + session logger
        tracker.update(state)
        logger.update(state)

        if len(hero) < 2:
            if prev_hero:
                send_overlay("Waiting for cards...")
                prev_hero = []
            return

        # Skip if hero has folded
        # Postflop: don't update if facing just flipped to False (wait for bet data)
        if board and not facing and prev_board and not hand_changed:
            # Same hand, postflop, not facing — might be stale, skip
            # (the bet data arrives in a later message)
            prev_board = board[:]
            prev_phase = phase
            return

        # Update logic with buffering to prevent CHECK→CALL flash
        hand_changed = hand_id != prev_hand_id
        board_changed = board != prev_board
        phase_changed = phase != prev_phase
        facing_changed = facing and not last_facing

        # Track facing
        if hand_changed or phase_changed:
            last_facing = facing
        elif facing:
            last_facing = True

        # If board/phase changed but not facing a bet yet — buffer the update
        # (bet data arrives in a later WS message)
        if (board_changed or phase_changed) and not facing and not hand_changed and board:
            pending_update = (state.copy(), _time.time())
            prev_board = board[:]
            prev_phase = phase
            return

        # If facing_bet just became True and we have a pending update — use it now
        if facing_changed and pending_update:
            pending_update = None
            last_facing = True

        # If nothing changed and no pending, skip
        if not hand_changed and not facing_changed and not board_changed and not phase_changed:
            # Check if pending update has expired (500ms — bet would have arrived by now)
            if pending_update and _time.time() - pending_update[1] > 0.5:
                # No bet came — hero can check. Show the update now.
                pending_update = None
                # Fall through to display
            else:
                return

        facing = facing or last_facing

        prev_hero = hero[:]
        prev_board = board[:]
        prev_hand_id = hand_id
        prev_phase = phase

        hero_str = " ".join(card_display(c) for c in hero)
        board_str = " ".join(card_display(c) for c in board)
        cards_text = hero_str + ("  |  " + board_str if board_str else "")

        # BB/hr calculation
        bb_hr_str = ""
        if starting_stack is not None and hero_stack > 0:
            elapsed_hrs = max((_time.time() - session_start) / 3600, 0.01)
            profit_cents = hero_stack - starting_stack
            bb_hr = (profit_cents / bb_cents) / elapsed_hrs
            table_info = tracker.get_table_summary(state.get("hero_seat", -1), state.get("players", []))
            table_str = f" | {table_info}" if table_info else ""
            bb_hr_str = f"  [{bb_hr:+.1f} bb/hr | {hands_played}h{table_str}]"

        # Build state dict for the base advisor's recommendation engine
        pos = state.get("position", "MP")
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

        # Get recommendation from base advisor (equity + board danger)
        rec = base._get_recommendation(advisor_state)

        # Try CFR for action decision (overrides threshold logic)
        cfr_result = None
        if cfr:
            try:
                cfr_result = cfr.get_action(
                    hero, board, pos, facing, call_amt,
                    state["pot"], state.get("hero_stack", 1000),
                    phase
                )
            except Exception:
                pass

        # Override preflop with direct chart (base advisor has stale CHECK logic)
        if rec and rec.get("phase") == "PREFLOP":
            pf_direct = preflop_advice(hero[0], hero[1], pos, facing_raise=facing)
            rec["preflop"] = pf_direct

        if rec:
            phase_str = rec.get("phase", "PREFLOP")
            eq = rec.get("equity", 0.5)

            if phase_str == "PREFLOP":
                pf = rec.get("preflop", {})
                action = pf.get("action", "?")

                # BB with no raise can check. All other positions need to act.
                if not facing and action.upper() == "FOLD":
                    if pos == "BB":
                        action = "CHECK"
                    elif pos == "SB":
                        # SB needs to complete — show fold or call
                        action = "FOLD"  # chart says fold, keep it

                # Add sizing to RAISE advice
                bb = 4  # 0.04 in cents
                pot_cents = state["pot"]
                if "RAISE" in action.upper():
                    if facing and call_amt > 0:
                        # 3-bet: ~3x the raise
                        raise_to = call_amt * 3
                        action = f"RAISE to {raise_to/100:.2f}"
                    else:
                        # Open raise: 2.5x BB + 1BB per limper
                        limpers = len([b for b in state.get("bets", []) if b == bb]) - 1  # exclude BB
                        limpers = max(0, limpers)
                        raise_size = int(bb * 2.5 + bb * limpers)
                        action = f"RAISE to {raise_size/100:.2f}"
                elif "CALL" in action.upper() and call_amt > 0:
                    action = f"CALL {call_amt/100:.2f}"

                # Show CFR probabilities if available
                cfr_info = ""
                if cfr_result:
                    p = cfr_result['probs']
                    cfr_info = f"  [F:{p['fold']:.0%} C:{p['call']:.0%} R:{p['raise']:.0%}]"
                    # Use CFR action instead of chart
                    cfr_action = cfr_result['action']
                    if cfr_action == 'CHECK' and facing:
                        cfr_action = 'CALL'  # can't check facing a bet
                    if cfr_action == 'RAISE' and cfr_result.get('amount'):
                        action = f"RAISE to {cfr_result['amount']/100:.2f}"
                    elif cfr_action == 'CALL' and call_amt > 0:
                        action = f"CALL {call_amt/100:.2f}"
                    else:
                        action = cfr_action

                info = f"Equity: {eq:.0%}  |  {pf.get('hand_key', '')}  {pos}{cfr_info}{bb_hr_str}"

                rec_bg = "#1a3a1a" if "RAISE" in action.upper() or "CALL" in action.upper() else "#3a1a1a"
                if action.upper() == "CHECK":
                    rec_bg = "#1a1a3a"

                send_overlay(cards_text, info, action, rec_bg)
                print(f"[{phase}] {hero_str} | {pf.get('hand_key','')} {pos} facing={facing} chart={pf.get('action','?')} -> {action}")
                logger.update(state, {"action": action, "equity": eq, "cfr_probs": cfr_result.get("probs") if cfr_result else None})

            else:
                danger = rec.get("danger", {})
                warnings = " ".join(danger.get("warnings", [])) or "clean"
                cat = rec.get("category", "")

                # Pot odds calculation
                pot_odds_str = ""
                pot_odds = 0
                if facing and call_amt > 0 and pot_cents > 0:
                    pot_odds = call_amt / (pot_cents + call_amt)
                    pot_odds_str = f"  |  Pot odds: {pot_odds:.0%}"
                    # +EV if equity > pot odds
                    if eq > pot_odds:
                        pot_odds_str += " (+EV)"
                    else:
                        pot_odds_str += " (-EV)"

                eq_str = f"Equity: {eq:.0%}"
                if facing and adjusted_eq < eq:
                    eq_str += f" (adj: {adjusted_eq:.0%})"

                # Use CFR for postflop action if available
                if cfr_result:
                    p = cfr_result['probs']
                    cfr_info = f"  [F:{p['fold']:.0%} C:{p['call']:.0%} R:{p['raise']:.0%}]"
                    cfr_action = cfr_result['action']
                    if cfr_action == 'RAISE' and cfr_result.get('amount'):
                        action = f"RAISE to {cfr_result['amount']/100:.2f}"
                    elif cfr_action == 'CALL' and call_amt > 0:
                        action = f"CALL {call_amt/100:.2f}"
                    elif cfr_action == 'CHECK':
                        action = "CHECK"
                    else:
                        action = cfr_action
                    # Skip the threshold-based logic below

                info = f"{eq_str}  |  {cat}  |  {warnings}{pot_odds_str}{bb_hr_str}"

                pot_cents = state["pot"]
                hero_stack = state.get("hero_stack", 9999)

                # Opponent action weighting — discount equity when facing bets
                # Bigger bet = stronger opponent range = our real equity is lower
                adjusted_eq = eq
                if facing and call_amt > 0 and pot_cents > 0:
                    bet_ratio = call_amt / pot_cents  # bet size relative to pot
                    if bet_ratio > 1.0:
                        # Overbet: opponent very strong, discount 35%
                        adjusted_eq = eq * 0.65
                    elif bet_ratio > 0.66:
                        # Large bet: discount 25%
                        adjusted_eq = eq * 0.75
                    elif bet_ratio > 0.33:
                        # Medium bet: discount 15%
                        adjusted_eq = eq * 0.85
                    else:
                        # Small bet: could be bluff, discount 10%
                        adjusted_eq = eq * 0.90

                # Use adjusted equity for decisions, show raw equity on overlay
                dec_eq = adjusted_eq if facing else eq

                # Board danger adjustment
                danger_warns = danger.get("warnings", [])
                is_scary = any(w in danger_warns for w in
                    ["STRAIGHT_POSSIBLE", "FLUSH_POSSIBLE", "FLUSH_DRAW", "PAIRED"])
                big_bet = call_amt > pot_cents * 0.5 if pot_cents > 0 else False

                if not facing:
                    if dec_eq < 0.5:
                        action = "CHECK / FOLD"
                    elif dec_eq < 0.7:
                        action = "CHECK / CALL"
                    else:
                        bet_size = int(pot_cents * 0.66)
                        if is_scary and dec_eq < 0.85:
                            bet_size = int(pot_cents * 0.33)
                        bet_size = min(bet_size, hero_stack)
                        if bet_size >= hero_stack:
                            action = "BET ALL-IN"
                        else:
                            action = f"BET {bet_size/100:.2f}"
                else:
                    # Pot odds: use RAW equity for +EV check (math doesn't lie)
                    # but use DISCOUNTED equity for raise/call thresholds
                    is_plus_ev = pot_odds > 0 and eq > pot_odds

                    if is_scary and big_bet:
                        if dec_eq > 0.90:
                            action = f"RAISE to {min(int(call_amt*3), hero_stack)/100:.2f}"
                        elif is_plus_ev and dec_eq > 0.40:
                            action = f"CALL {call_amt/100:.2f}"
                        else:
                            action = "FOLD"
                    elif is_scary:
                        if dec_eq > 0.80:
                            action = f"RAISE to {min(int(call_amt*3), hero_stack)/100:.2f}"
                        elif is_plus_ev or dec_eq > 0.40:
                            action = f"CALL {call_amt/100:.2f}"
                        else:
                            action = "FOLD"
                    else:
                        if dec_eq > 0.75:
                            action = f"RAISE to {min(int(call_amt*3), hero_stack)/100:.2f}"
                        elif is_plus_ev or dec_eq > 0.35:
                            action = f"CALL {call_amt/100:.2f}"
                        else:
                            action = "FOLD"

                rec_bg = "#1a3a1a" if "CALL" in action or "RAISE" in action or "BET" in action else "#3a1a1a"
                if "CHECK" in action:
                    rec_bg = "#1a1a3a"

                send_overlay(cards_text, info, action, rec_bg)
                adj_str = f" adj:{adjusted_eq:.0%}" if facing and adjusted_eq < eq else ""
                print(f"[{phase}] {hero_str} | Board: {board_str} | Eq: {eq:.0%}{adj_str} | {action}")
                logger.update(state, {"action": action, "equity": eq, "cfr_probs": cfr_result.get("probs") if cfr_result else None})

    reader.on_state_change(on_state)
    reader.start()

    print("\nListening. Play hands on Unibet.")
    print("Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        reader.stop()
        overlay.terminate()


if __name__ == "__main__":
    main()
