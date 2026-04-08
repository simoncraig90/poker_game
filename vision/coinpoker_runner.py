"""
CoinPoker advisor runner.

Reads frames from the JSONL stream produced by the patched PBClient.dll
(``C:\\Users\\Simon\\coinpoker_frames.jsonl``), feeds them through
``CoinPokerStateBuilder``, and dispatches each new snapshot to a callable
(typically ``AdvisorStateMachine.process_state``).

Two modes:
  --replay     Read the file once start-to-finish then exit (used by tests
               and offline what-if analysis).
  --follow     Tail the file, polling for new lines. The default for live
               play.

The runner is split into a pure ``CoinPokerSession`` class (no I/O against
the advisor — takes an injected ``on_snapshot`` callable) and a ``main()``
that wires the real ``AdvisorStateMachine`` up. This lets the session loop
be tested with a mock callback against the captured fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Iterator, Optional

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)
sys.path.insert(0, VISION_DIR)

from coinpoker_adapter import CHIP_SCALE, CoinPokerStateBuilder

DEFAULT_LOG = r"C:\Users\Simon\coinpoker_frames.jsonl"
HERO_USER_ID_DEFAULT = 1571120  # precious0864449 — hero on the practice table


# ── file iterators ────────────────────────────────────────────────────────────

def replay_iter(path: str) -> Iterator[str]:
    """Yield each non-empty line of the file once. Closes the file at EOF."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line:
                yield line


def follow_iter(path: str, poll: float = 0.2) -> Iterator[str]:
    """
    Tail a growing file. Yields each new line as it appears. Reopens the
    file if it shrinks (process truncation / log rotation). Blocks
    indefinitely; caller should run inside a try/KeyboardInterrupt.
    """
    f = open(path, "r", encoding="utf-8")
    try:
        # Start from the end so we don't replay history during a live tail.
        f.seek(0, 2)
        leftover = ""
        while True:
            chunk = f.read()
            if not chunk:
                # Detect truncation: if file size < current pos, reopen.
                try:
                    if os.path.getsize(path) < f.tell():
                        f.close()
                        f = open(path, "r", encoding="utf-8")
                        leftover = ""
                except OSError:
                    pass
                time.sleep(poll)
                continue
            data = leftover + chunk
            lines = data.split("\n")
            leftover = lines[-1]
            for line in lines[:-1]:
                if line:
                    yield line
    finally:
        try:
            f.close()
        except Exception:
            pass


# ── session loop ──────────────────────────────────────────────────────────────

class CoinPokerSession:
    """
    Orchestrates the per-frame loop. Pure: no advisor instantiation.

    Constructor takes:
      - hero_user_id: int — passed through to the builder
      - on_snapshot: callable(snapshot: dict) -> Any — invoked for each
        builder snapshot AFTER state has actually changed (we coalesce
        no-op frames so the advisor isn't woken up for every server_lag).
        Return value is ignored.
      - bb_chips: int or None — if given, overrides the auto-detected BB
        amount that the runner exposes via ``self.bb_cents``.
    """

    # Keys we treat as the meaningful "state changed" signature. Frames
    # that don't move any of these are passed through to the builder but
    # don't trigger on_snapshot. (AdvisorStateMachine has its own change
    # detection too, but we suppress noise here so its counters and the
    # console output stay readable.)
    _CHANGE_KEYS = (
        "hand_id", "phase", "hero_cards", "board_cards",
        "facing_bet", "call_amount", "pot", "hero_stack",
        "hero_turn", "position",
    )

    def __init__(self, hero_user_id: int,
                 on_snapshot: Callable[[dict], Any],
                 bb_chips: Optional[int] = None):
        self.builder = CoinPokerStateBuilder(hero_user_id)
        self.on_snapshot = on_snapshot
        self._override_bb = bb_chips
        self._last_signature: Optional[tuple] = None
        self.frames_seen = 0
        self.snapshots_dispatched = 0

    @property
    def hero_user_id(self) -> int:
        """Convenience accessor mirroring MultiTableCoinPokerSession's API."""
        return self.builder.hero_user_id

    @property
    def bb_cents(self) -> int:
        """
        Big blind in scaled chip units, suitable for AdvisorStateMachine's
        ``bb_cents`` constructor arg. Falls back to the practice-table
        default (100 chips × CHIP_SCALE) if we haven't seen a hand yet.
        """
        if self._override_bb is not None:
            return self._override_bb * CHIP_SCALE
        if self.builder.bb_amount > 0:
            return self.builder.bb_amount
        return 100 * CHIP_SCALE  # safe default for the practice table

    def bb_cents_for_room(self, room_name: Optional[str] = None) -> int:
        """
        Cross-class API: callers that want to support both single- and
        multi-table sessions should use this method instead of the
        no-arg ``bb_cents`` property. Single-table ignores the room arg.
        """
        return self.bb_cents

    def feed_line(self, line: str) -> Optional[dict]:
        """
        Parse + ingest one JSONL line. Returns the snapshot if it changed
        meaningfully, else None. Bad JSON is silently dropped (the patch
        wraps File.AppendAllText in a try/catch and may rarely write a
        partial line on shutdown).
        """
        if not line:
            return None
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            return None
        return self.feed_frame(frame)

    def feed_frame(self, frame: dict) -> Optional[dict]:
        self.frames_seen += 1
        self.builder.ingest(frame)
        snap = self.builder.snapshot()
        if snap is None:
            return None
        sig = tuple(self._sig_value(snap[k]) for k in self._CHANGE_KEYS)
        if sig == self._last_signature:
            return None
        self._last_signature = sig
        self.snapshots_dispatched += 1
        try:
            self.on_snapshot(snap)
        except Exception as e:
            # Swallow advisor errors so a buggy strategy module doesn't
            # take down the whole runner mid-session. Print + continue.
            import traceback
            print(f"[on_snapshot ERROR] {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
        return snap

    @staticmethod
    def _sig_value(v):
        # Lists/dicts aren't hashable; reduce them to a hashable shape.
        if isinstance(v, list):
            return tuple(v)
        if isinstance(v, dict):
            return tuple(sorted(v.items()))
        return v

    def run(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.feed_line(line)

    def warmup(self, lines: Iterable[str]) -> int:
        """
        Silently ingest frames into the builder without dispatching to
        on_snapshot. Used by --follow mode to seed state from the existing
        file before tailing for new lines. Returns the count ingested.
        """
        count = 0
        for line in lines:
            if not line:
                continue
            try:
                frame = json.loads(line) if isinstance(line, str) else line
            except json.JSONDecodeError:
                continue
            self.builder.ingest(frame)
            count += 1
        return count


# ── multi-table session ───────────────────────────────────────────────────────

class MultiTableCoinPokerSession:
    """
    Multi-table version of CoinPokerSession. Maintains a separate
    CoinPokerStateBuilder per `room_name` so 4+ tables can be played
    simultaneously without state cross-contamination.

    Why this exists: the user's £10/hour grind plan needs 4+ simultaneous
    tables to be viable on a 2hr/day budget at micro stakes. The single-
    table CoinPokerSession can only handle one room at a time — if frames
    from different tables interleave (which they DO in the live frame
    log), the single builder gets confused about which hand is current.

    The multi-table session:
      - Spawns one CoinPokerStateBuilder per room_name on demand
      - Routes each frame to the right builder by `frame['room_name']`
      - Injects `room_name` into every dispatched snapshot so the
        callback can keep per-room state (caches, SMs, overlays)
      - Coalesces no-op frames per-room (separate signature dict per room)
      - Tracks `bb_cents` per-room — the active table's BB drives the
        SM's threshold for that room

    The on_snapshot callback signature is unchanged: it receives a single
    snapshot dict, but with `snapshot['room_name']` populated. The callback
    is responsible for keying its own per-room state by that field.
    """

    _CHANGE_KEYS = CoinPokerSession._CHANGE_KEYS

    def __init__(self, hero_user_id: int,
                 on_snapshot: Callable[[dict], Any],
                 bb_chips: Optional[int] = None):
        self.hero_user_id = int(hero_user_id)
        self.on_snapshot = on_snapshot
        self._override_bb = bb_chips
        self._builders: dict[str, CoinPokerStateBuilder] = {}
        self._last_signatures: dict[str, tuple] = {}
        self.frames_seen = 0
        self.snapshots_dispatched = 0
        # Track room order so we can return a deterministic "active" room
        # for bb_cents queries when no specific room is requested.
        self._most_recent_room: Optional[str] = None

    @property
    def builders(self) -> dict[str, CoinPokerStateBuilder]:
        """Read-only access to the per-room builder map."""
        return dict(self._builders)

    @property
    def builder(self) -> Optional[CoinPokerStateBuilder]:
        """
        Backwards-compat shim: returns the most recently active room's
        builder, or None if no rooms seen yet. Single-table callers can
        use this just like CoinPokerSession.builder.
        """
        if self._most_recent_room and self._most_recent_room in self._builders:
            return self._builders[self._most_recent_room]
        if self._builders:
            return next(iter(self._builders.values()))
        return None

    def get_builder(self, room_name: str) -> Optional[CoinPokerStateBuilder]:
        """Lookup the builder for a specific room. Returns None if absent."""
        return self._builders.get(room_name)

    def bb_cents(self, room_name: Optional[str] = None) -> int:
        """
        Big blind in scaled chip units for a specific room. If room_name
        is None, returns the BB for the most recently active room
        (fallback for callers that aren't yet room-aware).
        Falls back to the practice-table default (100 chips × CHIP_SCALE)
        if we haven't seen any hand yet.
        """
        if self._override_bb is not None:
            return self._override_bb * CHIP_SCALE
        target_room = room_name or self._most_recent_room
        if target_room and target_room in self._builders:
            b = self._builders[target_room]
            if b.bb_amount > 0:
                return b.bb_amount
        return 100 * CHIP_SCALE

    def bb_cents_for_room(self, room_name: Optional[str] = None) -> int:
        """
        Cross-class API mirroring CoinPokerSession.bb_cents_for_room.
        Uses room_name if provided, else falls back to the most recent
        room. Both session classes implement this method so the runner
        can call it transparently regardless of which session type
        is active.
        """
        return self.bb_cents(room_name)

    def feed_line(self, line: str) -> Optional[dict]:
        if not line:
            return None
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            return None
        return self.feed_frame(frame)

    def feed_frame(self, frame: dict) -> Optional[dict]:
        """
        Route a frame to its room's builder. Frames missing room_name
        are silently dropped — the patched DLL always populates this
        field, so a missing one means corrupt input.
        """
        self.frames_seen += 1
        room = frame.get("room_name") or ""
        if not room:
            return None
        builder = self._builders.get(room)
        if builder is None:
            builder = CoinPokerStateBuilder(self.hero_user_id)
            self._builders[room] = builder
        builder.ingest(frame)
        # Track most-recent room on EVERY frame, not just on dispatch.
        # bb_cents() and other room-aware queries need to know which
        # table is "current" even before snapshots start firing
        # (snapshot() returns None until hero seat is known, which can
        # take many frames at hand start).
        self._most_recent_room = room
        snap = builder.snapshot()
        if snap is None:
            return None
        # Inject room metadata so callbacks can key their per-room state
        snap = dict(snap)
        snap["room_name"] = room
        sig = tuple(CoinPokerSession._sig_value(snap[k]) for k in self._CHANGE_KEYS)
        if sig == self._last_signatures.get(room):
            return None
        self._last_signatures[room] = sig
        self.snapshots_dispatched += 1
        try:
            self.on_snapshot(snap)
        except Exception as e:
            import traceback
            print(f"[on_snapshot ERROR] room={room} {type(e).__name__}: {e}",
                  file=sys.stderr)
            traceback.print_exc()
        return snap

    def run(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.feed_line(line)

    def warmup(self, lines: Iterable[str]) -> int:
        """
        Multi-table version of warmup: silently ingest frames into the
        per-room builders WITHOUT firing on_snapshot. Spawns a new
        builder for each room as needed (same logic as feed_frame
        minus the dispatch). Returns the count ingested.

        This was added 2026-04-09 after the single-table warmup loop
        in main() — which poked session.builder.ingest() directly —
        crashed on multi-table mode because session.builder returns
        None until at least one room has been seen.
        """
        count = 0
        for line in lines:
            if not line:
                continue
            try:
                frame = json.loads(line) if isinstance(line, str) else line
            except json.JSONDecodeError:
                continue
            room = frame.get("room_name") or ""
            if not room:
                continue
            builder = self._builders.get(room)
            if builder is None:
                builder = CoinPokerStateBuilder(self.hero_user_id)
                self._builders[room] = builder
            builder.ingest(frame)
            self._most_recent_room = room
            count += 1
        return count

    def __len__(self) -> int:
        return len(self._builders)


# ── overlay client ────────────────────────────────────────────────────────────

class OverlayClient:
    """
    Talks to vision/overlay_process.py via JSON-on-stdin.

    Two construction modes:
      OverlayClient.spawn()    — launches overlay_process.py as a subprocess
      OverlayClient(stream=f)  — writes to a file-like (used by tests)

    The ``send`` method is a no-op if the underlying stream is dead.
    """

    def __init__(self, stream=None, process: Optional[subprocess.Popen] = None,
                 table_id: str = "coinpoker_t1"):
        self._stream = stream
        self._proc = process
        self.table_id = table_id

    @classmethod
    def spawn(cls, table_id: str = "coinpoker_t1") -> "OverlayClient":
        overlay_script = os.path.join(VISION_DIR, "overlay_process.py")
        if not os.path.exists(overlay_script):
            print(f"[overlay] script not found at {overlay_script}, disabling")
            return cls(stream=None, process=None, table_id=table_id)
        # Kill any orphaned overlay first — pattern copied from advisor_ws.py.
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process python* -ErrorAction SilentlyContinue | "
                 "Where-Object { (Get-WmiObject Win32_Process -Filter "
                 "\"ProcessId=$($_.Id)\").CommandLine -match 'overlay_process' } | "
                 "Stop-Process -Force"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        proc = subprocess.Popen(
            [sys.executable, "-u", overlay_script],
            stdin=subprocess.PIPE, text=True,
        )
        print(f"[overlay] spawned (PID {proc.pid})")
        return cls(stream=proc.stdin, process=proc, table_id=table_id)

    def alive(self) -> bool:
        if self._stream is None:
            return False
        if self._proc is not None and self._proc.poll() is not None:
            return False
        return True

    def send(self, msg: dict) -> None:
        if not self.alive():
            return
        try:
            self._stream.write(json.dumps(msg) + "\n")
            self._stream.flush()
        except Exception as e:
            # Don't print every line — overlay died, give up on it.
            print(f"[overlay] send failed, stopping overlay output: {type(e).__name__}: {e}")
            self._stream = None

    def remove_table(self) -> None:
        self.send({"type": "table_remove", "table_id": self.table_id})

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


def parse_room_stake(room_name: str) -> str:
    """
    Pull a short human-readable stake string out of the CoinPoker room name.
    e.g. ``PR-NL 50-100 EV-INRIT-ANTE (A) 246519`` → ``50/100``.
    """
    if not room_name:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)", room_name)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return room_name[:30]


def room_to_table_id(room_name: str, fallback: str = "coinpoker_t1") -> str:
    """
    Map a CoinPoker room name to a stable, short table_id suitable for
    the overlay's per-table panel routing.

    The overlay uses table_id as a dict key to maintain a TablePanel
    per table. Same room → same table_id → same panel updated in place.
    Different rooms → different table_ids → separate panels rendered
    side-by-side.

    Format: "stake#tableNum", e.g. "0.05-0.10#246083" or "50/100#246519".
    Falls back to a sanitized truncation of the room name if the
    pattern doesn't match. Falls back to ``fallback`` if room is empty
    (single-table mode).
    """
    if not room_name:
        return fallback
    # Stake range
    m_stake = re.search(r"(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)", room_name)
    stake = f"{m_stake.group(1)}-{m_stake.group(2)}" if m_stake else ""
    # Table number (last numeric chunk in the room name)
    m_table = re.findall(r"\b(\d{3,})\b", room_name)
    table_num = m_table[-1] if m_table else ""
    if stake and table_num:
        return f"{stake}#{table_num}"
    if stake:
        return stake
    if table_num:
        return f"t#{table_num}"
    # Final fallback: sanitize the room name
    return re.sub(r"[^A-Za-z0-9_-]", "_", room_name)[:30] or fallback


def snapshot_to_overlay_msg(snap: dict, advisor_out, table_id: str,
                            room_name: str = "") -> dict:
    """
    Build the table_update message the overlay expects from a CoinPoker
    snapshot + AdvisorOutput.

    The overlay renders pot/stack/call with a euro prefix — for CoinPoker
    play-chip tables this displays as "€<chips>.<dd>" which is visually
    quirky but unambiguous. We embed "(chips)" in the site name so the
    user knows the units.
    """
    # Convert scaled-int "cents" back to chip floats so the overlay's
    # ``f"€{pot:.2f}"`` formatting renders the chip count.
    from coinpoker_adapter import CHIP_SCALE
    def to_chips(v):
        return (v or 0) / CHIP_SCALE

    rec = advisor_out.action if advisor_out else ""
    color = "neutral"
    rec_upper = rec.upper()
    if any(k in rec_upper for k in ("RAISE", "BET", "CALL")):
        color = "green"
    elif "FOLD" in rec_upper:
        color = "red"
    elif "CHECK" in rec_upper:
        color = "blue"

    cards_str = " ".join(snap["hero_cards"])
    board_str = " ".join(snap["board_cards"])

    eq = getattr(advisor_out, "equity", None) if advisor_out else None

    pot_chips = to_chips(snap.get("pot", 0))
    stack_chips = to_chips(snap.get("hero_stack", 0))
    call_chips = to_chips(snap.get("call_amount", 0))
    pot_odds = None
    if snap.get("facing_bet") and call_chips > 0 and pot_chips > 0:
        pot_odds = call_chips / (pot_chips + call_chips)

    return {
        "type": "table_update",
        "table_id": table_id,
        "site": "CoinPoker (chips)",
        "stake": parse_room_stake(room_name),
        "position": snap.get("position", ""),
        "cards": cards_str,
        "board": board_str,
        "phase": snap.get("phase", ""),
        "equity": eq,
        "pot": pot_chips,
        "stack": stack_chips,
        "facing_bet": bool(snap.get("facing_bet")),
        "call": call_chips,
        "pot_odds": pot_odds,
        "rec": rec,
        "rec_color": color,
        "opponent": "",
    }


# ── default snapshot printer (used when --print-only) ─────────────────────────

def make_console_printer() -> Callable[[dict], None]:
    """A snapshot consumer that just prints a one-line summary. No advisor."""
    def _print(snap: dict) -> None:
        hero = " ".join(snap["hero_cards"]) or "??"
        board = " ".join(snap["board_cards"]) or "-"
        flag = "*" if snap["hero_turn"] else " "
        print(f"{flag}[{snap['phase']:7}] hand={snap['hand_id']:>10} "
              f"pos={snap['position']:3} hero={hero:5} board={board:14} "
              f"pot={snap['pot']:>9} call={snap['call_amount']:>7} "
              f"facing={snap['facing_bet']!s:5} stack={snap['hero_stack']:>9}")
    return _print


# ── opponent tracker adapter ──────────────────────────────────────────────────

def _last_aggressor_user_id(snap):
    """
    Find the user_id of the most recent aggressor (highest bet among
    non-hero players in the snapshot). Used to look up HUD stats for
    the same villain the OpponentTracker.classify_villain considers
    the primary opponent — keeping both sources apples-to-apples.

    Returns None if there's no clear aggressor or hero is the only
    player with a bet.
    """
    if not snap:
        return None
    players = snap.get("players") or []
    hero_seat = snap.get("hero_seat")
    if not players or not isinstance(players[0], dict):
        return None
    best_uid = None
    best_bet = -1
    for p in players:
        if p.get("seat") == hero_seat:
            continue
        bet = p.get("bet", 0) or 0
        if bet > best_bet:
            best_bet = bet
            best_uid = p.get("user_id")
    if best_uid is not None:
        return best_uid
    # Fallback: pick any non-hero player with a user_id
    for p in players:
        if p.get("seat") == hero_seat:
            continue
        if p.get("user_id"):
            return p.get("user_id")
    return None


class _CoinPokerTrackerAdapter:
    """
    Wraps OpponentTracker so it can consume CoinPoker-format snapshots.

    The original OpponentTracker was built for the Unibet WS reader, where
    ``state["players"]`` is a list of player names indexed by seat-position
    in the list, and ``state["bets"]`` is a parallel list. CoinPoker
    snapshots, in contrast, have ``players`` as a list of dicts (each
    with ``seat``, ``user_id``, ``name``, ``bet``, ``last_action``).

    This adapter translates the CoinPoker shape into the Unibet shape on
    every call. The wrapped tracker is shared with Unibet code, so we
    must NOT mutate it — all conversion happens at the boundary.
    """

    def __init__(self, inner_tracker, hero_user_id: int,
                 bb_cents_provider=None, hud_loader=None):
        self._inner = inner_tracker
        self._hero_user_id = int(hero_user_id)
        # Callable that returns the current bb in scaled chip units.
        # Set to a closure pointing at session.bb_cents so the tracker
        # reads the real BB amount and not the Unibet NL2 default of 4.
        self._bb_cents_provider = bb_cents_provider
        # Optional CoinPokerHudLoader. If provided AND it has data for
        # the active villain, classify_villain prefers the HUD result
        # over the tracker's. This is the "HUD-first" behavior — HUD
        # is the server-side ground truth, tracker is the fallback.
        self._hud_loader = hud_loader

    def _convert(self, state: dict) -> dict:
        """Translate a CoinPoker snapshot to Unibet tracker format.
        Pass-through if already converted (or if state is empty)."""
        if not state:
            return state
        cp_players = state.get('players')
        if not cp_players or not isinstance(cp_players[0], dict):
            return state  # already in tracker format

        names = [(p.get('name') or '') for p in cp_players]
        bets = [p.get('bet', 0) for p in cp_players]
        hero_idx = -1
        for i, p in enumerate(cp_players):
            if p.get('user_id') == self._hero_user_id:
                hero_idx = i
                break
        # Shallow copy + override the four fields the tracker reads.
        # bb_amt MUST be passed through — without it the tracker uses
        # its Unibet NL2 default of 4, which is way smaller than any
        # CoinPoker bet, marking every player as VPIP every hand and
        # corrupting the persistent HandDB stats. Discovered 2026-04-09
        # by side-by-side comparison vs CoinPoker's HUD ground truth.
        out = dict(state)
        out['players'] = names
        out['bets'] = bets
        out['hero_seat'] = hero_idx
        # Surface the BB amount (in scaled chip units) so the tracker's
        # VPIP threshold matches the actual table BB.
        if 'bb_amt' not in out and self._bb_cents_provider is not None:
            try:
                out['bb_amt'] = self._bb_cents_provider()
            except Exception:
                pass
        return out

    def update(self, state: dict) -> None:
        try:
            self._inner.update(self._convert(state))
        except Exception as e:
            print(f"[tracker] update failed: {type(e).__name__}: {e}")

    def classify_villain(self, state: dict) -> str:
        # Prefer the HUD ground truth when available. The tracker is
        # the fallback because it can be corrupted by the bb_amt-mismatch
        # bug discovered 2026-04-09 (corruption persists in HandDB across
        # sessions even after the bug is fixed). HUD has clean
        # server-side stats and instant convergence.
        if self._hud_loader is not None and state:
            cp_players = state.get('players') or []
            if cp_players and isinstance(cp_players[0], dict):
                hero_seat = state.get('hero_seat')
                best_uid = None
                best_b = -1
                for p in cp_players:
                    if p.get('seat') == hero_seat:
                        continue
                    b = p.get('bet', 0) or 0
                    if b > best_b:
                        best_b = b
                        best_uid = p.get('user_id')
                if best_uid is not None:
                    hud_class = self._hud_loader.classify(best_uid)
                    if hud_class and hud_class != "UNKNOWN":
                        return hud_class
        # Fallback: existing OpponentTracker (potentially corrupted)
        try:
            return self._inner.classify_villain(self._convert(state)) or "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def get_table_summary(self, hero_seat, players):
        """
        AdvisorStateMachine calls this with ``state["hero_seat"]`` and
        ``state["players"]`` directly. The state is in CoinPoker format
        at that point — players is a list of dicts. Convert here.
        """
        try:
            if players and isinstance(players[0], dict):
                hero_idx = -1
                for i, p in enumerate(players):
                    if p.get('user_id') == self._hero_user_id:
                        hero_idx = i
                        break
                names = [(p.get('name') or '') for p in players]
                return self._inner.get_table_summary(hero_idx, names)
            return self._inner.get_table_summary(hero_seat, players)
        except Exception:
            return ""

    def flush(self) -> None:
        try:
            self._inner.flush()
        except Exception as e:
            print(f"[tracker] flush failed: {type(e).__name__}: {e}")

    def get_villain_hud_stats(self, state: dict) -> Optional[dict]:
        """
        Return the HUD stats dict for the active villain (highest non-hero
        bet) in this state, or None if no HUD data is available for them.

        This is the bridge that lets AdvisorStateMachine query the
        CoinPoker server-side ground-truth stats without coupling to the
        HUD loader directly. Used by the v1 equity-vs-range discount.
        """
        if self._hud_loader is None or not state:
            return None
        cp_players = state.get('players') or []
        if not cp_players or not isinstance(cp_players[0], dict):
            return None
        hero_seat = state.get('hero_seat')
        best_uid = None
        best_bet = -1
        for p in cp_players:
            if p.get('seat') == hero_seat:
                continue
            b = p.get('bet', 0) or 0
            if b > best_bet:
                best_bet = b
                best_uid = p.get('user_id')
        if best_uid is None:
            return None
        return self._hud_loader.get_stats(best_uid)


# ── advisor wiring (deferred imports so tests don't pay the cost) ─────────────

def make_advisor_callback(session: CoinPokerSession,
                          overlay: Optional[OverlayClient] = None,
                          room_name: str = ""):
    """
    Lazy-load AdvisorStateMachine and return a callback that drives it
    on each snapshot. Uses ``session.bb_cents`` so the BB scale matches
    the table being observed.

    OpponentTracker is wired via _CoinPokerTrackerAdapter so VPIP/PFR/AF
    accumulate per villain across hands and persist to HandDB. SessionLogger
    is still skipped (Unibet-specific format).

    If ``overlay`` is provided, each snapshot also renders to it via the
    table_update protocol.
    """
    print("[runner] loading advisor dependencies ...")
    from advisor import Advisor as BaseAdvisor  # noqa: WPS433
    from preflop_chart import preflop_advice    # noqa: WPS433
    from advisor_state_machine import AdvisorStateMachine  # noqa: WPS433

    try:
        from strategy.postflop_engine import PostflopEngine
        postflop = PostflopEngine()
        print("[runner]   PostflopEngine loaded")
    except Exception as e:
        print(f"[runner]   PostflopEngine SKIPPED: {e}")
        postflop = None

    try:
        from advisor import assess_board_danger
    except ImportError:
        assess_board_danger = lambda h, b: {"warnings": []}

    base = BaseAdvisor(use_overlay=False, terminal=False, debug=False, unibet=True)
    print("[runner]   base Advisor loaded")

    # CoinPoker server-side HUD stats loader. Constructed FIRST so the
    # tracker adapter can wrap it. The SM prefers HUD classification
    # over the tracker when HUD has data; tracker is fallback only.
    # 2026-04-09: discovered that the tracker's HandDB had corrupted
    # VPIP counts (>100%) due to a bb_amt mismatch with CoinPoker's
    # chip scale. HUD ground truth is now the preferred source until
    # the tracker corruption is cleaned and re-validated.
    hud_loader = None
    try:
        from coinpoker_hud_loader import CoinPokerHudLoader
        hud_loader = CoinPokerHudLoader()
        if len(hud_loader) > 0:
            print(f"[runner]   CoinPokerHudLoader loaded "
                  f"({len(hud_loader)} ground-truth player profiles)")
        else:
            print(f"[runner]   CoinPokerHudLoader empty -- "
                  f"run tools/coinpoker_stats_sniffer.py to capture")
    except Exception as e:
        print(f"[runner]   CoinPokerHudLoader SKIPPED: {type(e).__name__}: {e}")
        hud_loader = None

    # Opponent tracker -- wraps the shared OpponentTracker with a CoinPoker
    # adapter that converts dict-format snapshots to the Unibet name-list
    # format the tracker expects. Persistent across sessions via HandDB.
    # Now also wraps the HUD loader so classify_villain prefers HUD when
    # available, falls back to the (potentially-corrupted) tracker stats.
    tracker = None
    try:
        from opponent_tracker import OpponentTracker
        from hand_db import HandDB
        db = HandDB()
        inner_tracker = OpponentTracker(db=db)
        tracker = _CoinPokerTrackerAdapter(
            inner_tracker,
            hero_user_id=session.hero_user_id,
            bb_cents_provider=lambda: session.bb_cents_for_room(),
            hud_loader=hud_loader,
        )
        print(f"[runner]   OpponentTracker loaded "
              f"({len(inner_tracker.players)} known players)")
    except Exception as e:
        print(f"[runner]   OpponentTracker SKIPPED: {type(e).__name__}: {e}")
        tracker = None

    # Per-room state machines + caches. For single-table mode, only the
    # empty-string key "" is ever used. For multi-table mode, each
    # room_name gets its own SM with isolated action history etc.
    # The base Advisor and PostflopEngine are SHARED across SMs (heavy
    # to load, immutable in operation), so per-room SMs are cheap.
    sms: dict[str, "AdvisorStateMachine"] = {}
    caches: dict[str, dict] = {}

    def get_sm_for_room(room: str) -> "AdvisorStateMachine":
        if room in sms:
            return sms[room]
        new_sm = AdvisorStateMachine(
            base_advisor=base,
            preflop_advice_fn=preflop_advice,
            postflop_engine=postflop,
            assess_board_danger_fn=assess_board_danger,
            tracker=tracker,
            bb_cents=session.bb_cents_for_room(room or None),
        )
        sms[room] = new_sm
        if room:
            print(f"[runner]   AdvisorStateMachine ready for room={room!r} "
                  f"(bb_cents={new_sm.bb_cents})")
        else:
            print(f"[runner]   AdvisorStateMachine ready (bb_cents={new_sm.bb_cents})")
        return new_sm

    # Eagerly create the single-table SM so the existing log line still
    # appears even if no snapshot has fired yet (matches the old runner
    # output for tests / smoke checks).
    get_sm_for_room("")

    # Register flush on shutdown so any in-session VPIP/PFR updates land
    # in HandDB. Safe even if tracker is None.
    if tracker is not None:
        import atexit
        atexit.register(tracker.flush)

    def on_snapshot(snap: dict) -> None:
        # Multi-table dispatch: each room has its own SM + cache so
        # action history and last-rec state don't cross-contaminate.
        # In single-table mode, room is "" and behavior is identical
        # to the pre-refactor flow.
        room = snap.get("room_name", "") or ""
        sm = get_sm_for_room(room)
        if room not in caches:
            caches[room] = {"hand": None, "out": None}
        cache = caches[room]

        # Refresh bb_cents in case the table BB changed (e.g. user
        # switched practice → real money mid-session, or because we
        # only just learned the BB for this room).
        room_bb = session.bb_cents_for_room(room or None)
        if sm.bb_cents != room_bb:
            sm.bb_cents = room_bb

        # Feed the opponent tracker on every snapshot — VPIP/PFR/AF
        # accumulate from preflop bet patterns, so we need to see the
        # snapshot stream regardless of hero_turn. Failures are logged
        # by the adapter; do not block the recommendation pipeline.
        if tracker is not None:
            tracker.update(snap)

        # New hand → drop the cached rec.
        if cache["hand"] != snap["hand_id"]:
            cache["hand"] = snap["hand_id"]
            cache["out"] = None

        # Compute a fresh recommendation only when it's hero's turn AND
        # hero has cards (not spectating). Otherwise reuse the cached
        # rec for display so the overlay stays informative.
        out = None
        if snap["hero_turn"] and len(snap["hero_cards"]) >= 2:
            out = sm.process_state(snap)
            if out is not None and out.action:
                cache["out"] = out

        display_out = cache["out"]

        # Console line — only print when the advisor JUST produced a fresh
        # action this turn. Avoids spamming the same rec on every villain
        # bet update.
        if out is not None and out.action:
            hero = " ".join(snap["hero_cards"]) or "??"
            board = " ".join(snap["board_cards"]) or "-"
            # Show villain classification source breakdown:
            #   - "v:TYPE" — the classification the SM actually used
            #   - "src:hud" or "src:trk" — which source provided it
            #     (HUD is preferred when it has data; tracker is fallback)
            #   - if the two sources DISAGREE, show "(trk:OTHER)" so we
            #     can spot disagreements without losing the comparison
            sm_class = ""  # what the SM saw via tracker.classify_villain
            if tracker is not None:
                try:
                    sm_class = (tracker.classify_villain(snap) or "").upper()
                except Exception:
                    sm_class = ""

            # Direct lookup in HUD and underlying tracker (bypassing
            # the prefer-HUD logic) so we can show both sides
            hud_class_raw = ""
            trk_class_raw = ""
            if hud_loader is not None:
                hud_uid = _last_aggressor_user_id(snap)
                if hud_uid is not None:
                    hud_class_raw = (hud_loader.classify(hud_uid) or "").upper()
            if tracker is not None and hasattr(tracker, "_inner"):
                try:
                    converted = tracker._convert(snap)
                    trk_class_raw = (tracker._inner.classify_villain(converted) or "").upper()
                except Exception:
                    trk_class_raw = ""

            # Source label
            src = ""
            if sm_class and hud_class_raw == sm_class:
                src = "hud"
            elif sm_class and trk_class_raw == sm_class:
                src = "trk"

            tag_parts = []
            if sm_class and sm_class != "UNKNOWN":
                tag_parts.append(f"v:{sm_class}")
            if src:
                tag_parts.append(f"src:{src}")
            # Show the OTHER source's answer if it disagrees (for analysis)
            if (hud_class_raw and trk_class_raw
                    and hud_class_raw != trk_class_raw
                    and hud_class_raw != "UNKNOWN"
                    and trk_class_raw != "UNKNOWN"):
                other = trk_class_raw if src == "hud" else hud_class_raw
                other_label = "trk" if src == "hud" else "hud"
                tag_parts.append(f"({other_label}:{other})")
            opp_tag = (" " + " ".join(tag_parts)) if tag_parts else ""
            print(f"*** [{out.phase:7}] {hero:5}  board={board:14}  "
                  f"pos={snap['position']:3}  eq={out.equity:.0%}  "
                  f"=> {out.action}{opp_tag}")

        # Overlay update — fire on every state change so the HUD reflects
        # current cards/board/phase even when the advisor has no fresh rec.
        # Per-table routing: each room gets its own table_id, which makes
        # the overlay render a separate TablePanel per table side-by-side.
        # Multi-table mode shows N panels; single-table mode shows 1.
        if overlay is not None:
            try:
                snap_room = snap.get("room_name") or ""
                table_id_for_panel = (
                    room_to_table_id(snap_room, fallback=overlay.table_id)
                    if snap_room else overlay.table_id
                )
                msg = snapshot_to_overlay_msg(
                    snap, display_out,
                    table_id=table_id_for_panel,
                    room_name=snap_room or room_name,
                )
                overlay.send(msg)
            except Exception as e:
                print(f"[overlay] build failed: {type(e).__name__}: {e}")

    return on_snapshot


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CoinPoker advisor runner")
    p.add_argument("--file", default=DEFAULT_LOG,
                   help="JSONL frame log to read (default: %(default)s)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--follow", action="store_true",
                      help="Tail the file (live mode, default)")
    mode.add_argument("--replay", action="store_true",
                      help="Read once start-to-finish then exit")
    p.add_argument("--hero-id", type=int, default=HERO_USER_ID_DEFAULT,
                   help="CoinPoker userId to treat as hero (default: %(default)s)")
    p.add_argument("--bb-chips", type=int, default=None,
                   help="Override big-blind chip amount (default: auto-detect)")
    p.add_argument("--print-only", action="store_true",
                   help="Skip advisor wiring; just print state snapshots")
    p.add_argument("--no-overlay", action="store_true",
                   help="Don't spawn the Tk overlay (default: spawn in --follow, "
                        "skip in --replay/--print-only)")
    p.add_argument("--multi-table", action="store_true",
                   help="Multi-table mode: maintain separate state per "
                        "room_name. Use this when playing 2+ tables "
                        "simultaneously. Each table gets its own SM, "
                        "action history, and rec cache. Single-table is "
                        "the default for backwards compat.")
    args = p.parse_args(argv)

    if not args.follow and not args.replay:
        args.follow = True  # default

    if not os.path.exists(args.file):
        print(f"frame log not found: {args.file}", file=sys.stderr)
        return 2

    # Overlay: spawn only in live mode unless explicitly disabled.
    overlay: Optional[OverlayClient] = None
    want_overlay = (args.follow and not args.print_only and not args.no_overlay)
    if want_overlay:
        overlay = OverlayClient.spawn()

    # Build session with a placeholder that's swapped after we know bb_cents.
    # MultiTableCoinPokerSession is structurally interchangeable with
    # CoinPokerSession (same API surface) so make_advisor_callback works
    # against either without modification.
    callback_holder: list = [None]
    session_cls = MultiTableCoinPokerSession if args.multi_table else CoinPokerSession
    session = session_cls(
        hero_user_id=args.hero_id,
        on_snapshot=lambda snap: callback_holder[0](snap) if callback_holder[0] else None,
        bb_chips=args.bb_chips,
    )
    if args.multi_table:
        print("[runner] MULTI-TABLE mode enabled — per-room state isolation")

    if args.print_only:
        callback_holder[0] = make_console_printer()
        print(f"[runner] print-only mode, hero_id={args.hero_id}, file={args.file}")
    else:
        callback_holder[0] = make_advisor_callback(session, overlay=overlay)

    print("=" * 60)
    print(f"  CoinPoker Advisor Runner — {'follow' if args.follow else 'replay'} mode")
    print("=" * 60)

    import atexit
    def _cleanup():
        if overlay is not None:
            try:
                overlay.remove_table()
            except Exception:
                pass
            overlay.close()
    atexit.register(_cleanup)

    try:
        if args.replay:
            session.run(replay_iter(args.file))
            print(f"\n[runner] replay done. frames={session.frames_seen} "
                  f"snapshots={session.snapshots_dispatched}")
        else:
            # Warmup: silently feed the existing file into the builder(s)
            # so they know the current hand_id / hero_seat / blinds
            # before we start dispatching. Without this, --follow seeks
            # to EOF and snapshot() returns None for every new frame
            # because the builder never sees the seed events.
            #
            # Uses session.warmup() which both CoinPokerSession and
            # MultiTableCoinPokerSession implement. The multi-table
            # version routes to per-room builders without firing the
            # on_snapshot callback.
            print(f"[runner] warming up builder from {args.file} ...")
            with open(args.file, "r", encoding="utf-8") as wf:
                warmup_n = session.warmup(wf)
            # Builder summary — for single-table this is the only builder,
            # for multi-table it's the most recently seen room's builder.
            b = session.builder
            if b is not None:
                print(f"[runner] warmup ingested {warmup_n} frames; "
                      f"hand={b.hand_id} hero_seat={b.hero_seat} "
                      f"phase={b.phase}")
            else:
                print(f"[runner] warmup ingested {warmup_n} frames "
                      f"(no rooms seen yet)")
            if hasattr(session, "_builders"):
                room_count = len(session._builders)
                if room_count > 1:
                    print(f"[runner] {room_count} rooms tracked: "
                          f"{', '.join(sorted(session._builders.keys())[:4])}"
                          f"{' ...' if room_count > 4 else ''}")
            # Force one dispatch of the current state so the overlay is
            # populated immediately rather than empty until the next change.
            if b is not None:
                current = b.snapshot()
                if current is not None and callback_holder[0] is not None:
                    # Inject room_name for multi-table compatibility
                    if hasattr(session, "_most_recent_room") and session._most_recent_room:
                        current = dict(current)
                        current["room_name"] = session._most_recent_room
                    try:
                        callback_holder[0](current)
                    except Exception as e:
                        print(f"[runner] warmup snapshot dispatch failed: {e}")
            print(f"[runner] tailing {args.file} — Ctrl+C to stop\n")
            session.run(follow_iter(args.file))
    except KeyboardInterrupt:
        print(f"\n[runner] stopped. frames={session.frames_seen} "
              f"snapshots={session.snapshots_dispatched}")
    finally:
        _cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
