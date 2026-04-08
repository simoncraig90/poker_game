"""
End-to-end test of the CoinPoker auto-player against the replica.

Starts the player as a subprocess, drives the replica through several
hand cycles, and verifies the player's clicks reach the buttons.
"""

import os
import sys
import time
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def trigger_replica(js):
    """Run JS in the replica via CDP."""
    node = (
        f"const CDP=require('chrome-remote-interface');"
        f"(async()=>{{"
        f"  const tabs = await CDP.List({{port:9222}});"
        f"  const t = tabs.find(x => x.url.includes('coinpoker-replica'));"
        f"  if (!t) return console.log('NO_REPLICA');"
        f"  const c = await CDP({{target:t.id, port:9222}});"
        f"  await c.Runtime.enable();"
        f"  const r = await c.Runtime.evaluate({{returnByValue:true,expression:{json.dumps(js)}}});"
        f"  console.log(r.result && r.result.value !== undefined ? r.result.value : 'undefined');"
        f"  await c.close();"
        f"}})().catch(e => console.log('ERR:'+e.message));"
    )
    p = subprocess.run(["node", "-e", node], capture_output=True, text=True, cwd=ROOT, timeout=5)
    return p.stdout.strip()


def get_replica_stats():
    return trigger_replica(
        "JSON.stringify({clicks: window.replica.state.clicks, "
        "handNum: window.replica.state.handNum, "
        "lastAction: document.getElementById('last-action').textContent, "
        "pot: window.replica.state.pot, "
        "heroCards: window.replica.state.heroCards.length, "
        "phase: window.replica.state.phase, "
        "actionBarVisible: document.getElementById('action-bar').classList.contains('visible')})"
    )


def main():
    print("=" * 60)
    print("  COINPOKER PLAYER END-TO-END TEST")
    print("=" * 60)
    print("Requires: Chrome on :9222 with replica open")
    print()

    # Reset replica
    print("Step 1: Reset replica")
    trigger_replica("window.replica.reset()")
    time.sleep(0.5)
    stats = get_replica_stats()
    print(f"  Initial: {stats}")

    # Quick join via JS click on replica
    print("\nStep 2: Quick Join a table")
    trigger_replica("document.querySelector('.quick-join').click()")
    time.sleep(1.2)  # let auto-startHand fire
    stats = get_replica_stats()
    print(f"  After join: {stats}")

    # Now start the auto-player as subprocess
    print("\nStep 3: Launch coinpoker_player.py")
    player = subprocess.Popen(
        ["python", "-u", "vision/coinpoker_player.py", "--target=replica"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=ROOT, text=True, encoding='utf-8',
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
    )
    time.sleep(8)  # let player initialize and process initial state

    # Player should detect hero's turn and act
    print("\nStep 4: Wait for player to act on first hand")
    time.sleep(8)  # think time + click

    stats = get_replica_stats()
    print(f"  After player action: {stats}")

    # Drive a few more hands
    print("\nStep 5: Trigger 3 more hand cycles (deal -> wait for player -> end)")
    for i in range(3):
        # Replica auto-deals new hand after player's fold
        time.sleep(6)
        stats = get_replica_stats()
        print(f"  Hand {i+1}: {stats}")
        # Force a new hand if needed
        trigger_replica("if (!window.replica.state.inHand) window.replica.startHand()")

    # Check final stats
    print("\nStep 6: Final replica stats")
    final = get_replica_stats()
    print(f"  {final}")

    # Read player log
    print("\nStep 7: Player log tail")
    log_path = os.path.join(ROOT, "coinpoker_player.log")
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        print("--- last 30 lines ---")
        for line in lines[-30:]:
            print(f"  {line.rstrip()}")

    # Stop player
    print("\nStep 8: Stop player")
    try:
        player.terminate()
        player.wait(timeout=5)
    except Exception:
        try: player.kill()
        except Exception: pass

    # Parse final stats and check player acted
    try:
        final_data = json.loads(final)
        clicks_total = final_data.get('clicks', 0)
        hands_played = final_data.get('handNum', 0)
        print(f"\nResult: {clicks_total} total clicks, {hands_played} hands")
        if clicks_total > 1:  # at least the player clicked once
            print("PASS: Player successfully clicked at least once")
            return 0
        else:
            print("FAIL: Player did not click any buttons")
            return 1
    except Exception as e:
        print(f"FAIL: could not parse stats: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
