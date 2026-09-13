"""Throttled "last seen" tracking for signed-in users, so admin stats measure real activity instead of logins."""

LAST_SEEN_TOUCH_INTERVAL_SEC = 3600

# Letzte Aktivitaet: last_seen_at (Tracking ab 09/2026), fuer aeltere Nutzer ersatzweise der letzte Login.
ACTIVITY_TS_SQL = "GREATEST(COALESCE(last_seen_at, 0), COALESCE(last_login_at, 0))"


def should_touch_last_seen(last_seen_at: int | None, now: int,
                           interval: int = LAST_SEEN_TOUCH_INTERVAL_SEC) -> bool:
    """True when the stored timestamp is missing, at least `interval` old, or implausibly in the future."""
    if last_seen_at is None:
        return True
    return last_seen_at > now or now - last_seen_at >= interval
