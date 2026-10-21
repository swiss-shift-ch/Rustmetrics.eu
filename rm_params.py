"""Query-string parsing for the public API: bounded integers, no DB access."""
from __future__ import annotations


def int_param(raw: str | None, default: int, lo: int, hi: int) -> int:
    """Integer query value clamped to [lo, hi]; missing or empty gives `default`.

    Raises ValueError for non-integer input so the handler can answer 400
    instead of a 500. Clamping both ends matters: a negative `limit` used to
    slice `out[offset:offset + limit]` from the end and returned nearly the
    whole server list, bypassing BROWSE_RESULT_CAP.
    """
    if raw is None or not raw.strip():
        return max(lo, min(hi, default))
    return max(lo, min(hi, int(raw.strip())))
