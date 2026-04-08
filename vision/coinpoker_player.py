"""
CoinPoker auto-player.

Architecture (clean separation):
  CoinPokerReader → AdvisorStateMachine → Humanizer → CoinPokerClicker

Unlike auto_player.py (Unibet), this version:
- Uses CDP JS .click() — no cursor movement, no focus stealing
- Multi-venue safe (won't conflict with Unibet's cursor-based clicks)
- Click verification via state-change polling
- Same humanization as Unibet (timing, mistakes, sessions)

Usage: python -u vision/coinpoker_player.py [--target=replica|live]
"""

import os
import sys
import json
import time
import threading
import random

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

# Tee output to log file
LOG_PATH = os.path.join(ROOT, "coinpoker_player.log")

class TeeWriter:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
    def flush(self):
        for s in self.streams:
            try: s.flush()
            except Exception: pass

_log_file = open(LOG_PATH, "w", encoding="utf-8")
sys.stdout = TeeWriter(sys.__stdout__, _log_file)
sys.stderr = TeeWriter(sys.__stderr__, _log_file)

from coinpoker_dom import CoinPokerReader
from coinpoker_clicker import CoinPokerClicker
from advisor_state_machine import AdvisorStateMachine
from humanizer import (
    get_think_time, SessionManager, PlayVariation,
)

# Action timer constraints (CoinPoker timer is similar to Unibet)
MAX_THINK_TIME = 6.0
MIN_THINK_TIME = 1.5


def main():
    target_match = "coinpoker"
    port = 9222
    for arg in sys.argv[1:]:
        if arg == "--target=replica":
            target_match = "coinpoker-replica"
            port = 9222
        elif arg == "--target=live":
            target_match = "coinpoker"
            port = 9223  # CoinPoker app debug port

    print("=" * 50)
    print(f"  COINPOKER AUTO-PLAYER")
    print(f"  Target: {target_match} on port {port}")
    print("=" * 50)

    # ── Load strategy dependencies ──
    from strategy.postflop_engine import PostflopEngine
    try:
        postflop = PostflopEngine()
    except Exception as e:
        print(f"[CP] PostflopEngine failed: {e}")
        postflop = None

    from advisor import Advisor as BaseAdvisor
    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
    print("[CP] Base advisor loaded")

    from preflop_chart import preflop_advice
    from opponent_tracker import OpponentTracker
    from session_logger import SessionLogger
    from hand_db import HandDB
    from collusion_detector import CollusionDetector
    from bot_detector import BotDetector
    from action_inferrer import WSActionInferrer

    db = HandDB()
    tracker = OpponentTracker(db=db)
    collusion = CollusionDetector(db=db)
    bots = BotDetector(db=db)
    action_inf = WSActionInferrer()
    logger = SessionLogger()

    sm = AdvisorStateMachine(
        base_advisor=base,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        tracker=tracker,
        bb_cents=4,
    )
    session = SessionManager()
    variation = PlayVariation(mistake_rate=0.0)  # disabled — was converting folds to calls
    print(f"[CP] Session length: {session.session_length/60:.0f} min")

    # ── Reader + Clicker ──
    reader = CoinPokerReader(port=port, target_match=target_match, poll_hz=4)
    clicker = CoinPokerClicker(port=port, target_match=target_match)

    # Verify connection by reading initial state
    initial = reader.get_state()
    if initial is None:
        print(f"[CP] FATAL: cannot read state. Is target '{target_match}' open on port {port}?")
        return
    print(f"[CP] Initial state: hero={initial.get('hero_cards')} pot={initial.get('pot')} hand={initial.get('hand_id')}")

    # ── Action execution ──
    current_action_id = [0]
    pending_action = [None]
    last_acted_key = [None]
    latest_state = [initial]

    def execute_action(out, action_id, pre_state):
        action = out.action
        phase = out.phase

        # Apply play variation (currently disabled but hook is here)
        action, was_mistake = variation.maybe_modify_action(
            action, out.equity, phase, out.facing_bet
        )
        if was_mistake:
            print(f"[CP] VARIATION: {out.action} -> {action}")

        # Humanized think time (1.5-6.0s)
        think = get_think_time(phase, action)
        base_t = 1.5
        humanized = think * 0.4
        think = base_t + humanized + random.uniform(0, 1.0)
        if "FOLD" in action.upper():
            think = max(1.2, min(think, 4.0))
        else:
            think = max(MIN_THINK_TIME, min(think, MAX_THINK_TIME))

        print(f"[CP] Thinking {think:.1f}s for {action}...")

        # Cancellable think time
        elapsed = 0
        while elapsed < think:
            chunk = min(0.3, think - elapsed)
            time.sleep(chunk)
            elapsed += chunk
            if current_action_id[0] != action_id:
                print(f"[CP] Cancelled (superseded)")
                pending_action[0] = None
                return

        # Determine action type and amount
        action_upper = action.upper()
        amount = None
        if "FOLD" in action_upper:
            cmd = "FOLD"
        elif "CHECK" in action_upper:
            cmd = "CHECK"
        elif "CALL" in action_upper:
            cmd = "CALL"
        elif "RAISE" in action_upper:
            cmd = "RAISE"
            import re as _re
            m = _re.search(r'(\d+\.\d+)', action)
            if m:
                amount = float(m.group(1))
        elif "BET" in action_upper:
            cmd = "BET"
            import re as _re
            m = _re.search(r'(\d+\.\d+)', action)
            if m:
                amount = float(m.group(1))
        else:
            cmd = "CHECK"

        # Click via clicker (CDP JS .click())
        success = clicker.click(cmd, amount=amount)
        print(f"[CP] {cmd}{f' {amount}' if amount else ''} -> {'OK' if success else 'FAILED'}")

        # Verify by checking WS state changed within 2.5s
        verify_start = time.time()
        state_changed = False
        while time.time() - verify_start < 2.5:
            cur = latest_state[0] or {}
            if (cur.get("hand_id") != pre_state.get("hand_id") or
                cur.get("pot") != pre_state.get("pot") or
                cur.get("facing_bet") != pre_state.get("facing_bet") or
                cur.get("hero_turn") != pre_state.get("hero_turn") or
                cur.get("phase") != pre_state.get("phase")):
                state_changed = True
                break
            time.sleep(0.1)

        if not state_changed:
            print(f"[CP] WARN: state didn't change after click — retrying once")
            time.sleep(0.3)
            clicker.click(cmd, amount=amount)

        session.record_hand()
        pending_action[0] = None

    # ── State change handler ──
    def on_state(state):
        latest_state[0] = state
        tracker.update(state)
        logger.update(state)

        # Detect new hand → notify collusion detector
        hand_id = state.get('hand_id')
        if hand_id and hand_id != getattr(on_state, '_last_hand', None):
            seated = [p for p in state.get('players', []) if p]
            collusion.hand_started(hand_id, seated)
            on_state._last_hand = hand_id

        # Infer per-player actions and feed both detectors
        actions = action_inf.update(state)
        if actions:
            for actor, action_type, amount in actions:
                collusion.record_action(actor, action_type, amount)
                bots.record_action(actor, action_type, amount)

        out = sm.process_state(state)
        if out is None:
            return

        if out.log_line:
            print(out.log_line)

        if out.hand_id and out.phase:
            try:
                db.log_hand_start(out.hand_id, state["hero_cards"], out.position, out.hero_stack)
                db.log_street(out.hand_id, out.phase, out.board, out.pot,
                              out.facing_bet, out.call_amount, out.hero_stack,
                              out.action, out.equity, out.source)
            except Exception:
                pass

        logger.update(state, {"action": out.action, "equity": out.equity})

        if not out.action or "Waiting" in out.cards_text:
            return

        # Only act if hero_turn is True (button is showing)
        if not state.get("hero_turn", False):
            return

        if session.should_end_session():
            print("[CP] Session complete.")
            return

        # Dedupe: only act once per (hand, phase, facing, call) combination
        action_key = (out.hand_id, out.phase, out.facing_bet, out.call_amount)
        if action_key == last_acted_key[0]:
            return
        last_acted_key[0] = action_key

        # Don't fire new action if pending action is for same hand+phase
        if pending_action[0] and pending_action[0].get('key', (None, None))[:2] == action_key[:2]:
            return

        current_action_id[0] += 1
        aid = current_action_id[0]
        pending_action[0] = {'id': aid, 'key': action_key}
        t = threading.Thread(target=execute_action, args=(out, aid, dict(state)), daemon=True)
        t.start()

    reader.on_state_change(on_state)
    reader.start()

    print("\n[CP] LIVE — CoinPoker auto-player active")
    print("[CP] Ctrl+C to stop\n")

    import atexit
    def cleanup():
        try:
            tracker.flush()
            collusion.flush()
            bots.flush()
        except Exception:
            pass
        try:
            reader.stop()
        except Exception:
            pass
    atexit.register(cleanup)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[CP] Stopping...")
        cleanup()


if __name__ == "__main__":
    main()
