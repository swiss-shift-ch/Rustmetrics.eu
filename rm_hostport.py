"""Split a typed server address into host and port (pure, no network access)."""
from __future__ import annotations

_ASCII_DIGITS = frozenset("0123456789")


def parse_port(text: str) -> int | None:
    """Port 1..65535 from ASCII digits only; None otherwise (str.isdigit also accepts e.g. '²')."""
    if not text or not set(text) <= _ASCII_DIGITS:
        return None
    port = int(text)
    return port if 0 < port < 65536 else None


def split_host_port(text: str, port: int) -> tuple[str, int]:
    """Host and port from 'host:port' (port in the text wins over the form field); otherwise unchanged.

    Surrounding slashes/spaces from copied links ('1.2.3.4:28015/') are ignored.
    Bare IPv6 addresses (several colons) are left as they are.
    """
    cleaned = text.strip("/ ")
    if cleaned.count(":") == 1:
        left, right = cleaned.split(":", 1)
        parsed = parse_port(right)
        if left and parsed is not None:
            return left, parsed
    return cleaned, port
