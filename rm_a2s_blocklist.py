"""Remembers Rust servers that block A2S, so the poller asks BattleMetrics first instead of waiting for timeouts."""

import threading
import time

RECHECK_AFTER_SEC = 6 * 3600
MODE_FULL = "full"
MODE_PLAYERS = "players"


def classify_a2s_block(a2s_online: bool, a2s_players_failed: bool,
                       bm_online: bool, bm_has_players: bool) -> str | None:
    """Blocked mode from one full A2S probe; only BattleMetrics-confirmed blocks count."""
    if not a2s_online:
        return MODE_FULL if bm_online else None
    if a2s_players_failed and bm_has_players:
        return MODE_PLAYERS
    return None


class A2SBlocklist:
    """Thread-safe (host, port) -> blocked mode; an entry expires so A2S gets re-probed."""

    def __init__(self, recheck_after_sec: float = RECHECK_AFTER_SEC):
        self._recheck_after_sec = recheck_after_sec
        self._entries: dict[tuple[str, int], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def mode(self, host: str, port: int, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        key = (host, port)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            mode, since = entry
            if now - since >= self._recheck_after_sec:
                del self._entries[key]
                return None
            return mode

    def record(self, host: str, port: int, *, a2s_online: bool, a2s_players_failed: bool,
               bm_online: bool, bm_has_players: bool, now: float | None = None) -> str | None:
        """Stores the result of a full A2S probe and returns the resulting mode."""
        now = time.time() if now is None else now
        new_mode = classify_a2s_block(a2s_online, a2s_players_failed, bm_online, bm_has_players)
        with self._lock:
            if new_mode is None:
                self._entries.pop((host, port), None)
            else:
                self._entries[(host, port)] = (new_mode, now)
        return new_mode
