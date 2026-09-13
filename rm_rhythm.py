"""Online rhythm for watched players: pure interval math, no DB access.

Sources: Steam presence sessions ("steam") and A2S/BattleMetrics player
sessions ("server"). All timestamps are UTC unix seconds.
"""
from __future__ import annotations

RHYTHM_DAYS = 7
RHYTHM_WINDOW_SEC = RHYTHM_DAYS * 86400

SOURCE_STEAM = "steam"
SOURCE_SERVER = "server"

ACTION_OPEN = "open"
ACTION_CLOSE = "close"

Interval = tuple[int, int]


def _addr_ip(addr: str | None) -> str | None:
    """'ip:port' -> 'ip'. Steam reports the game port, so only the IP identifies a server."""
    if not addr:
        return None
    return addr.rsplit(":", 1)[0] if ":" in addr else addr


def presence_session_actions(has_open: bool, open_addr: str | None,
                             in_rust: bool, server_addr: str | None) -> tuple[str, ...]:
    """Decide how presence_sessions must change for one observation.

    Based on the stored open session rather than the previous presence row, so
    a player who is already online when tracking starts still gets a session.
    """
    if not in_rust:
        return (ACTION_CLOSE,) if has_open else ()
    if not has_open:
        return (ACTION_OPEN,)
    old_ip, new_ip = _addr_ip(open_addr), _addr_ip(server_addr)
    if old_ip and new_ip and old_ip != new_ip:
        return (ACTION_CLOSE, ACTION_OPEN)
    return ()


def clip_interval(start: int, end: int | None, window_start: int, now: int) -> Interval | None:
    """Clip one session to [window_start, now]; open sessions end at now. None if nothing remains."""
    effective_end = now if end is None else min(end, now)
    clipped_start = max(start, window_start)
    if effective_end <= clipped_start:
        return None
    return (clipped_start, effective_end)


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Sorted union of intervals; overlapping or touching ones are joined. Input is not modified."""
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _clip_all(rows: list[tuple[int, int | None]], window_start: int, now: int) -> list[Interval]:
    clipped = (clip_interval(int(s), None if e is None else int(e), window_start, now)
               for s, e in rows)
    return [iv for iv in clipped if iv is not None]


def build_rhythm(steam_rows: list[tuple[int, int | None]],
                 server_rows: list[tuple[int, int | None]],
                 now: int, days: int = RHYTHM_DAYS) -> dict:
    """API payload: merged intervals of both sources within the last `days` days."""
    window_start = now - days * 86400
    steam = _clip_all(steam_rows, window_start, now)
    server = _clip_all(server_rows, window_start, now)
    sources = [name for name, ivs in ((SOURCE_STEAM, steam), (SOURCE_SERVER, server)) if ivs]
    intervals = [[s, e] for s, e in merge_intervals(steam + server)]
    return {
        "available": bool(intervals),
        "days": days,
        "now": now,
        "intervals": intervals,
        "sources": sources,
    }
