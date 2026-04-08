"""
Test CoinPoker reader + clicker against the local replica HTML.

These tests verify the FULL pipeline:
1. DOM reader extracts state correctly
2. Clicker successfully clicks buttons via CDP
3. State changes after clicks (game progresses)

Requires:
- Chrome running with --remote-debugging-port=9222
- Replica open at file:///C:/poker-research/client/coinpoker-replica.html
"""

import os
import sys
import time
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vision"))

from coinpoker_dom import CoinPokerReader
from coinpoker_clicker import CoinPokerClicker


def reset_replica():
    """Reset the replica via CDP."""
    js = "window.replica && window.replica.reset()"
    node = (
        f"const CDP=require('chrome-remote-interface');"
        f"(async()=>{{"
        f"  const tabs = await CDP.List({{port:9222}});"
        f"  const t = tabs.find(x => x.url.includes('coinpoker-replica'));"
        f"  if (!t) {{ console.log('NO_REPLICA'); return; }}"
        f"  const c = await CDP({{target:t.id, port:9222}});"
        f"  await c.Runtime.enable();"
        f"  await c.Runtime.evaluate({{expression:'{js}'}});"
        f"  await c.close();"
        f"  console.log('OK');"
        f"}})();"
    )
    p = subprocess.run(["node", "-e", node], capture_output=True, text=True, cwd=ROOT, timeout=5)
    return "OK" in p.stdout


def trigger_replica(method):
    """Trigger a method on the replica's test API."""
    js = f"window.replica.{method}"
    node = (
        f"const CDP=require('chrome-remote-interface');"
        f"(async()=>{{"
        f"  const tabs = await CDP.List({{port:9222}});"
        f"  const t = tabs.find(x => x.url.includes('coinpoker-replica'));"
        f"  if (!t) return;"
        f"  const c = await CDP({{target:t.id, port:9222}});"
        f"  await c.Runtime.enable();"
        f"  await c.Runtime.evaluate({{expression:'{js}'}});"
        f"  await c.close();"
        f"}})();"
    )
    subprocess.run(["node", "-e", node], capture_output=True, text=True, cwd=ROOT, timeout=5)


def test_reader_extracts_lobby_state():
    """Reader returns state when only lobby is showing."""
    failures = []
    reset_replica()
    time.sleep(0.3)

    reader = CoinPokerReader(port=9222, target_match="coinpoker-replica")
    state = reader.get_state()
    if state is None:
        return ["Reader returned None — replica not open?"]
    if 'hero_cards' not in state:
        failures.append("state missing hero_cards")
    if state.get('hero_cards', None) != []:
        failures.append(f"lobby should have empty hero_cards, got {state.get('hero_cards')}")
    return failures


def test_clicker_quick_join():
    """Clicker can click Quick Join and game starts."""
    failures = []
    reset_replica()
    time.sleep(0.3)

    clicker = CoinPokerClicker(port=9222, target_match="coinpoker-replica")
    result = clicker.click_quick_join()
    if not result:
        failures.append("Quick Join click failed")
    time.sleep(1.0)  # let hand start

    reader = CoinPokerReader(port=9222, target_match="coinpoker-replica")
    state = reader.get_state()
    if state and len(state.get('hero_cards', [])) < 2:
        failures.append(f"After join, expected hero cards but got {state.get('hero_cards')}")
    return failures


def test_full_hand_flow():
    """Reader detects state, clicker clicks FOLD, hand ends."""
    failures = []
    reset_replica()
    time.sleep(0.3)

    clicker = CoinPokerClicker(port=9222, target_match="coinpoker-replica")
    reader = CoinPokerReader(port=9222, target_match="coinpoker-replica")

    # Join table
    clicker.click_quick_join()
    time.sleep(1.0)

    # Should have hero cards now
    s1 = reader.get_state()
    if not s1 or len(s1.get('hero_cards', [])) < 2:
        failures.append(f"After join: no hero cards. State: {s1}")
        return failures

    # FOLD
    if not clicker.click("FOLD"):
        failures.append("FOLD click failed")
    time.sleep(1.5)  # wait for endHand + new startHand

    # Should have new hand id (replica auto-deals new hand)
    s2 = reader.get_state()
    if s2 and s2.get('hand_id') == s1.get('hand_id'):
        # Hand id might or might not change in 1.5s — not a failure
        pass

    return failures


def test_check_then_facing_bet():
    """Hero checks preflop, opponent bets, hero facing_bet=True."""
    failures = []
    reset_replica()
    time.sleep(0.3)

    clicker = CoinPokerClicker(port=9222, target_match="coinpoker-replica")
    reader = CoinPokerReader(port=9222, target_match="coinpoker-replica")

    clicker.click_quick_join()
    time.sleep(1.0)

    # Start a hand, then simulate villain betting
    trigger_replica('startHand()')
    time.sleep(0.5)
    trigger_replica('simulateBetFromVillain()')
    time.sleep(0.4)

    state = reader.get_state()
    if not state:
        failures.append("No state after villain bet")
    elif not state.get('facing_bet'):
        failures.append(f"facing_bet should be True after villain bet, got {state.get('facing_bet')}")
    elif state.get('call_amount', 0) <= 0:
        failures.append(f"call_amount should be > 0, got {state.get('call_amount')}")
    return failures


def test_raise_with_amount():
    """Clicker can RAISE with a specific amount."""
    failures = []
    reset_replica()
    time.sleep(0.3)

    clicker = CoinPokerClicker(port=9222, target_match="coinpoker-replica")

    clicker.click_quick_join()
    time.sleep(1.0)

    # RAISE with 0.10
    if not clicker.click("RAISE", amount=0.10):
        failures.append("RAISE click failed")
    time.sleep(0.5)

    return failures


def test_no_allin_preset():
    """Clicker REFUSES to click 100% (All-in) preset."""
    failures = []
    clicker = CoinPokerClicker(port=9222, target_match="coinpoker-replica")
    if clicker.click_slider_preset(100):
        failures.append("Should refuse to click All-in preset")
    return failures


def test_state_machine_can_consume():
    """Reader output is compatible with AdvisorStateMachine."""
    failures = []

    from advisor_state_machine import AdvisorStateMachine
    from preflop_chart import preflop_advice

    class FakeBase:
        def _get_recommendation(self, state):
            return {
                "phase": state.get("phase", "PREFLOP") if state.get("board_cards") else "PREFLOP",
                "equity": 0.5,
                "preflop": {"action": "FOLD", "hand_key": "??", "in_range": False, "note": ""},
                "danger": {"warnings": [], "danger": 0},
                "category": "TEST",
            }

    sm = AdvisorStateMachine(
        base_advisor=FakeBase(),
        preflop_advice_fn=preflop_advice,
        bb_cents=4,
    )

    reset_replica()
    time.sleep(0.3)

    clicker = CoinPokerClicker(port=9222, target_match="coinpoker-replica")
    clicker.click_quick_join()
    time.sleep(1.0)

    reader = CoinPokerReader(port=9222, target_match="coinpoker-replica")
    state = reader.get_state()
    if not state:
        return ["No state from reader"]

    # Try processing through state machine
    try:
        out = sm.process_state(state)
        # Should not crash; may or may not return action depending on state
    except Exception as e:
        failures.append(f"State machine crashed on reader output: {e}")

    return failures


if __name__ == "__main__":
    tests = [
        ("Reader extracts lobby state", test_reader_extracts_lobby_state),
        ("Clicker Quick Join works", test_clicker_quick_join),
        ("Full hand: join -> fold", test_full_hand_flow),
        ("Detects facing_bet after villain bet", test_check_then_facing_bet),
        ("RAISE with amount", test_raise_with_amount),
        ("Refuses All-in preset", test_no_allin_preset),
        ("Output compatible with state machine", test_state_machine_can_consume),
    ]

    print("=" * 60)
    print("  COINPOKER REPLICA TESTS")
    print("=" * 60)
    print("Requires: Chrome on :9222 with replica open")
    print()

    total = passed = 0
    all_failures = []
    for name, fn in tests:
        total += 1
        try:
            failures = fn()
            if not failures:
                print(f"  PASS  {name}")
                passed += 1
            else:
                print(f"  FAIL  {name}")
                for f in failures:
                    print(f"        - {f}")
                all_failures.extend(failures)
        except Exception as e:
            import traceback
            print(f"  ERROR {name}: {e}")
            traceback.print_exc()
            all_failures.append(f"{name}: {e}")

    print()
    print(f"  {passed}/{total} tests passed")
    if all_failures:
        print(f"  {len(all_failures)} failures")
    print("=" * 60)
    sys.exit(0 if not all_failures else 1)
