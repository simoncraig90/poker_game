"""
Cross-check vision card detection against PokerStars hand history.
Matches captured frames to hands by timestamp and verifies card identification.
"""

import re
import os
import sys
import cv2
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from detect import find_cards_by_color
from card_id import identify_cards


def parse_hands(text):
    """Parse PS hand history into a list of hands with boards and hero cards."""
    hands = []
    for block in re.split(r'\*{5,}.*?\*{5,}', text):
        block = block.strip()
        if not block:
            continue

        # Hand ID and timestamp
        m = re.search(r'Hand #(\d+).*?(\d{4}/\d{2}/\d{2} \d+:\d+:\d+) ET', block)
        if not m:
            continue
        hand_id = m.group(1)
        ts_str = m.group(2)
        ts = datetime.strptime(ts_str, '%Y/%m/%d %H:%M:%S')

        # Hero cards
        m_hero = re.search(r'Dealt to Skurj_poker \[(.+?)\]', block)
        hero = m_hero.group(1).split() if m_hero else []

        # Board
        boards = re.findall(r'\*\*\* (?:FLOP|TURN|RIVER) \*\*\* \[(.+?)\]', block)
        board = []
        if boards:
            # Last board line has all cards
            last = boards[-1]
            # Handle turn/river format: [Xx Xx Xx] [Xx]
            parts = re.findall(r'\[(.+?)\]', ''.join(f'[{b}]' for b in boards))
            all_cards = []
            for p in parts:
                all_cards.extend(p.split())
            # Deduplicate while preserving order
            seen = set()
            for c in all_cards:
                if c not in seen:
                    board.append(c)
                    seen.add(c)

        hands.append({
            'id': hand_id,
            'ts': ts,
            'hero': hero,
            'board': board,
        })

    return hands


def main():
    # Load hand history
    hh_path = os.path.join(os.path.dirname(__file__), '..', 'hands', 'poker_stars', 'hands_002.txt')
    if not os.path.exists(hh_path):
        print(f"Hand history not found at {hh_path}")
        return

    with open(hh_path) as f:
        text = f.read()

    hands = parse_hands(text)
    print(f"Parsed {len(hands)} hands from history")

    # Only check hands that have boards (cards were dealt)
    hands_with_cards = [h for h in hands if h['board'] or h['hero']]
    print(f"Hands with visible cards: {len(hands_with_cards)}")

    # Load frames and detect cards
    training_dir = os.path.join(os.path.dirname(__file__), 'captures', 'training')
    frames = sorted([f for f in os.listdir(training_dir) if f.startswith('frame_')])

    # Convert frame timestamps to datetime
    # Frames are named frame_{unix_ms}.png
    # Hand times are in ET — we need to match by content, not time
    # (timezone conversion is tricky, so match by card content instead)

    print(f"\nAnalyzing frames for cross-check...")
    print("=" * 60)

    # Build a lookup of board combinations from hands
    board_lookup = {}
    for h in hands_with_cards:
        if h['board']:
            key = ' '.join(h['board'])
            board_lookup[key] = h

    # Test a sample of frames
    matches = 0
    mismatches = 0
    checked = 0

    for fname in frames[::15]:  # every 15th frame
        img = cv2.imread(os.path.join(training_dir, fname))
        if img is None:
            continue

        cards = find_cards_by_color(img)
        if not cards['board'] and not cards['hero']:
            continue

        board_ids = identify_cards(img, cards['board'])
        hero_ids = identify_cards(img, cards['hero'])

        board_str = ' '.join(l for l, _ in board_ids)
        hero_str = ' '.join(l for l, _ in hero_ids)

        if not board_str and not hero_str:
            continue

        checked += 1

        # Try to match board to hand history
        if board_str in board_lookup:
            h = board_lookup[board_str]
            matches += 1
            print(f"MATCH  {fname}")
            print(f"  Board: {board_str}")
            print(f"  Hand #{h['id']} at {h['ts']}")
        elif board_str:
            # Check partial matches (flop matches but turn/river different)
            partial = False
            for key, h in board_lookup.items():
                if key.startswith(board_str) or board_str.startswith(key):
                    matches += 1
                    partial = True
                    print(f"PARTIAL {fname}")
                    print(f"  Detected: {board_str}")
                    print(f"  Hand:     {key} (#{h['id']})")
                    break
            if not partial:
                mismatches += 1
                print(f"NO MATCH {fname}")
                print(f"  Board: {board_str}")
                # Show closest hand
                for key in board_lookup:
                    if any(c in key for c in board_str.split()[:2]):
                        print(f"  Closest: {key}")
                        break

        # Check hero cards
        if hero_str:
            hero_match = False
            for h in hands:
                if ' '.join(h['hero']) == hero_str:
                    hero_match = True
                    print(f"  Hero: {hero_str} -> Hand #{h['id']} MATCH")
                    break
            if not hero_match:
                print(f"  Hero: {hero_str} -> NO MATCH in history")

    print(f"\n{'=' * 60}")
    print(f"Checked: {checked} frames")
    print(f"Matches: {matches}")
    print(f"Mismatches: {mismatches}")
    print(f"Match rate: {matches/(matches+mismatches)*100:.0f}%" if matches + mismatches > 0 else "N/A")


if __name__ == '__main__':
    main()
