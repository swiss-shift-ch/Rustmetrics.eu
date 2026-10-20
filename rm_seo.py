"""SEO helpers: canonical-host redirect and the server-rendered /browser page."""

from html import escape
from urllib.parse import quote, unquote

BROWSER_ROWS_MAX = 100
MAX_HOST_LEN = 253

_HOME_TITLE = "<title>RustMetrics — Rust Server &amp; Player Tracker</title>"
_BROWSER_TITLE = "<title>Rust Server Browser — Live Pop, Wipes &amp; Filters | RustMetrics</title>"
_HOME_DESCRIPTION = (
    '<meta name="description" content="RustMetrics — focused, ad-free server &amp; player '
    "tracker for Facepunch Rust. Watchlist with Discord webhook notifications, live server "
    'browser, wipe countdown, population charts. Steam login, no email needed." />'
)
_BROWSER_DESCRIPTION = (
    '<meta name="description" content="Live Rust server browser: thousands of servers with '
    "population, queue, map and next wipe. Filter by region, vanilla/modded, solo/duo/trio "
    'and fresh wipes." />'
)


def canonical_redirect_location(host_header: str | None, request_path: str, origin: str) -> str | None:
    """Target for a 301 when the request arrived on the www host, otherwise None."""
    host = (host_header or "").strip().lower()
    if not host.startswith("www."):
        return None
    path = request_path if request_path.startswith("/") else "/"
    return f"{origin.rstrip('/')}{path}"


def parse_server_path(spec: str) -> tuple[str, int] | None:
    """(host, port) from the part after /server/; None if malformed.

    Links from the browser page percent-encode the host (quote(..., safe='')),
    so it has to be decoded here, otherwise e.g. IPv6 hosts never match.
    """
    spec = unquote(spec).rstrip("/")
    host, sep, port_str = spec.rpartition(":")
    if not sep or not host or len(host) > MAX_HOST_LEN:
        return None
    if any(c.isspace() or not c.isprintable() for c in host):  # e.g. %00 would break the DB query
        return None
    if not (port_str.isascii() and port_str.isdigit()):
        return None
    port = int(port_str)
    if not 0 < port < 65536:
        return None
    return host, port


def _browser_row_html(server: dict) -> str:
    href = f"/server/{quote(str(server['host']), safe='')}:{int(server['port'])}"
    return (
        '<div class="browse-row">'
        f'<div class="brow-name"><a class="brow-link" href="{escape(href)}">'
        f"<strong>{escape(str(server.get('name') or ''))}</strong></a></div>"
        f'<div class="brow-pop">{int(server.get("players_count") or 0)}'
        f'<span class="max">/{int(server.get("max_players") or 0)}</span></div>'
        f'<div class="brow-map">{escape(str(server.get("map") or "—"))}</div>'
        '<div class="brow-wipe">—</div>'
        '<div class="brow-actions"></div></div>'
    )


def browser_rows_html(servers: list[dict], limit: int = BROWSER_ROWS_MAX) -> str:
    ranked = sorted(
        (s for s in servers if s.get("host") and s.get("port")),
        key=lambda s: s.get("players_count") or 0,
        reverse=True,
    )
    return "".join(_browser_row_html(s) for s in ranked[:limit])


def render_browser_page(index_html: str, servers: list[dict], origin: str) -> str:
    """index.html with the browser view active, its own title/canonical and crawlable server links."""
    base = origin.rstrip("/")
    replacements = [
        (_HOME_TITLE, _BROWSER_TITLE),
        (_HOME_DESCRIPTION, _BROWSER_DESCRIPTION),
        (f'<link rel="canonical" href="{base}/" />', f'<link rel="canonical" href="{base}/browser" />'),
        (f'<meta property="og:url" content="{base}/" />', f'<meta property="og:url" content="{base}/browser" />'),
        ('<button class="nav-item is-active" data-tab="watchlist"', '<button class="nav-item" data-tab="watchlist"'),
        ('<button class="nav-item" data-tab="browse"', '<button class="nav-item is-active" data-tab="browse"'),
        (
            '<section class="view view-watchlist is-active" data-view="watchlist">',
            '<section class="view view-watchlist" data-view="watchlist" hidden>',
        ),
        (
            '<section class="view view-browse" data-view="browse" hidden>',
            '<section class="view view-browse is-active" data-view="browse">',
        ),
        (
            '<div id="browse-results" class="browse-results"></div>',
            f'<div id="browse-results" class="browse-results">{browser_rows_html(servers)}</div>',
        ),
    ]
    missing = [old for old, _ in replacements if index_html.count(old) != 1]
    if missing:
        raise ValueError(f"index.html markers not found exactly once: {missing}")
    html = index_html
    for old, new in replacements:
        html = html.replace(old, new, 1)
    return html
