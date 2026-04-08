"""
CoinPoker action clicker — writes pending actions to a control file
that the IL-injected ``CoinPokerInjector.Injector.Tick()`` (loaded into
the Unity table process) reads on every inbound game event.

Phase 2: dry-run. The injector LOGS the requested action without
actually calling ``UserActionHandler.UserAction``. Phase 3 will swap
the injector to the live call.

Pause flag: respects ``.autoplay_pause`` in the project root, same as
the Unibet auto-player. Default behavior on a fresh runner startup is
PAUSED — the operator must explicitly toggle to enable any action
emission. Per the no-live-without-tests memory, no live click can be
issued without an explicit operator opt-in.

The clicker is intentionally STATELESS aside from a couple of file
paths. Humanizer integration (timing distributions, mouse-path-style
delays, mistake injection) is handled by the caller — this module is
just the OS-level handoff.

The previous (replica-era) DOM clicker is preserved at
``coinpoker_clicker_legacy_dom.py`` for reference but is unused.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Callable, Optional

VISION_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VISION_DIR)

# Defaults match the IL injector's hard-coded paths in
# coinpoker_patcher/CoinPokerInjector/Injector.cs.
DEFAULT_CONTROL_PATH = r"C:\Users\Simon\coinpoker_pending_action.json"
DEFAULT_LOG_PATH     = r"C:\Users\Simon\coinpoker_inject.log"
DEFAULT_PAUSE_FLAG   = os.path.join(ROOT, ".autoplay_pause")

# Action names accepted by the injector. These map 1:1 onto
# PBCommon.TableEvents.Enums.ActionId in Phase 3:
#     Check = 3, Call = 4, Raise = 5, AllIn = 6, Fold = 7
VALID_ACTIONS = frozenset({"FOLD", "CHECK", "CALL", "RAISE", "ALLIN"})


class CoinPokerClicker:
    """
    Writes JSON action requests to a control file. The IL-injected
    helper inside the Unity process polls for this file and either
    logs (Phase 2) or fires the action (Phase 3).

    Constructor args are all paths so tests can stub them with temp
    locations.
    """

    def __init__(self,
                 control_path: str = DEFAULT_CONTROL_PATH,
                 pause_flag_path: str = DEFAULT_PAUSE_FLAG,
                 inject_log_path: str = DEFAULT_LOG_PATH,
                 default_paused: bool = True,
                 current_hand_provider: Optional[Callable[[], Optional[str]]] = None):
        """
        ``current_hand_provider`` — optional callable returning the
        observed-current hand_id from the live frame stream. When set,
        ``request_action(action, hand_id, ...)`` rejects the call if the
        provider's current hand is None or differs from the requested
        ``hand_id``. This prevents the race where:

            1. Python decides to act on hand H1
            2. Hand ends and H2 starts before the control file is written
            3. The IL would otherwise consume the file under hand H2 and
               fire an action for the wrong hand
        """
        self.control_path = control_path
        self.pause_flag_path = pause_flag_path
        self.inject_log_path = inject_log_path
        self.current_hand_provider = current_hand_provider
        self.requests_sent = 0
        self.requests_blocked = 0
        self.requests_stale = 0
        # On startup, ensure the pause flag exists by default. The
        # operator must explicitly resume(). Subsequent runs of the
        # advisor share the same flag file across processes.
        if default_paused and not os.path.exists(self.pause_flag_path):
            try:
                with open(self.pause_flag_path, "w", encoding="utf-8") as f:
                    f.write("paused at startup\n")
            except Exception:
                pass

    # ── pause control ──

    def is_paused(self) -> bool:
        return os.path.exists(self.pause_flag_path)

    def pause(self) -> None:
        try:
            with open(self.pause_flag_path, "w", encoding="utf-8") as f:
                f.write("paused\n")
        except Exception:
            pass

    def resume(self) -> None:
        try:
            if os.path.exists(self.pause_flag_path):
                os.unlink(self.pause_flag_path)
        except Exception:
            pass

    # ── action emission ──

    def request_action(self,
                       action: str,
                       hand_id: str,
                       size: Optional[float] = None,
                       reason: str = "") -> bool:
        """
        Write a pending action to the control file.

        Returns True if written, False if blocked (paused, invalid
        action, or already-pending action that hasn't been picked up
        yet).

        Writes are atomic: we write to a temp file in the same
        directory and ``os.replace`` it onto the control path so the
        injector never sees a partial file.
        """
        action = action.upper().strip()
        if action not in VALID_ACTIONS:
            self.requests_blocked += 1
            return False
        if size is not None and size < 0:
            self.requests_blocked += 1
            return False
        if self.is_paused():
            self.requests_blocked += 1
            return False
        # Hand-id staleness check: if a current_hand_provider is wired
        # up, refuse to write a request whose hand_id no longer matches
        # the live observed hand. This prevents Phase 3 from clicking
        # an action for a hand that's already over.
        if self.current_hand_provider is not None:
            try:
                live_hand = self.current_hand_provider()
            except Exception:
                live_hand = None
            if live_hand is None or str(live_hand) != str(hand_id):
                self.requests_stale += 1
                return False
        # Don't pile up — if a previous request hasn't been consumed
        # yet, drop this one. Either the injector hasn't fired or
        # CoinPoker isn't running. Either way, queueing helps no one.
        if os.path.exists(self.control_path):
            self.requests_blocked += 1
            return False

        payload = {
            "action": action,
            "size": size,
            "handId": hand_id,
            "ts": time.time(),
            "reason": reason,
        }

        target_dir = os.path.dirname(self.control_path) or "."
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            pass

        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".pending_", suffix=".json.tmp", dir=target_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, self.control_path)
        except Exception as e:
            print(f"[clicker] write failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            self.requests_blocked += 1
            return False

        self.requests_sent += 1
        return True

    # ── verification ──

    def tail_inject_log(self, since_offset: int = 0) -> tuple[list[str], int]:
        """
        Read new lines from the inject log starting at byte offset
        ``since_offset``. Returns ``(lines, new_offset)``. Returns an
        empty list if the log doesn't exist.

        Used by tests / integration code to confirm the IL-injected
        Tick() actually picked up a request.
        """
        if not os.path.exists(self.inject_log_path):
            return [], since_offset
        try:
            with open(self.inject_log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(since_offset)
                data = f.read()
                new_offset = f.tell()
        except Exception:
            return [], since_offset
        if not data:
            return [], new_offset
        lines = [l for l in data.split("\n") if l.strip()]
        return lines, new_offset
