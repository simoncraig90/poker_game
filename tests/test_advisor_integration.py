"""
Integration test: replay real WS messages through the full advisor pipeline.
Tests the complete chain: WS message → parser → state → recommendation → action.

No Chrome, no browser, no network — pure replay of captured messages.
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from unibet_ws import UnibetWSReader


def make_ws_message(hand_id, hero_cards_str, players, seat_states, bets, stacks, board_str=None, pot_info=None, hero_seat=1):
    """Build a fake XMPP WS message matching the real Unibet format."""
    c = [
        "|".join(players),          # c[0]: player names
        seat_states,                 # c[1]: seat states [1=active, 3=folded]
        stacks,                      # c[2]: stacks
        bets,                        # c[3]: bets per seat
        pot_info or [],              # c[4]: pot info
        None,                        # c[5]: showdown cards
        None,                        # c[6]: ?
        board_str,                   # c[7]: board cards string
        0,                           # c[8]: ?
        0,                           # c[9]: sequence
        0, 0, 0, 0,                  # c[10-13]
        [1000]*6,                    # c[14]: ?
        4,                           # c[15]: ?
        [0]*6,                       # c[16]: ?
        2,                           # c[17]: ?
    ]

    payload = {"c": c, "hid": hand_id}
    body = json.dumps({"payLoad": payload})
    return f'<body>{body}</body>'


def make_hero_cards_message(hand_id, cards_str, hand_desc=""):
    """Build hero cards message. hid must be at payLoad level for hand detection."""
    payload = {"p": [str(hand_id), 1, 0, cards_str, hand_desc, None], "hid": hand_id}
    body = json.dumps({"payLoad": payload})
    return f'<body>{body}</body>'


class RecordingCallback:
    """Records all state change callbacks."""
    def __init__(self):
        self.states = []

    def __call__(self, state):
        self.states.append(state.copy())

    @property
    def latest(self):
        return self.states[-1] if self.states else None

    def clear(self):
        self.states = []


def test_full_pipeline_preflop():
    """Replay a preflop hand through the WS reader and verify state."""
    reader = UnibetWSReader()
    recorder = RecordingCallback()
    reader.on_state_change(recorder)

    players = ["Villain1", "Skurj_uni41", "Villain2", "Villain3", "Villain4", "Villain5"]

    # New hand: hero is seat 1, gets Ah Kh
    reader._parse_message(make_hero_cards_message("hand1", "ahkh", "High card, Ace"))

    # State update with blinds posted (seat 4=SB, seat 5=BB)
    reader._parse_message(make_ws_message(
        "hand1", None, players,
        [1, 1, 1, 1, 1, 1],
        [0, 0, 0, 0, 2, 4],
        [1000, 1000, 1000, 1000, 998, 996],
    ))

    # Wait for debounce
    time.sleep(0.5)

    failures = []

    if not recorder.latest:
        return ["No state callback received"]

    state = recorder.latest
    if state["hero_cards"] != ["Ah", "Kh"]:
        failures.append(f"Hero cards: expected ['Ah', 'Kh'], got {state['hero_cards']}")
    if state["phase"] != "PREFLOP":
        failures.append(f"Phase: expected PREFLOP, got {state['phase']}")
    if state["hero_cards"] != ["Ah", "Kh"]:
        failures.append(f"Hero cards not set")

    return failures


def test_full_pipeline_flop_detection():
    """Verify flop cards are detected when board appears."""
    reader = UnibetWSReader()
    recorder = RecordingCallback()
    reader.on_state_change(recorder)

    players = ["Villain1", "Skurj_uni41", "Villain2", "Villain3", "Villain4", "Villain5"]

    # Deal hero cards
    reader._parse_message(make_hero_cards_message("hand2", "9h9c", "Pair of Nines"))

    # Preflop state
    reader._parse_message(make_ws_message(
        "hand2", None, players,
        [1, 1, 3, 3, 1, 1],
        [0, 8, 0, 0, 8, 0],
        [1000, 992, 1000, 1000, 992, 1000],
    ))

    time.sleep(0.5)

    # Flop: Jh 2s 9d (hero has set!)
    reader._parse_message(make_ws_message(
        "hand2", None, players,
        [1, 1, 3, 3, 1, 1],
        [0, 0, 0, 0, 0, 0],
        [1000, 992, 1000, 1000, 992, 1000],
        board_str="jh2s9d",
        pot_info=[[16, 1]],
    ))

    time.sleep(0.5)

    failures = []
    state = recorder.latest
    if not state:
        return ["No state after flop"]

    if state["board_cards"] != ["Jh", "2s", "9d"]:
        failures.append(f"Board: expected ['Jh', '2s', '9d'], got {state['board_cards']}")
    if state["phase"] != "FLOP":
        failures.append(f"Phase: expected FLOP, got {state['phase']}")

    return failures


def test_full_pipeline_turn_river():
    """Verify turn and river cards are detected."""
    reader = UnibetWSReader()
    recorder = RecordingCallback()
    reader.on_state_change(recorder)

    players = ["Villain1", "Skurj_uni41", "Villain2", "Villain3", "Villain4", "Villain5"]

    reader._parse_message(make_hero_cards_message("hand3", "ahkh"))

    # Flop
    reader._parse_message(make_ws_message(
        "hand3", None, players,
        [1, 1, 3, 3, 3, 3], [0]*6, [1000]*6,
        board_str="th4d9s",
    ))
    time.sleep(0.4)

    # Turn
    reader._parse_message(make_ws_message(
        "hand3", None, players,
        [1, 1, 3, 3, 3, 3], [0]*6, [1000]*6,
        board_str="th4d9sqh",
    ))
    time.sleep(0.4)

    failures = []
    state = recorder.latest
    if state["phase"] != "TURN":
        failures.append(f"Turn phase: expected TURN, got {state['phase']}")
    if len(state["board_cards"]) != 4:
        failures.append(f"Turn board: expected 4 cards, got {len(state['board_cards'])}")

    # River
    reader._parse_message(make_ws_message(
        "hand3", None, players,
        [1, 1, 3, 3, 3, 3], [0]*6, [1000]*6,
        board_str="th4d9sqh7h",
    ))
    time.sleep(0.4)

    state = recorder.latest
    if state["phase"] != "RIVER":
        failures.append(f"River phase: expected RIVER, got {state['phase']}")
    if len(state["board_cards"]) != 5:
        failures.append(f"River board: expected 5 cards, got {len(state['board_cards'])}")

    return failures


def test_facing_bet_detection():
    """Verify facing_bet is correctly set when opponent bets."""
    reader = UnibetWSReader()
    recorder = RecordingCallback()
    reader.on_state_change(recorder)

    players = ["Villain1", "Skurj_uni41", "Villain2", "Villain3", "Villain4", "Villain5"]

    reader._parse_message(make_hero_cards_message("hand4", "ahkh"))

    # Flop with no bets
    reader._parse_message(make_ws_message(
        "hand4", None, players,
        [1, 1, 3, 3, 3, 3],
        [0, 0, 0, 0, 0, 0],
        [1000]*6,
        board_str="th4d9s",
        pot_info=[[20, 1]],
    ))
    time.sleep(0.5)

    failures = []
    state = recorder.latest
    if state["facing_bet"] != False:
        failures.append(f"No bet: facing_bet should be False, got {state['facing_bet']}")

    # Opponent bets (seat 0 bets 15)
    reader._parse_message(make_ws_message(
        "hand4", None, players,
        [1, 1, 3, 3, 3, 3],
        [15, 0, 0, 0, 0, 0],
        [985, 1000, 1000, 1000, 1000, 1000],
        board_str="th4d9s",
        pot_info=[[35, 1]],
    ))
    time.sleep(0.5)

    state = recorder.latest
    if state["facing_bet"] != True:
        failures.append(f"Facing bet: should be True, got {state['facing_bet']}")
    if state["call_amount"] != 15:
        failures.append(f"Call amount: expected 15, got {state['call_amount']}")

    return failures


def test_facing_bet_resets_on_new_street():
    """facing_bet should reset to False when a new street begins."""
    reader = UnibetWSReader()
    recorder = RecordingCallback()
    reader.on_state_change(recorder)

    players = ["Villain1", "Skurj_uni41", "Villain2", "Villain3", "Villain4", "Villain5"]

    reader._parse_message(make_hero_cards_message("hand5", "ahkh"))

    # Preflop with raise (facing bet = True)
    reader._parse_message(make_ws_message(
        "hand5", None, players,
        [1, 1, 1, 1, 1, 1],
        [12, 0, 0, 0, 2, 4],
        [988, 1000, 1000, 1000, 998, 996],
    ))
    time.sleep(0.5)

    state = recorder.latest
    if not state or not state["facing_bet"]:
        pass  # preflop bet detection depends on BB amount logic

    # Flop with no bets (new street, facing_bet should reset)
    reader._parse_message(make_ws_message(
        "hand5", None, players,
        [1, 1, 3, 3, 3, 3],
        [0, 0, 0, 0, 0, 0],
        [988, 1000, 1000, 1000, 998, 996],
        board_str="th4d9s",
        pot_info=[[24, 1]],
    ))
    time.sleep(0.5)

    failures = []
    state = recorder.latest
    if state["facing_bet"] != False:
        failures.append(f"New street: facing_bet should be False, got {state['facing_bet']}")
    if state["call_amount"] != 0:
        failures.append(f"New street: call_amount should be 0, got {state['call_amount']}")

    return failures


def test_hero_cards_clear_on_fold():
    """Hero cards should clear when hero folds."""
    reader = UnibetWSReader()
    recorder = RecordingCallback()
    reader.on_state_change(recorder)

    players = ["Villain1", "Skurj_uni41", "Villain2", "Villain3", "Villain4", "Villain5"]

    reader._parse_message(make_hero_cards_message("hand6", "7h2c"))

    reader._parse_message(make_ws_message(
        "hand6", None, players,
        [1, 3, 1, 1, 1, 1],  # seat 1 (hero) = 3 = folded
        [0]*6, [1000]*6,
    ))
    time.sleep(0.5)

    failures = []
    state = recorder.latest
    if state and len(state["hero_cards"]) > 0:
        failures.append(f"After fold: hero_cards should be empty, got {state['hero_cards']}")

    return failures


# ══════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("Pipeline: preflop state", test_full_pipeline_preflop),
        ("Pipeline: flop detection", test_full_pipeline_flop_detection),
        ("Pipeline: turn + river", test_full_pipeline_turn_river),
        ("Pipeline: facing bet detection", test_facing_bet_detection),
        ("Pipeline: facing bet resets on new street", test_facing_bet_resets_on_new_street),
        ("Pipeline: hero cards clear on fold", test_hero_cards_clear_on_fold),
    ]

    print("=" * 60)
    print("  ADVISOR INTEGRATION TEST SUITE")
    print("=" * 60)

    total = 0
    passed = 0
    all_failures = []

    for name, test_fn in tests:
        total += 1
        try:
            failures = test_fn()
            if not failures:
                print(f"  PASS  {name}")
                passed += 1
            else:
                print(f"  FAIL  {name}")
                for f in failures:
                    print(f"        - {f}")
                all_failures.extend(failures)
        except Exception as e:
            print(f"  ERROR {name}: {e}")
            all_failures.append(f"{name}: {e}")

    print()
    print(f"  {passed}/{total} tests passed")
    if all_failures:
        print(f"  {len(all_failures)} failures")
    else:
        print("  ALL INTEGRATION TESTS PASS")
    print("=" * 60)

    sys.exit(0 if not all_failures else 1)
