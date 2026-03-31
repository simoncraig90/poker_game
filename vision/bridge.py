"""
Bridge between vision pipeline and poker-lab engine.
Connects to the WebSocket server and mirrors detected PokerStars game state.
"""

import asyncio
import json
import time
import sys
import os

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    os.system(f"{sys.executable} -m pip install websockets -q")
    import websockets

sys.path.insert(0, os.path.dirname(__file__))


class PokerBridge:
    """Translates vision detections into poker-lab engine commands."""

    def __init__(self, ws_url="ws://localhost:9100"):
        self.ws_url = ws_url
        self.ws = None
        self.msg_id = 0
        self.session_id = None
        self.engine_state = None

        # Track what we've already sent to avoid duplicates
        self.seated_players = {}   # name -> seat
        self.current_hand_id = None
        self.hand_started = False
        self.last_board = []
        self.last_hero_cards = []
        self.last_pot = None
        self.actions_sent = 0

    async def connect(self):
        """Connect to the poker-lab WebSocket server."""
        self.ws = await websockets.connect(self.ws_url)
        welcome = json.loads(await self.ws.recv())
        if welcome.get("welcome"):
            self.session_id = welcome["sessionId"]
            self.engine_state = welcome.get("state")
            print(f"Connected to poker-lab session: {self.session_id}")
            print(f"Table: {self.engine_state.get('tableName', '?')}")
            return True
        return False

    async def send_cmd(self, cmd, payload=None):
        """Send a command and wait for response."""
        self.msg_id += 1
        msg = {
            "id": f"vision-{self.msg_id}",
            "cmd": cmd,
            "payload": payload or {},
        }
        await self.ws.send(json.dumps(msg))

        # Wait for response (skip broadcasts)
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
            data = json.loads(raw)
            if data.get("broadcast"):
                continue  # skip broadcasts, wait for our response
            return data

    async def get_state(self):
        """Get current engine state."""
        resp = await self.send_cmd("GET_STATE")
        if resp.get("ok"):
            self.engine_state = resp.get("state")
        return self.engine_state

    async def seat_player(self, seat, name, stack_cents):
        """Seat a player if not already seated."""
        if name in self.seated_players:
            return True

        resp = await self.send_cmd("SEAT_PLAYER", {
            "seat": seat,
            "name": name,
            "buyIn": stack_cents,
            "country": "XX",
        })

        if resp.get("ok"):
            self.seated_players[name] = seat
            print(f"  Seated {name} at seat {seat} (${stack_cents/100:.2f})")
            return True
        else:
            print(f"  Failed to seat {name}: {resp.get('error')}")
            return False

    async def start_hand(self):
        """Start a new hand."""
        resp = await self.send_cmd("START_HAND")
        if resp.get("ok"):
            self.hand_started = True
            self.actions_sent = 0
            self.last_board = []
            self.last_hero_cards = []
            # Extract hand ID from events
            for ev in resp.get("events", []):
                if ev.get("type") == "HAND_START":
                    self.current_hand_id = ev.get("handId")
                    print(f"  Hand started: #{self.current_hand_id}")
                    break
            return True
        else:
            print(f"  Failed to start hand: {resp.get('error')}")
            return False

    async def send_action(self, seat, action, amount=None):
        """Send a player action."""
        payload = {"seat": seat, "action": action}
        if amount is not None:
            payload["amount"] = amount

        resp = await self.send_cmd("PLAYER_ACTION", payload)
        if resp.get("ok"):
            self.actions_sent += 1
            return True
        else:
            print(f"  Action failed: {resp.get('error')}")
            return False

    async def sync_players(self, detected_players):
        """
        Sync detected players with engine state.
        detected_players: list of {name, stack, cx, cy}
        """
        if not detected_players:
            return

        # Sort players by position (clockwise from bottom-center = hero)
        # For now, assign seats based on vertical position
        # Bottom = seat 0 (hero), then clockwise
        sorted_players = sorted(detected_players, key=lambda p: -p["cy"])

        for i, p in enumerate(sorted_players):
            seat = i % 6
            stack_cents = int(p["stack"] * 100)
            await self.seat_player(seat, p["name"], stack_cents)

    async def process_game_state(self, state):
        """
        Process a detected game state and send appropriate commands.
        state: dict from extract_game_state() in live.py
        """
        players = state.get("players", [])
        pot = state.get("pot")
        board_cards = state.get("board_cards", [])
        hero_cards = state.get("hero_cards", [])
        hero_turn = state.get("hero_turn", False)
        actions = state.get("actions", [])

        # 1. Sync players
        if players and not self.seated_players:
            await self.sync_players(players)

        # 2. Detect new hand (pot appeared or hero cards changed)
        if hero_cards and hero_cards != self.last_hero_cards:
            if not self.hand_started or hero_cards != self.last_hero_cards:
                self.last_hero_cards = hero_cards
                # New hand detected

        # 3. Detect board changes (new street)
        if board_cards and board_cards != self.last_board:
            new_cards = [c for c in board_cards if c not in self.last_board]
            if new_cards:
                street = {0: "PREFLOP", 3: "FLOP", 4: "TURN", 5: "RIVER"}.get(
                    len(board_cards), "UNKNOWN"
                )
                print(f"  Board update ({street}): {' '.join(board_cards)}")
                self.last_board = board_cards[:]

        # 4. Log state
        if pot != self.last_pot:
            self.last_pot = pot
            if pot:
                print(f"  Pot: ${pot:.2f}")

        # 5. Hero's turn — log available actions
        if hero_turn and actions:
            print(f"  YOUR TURN: {', '.join(actions)}")

    async def close(self):
        """Close the WebSocket connection."""
        if self.ws:
            await self.ws.close()
            print("Disconnected from poker-lab")


async def run_bridge():
    """Run the bridge — connect and process states from the live reader."""
    bridge = PokerBridge()

    try:
        if not await bridge.connect():
            print("Failed to connect")
            return

        state = await bridge.get_state()
        print(f"Engine state: {state.get('handsPlayed', 0)} hands played")
        print(f"Seats: {sum(1 for s in state.get('seats', {}).values() if s.get('status') == 'OCCUPIED')}/6 occupied")
        print()

        # Import mode: read from vision pipeline
        print("Bridge ready. Waiting for vision input...")
        print("Run vision/live.py in another terminal to feed game states.")
        print()

        # For now, just keep connection alive and respond to input
        while True:
            try:
                # Check for incoming messages (broadcasts from other clients)
                raw = await asyncio.wait_for(bridge.ws.recv(), timeout=1.0)
                data = json.loads(raw)
                if data.get("broadcast"):
                    for ev in data.get("events", []):
                        print(f"  Engine event: {ev.get('type')}")
            except asyncio.TimeoutError:
                pass
            except websockets.ConnectionClosed:
                print("Connection lost")
                break

    except ConnectionRefusedError:
        print(f"Cannot connect to {bridge.ws_url}")
        print("Start the poker-lab server first: node src/server/ws-server.js")
    except KeyboardInterrupt:
        print("\nStopping bridge...")
    finally:
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(run_bridge())
