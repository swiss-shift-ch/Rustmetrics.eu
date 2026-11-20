"""Format check for opaque URL tokens before they reach the database."""

import re

# secrets.token_urlsafe(32) liefert 43 Zeichen aus [A-Za-z0-9_-]; Spielraum fuer aeltere/laengere Tokens.
CALENDAR_TOKEN_MIN_LEN = 16
CALENDAR_TOKEN_MAX_LEN = 128
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")


def is_plausible_calendar_token(token: str | None) -> bool:
    """True if the token could have come from secrets.token_urlsafe.

    Rejects NUL bytes (psycopg refuses them with ValueError, which surfaced as a
    dropped connection instead of 401), other foreign characters and absurd lengths.
    """
    if not isinstance(token, str):
        return False
    if not CALENDAR_TOKEN_MIN_LEN <= len(token) <= CALENDAR_TOKEN_MAX_LEN:
        return False
    return _TOKEN_RE.fullmatch(token) is not None
