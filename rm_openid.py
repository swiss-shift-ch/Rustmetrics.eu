"""Strict local checks of a Steam OpenID response before it is verified with Steam."""

import re

_STEAM_CLAIMED_ID = re.compile(r"https?://steamcommunity\.com/openid/id/([0-9]+)", re.ASCII)


def steamid_from_claimed_id(claimed: str | None) -> int | None:
    """SteamID64 from a claimed_id URL; the whole value must match (no trailing newline, ASCII digits only)."""
    m = _STEAM_CLAIMED_ID.fullmatch(claimed or "")
    return int(m.group(1)) if m else None


def openid_precheck(query: dict, origin: str) -> int | None:
    """SteamID64 when mode, claimed_id, return_to and a nonce are plausible; None otherwise.

    A nonce is required so the replay protection can never be skipped.
    """
    if query.get("openid.mode") != "id_res":
        return None
    if not query.get("openid.return_to", "").startswith(f"{origin}/auth/steam/callback"):
        return None
    if not query.get("openid.response_nonce"):
        return None
    return steamid_from_claimed_id(query.get("openid.claimed_id"))
