"""SEO helpers: canonical-host redirect and the server-rendered /browser page."""

from html import escape
from urllib.parse import quote

BROWSER_ROWS_MAX = 100

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


def _as_int(value, default: int = 0) -> int:
    """Upstream (BattleMetrics) values as int; anything unparseable becomes the default."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _has_valid_port(server: dict) -> bool:
    return 1 <= _as_int(server.get("port")) <= 65535


def _browser_row_html(server: dict) -> str:
    href = f"/server/{quote(str(server['host']), safe='')}:{_as_int(server['port'])}"
    return (
        '<div class="browse-row">'
        f'<div class="brow-name"><a class="brow-link" href="{escape(href)}">'
        f"<strong>{escape(str(server.get('name') or ''))}</strong></a></div>"
        f'<div class="brow-pop">{_as_int(server.get("players_count"))}'
        f'<span class="max">/{_as_int(server.get("max_players"))}</span></div>'
        f'<div class="brow-map">{escape(str(server.get("map") or "—"))}</div>'
        '<div class="brow-wipe">—</div>'
        '<div class="brow-actions"></div></div>'
    )


def browser_rows_html(servers: list[dict], limit: int = BROWSER_ROWS_MAX) -> str:
    ranked = sorted(
        (s for s in servers if s.get("host") and _has_valid_port(s)),
        key=lambda s: _as_int(s.get("players_count")),
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
