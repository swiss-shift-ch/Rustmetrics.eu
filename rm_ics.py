"""Wipe calendar (ICS) helpers: pure functions, no DB access."""

from __future__ import annotations

# Serveruhren gehen teils vor: born bis so weit in der Zukunft gilt als "gerade gewipt".
BORN_FUTURE_TOLERANCE_SEC = 86400


def ics_wipe_base_ts(born_ts: int | None, now: int) -> int | None:
    """Last wipe from a server's 'born' keyword, or None when it cannot be a real wipe time.

    'born' comes straight from the server operator's keywords; a negative or absurd value
    (e.g. born99999999999999999999) must skip that server, not break the whole feed.
    """
    if not born_ts or born_ts < 0:
        return None
    if born_ts > now + BORN_FUTURE_TOLERANCE_SEC:
        return None
    return born_ts
