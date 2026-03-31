"""
Real-time PokerStars table reader.
Captures the screen continuously, detects game state, outputs changes.
Optionally feeds into poker-lab engine via WebSocket bridge.

Usage:
  python vision/live.py              # standalone mode (print only)
  python vision/live.py --bridge     # connect to poker-lab engine
"""

import mss
import cv2
import numpy as np
import time
import json
import sys
import os
import argparse

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(__file__))
from detect import read_text_regions, find_dollar_amounts, find_pot, find_player_names, find_dealer_button, find_cards_by_color, find_action_buttons
from card_id import identify_cards


def capture_screen():
    """Capture the full screen."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def find_table_region(frame):
    """Find the PokerStars table by green felt detection."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([35, 80, 60])
    upper = np.array([75, 255, 200])
    mask = cv2.inRange(hsv, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < frame.shape[0] * frame.shape[1] * 0.03:
        return None
    x, y, w, h = cv2.boundingRect(largest)
    return (x, y, w, h)


def crop_table(frame, region):
    """Crop table region with padding."""
    x, y, w, h = region
    pad = 50
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(frame.shape[1], x + w + pad)
    y2 = min(frame.shape[0], y + h + pad * 3)
    return frame[y1:y2, x1:x2]


def extract_game_state(table_img):
    """Extract full game state from a table image."""
    h, w = table_img.shape[:2]

    texts = read_text_regions(table_img)
    amounts = find_dollar_amounts(texts)
    pot = find_pot(texts, h)
    players = find_player_names(texts, amounts, w, h)
    cards = find_cards_by_color(table_img)
    actions = find_action_buttons(texts)
    dealer = find_dealer_button(table_img)

    # Identify actual cards (rank + suit)
    board_ids = [label for label, _ in identify_cards(table_img, cards["board"])] if cards["board"] else []
    hero_ids = [label for label, _ in identify_cards(table_img, cards["hero"])] if cards["hero"] else []

    state = {
        "players": [{
            "name": p["name"],
            "stack": p["stack"],
            "position": {"x": p["cx"], "y": p["cy"]},
        } for p in players],
        "pot": pot["amount"] if pot and "amount" in pot else None,
        "board_cards": board_ids,
        "hero_cards": hero_ids,
        "hero_turn": len(actions) > 0,
        "actions": [a["action"] for a in actions],
        "dealer_button": {"x": dealer["cx"], "y": dealer["cy"]} if dealer else None,
        "timestamp": time.time(),
    }
    return state


def states_differ(old, new):
    """Check if game state has meaningfully changed."""
    if old is None:
        return True
    if len(old.get("players", [])) != len(new.get("players", [])):
        return True
    if old.get("pot") != new.get("pot"):
        return True
    if old.get("board_cards") != new.get("board_cards"):
        return True
    if old.get("hero_cards") != new.get("hero_cards"):
        return True
    if old.get("hero_turn") != new.get("hero_turn"):
        return True
    old_stacks = {p["name"]: p["stack"] for p in old.get("players", [])}
    new_stacks = {p["name"]: p["stack"] for p in new.get("players", [])}
    if old_stacks != new_stacks:
        return True
    return False


def print_state(state):
    """Print a game state summary."""
    print(f"  Pot: ${state['pot']:.2f}" if state["pot"] else "  Pot: -")
    for p in state["players"]:
        print(f"  {p['name']}: ${p['stack']:.2f}")
    if state["board_cards"]:
        print(f"  Board: {' '.join(state['board_cards'])}")
    if state["hero_cards"]:
        print(f"  Hero: {' '.join(state['hero_cards'])}")
    if state["hero_turn"]:
        print(f"  >>> YOUR TURN: {', '.join(state['actions'])}")
    if state["dealer_button"]:
        print(f"  Dealer: ({state['dealer_button']['x']}, {state['dealer_button']['y']})")


def main():
    parser = argparse.ArgumentParser(description="PokerStars Live Reader")
    parser.add_argument("--bridge", action="store_true", help="Connect to poker-lab engine")
    args = parser.parse_args()

    print("PokerStars Live Reader")
    print("=" * 40)

    bridge = None
    if args.bridge:
        import asyncio
        from bridge import PokerBridge
        bridge = PokerBridge()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bridge.connect())
            print("Bridge connected to poker-lab engine")
        except Exception as e:
            print(f"Bridge connection failed: {e}")
            print("Running in standalone mode")
            bridge = None

    print("Looking for PokerStars table on screen...")
    print("Press Ctrl+C to stop.\n")

    prev_state = None
    frame_count = 0
    ocr_interval = 3  # seconds between OCR reads (OCR is slow)
    last_ocr = 0

    while True:
        try:
            frame = capture_screen()
            region = find_table_region(frame)

            if not region:
                if frame_count % 10 == 0:
                    print("No table found...")
                frame_count += 1
                time.sleep(1)
                continue

            now = time.time()
            if now - last_ocr < ocr_interval:
                time.sleep(0.5)
                continue

            table = crop_table(frame, region)
            state = extract_game_state(table)
            last_ocr = now

            if states_differ(prev_state, state):
                print(f"\n[{time.strftime('%H:%M:%S')}] Game state changed:")
                print_state(state)

                # Feed to bridge if connected
                if bridge:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    loop.run_until_complete(bridge.process_game_state(state))

                prev_state = state
            else:
                print(".", end="", flush=True)

            frame_count += 1

        except KeyboardInterrupt:
            print("\n\nStopped.")
            if bridge:
                import asyncio
                asyncio.get_event_loop().run_until_complete(bridge.close())
            break
        except Exception as e:
            print(f"\nError: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
