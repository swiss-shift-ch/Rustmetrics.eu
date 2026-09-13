"""Remembers Rust servers whose A2S data is blocked or name-anonymized, so the poller asks BattleMetrics first."""

import threading
import time

from rm_rust import ANON_DETECTION_MIN_NAMES, detect_name_anonymization

RECHECK_AFTER_SEC = 6 * 3600
MODE_FULL = "full"
MODE_PLAYERS = "players"
MODE_ANON = "anonymized"
PLAYER_LIST_FROM_BM_MODES = (MODE_PLAYERS, MODE_ANON)


def bm_names_confirmed_real(bm_names: list[str]) -> bool:
    """Enough names for the heuristic to judge, and they don't look randomized."""
    return len(bm_names) >= ANON_DETECTION_MIN_NAMES and not detect_name_anonymization(bm_names)


def should_use_bm_names(a2s_names: list[str], bm_names: list[str]) -> bool:
    if not bm_names:
        return False
    if not a2s_names:
        return True
    return detect_name_anonymization(a2s_names) and bm_names_confirmed_real(bm_names)


def classify_a2s_block(a2s_online: bool, a2s_names: list[str], a2s_players_failed: bool,
                       bm_online: bool, bm_names: list[str]) -> str | None:
    """Blocked mode from one full A2S probe; only BattleMetrics-confirmed cases count."""
    if not a2s_online:
        return MODE_FULL if bm_online else None
    if (a2s_players_failed or not a2s_names) and bm_names:
        return MODE_PLAYERS
    if detect_name_anonymization(a2s_names) and bm_names_confirmed_real(bm_names):
        return MODE_ANON
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

    def record(self, host: str, port: int, *, a2s_online: bool, a2s_names: list[str],
               a2s_players_failed: bool, bm_online: bool, bm_names: list[str],
               now: float | None = None) -> str | None:
        """Stores the result of a full A2S probe and returns the resulting mode."""
        now = time.time() if now is None else now
        new_mode = classify_a2s_block(a2s_online, a2s_names, a2s_players_failed, bm_online, bm_names)
        with self._lock:
            if new_mode is None:
                self._entries.pop((host, port), None)
            else:
                self._entries[(host, port)] = (new_mode, now)
        return new_mode
