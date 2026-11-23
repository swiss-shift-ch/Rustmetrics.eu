"""Validation for numeric row IDs taken from URL paths."""

import re

MAX_PG_INT = 2**31 - 1  # servers.id / watched_players.id sind SERIAL (int4)

_ID_RE = re.compile(r"[0-9]{1,10}")


def parse_pg_id(raw: str | None) -> int | None:
    """Positive SERIAL id from a path segment, or None.

    Only ASCII digits: int() would also take «+5», « 5», «1_0» or «٣», and values
    beyond int4 made Postgres raise outside the handler's try → dropped connection.
    """
    if raw is None or not _ID_RE.fullmatch(raw):
        return None
    value = int(raw)
    return value if 0 < value <= MAX_PG_INT else None
