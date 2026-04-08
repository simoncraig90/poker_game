"""
Unibet poker advisor using WebSocket game state.
100% accurate card detection via protocol interception.

The core recommendation logic lives in AdvisorStateMachine.
This file handles I/O: WS reader, overlay subprocess, DB logging.
"""
import os
import sys
import json
import time
import subprocess

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

# Tee stdout/stderr to log file so the session can be inspected after the fact.
# Same pattern as auto_player.py — written fresh each launch.
LOG_PATH = os.path.join(ROOT, "advisor_ws.log")

class _TeeWriter:
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
            try:
                s.flush()
            except Exception:
                pass

_log_file = open(LOG_PATH, "w", encoding="utf-8")
sys.stdout = _TeeWriter(sys.__stdout__, _log_file)
sys.stderr = _TeeWriter(sys.__stderr__, _log_file)

from unibet_ws import UnibetWSReader
from advisor_state_machine import AdvisorStateMachine


def main():
    print("=" * 50)
    print("  UNIBET ADVISOR — WebSocket (100% accurate)")
    print("=" * 50)

    # Load new postflop engine (flop CFR + turn/river rules)
    from strategy.postflop_engine import PostflopEngine
    try:
        postflop = PostflopEngine()
    except Exception as e:
        print(f"[Advisor] PostflopEngine failed: {e}")
        postflop = None

    # Load the base advisor for equity + board danger
    from advisor import Advisor as BaseAdvisor
    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
    print(f"[Advisor] Base advisor loaded")

    # Load preflop chart
    from preflop_chart import preflop_advice
    from opponent_tracker import OpponentTracker
    from session_logger import SessionLogger
    from hand_db import HandDB

    db = HandDB()
    tracker = OpponentTracker(db=db)  # persistent across sessions
    logger = SessionLogger()
    print(f"[Advisor] Opponent tracker (persistent) + session logger + DB active ({db.db_path})")

    # Load board danger
    try:
        from advisor import assess_board_danger
    except ImportError:
        assess_board_danger = lambda h, b: {"warnings": []}

    # Create the state machine (all recommendation logic lives here)
    sm = AdvisorStateMachine(
        base_advisor=base,
        preflop_advice_fn=preflop_advice,
        postflop_engine=postflop,
        tracker=tracker,
        bb_cents=4,
    )
    print("[Advisor] State machine initialized")

    # Kill any orphaned overlay processes before starting a new one
    try:
        subprocess.run(
            ["taskkill", "/F", "/FI", "WINDOWTITLE eq Poker Advisor"],
            capture_output=True, text=True
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Get-Process python* -ErrorAction SilentlyContinue | "
             "Where-Object { $_.MainWindowTitle -match 'Poker Advisor' -or "
             "(Get-WmiObject Win32_Process -Filter \"ProcessId=$($_.Id)\").CommandLine -match 'overlay_process' } | "
             "Stop-Process -Force"],
            capture_output=True, text=True, timeout=5
        )
    except Exception:
        pass

    # Start overlay subprocess
    overlay_script = os.path.join(VISION_DIR, "overlay_process.py")
    overlay = subprocess.Popen(
        [sys.executable, "-u", overlay_script],
        stdin=subprocess.PIPE, text=True
    )
    print(f"[Advisor] Overlay started (PID {overlay.pid})")

    def send_overlay(cards="", info="", rec="", rec_bg="#1a1a2e", rec_fg="#ffd700", opponent=""):
        msg = {"cards": cards, "info": info, "rec": rec,
               "rec_bg": rec_bg, "rec_fg": rec_fg, "opponent": opponent}
        if overlay.poll() is None:
            try:
                overlay.stdin.write(json.dumps(msg) + "\n")
                overlay.stdin.flush()
                print(f"[OVERLAY-SEND] {json.dumps(msg)}")
            except Exception as e:
                print(f"[OVERLAY-ERROR] write failed: {type(e).__name__}: {e}")
        else:
            print(f"[OVERLAY-DEAD] subprocess exited (code={overlay.poll()}), msg dropped: {json.dumps(msg)}")

    # Start WebSocket reader
    reader = UnibetWSReader()

    # Counters for diagnostic visibility
    _state_count = [0]
    _suppressed_count = [0]
    import traceback as _tb

    def on_state(state):
        _state_count[0] += 1
        try:
            # Update tracker + logger (side effects)
            tracker.update(state)
            logger.update(state)

            # Compact state log every Nth call so we can correlate frames to recs
            if _state_count[0] % 20 == 0:
                hero = state.get("hero_cards", [])
                board = state.get("board_cards", [])
                print(f"[STATE #{_state_count[0]}] hero={hero} board={board} "
                      f"phase={state.get('phase')} pos={state.get('position')} "
                      f"facing={state.get('facing_bet')} call={state.get('call_amount')} "
                      f"pot={state.get('pot')} stack={state.get('hero_stack')} "
                      f"(suppressed={_suppressed_count[0]})")

            # Core logic — pure state machine
            out = sm.process_state(state)
            if out is None:
                _suppressed_count[0] += 1
                return

            # Build combined opponent string: villain type + table summary
            # e.g. "TAG | FISHY table" or just "FISHY table" if no specific villain.
            opp_parts = []
            if out.opponent_type and out.opponent_type != "UNKNOWN":
                opp_parts.append(out.opponent_type)
            try:
                tsum = tracker.get_table_summary(
                    hero_seat=state.get("hero_seat", -1),
                    players=state.get("players", []),
                )
                if tsum:
                    opp_parts.append(tsum)
            except Exception as e:
                print(f"[TRACKER-ERROR] get_table_summary failed: {e}")
            opp_display = " | ".join(opp_parts)

            # Send to overlay
            if out.should_update_overlay:
                send_overlay(out.cards_text, out.info, out.action, out.rec_bg,
                             out.rec_fg, opp_display)

            # Console log
            if out.log_line:
                print(out.log_line)

            # Verbose REC dump for diagnostic — full action context
            if out.action:
                print(f"[REC] action={out.action!r} phase={out.phase} "
                      f"eq={out.equity:.3f} pot={out.pot} call={out.call_amount} "
                      f"facing={out.facing_bet} stack={out.hero_stack} "
                      f"source={getattr(out, 'source', '?')} "
                      f"villain={out.opponent_type!r} table={opp_display!r} "
                      f"hero={state.get('hero_cards')} board={state.get('board_cards')} "
                      f"players={state.get('players', [])}")

            # DB logging
            if out.hand_id and out.phase:
                try:
                    db.log_hand_start(out.hand_id, state["hero_cards"], out.position, out.hero_stack)
                    db.log_street(out.hand_id, out.phase, out.board, out.pot,
                                  out.facing_bet, out.call_amount, out.hero_stack,
                                  out.action, out.equity, out.source)
                except Exception as e:
                    print(f"[DB-ERROR] {type(e).__name__}: {e}")

            # Session logger
            logger.update(state, {"action": out.action, "equity": out.equity,
                                  "opponent_type": out.opponent_type})
        except Exception as e:
            # No more silent failures — full traceback to log so we can diagnose
            print(f"[ON_STATE-ERROR] {type(e).__name__}: {e}")
            print(f"[ON_STATE-ERROR] state snapshot: {json.dumps(state, default=str)[:500]}")
            _tb.print_exc()

    reader.on_state_change(on_state)
    reader.start()

    print("\nListening. Play hands on Unibet.")
    print("Ctrl+C to stop.\n")

    import atexit
    def cleanup():
        try:
            tracker.flush()  # save opponent stats to DB
        except Exception:
            pass
        try:
            overlay.terminate()
            overlay.kill()
        except Exception:
            pass
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "Get-Process python* -ErrorAction SilentlyContinue | "
                 "Where-Object { (Get-WmiObject Win32_Process -Filter "
                 "\"ProcessId=$($_.Id)\").CommandLine -match 'overlay_process' } | "
                 "Stop-Process -Force"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass
    atexit.register(cleanup)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        reader.stop()
        cleanup()


if __name__ == "__main__":
    main()
