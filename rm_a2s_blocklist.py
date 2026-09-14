"""Remembers Rust servers whose A2S data is blocked or name-anonymized, so the poller asks BattleMetrics first."""

import threading
import time

from rm_rust import detect_name_anonymization, is_pool_style_name

RECHECK_AFTER_SEC = 6 * 3600
MODE_FULL = "full"
MODE_PLAYERS = "players"
MODE_ANON = "anonymized"
PLAYER_LIST_FROM_BM_MODES = (MODE_PLAYERS, MODE_ANON)

MIN_COMPARE_NAMES = 3  # low enough that low-pop servers (poller always BM-checks those) still get compared
POOL_NAME_RATIO = 0.70
MAX_SHARED_NAME_RATIO = 0.10

# detect_name_anonymization()/looks_randomized()'s sample-size gates never fire on a
# small server's full player list — poll_server_row() always cross-checks against
# BattleMetrics below this population instead of waiting for the heuristic to trigger.
LOW_POP_ALWAYS_BM_CHECK = 10


def looks_randomized(names: list[str]) -> bool:
    """Full heuristic for big lists; for small lists a high share of pool-style names is enough suspicion."""
    if detect_name_anonymization(names):
        return True
    if len(names) < MIN_COMPARE_NAMES:
        return False
    return sum(1 for n in names if is_pool_style_name(n)) / len(names) >= POOL_NAME_RATIO


def names_look_real(names: list[str]) -> bool:
    return len(names) >= MIN_COMPARE_NAMES and not looks_randomized(names)


def bm_names_replace_randomized(a2s_names: list[str], bm_names: list[str]) -> bool:
    """A2S names look randomized while BattleMetrics has different, real-looking names (RCON-connected server)."""
    if not looks_randomized(a2s_names) or not names_look_real(bm_names):
        return False
    shared = len({n.lower() for n in a2s_names} & {n.lower() for n in bm_names})
    return shared <= MAX_SHARED_NAME_RATIO * min(len(a2s_names), len(bm_names))


def should_use_bm_names(a2s_names: list[str], bm_names: list[str]) -> bool:
    if not bm_names:
        return False
    if not a2s_names:
        return True
    return bm_names_replace_randomized(a2s_names, bm_names)


def classify_a2s_block(a2s_online: bool, a2s_names: list[str], a2s_players_failed: bool,
                       bm_online: bool, bm_names: list[str]) -> str | None:
    """Blocked mode from one full A2S probe; only BattleMetrics-confirmed cases count."""
    if not a2s_online:
        return MODE_FULL if bm_online else None
    if (a2s_players_failed or not a2s_names) and bm_names:
        return MODE_PLAYERS
    if bm_names_replace_randomized(a2s_names, bm_names):
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
