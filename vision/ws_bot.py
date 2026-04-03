"""
WebSocket bot that plays the lab client via the server API.

Connects to ws://localhost:9100, reads game state from messages,
makes decisions via strategy, and sends actions back.

This is the "smart" bot for testing — no OCR needed.
The screen-reading bot (client_bot.py) is for detection testing.

Usage:
  python vision/ws_bot.py                    # play as seat 0
  python vision/ws_bot.py --strategy cfr     # use CFR strategy
  python vision/ws_bot.py --seat 0           # specific seat
  python vision/ws_bot.py --hands 100        # stop after N hands
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

from websocket import create_connection, WebSocketTimeoutException

# ── Strategy Helpers ─────────────────────────────────────────────────────

def evaluate_hand_strength(cards, board, phase):
    """Simple hand strength heuristic (0-1). Port of JS version."""
    if not cards or len(cards) < 2:
        return 0.5
    c1, c2 = cards[0], cards[1]
    r1, r2 = c1["rank"], c2["rank"]
    suited = c1["suit"] == c2["suit"]
    pair = r1 == r2
    high = max(r1, r2)
    gap = abs(r1 - r2)

    pf = 0
    if pair:
        pf = 0.5 + (r1 / 14) * 0.5
    else:
        pf = (high / 14) * 0.4
        if suited: pf += 0.08
        if gap <= 1: pf += 0.06
        if gap <= 3: pf += 0.03
        if r1 >= 10 and r2 >= 10: pf += 0.15
        if high == 14: pf += 0.1

    if phase == "PREFLOP":
        return min(1.0, pf)

    board_ranks = [c["rank"] for c in board] if board else []
    post = pf
    if r1 in board_ranks: post += 0.25
    if r2 in board_ranks: post += 0.20
    if r1 in board_ranks and r2 in board_ranks and not pair: post += 0.20
    if pair and r1 in board_ranks: post += 0.35
    if pair and board_ranks and r1 > max(board_ranks): post += 0.15
    return min(1.0, post)


def tag_decide(state, seat):
    """TAG strategy decision."""
    hand = state.get("hand")
    if not hand:
        return None
    legal = hand.get("legalActions")
    if not legal or hand.get("actionSeat") != seat:
        return None

    actions = legal["actions"]
    if not actions:
        return None
    if len(actions) == 1:
        return {"action": actions[0]}

    seat_state = state["seats"].get(str(seat), state["seats"].get(seat, {}))
    cards = seat_state.get("holeCards", [])
    board = hand.get("board", [])
    phase = hand.get("phase", "PREFLOP")
    pot = hand.get("pot", 0)
    call_amt = legal.get("callAmount", 0)
    min_bet = legal.get("minBet", 0)
    min_raise = legal.get("minRaise", 0)
    max_raise = legal.get("maxRaise", 0)
    stack = seat_state.get("stack", 0)

    strength = evaluate_hand_strength(cards, board, phase)

    if phase == "PREFLOP":
        if strength > 0.7 and "RAISE" in actions:
            amt = max(min_raise, min(min_raise + int(pot * 0.5), max_raise))
            return {"action": "RAISE", "amount": amt}
        if strength > 0.35 and "CALL" in actions:
            return {"action": "CALL"}
        if strength > 0.35 and "CHECK" in actions:
            return {"action": "CHECK"}
        return {"action": "FOLD"}

    if strength > 0.7:
        if "RAISE" in actions:
            amt = max(min_raise, min(min_raise + int(pot * 0.75), max_raise))
            return {"action": "RAISE", "amount": amt}
        if "BET" in actions:
            amt = max(min_bet, min(int(pot * 0.66), stack))
            return {"action": "BET", "amount": amt}
        if "CALL" in actions:
            return {"action": "CALL"}
        return {"action": "CHECK"}

    if strength > 0.35:
        if "CHECK" in actions:
            return {"action": "CHECK"}
        if "CALL" in actions and call_amt < pot * 0.5:
            return {"action": "CALL"}
        return {"action": "FOLD"}

    if "CHECK" in actions:
        return {"action": "CHECK"}
    return {"action": "FOLD"}


def fish_decide(state, seat):
    """FISH strategy — calls everything."""
    hand = state.get("hand")
    if not hand or hand.get("actionSeat") != seat:
        return None
    legal = hand.get("legalActions")
    if not legal:
        return None
    actions = legal["actions"]
    if not actions:
        return None
    if "CALL" in actions:
        return {"action": "CALL"}
    if "CHECK" in actions:
        return {"action": "CHECK"}
    return {"action": "FOLD"}


STRATEGIES = {
    "tag": tag_decide,
    "fish": fish_decide,
}


# ── Bot ──────────────────────────────────────────────────────────────────

def run_bot(args):
    seat = args.seat
    strategy = STRATEGIES.get(args.strategy, tag_decide)
    max_hands = args.hands

    print(f"WS Bot — seat {seat}, strategy={args.strategy}, max_hands={max_hands}")
    print("Connecting to ws://localhost:9100...")

    ws = create_connection("ws://localhost:9100")
    ws.settimeout(1.0)

    # Read welcome
    msg = json.loads(ws.recv())
    session_id = msg.get("sessionId", "?")
    state = msg.get("state", {})
    print(f"  Session: {session_id}")

    # Check if we're seated
    seats = state.get("seats", {})
    seat_state = seats.get(str(seat), seats.get(seat, {}))
    if seat_state.get("status") != "OCCUPIED":
        print(f"  Seating at seat {seat}...")
        ws.send(json.dumps({
            "id": "seat-hero",
            "cmd": "SEAT_PLAYER",
            "payload": {"seat": seat, "name": "BotPlayer", "buyIn": 1000}
        }))
        resp = json.loads(ws.recv())
        print(f"  Seated: {resp.get('ok', resp)}")
    else:
        name = seat_state.get("player", {}).get("name", "?")
        print(f"  Already seated: {name}")

    hands_played = 0
    actions_taken = 0
    start_time = time.time()

    print(f"\n  Playing... (Ctrl+C to stop)\n")

    try:
        while max_hands == 0 or hands_played < max_hands:
            # Read messages
            try:
                raw = ws.recv()
                msg = json.loads(raw)
            except WebSocketTimeoutException:
                continue
            except Exception as e:
                print(f"  Error: {e}")
                break

            # Update state
            if msg.get("state"):
                state = msg["state"] if "seats" in msg.get("state", {}) else state

            # Handle broadcast events
            events = msg.get("events", [])
            for evt in events:
                if evt.get("type") == "HAND_END":
                    hands_played += 1
                    if hands_played % 10 == 0:
                        elapsed = time.time() - start_time
                        hps = hands_played / elapsed if elapsed > 0 else 0
                        print(f"  {hands_played} hands | {actions_taken} actions | {hps:.1f} hands/sec")

            # Try to act if it's our turn
            if msg.get("state") and "seats" in msg.get("state", {}):
                state = msg["state"]

            hand = state.get("hand")
            if hand and hand.get("actionSeat") == seat and hand.get("phase") != "COMPLETE":
                decision = strategy(state, seat)
                if decision:
                    # Add humanized delay
                    delay = random.uniform(0.3, 2.0)
                    time.sleep(delay)

                    payload = {"seat": seat, "action": decision["action"]}
                    if "amount" in decision:
                        payload["amount"] = decision["amount"]

                    ws.send(json.dumps({
                        "id": f"act-{actions_taken}",
                        "cmd": "PLAYER_ACTION",
                        "payload": payload
                    }))
                    actions_taken += 1

                    # Read response
                    try:
                        resp = json.loads(ws.recv())
                        if resp.get("state"):
                            state = resp["state"]
                    except WebSocketTimeoutException:
                        pass

    except KeyboardInterrupt:
        pass

    elapsed = time.time() - start_time
    print(f"\n  Done: {hands_played} hands, {actions_taken} actions in {elapsed:.0f}s")
    ws.close()


def main():
    parser = argparse.ArgumentParser(description="WebSocket bot for lab client")
    parser.add_argument("--seat", type=int, default=0)
    parser.add_argument("--strategy", default="tag", choices=list(STRATEGIES.keys()))
    parser.add_argument("--hands", type=int, default=0, help="0 = unlimited")
    run_bot(parser.parse_args())


if __name__ == "__main__":
    main()
