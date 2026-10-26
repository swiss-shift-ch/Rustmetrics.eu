"""RustMetrics — Konfiguration.

Alle Env-Variablen und Konstanten an einem Ort. Kein Laufzeit-State,
keine Abhaengigkeiten auf andere rm_*-Module (unterste Schicht).
"""
from __future__ import annotations

import os

def _env(k, d=None, cast=str):
    v = os.environ.get(k, d)
    if v is None: return None
    try: return cast(v)
    except Exception: return d

HOST            = _env("RM_HOST", "127.0.0.1")
PORT            = _env("RM_PORT", 8765, int)
ORIGIN          = _env("RM_ORIGIN", "https://rustmetrics.eu").rstrip("/")

PG_DSN = (
    f"host={_env('RM_PGHOST', '127.0.0.1')} "
    f"port={_env('RM_PGPORT', 5432, int)} "
    f"user={_env('RM_PGUSER', 'rustmetrics')} "
    f"password={_env('RM_PGPASSWORD', '')} "
    f"dbname={_env('RM_PGDATABASE', 'rustmetrics')}"
)

ADMIN_STEAMIDS   = set(
    s.strip() for s in (_env("RM_ADMIN_STEAMIDS", "") or "").split(",") if s.strip()
)
STEAM_API_KEY    = _env("RM_STEAM_API_KEY", "") or None
SESSION_TTL      = _env("RM_SESSION_TTL", 30 * 86400, int)
# Untergrenze: bei 0/negativ liefe poller_loop ohne Pause (Dauer-Abfrage aller
# Server bzw. Fehler-Spam, wenn die DB weg ist).
POLL_INTERVAL_MIN = 5
POLL_INTERVAL    = max(POLL_INTERVAL_MIN, _env("RM_POLL_INTERVAL_SEC", 20, int))
A2S_TIMEOUT      = _env("RM_A2S_TIMEOUT_SEC", 3.0, float)
SNAPSHOT_TTL_DAY = _env("RM_SNAPSHOTS_KEEP_DAYS", 14, int)
# Wie lange die volle Spielerliste pro Snapshot aufgehoben wird. Danach bleibt
# der Snapshot (fuer den Graphen), nur players_json wird geleert.
PLAYERS_JSON_KEEP_H = _env("RM_PLAYERS_JSON_KEEP_HOURS", 24, int)

BROWSE_CACHE_TTL = _env("RM_BROWSE_CACHE_TTL", 180, int)
BROWSE_PARALLEL  = _env("RM_BROWSE_PARALLEL", 80, int)
BROWSE_TIMEOUT   = _env("RM_BROWSE_A2S_TIMEOUT", 2.5, float)
# BattleMetrics paginiert mit max. 100 Servern/Page. Früher hart auf 8 Pages
# (= 800 Server) begrenzt — dadurch zeigte der Browser "800 of 800 online".
# Jetzt limit-getrieben mit konfigurierbarer Obergrenze (40 Pages = 4000 Server;
# BM erlaubt unauth ~60 req/min, also nicht beliebig hochdrehen).
BROWSE_BM_MAX_PAGES = _env("RM_BROWSE_BM_MAX_PAGES", 40, int)
# Hartes Cap für den ?limit=-Parameter von /api/browse (Zeilen pro Response).
BROWSE_RESULT_CAP   = _env("RM_BROWSE_RESULT_CAP", 2000, int)

RL_BROWSE_PER_H  = _env("RM_RL_BROWSE_PER_HOUR", 12, int)
RL_REQ_PER_MIN   = _env("RM_RL_REQUESTS_PER_MIN", 120, int)

# Caddy-Access-Log Pfad (für /admin/stats Page). Auf nicht-existierende Datei
# fällt der Parser still zurück auf "log unavailable".
CADDY_LOG_PATH   = _env("RM_CADDY_LOG", "/var/log/caddy/rustmetrics.log")
CADDY_LOG_MAX_BYTES = _env("RM_CADDY_LOG_MAX_BYTES", 5 * 1024 * 1024, int)  # nur die letzten 5 MB lesen

ROOT             = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR       = os.path.join(ROOT, "static")
TEMPLATES_DIR    = os.path.join(ROOT, "templates")

RUST_APPID       = 252490
# Valve hat den Public-Master-Server Q4-2025 abgeschaltet (DNS NXDOMAIN, IPs tot).
# Wir benutzen jetzt einen Hybrid-Stack:
#   1. Steam Web API (IGameServersService/GetServerList) — wenn RM_STEAM_API_KEY gesetzt
#   2. BattleMetrics Public API als Fallback — ohne Key, rate-limited
STEAM_GETSERVERLIST_URL = "https://api.steampowered.com/IGameServersService/GetServerList/v1/"
STEAM_GETUSERSTATS_URL  = "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v2/"
STEAM_GETPLAYERSUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
STEAM_RESOLVEVANITY_URL      = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
BATTLEMETRICS_URL       = "https://api.battlemetrics.com/servers"
BATTLEMETRICS_PLAYER_URL = "https://api.battlemetrics.com/players"

# BattleMetrics hat die oeffentliche API 2026 hinter ein Abo gestellt:
#   403 {"detail":"Access denied. A subscription is required to use the API."}
# Ohne Token ist jeder Aufruf verlorene Zeit (bis 10s Timeout im Request-Thread)
# und Log-Rauschen. Deshalb: optionaler Token + Schutzschalter, der nach
# mehreren 403ern zumacht und es erst nach BM_BREAKER_COOLDOWN wieder versucht.
BM_TOKEN            = _env("RM_BM_TOKEN", "") or None
BM_BREAKER_FAILS    = _env("RM_BM_BREAKER_FAILS", 3, int)
BM_BREAKER_COOLDOWN = _env("RM_BM_BREAKER_COOLDOWN", 21600, int)   # 6h
BM_USER_AGENT       = "RustMetrics/1.0 (+https://rustmetrics.eu)"

# Cache-TTL für Rust-Stats (Steam-Counter ändern sich langsam, 1h reicht für Live-Page).
RUST_STATS_CACHE_TTL = _env("RM_RUST_STATS_CACHE_TTL", 3600, int)

# Steam-Presence-Polling (Watched Players per SteamID64 — funktioniert auch auf
# Servern mit server.censorplayerlist/Streamer-Mode, z.B. Facepunch-Officials).
# GetPlayerSummaries erlaubt 100 IDs pro Call; 60s-Intervall ist weit unter
# Steams Rate-Limit (100k Calls/Tag).
PRESENCE_POLL_SEC = _env("RM_PRESENCE_POLL_SEC", 60, int)

# Region-Mapping für BM (BM filtert nach ISO-Ländercodes statt Steam-Region-Bytes)
REGION_TO_COUNTRIES = {
    "eu":   "DE,FR,NL,GB,CH,AT,SE,FI,NO,PL,IT,ES,IE,DK,BE,CZ,HU,PT,RO",
    "us-e": "US",  # BM differenziert nicht E/W
    "us-w": "US",
    "sa":   "BR,AR,CL,UY,CO,PE,MX",
    "asia": "JP,KR,HK,SG,TW,TH,VN,ID,MY",
    "au":   "AU,NZ",
    "me":   "AE,SA,IL,TR",
    "af":   "ZA",
}
# Wie viele Server wir maximal vom Master holen. Rust hat ~9'000-10'000
# registrierte Server; GetServerList hat KEINE Pagination, keinen Sort-Param
# und liefert in Valves interner (quasi-willkuerlicher) Reihenfolge.
# Ein zu kleines Limit ergibt also einen zufaelligen Teil-Slice — grosse
# Netzwerke (Moose, Rustafied, Facepunch-Officials) fehlen dann einfach
# und die Suche findet sie nie, weil sie nur den Cache filtert.
# 20000 holt die komplette Liste in einem Call (~3-5 MB JSON).
BROWSE_MASTER_LIMIT = _env("RM_BROWSE_MASTER_LIMIT", 20000, int)
# Steams Region-Byte im GetServerList-Filter; 0xFF = "keine Region-Einschraenkung".
STEAM_REGION_ALL = 0xFF
REGION_BYTES     = {
    "all": STEAM_REGION_ALL, "us-e": 0x00, "us-w": 0x01, "sa": 0x02,
    "eu":  0x03, "asia": 0x04, "au":  0x05, "me": 0x06, "af": 0x07,
}
COOKIE_NAME      = "rm_session"

A2S_HEADER             = b"\xFF\xFF\xFF\xFF"
A2S_INFO_REQUEST       = A2S_HEADER + b"TSource Engine Query\x00"
A2S_PLAYER_REQHDR      = b"\x55"
A2S_CHALLENGE_TYPE     = b"\x41"
