"""Win32 table focus manager for shadow mode.

Brings the correct CoinPoker table window to the foreground when hero
needs to act. Includes cooldown, dedup, and priority queueing.

Non-Windows: all operations are no-ops (logged and skipped).
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Dict, Optional, Tuple

_IS_WIN32 = sys.platform == "win32"

if _IS_WIN32:
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32

    # Type aliases
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _enum_windows_by_title(substring: str) -> list:
        """Return list of (hwnd, title) for visible windows matching substring."""
        results = []

        @WNDENUMPROC
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if substring in title:
                results.append((hwnd, title))
            return True

        user32.EnumWindows(callback, 0)
        return results

    def _get_foreground_hwnd() -> int:
        return user32.GetForegroundWindow()

    def _set_foreground(hwnd: int) -> bool:
        """Attempt to bring hwnd to foreground. Returns True on success."""
        # ShowWindow(SW_RESTORE) in case it's minimized
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(hwnd))

else:
    def _enum_windows_by_title(substring: str) -> list:
        return []

    def _get_foreground_hwnd() -> int:
        return 0

    def _set_foreground(hwnd: int) -> bool:
        return False


# Focus request dedup key
_FocusKey = Tuple[str, str, str]  # (room_name, hand_id, phase)


class FocusManager:
    """Manages table focus with cooldown and dedup.

    Constructor args:
      cooldown_secs: minimum time between focus changes (prevents thrash)
      on_event: optional callback(table_id, hand_id, succeeded, reason)
                for logging focus events externally
    """

    def __init__(
        self,
        cooldown_secs: float = 2.0,
        on_event: Optional[Callable] = None,
    ):
        self._cooldown = cooldown_secs
        self._on_event = on_event
        self._last_focus_ts: float = 0.0
        self._last_focus_hwnd: int = 0
        self._seen_keys: set = set()
        # Cache room_name → hwnd so we don't EnumWindows every frame
        self._hwnd_cache: Dict[str, int] = {}
        self._hwnd_cache_ts: float = 0.0
        self._CACHE_TTL = 10.0  # re-enumerate every 10s

    def _find_hwnd(self, room_name: str) -> Optional[int]:
        """Find the HWND for a CoinPoker table by room_name substring."""
        now = time.monotonic()
        if now - self._hwnd_cache_ts > self._CACHE_TTL:
            self._hwnd_cache.clear()
            self._hwnd_cache_ts = now

        if room_name in self._hwnd_cache:
            return self._hwnd_cache[room_name]

        # CoinPoker table window titles contain the room name
        matches = _enum_windows_by_title(room_name)
        if not matches:
            # Try a shorter substring (table number at the end)
            parts = room_name.split()
            if parts:
                last_num = [p for p in parts if p.isdigit()]
                if last_num:
                    matches = _enum_windows_by_title(last_num[-1])

        if matches:
            hwnd = matches[0][0]
            self._hwnd_cache[room_name] = hwnd
            return hwnd
        return None

    def request_focus(
        self,
        room_name: str,
        hand_id: str,
        phase: str,
        table_id: str = "",
        pot: int = 0,
    ) -> bool:
        """Request focus for a table. Returns True if focus was changed.

        Skips if:
          - same (room, hand, phase) already focused
          - cooldown not expired
          - table already has foreground
          - hwnd not found
          - not on Win32
        """
        if not _IS_WIN32:
            self._emit(table_id, hand_id, False, "not_win32")
            return False

        # Dedup: same decision point
        key: _FocusKey = (room_name, str(hand_id), phase)
        if key in self._seen_keys:
            return False  # already handled, no event
        self._seen_keys.add(key)

        # Cooldown
        now = time.monotonic()
        if now - self._last_focus_ts < self._cooldown:
            self._emit(table_id, hand_id, False, "cooldown")
            return False

        hwnd = self._find_hwnd(room_name)
        if hwnd is None:
            self._emit(table_id, hand_id, False, "hwnd_not_found")
            return False

        # Already foreground?
        if _get_foreground_hwnd() == hwnd:
            self._emit(table_id, hand_id, True, "already_foreground")
            return True

        ok = _set_foreground(hwnd)
        self._last_focus_ts = now
        self._last_focus_hwnd = hwnd

        if ok:
            self._emit(table_id, hand_id, True, "")
        else:
            self._emit(table_id, hand_id, False, "set_foreground_failed")
        return ok

    def clear_hand(self, room_name: str, hand_id: str) -> None:
        """Clear dedup keys for a finished hand so a new phase in the
        same hand can re-trigger focus (shouldn't happen, but defensive)."""
        to_remove = [
            k for k in self._seen_keys
            if k[0] == room_name and k[1] == str(hand_id)
        ]
        for k in to_remove:
            self._seen_keys.discard(k)

    def _emit(
        self, table_id: str, hand_id: str, succeeded: bool, reason: str
    ) -> None:
        if self._on_event is not None:
            try:
                self._on_event(table_id, hand_id, succeeded, reason)
            except Exception:
                pass
