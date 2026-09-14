#!/usr/bin/env python3
"""
RustMetrics — Public Multi-User Edition.

Public-Service-Version mit:
  • Steam OpenID 2.0 Login
  • Per-User Watchlist (eigene Watched-Server + Watched-Player)
  • Geteilte Snapshot-Daten (1× Polling pro Server, alle User profitieren)
  • Postgres-Backend
  • Rate-Limiting pro IP
  • Caddy-/systemd-ready

Konfiguration über Environment-Variablen (siehe .env.example).
"""

from __future__ import annotations

import ipaddress
import json
import glob
import gzip
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as _FuturesTimeout
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler


# ── Modul-Split (Reddit-Feedback): Schichten unten, Fassade hier ─────────────
# rustmetrics.py bleibt Entry-Point und re-exportiert die Namen, damit
# systemd-Start und test_presence.py unveraendert funktionieren.
from rm_config import (  # noqa: F401
    _env, HOST, PORT, ORIGIN, PG_DSN, ADMIN_STEAMIDS, STEAM_API_KEY,
    SESSION_TTL, POLL_INTERVAL, A2S_TIMEOUT, SNAPSHOT_TTL_DAY,
    PLAYERS_JSON_KEEP_H, BROWSE_CACHE_TTL, BROWSE_PARALLEL, BROWSE_TIMEOUT,
    BROWSE_BM_MAX_PAGES, BROWSE_RESULT_CAP, RL_BROWSE_PER_H, RL_REQ_PER_MIN,
    CADDY_LOG_PATH, CADDY_LOG_MAX_BYTES, ROOT, STATIC_DIR, TEMPLATES_DIR,
    RUST_APPID, STEAM_GETSERVERLIST_URL, STEAM_GETUSERSTATS_URL,
    STEAM_GETPLAYERSUMMARIES_URL, STEAM_RESOLVEVANITY_URL,
    BATTLEMETRICS_URL, BATTLEMETRICS_PLAYER_URL, BM_TOKEN, BM_BREAKER_FAILS,
    BM_BREAKER_COOLDOWN, BM_USER_AGENT, RUST_STATS_CACHE_TTL,
    PRESENCE_POLL_SEC, REGION_TO_COUNTRIES, BROWSE_MASTER_LIMIT,
    STEAM_REGION_ALL, REGION_BYTES, COOKIE_NAME,
)
from rm_a2s import query_a2s  # noqa: F401
from rm_rhythm import (ACTION_CLOSE, ACTION_OPEN, PRESENCE_STALE_POLLS, RHYTHM_WINDOW_SEC,
                       build_rhythm, presence_session_actions, stale_session_end)
from rm_a2s_blocklist import (MODE_FULL, PLAYER_LIST_FROM_BM_MODES, A2SBlocklist,
                              looks_randomized, names_look_real, should_use_bm_names)
from rm_rust import (  # noqa: F401
    parse_rust_keywords, detect_rust_tier, detect_rust_category,
    detect_rust_region, detect_rust_group_limit, detect_name_anonymization,
    next_wipe_estimate, pick_bm_server_id, match_presence_server, presence_query_port,
    _REGION_FILTER_ALIASES, _REGION_GROUPS,
)
from rm_db import init_pool, _Conn, ensure_migrations, ensure_indexes  # noqa: F401
from rm_seo import canonical_redirect_location, render_browser_page
from rm_activity import ACTIVITY_TS_SQL, should_touch_last_seen
from rm_skins import refresh_skin_prices, SKINPORT_MIN_INTERVAL_SEC, MARKETPLACES


_bm_state = {"fails": 0, "blocked_until": 0.0, "logged": False, "paused_until": 0.0}
_bm_lock  = threading.Lock()
BM_RATE_LIMIT_PAUSE_SEC = 60


class BMPaused(RuntimeError):
    """BattleMetrics answered 429; requests are held back until the pause ends."""


def _bm_pause_after_429(retry_after: str | None) -> None:
    try:
        pause = max(1.0, float(retry_after)) if retry_after else BM_RATE_LIMIT_PAUSE_SEC
    except ValueError:
        pause = BM_RATE_LIMIT_PAUSE_SEC
    with _bm_lock:
        now = time.time()
        starts_pause = now >= _bm_state["paused_until"]
        _bm_state["paused_until"] = max(_bm_state["paused_until"], now + pause)
    if starts_pause:
        print(f"[battlemetrics] HTTP 429 — pausiere BM-Anfragen fuer {int(pause)}s",
              file=sys.stderr, flush=True)

def bm_available() -> bool:
    with _bm_lock:
        return time.time() >= _bm_state["blocked_until"]

def _bm_note_result(ok: bool, code: int | None = None) -> None:
    with _bm_lock:
        if ok:
            _bm_state["fails"] = 0
            _bm_state["logged"] = False
            return
        if code in (401, 403):
            _bm_state["fails"] += 1
            if _bm_state["fails"] >= BM_BREAKER_FAILS:
                _bm_state["blocked_until"] = time.time() + BM_BREAKER_COOLDOWN
                if not _bm_state["logged"]:
                    _bm_state["logged"] = True
                    print(f"[battlemetrics] API verweigert (HTTP {code}) — Abo noetig. "
                          f"Fallback fuer {BM_BREAKER_COOLDOWN//3600}h deaktiviert. "
                          f"Mit RM_BM_TOKEN reaktivierbar.", file=sys.stderr, flush=True)

def bm_get(url: str, timeout: float = 10.0):
    """Einziger Weg zu BattleMetrics — mit Token, Schutzschalter und Logging."""
    if not bm_available():
        raise RuntimeError("battlemetrics disabled (breaker open)")
    with _bm_lock:
        if time.time() < _bm_state["paused_until"]:
            raise BMPaused("battlemetrics rate-limited, pausing")
    headers = {"User-Agent": BM_USER_AGENT, "Accept": "application/json"}
    if BM_TOKEN:
        headers["Authorization"] = f"Bearer {BM_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        _bm_note_result(True)
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _bm_pause_after_429(e.headers.get("Retry-After"))
        _bm_note_result(False, e.code)
        raise
    except Exception:
        _bm_note_result(False)
        raise



# ═══════════════════════════════════════════════════════════════════════════
#  Server-List-Datenquellen (Steam-Web-API + BattleMetrics-Fallback)
#  Valve hat den Public-UDP-Master Q4-2025 abgeschaltet — diese zwei HTTP-APIs
#  ersetzen ihn.
# ═══════════════════════════════════════════════════════════════════════════

def query_steam_web_api(region: int = STEAM_REGION_ALL, limit: int = BROWSE_MASTER_LIMIT) -> list[dict]:
    """
    IGameServersService/GetServerList — Steam Web API.
    Liefert komplette Server-Daten in einem HTTPS-Call, kein separates A2S nötig.
    Filter-Syntax identisch zum alten Master-Protokoll.
    Braucht STEAM_API_KEY in der .env.
    """
    if not STEAM_API_KEY:
        return []
    filter_parts = [rf"\appid\{RUST_APPID}", r"\dedicated\1"]
    if region != STEAM_REGION_ALL:
        filter_parts.append(rf"\region\{region}")
    params = {"key": STEAM_API_KEY, "filter": "".join(filter_parts), "limit": str(limit)}
    url = STEAM_GETSERVERLIST_URL + "?" + urllib.parse.urlencode(params)
    try:
        # 30s: bei limit=20000 ist die Response ~3-5 MB
        with urllib.request.urlopen(url, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[steam_web_api] error: {e}", file=sys.stderr, flush=True)
        return []
    out = []
    for s in payload.get("response", {}).get("servers", []):
        addr = s.get("addr", "")
        if ":" not in addr:
            continue
        host, port_str = addr.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            continue
        out.append({
            "host": host, "port": port,
            "name": s.get("name") or "",
            "map":  s.get("map"),
            "players_count": s.get("players") or 0,
            "max_players":   s.get("max_players") or 0,
            "ping_ms":       None,
            "keywords":      s.get("gametype") or "",   # Steam liefert Keywords als "gametype"
            "online":        True,
            "_source":       "steam_web_api",
        })
    return out


def query_battlemetrics(region: str = "all", limit: int = BROWSE_MASTER_LIMIT) -> list[dict]:
    """
    Public BattleMetrics-API als Fallback. Rate-limited (~60 req/min unauth),
    aber kein Key nötig. Attribution-Pflicht bei produktivem Einsatz.
    """
    if not bm_available():
        return []
    fetched: list[dict] = []
    page_size = 100  # BM-Max pro Page
    base_params = {
        "filter[game]":   "rust",
        "filter[status]": "online",
        "page[size]":     str(page_size),
        "sort":           "-players",
    }
    if region != "all" and region in REGION_TO_COUNTRIES:
        base_params["filter[countries]"] = REGION_TO_COUNTRIES[region]
    url = BATTLEMETRICS_URL + "?" + urllib.parse.urlencode(base_params)
    # So viele Pages wie das Limit verlangt, gedeckelt durch BROWSE_BM_MAX_PAGES
    max_pages = min(BROWSE_BM_MAX_PAGES, -(-limit // page_size))  # ceil-div
    pages = 0
    while url and len(fetched) < limit and pages < max_pages:
        pages += 1
        try:
            payload = bm_get(url, timeout=10)
        except Exception as e:
            print(f"[battlemetrics] page {pages} error: {e}", file=sys.stderr, flush=True)
            break
        for s in payload.get("data", []):
            a = s.get("attributes", {}) or {}
            details = a.get("details", {}) or {}
            kw_tags = details.get("rust_modded_tags") or []
            tier_hint = details.get("rust_type") or ""   # "vanilla" / "modded"
            keywords = ",".join([t for t in (kw_tags + [tier_hint]) if t])
            if a.get("status") != "online":
                continue
            fetched.append({
                "host":          a.get("ip") or "",
                "port":          a.get("port") or 28015,
                "name":          a.get("name") or "",
                "map":           details.get("map"),
                "players_count": a.get("players") or 0,
                "max_players":   a.get("maxPlayers") or 0,
                "ping_ms":       None,
                "keywords":      keywords,
                "online":        True,
                "_source":       "battlemetrics",
                "_bm_rank":      a.get("rank"),
                "_bm_country":   a.get("country"),
            })
        url = (payload.get("links") or {}).get("next")
    return fetched[:limit]


def fetch_server_list(region: str = "all", limit: int = BROWSE_MASTER_LIMIT) -> tuple[list[dict], str]:
    """
    Hybrid:
      • region='all' und Steam-Key vorhanden → Steam Web API (global, schnell, kein Dritt-Anbieter)
      • region=eu/us-e/… → BattleMetrics (deren Country-Filter ist zuverlässig;
        Steams \\region\\X Filter ist unbrauchbar, weil die meisten Rust-Server
        \\region\\-1 reporten)
    Fallback in beide Richtungen falls die primäre Quelle leer kommt.
    """
    if region == "all" and STEAM_API_KEY:
        servers = query_steam_web_api(region=STEAM_REGION_ALL, limit=limit)
        if servers:
            return servers, "steam_web_api"
        print("[fetch] Steam Web API leer, falle zurück auf BattleMetrics", file=sys.stderr, flush=True)
    servers = query_battlemetrics(region=region, limit=limit)
    if servers:
        return servers, "battlemetrics"
    # Notfall: wenn BM auch leer und Steam noch nicht versucht
    if STEAM_API_KEY:
        servers = query_steam_web_api(region=STEAM_REGION_ALL, limit=limit)
        if servers:
            return servers, "steam_web_api"
    return [], "none"


# ── Steam-Presence: Spieler per SteamID64 tracken ──────────────────────────
# Umgeht server.censorplayerlist (Streamer-Mode auf Facepunch-Officials):
# statt Namen in der (zensierten) A2S-Player-List zu suchen, fragen wir Steam
# direkt, ob der Spieler Rust spielt und auf welchem Server (gameserverip).
# Voraussetzung: Das Steam-Profil hat "Spieldetails: öffentlich".

_STEAMID64_RE    = re.compile(r"^7656\d{13}$")
_PROFILE_URL_RE  = re.compile(r"steamcommunity\.com/(profiles|id)/([^/?#]+)", re.I)

def resolve_vanity_url(vanity: str) -> int | None:
    """ResolveVanityURL: Custom-Profil-Name → SteamID64 (None wenn unbekannt)."""
    if not STEAM_API_KEY: return None
    params = {"key": STEAM_API_KEY, "vanityurl": vanity}
    try:
        with urllib.request.urlopen(
                STEAM_RESOLVEVANITY_URL + "?" + urllib.parse.urlencode(params),
                timeout=10) as r:
            resp = json.loads(r.read().decode("utf-8")).get("response", {})
        if resp.get("success") == 1:
            return int(resp["steamid"])
    except Exception as e:
        print(f"[steam_vanity] {vanity}: {e}", file=sys.stderr, flush=True)
    return None

def parse_steam_player_input(raw: str) -> int | None:
    """
    Erkennt SteamID64 oder steamcommunity-Profil-URL im Add-Watch-Input.
    Liefert die SteamID64 oder None (= normaler Spielername).
    """
    s = (raw or "").strip()
    if _STEAMID64_RE.match(s):
        return int(s)
    m = _PROFILE_URL_RE.search(s)
    if m:
        kind, val = m.group(1).lower(), m.group(2)
        if kind == "profiles" and _STEAMID64_RE.match(val):
            return int(val)
        if kind == "id":
            return resolve_vanity_url(val)
    return None

STEAM_GETFRIENDLIST_URL = "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"

def fetch_friend_list(steam_id: int) -> list[int]:
    """
    Steam-Freundesliste. Geht nur, wenn der Nutzer seine Freundesliste
    oeffentlich hat — sonst 401/403, dann leere Liste.
    """
    if not STEAM_API_KEY:
        return []
    params = {"key": STEAM_API_KEY, "steamid": str(steam_id), "relationship": "friend"}
    try:
        with urllib.request.urlopen(
                STEAM_GETFRIENDLIST_URL + "?" + urllib.parse.urlencode(params),
                timeout=12) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [int(f["steamid"]) for f in data.get("friendslist", {}).get("friends", [])]
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return []                     # Freundesliste privat — kein Fehlerfall
        print(f"[steam_friends] {steam_id}: HTTP {e.code}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[steam_friends] {steam_id}: {e}", file=sys.stderr, flush=True)
    return []


def api_friends_in_rust(user_id: int) -> dict:
    """
    Freunde des eingeloggten Nutzers, die GERADE Rust spielen — inkl. Server,
    und ob dieser Server schon auf seiner Watchlist steht.
    """
    friends = fetch_friend_list(user_id)
    if not friends:
        return {"available": False, "reason": "friends list private or empty",
                "friends": [], "total_friends": 0}
    sums = fetch_player_summaries(friends)
    playing = []
    for sid, p in sums.items():
        if str(p.get("gameid") or "") != str(RUST_APPID):
            continue
        addr = p.get("gameserverip")
        playing.append({
            "steam_id": str(sid),
            "persona":  p.get("personaname") or str(sid),
            "avatar":   p.get("avatar"),
            "server_addr": addr,
            "server_ip":   presence_ip(addr),
        })
    playing.sort(key=lambda x: (x["persona"] or "").lower())

    # Servernamen und Watchlist-Status anreichern
    ips = [f["server_ip"] for f in playing if f["server_ip"]]
    rows: list[dict] = []
    if ips:
        with _Conn() as (conn, cur):
            cur.execute(
                "SELECT s.id, s.host, s.port, s.name, "
                "  EXISTS(SELECT 1 FROM watched_servers w "
                "         WHERE w.server_id=s.id AND w.user_id=%s) AS watched "
                "FROM servers s WHERE s.host = ANY(%s)", (user_id, ips))
            rows = [dict(r) for r in cur.fetchall()]
    # Mehrere Server pro IP (z.B. Facepunch 8/9): per Port zuordnen, Watchlist-Server zuerst
    rows_watched_first = sorted(rows, key=lambda r: not r["watched"])
    browse_rows = _cached_browse_servers() if playing else []
    for f in playing:
        k = match_presence_server(rows_watched_first, f["server_ip"], f["server_addr"])
        # Server, die noch niemand beobachtet: Name und Query-Port aus der Browser-Serverliste
        listed = None if k else match_presence_server(browse_rows, f["server_ip"], f["server_addr"])
        f["server_name"]  = (k or listed or {}).get("name")
        f["server_id"]    = (k or {}).get("id")
        f["on_watchlist"] = bool((k or {}).get("watched"))
        f["server_query_port"] = (k or listed or {}).get("port") or presence_query_port(f["server_addr"])
    return {"available": True, "friends": playing, "total_friends": len(friends),
            "playing": len(playing)}


def fetch_player_summaries(steam_ids: list[int]) -> dict[int, dict]:
    """
    ISteamUser/GetPlayerSummaries für bis zu N IDs (Batches à 100).
    Relevante Felder: personaname, gameid (252490 = Rust), gameserverip,
    communityvisibilitystate (3 = öffentlich).
    """
    out: dict[int, dict] = {}
    if not STEAM_API_KEY or not steam_ids:
        return out
    for i in range(0, len(steam_ids), 100):
        chunk = steam_ids[i:i+100]
        params = {"key": STEAM_API_KEY, "steamids": ",".join(str(s) for s in chunk)}
        try:
            with urllib.request.urlopen(
                    STEAM_GETPLAYERSUMMARIES_URL + "?" + urllib.parse.urlencode(params),
                    timeout=15) as r:
                players = (json.loads(r.read().decode("utf-8"))
                           .get("response", {}).get("players", []))
            for p in players:
                try: out[int(p["steamid"])] = p
                except (KeyError, ValueError): pass
        except Exception as e:
            print(f"[steam_presence] batch error: {e}", file=sys.stderr, flush=True)
    return out


# ── BattleMetrics per-Server Player-List Fallback ─────────────────────────
# Viele grosse Rust-Server (Atlas, Moose, Reborn, Helli's …) blocken A2S_PLAYER.
# BM hat die Daten trotzdem (whitelisted/Steamworks-Pfad). Wir nutzen sie als
# Fallback wenn unser eigener A2S-Call leer/garbage zurückgibt.

_bm_id_cache: dict = {}
_bm_id_lock = threading.Lock()
BM_ID_CACHE_TTL = 86400  # 24h — BM-Server-IDs sind stabil
BM_ID_RETRY_AFTER_ERROR_SEC = 300
BM_DETAIL_CACHE_TTL = 60  # BM aktualisiert selbst seltener; spart ~2/3 der Detail-Anfragen
_bm_detail_cache: dict[int, tuple[float, dict, list[tuple[int, str]]]] = {}

def _bm_search_id(host: str, port: int) -> int | None:
    """
    BM-Search behandelt ':' im search-Term als Text-Token und matcht im Namen.
    Suchen daher nur per IP, dann clientseitig auf Port filtern.
    """
    url = (BATTLEMETRICS_URL + "?" + urllib.parse.urlencode({
        "filter[game]":    "rust",
        "filter[search]":  host,
        "page[size]":      "25",
    }))
    payload = bm_get(url, timeout=8)
    return pick_bm_server_id(payload.get("data") or [], host, port)

def get_bm_id(host: str, port: int) -> int | None:
    """Cached BM-ID-Lookup für Host:Port."""
    key = (host, port)
    with _bm_id_lock:
        entry = _bm_id_cache.get(key)
        if entry and (time.time() - entry["ts"]) < entry.get("ttl", BM_ID_CACHE_TTL):
            return entry["id"]
    if not bm_available():
        return None
    try:
        bm_id, ttl = _bm_search_id(host, port), BM_ID_CACHE_TTL
    except BMPaused:
        return None
    except Exception as err:
        # Fehler nur kurz merken: nicht 24h ohne BM, aber auch kein Retry bei jedem Poll
        print(f"[bm_search] {host}:{port}: {err}", file=sys.stderr, flush=True)
        bm_id, ttl = None, BM_ID_RETRY_AFTER_ERROR_SEC
    with _bm_id_lock:
        _bm_id_cache[key] = {"ts": time.time(), "id": bm_id, "ttl": ttl}
    return bm_id

def query_bm_server(host: str, port: int) -> tuple[dict | None, list[tuple[int, str]]]:
    """
    Holt Server-Daten + Spielerliste von BM.
    Returns (server_attrs, [(bm_player_id, name), …]) — bm_player_id ist 0 wenn nicht parsbar.
    """
    if not bm_available():
        return None, []
    bm_id = get_bm_id(host, port)
    if not bm_id:
        return None, []
    with _bm_id_lock:
        cached = _bm_detail_cache.get(bm_id)
    if cached and time.time() - cached[0] < BM_DETAIL_CACHE_TTL:
        return cached[1], cached[2]
    try:
        url = f"{BATTLEMETRICS_URL}/{bm_id}?include=player"
        payload = bm_get(url, timeout=10)
    except BMPaused:
        # Waehrend einer 429-Pause lieber den letzten Stand zeigen als "offline"
        return (cached[1], cached[2]) if cached else (None, [])
    except Exception as e:
        print(f"[bm_detail] id={bm_id}: {e}", file=sys.stderr, flush=True)
        return None, []
    attrs = ((payload.get("data") or {}).get("attributes")) or {}
    pairs: list[tuple[int, str]] = []
    for p in (payload.get("included") or []):
        if p.get("type") != "player": continue
        nm = (p.get("attributes") or {}).get("name")
        if not nm: continue
        try: pid = int(p.get("id"))
        except (TypeError, ValueError): pid = 0
        pairs.append((pid, nm))
    with _bm_id_lock:
        _bm_detail_cache[bm_id] = (time.time(), attrs, pairs)
    return attrs, pairs


# Behalten wir für individuelle Server-Polls (z.B. wenn der User direkt eine IP hinzufügt)
def query_a2s_batch(addrs, workers=BROWSE_PARALLEL, timeout=BROWSE_TIMEOUT) -> list[dict]:
    results: list[dict] = []
    if not addrs: return results
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(query_a2s, ip, port, timeout): (ip, port)
                      for ip, port in addrs}
        for fut in as_completed(future_map):
            try:
                r = fut.result()
                if r.get("online"): results.append(r)
            except Exception as e:
                # query_a2s faengt Offline/Timeout selbst ab — hier landet nur
                # Unerwartetes, und das soll sichtbar sein.
                ip, port = future_map[fut]
                print(f"[a2s_batch] {ip}:{port} {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
    return results


# In-Memory Cache für Browse-Ergebnisse
_browse_cache: dict = {}
_browse_lock = threading.Lock()

def _cache_get(key, ttl=BROWSE_CACHE_TTL):
    with _browse_lock:
        e = _browse_cache.get(key)
        if e and (time.time() - e["ts"]) < ttl:
            return e["data"]
    return None

def _cache_put(key, data):
    with _browse_lock:
        _browse_cache[key] = {"ts": time.time(), "data": data}

def _cached_browse_servers() -> list[dict]:
    """Server-Liste aus dem ersten vorhandenen Browse-Cache-Eintrag, unabhaengig vom Alter."""
    with _browse_lock:
        entry = (_browse_cache.get("browse:all")
                 or _browse_cache.get("browse:eu")
                 or next((v for k, v in _browse_cache.items() if k.startswith("browse:")), None))
    if not entry:
        return []
    raw = entry.get("data", entry)
    return raw.get("servers") or []


# ═══════════════════════════════════════════════════════════════════════════
#  STEAM OPENID 2.0
# ═══════════════════════════════════════════════════════════════════════════

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
STEAM_ID_PATTERN = re.compile(r"^https?://steamcommunity\.com/openid/id/(\d+)$")

# OpenID-Nonce-Replay-Schutz (Sicherheits-Audit #6)
_openid_nonces: dict[str, float] = {}
_openid_nonce_lock = threading.Lock()
OPENID_NONCE_TTL = 600  # 10 Minuten — Steam-Nonces sind timestamp-basiert mit kurzer Gültigkeit

def _openid_nonce_seen(nonce: str) -> bool:
    """Returns True wenn nonce schon mal gesehen (= replay-Versuch). Idempotent verzeichnet sich selbst."""
    if not nonce: return False
    now = time.time()
    with _openid_nonce_lock:
        # Lazy-Cleanup wenn map zu gross
        if len(_openid_nonces) > 2000:
            cutoff = now - OPENID_NONCE_TTL
            for k in list(_openid_nonces.keys()):
                if _openid_nonces[k] < cutoff:
                    del _openid_nonces[k]
        if nonce in _openid_nonces:
            return True
        _openid_nonces[nonce] = now
        return False

def steam_login_url() -> str:
    params = {
        "openid.ns":         "http://specs.openid.net/auth/2.0",
        "openid.mode":       "checkid_setup",
        "openid.return_to":  f"{ORIGIN}/auth/steam/callback",
        "openid.realm":      f"{ORIGIN}/",
        "openid.identity":   "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return f"{STEAM_OPENID_URL}?{urllib.parse.urlencode(params)}"


def steam_verify(query: dict) -> int | None:
    """
    Verifiziert eine OpenID-Antwort von Steam via check_authentication.
    Gibt SteamID64 (int) zurück bei Erfolg, sonst None.
    """
    if query.get("openid.mode") != "id_res": return None
    claimed = query.get("openid.claimed_id", "")
    m = STEAM_ID_PATTERN.match(claimed)
    if not m: return None
    steamid64 = int(m.group(1))
    # return_to muss zu unserer Domain passen
    expected_return = f"{ORIGIN}/auth/steam/callback"
    if not query.get("openid.return_to", "").startswith(expected_return):
        return None
    # Replay-Schutz: Steam-Nonce nur einmal akzeptieren
    nonce = query.get("openid.response_nonce", "")
    if nonce and _openid_nonce_seen(nonce):
        print(f"[openid] replay attempt rejected (nonce={nonce[:30]}…)", file=sys.stderr, flush=True)
        return None
    # check_authentication: alle Parameter zurück an Steam, mode überschreiben
    verify_params = dict(query)
    verify_params["openid.mode"] = "check_authentication"
    data = urllib.parse.urlencode(verify_params).encode("ascii")
    req = urllib.request.Request(
        STEAM_OPENID_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    is_valid = False
    for line in body.splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "is_valid" and v.strip() == "true":
            is_valid = True
            break
    return steamid64 if is_valid else None


def fetch_steam_profile(steamid64: int) -> dict:
    """Holt Display-Name + Avatar via Steam Web API (optional, braucht API-Key)."""
    if not STEAM_API_KEY:
        return {}
    url = (
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
        f"?key={STEAM_API_KEY}&steamids={steamid64}"
    )
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        players = data.get("response", {}).get("players", [])
        if not players: return {}
        p = players[0]
        return {
            "display_name": p.get("personaname"),
            "avatar_url":   p.get("avatarfull"),
            "profile_url":  p.get("profileurl"),
        }
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════
#  RUST USER-STATS (Steam GetUserStatsForGame, appid 252490)
# ═══════════════════════════════════════════════════════════════════════════
#
# Rust selber lädt ~80 In-Game-Counter pro Spieler via Steam-Stats-API hoch
# (Kills, Deaths, gesammelte Materialien, Bullet-Hits per Tier-Tier, etc.).
# Wir fetchen die via Steam Web API und cachen in DB (1h TTL).
# Profile mit privatem "Game Details"-Setting liefern 403/leeres Result,
# das wird als is_private=TRUE markiert.

# Mapping von Steam-Stat-Namen → DB-Spalte. Nur die ~30 wichtigsten;
# raw_json speichert den kompletten Response für Future-Use.
RUST_STAT_COLUMNS = {
    "seconds_played":           "seconds_played",
    "deaths":                   "deaths",
    "kill_player":              "kill_player",
    "headshot":                 "headshot",
    "wounded":                  "wounded",
    "bullet_fired":             "bullet_fired",
    "bullet_hit_player":        "bullet_hit_player",
    "bullet_hit_building":      "bullet_hit_building",
    "bullet_hit_sign":          "bullet_hit_sign",
    "bullet_hit_wolf":          "bullet_hit_wolf",
    "bullet_hit_bear":          "bullet_hit_bear",
    "bullet_hit_boar":          "bullet_hit_boar",
    "bullet_hit_stag":          "bullet_hit_stag",
    "bullet_hit_horse":         "bullet_hit_horse",
    "bullet_hit_corpse":        "bullet_hit_corpse",
    "arrow_fired":              "arrow_fired",
    "arrow_hit_player":         "arrow_hit_player",
    "arrow_hit_entity":         "arrow_hit_entity",
    "harvested_wood":           "harvested_wood",
    "harvested_stones":         "harvested_stones",
    "harvested_cloth":          "harvested_cloth",
    "harvested_leather":        "harvested_leather",
    "harvested_sulfur.ore":     "harvested_sulfur_ore",
    "harvested_metal.ore":      "harvested_metal_ore",
    "harvested_hq.metal.ore":   "harvested_hq_metal_ore",
    "acquired_scrap":           "acquired_scrap",
    "acquired_lowgradefuel":    "acquired_lowgradefuel",
    "acquired_metal.fragments": "acquired_metalfrag",
    "acquired_sulfur":          "acquired_sulfur",
    "seconds_cold":             "seconds_cold",
    "seconds_hot":              "seconds_hot",
    "seconds_comfort":          "seconds_comfort",
    "melee_thrown":             "melee_thrown",
    "c4_thrown":                "c4_thrown",
    "rocket_fired":             "rocket_fired",
}


def query_rust_user_stats(steamid64: int) -> dict:
    """Roh-Fetch von Steam ISteamUserStats/GetUserStatsForGame.
    Returns: {'is_private': bool, 'stats': {steam_stat_name: int}, 'raw': dict, 'error': str|None}"""
    if not STEAM_API_KEY:
        return {"is_private": False, "stats": {}, "raw": None, "error": "no Steam API key configured"}
    url = (f"{STEAM_GETUSERSTATS_URL}?key={STEAM_API_KEY}"
           f"&appid={RUST_APPID}&steamid={int(steamid64)}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "RustMetrics/1.0 (+https://rustmetrics.eu)"
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 403 / 400 → profile private oder Game-Details private
        if e.code in (400, 403):
            return {"is_private": True, "stats": {}, "raw": None, "error": None}
        return {"is_private": False, "stats": {}, "raw": None, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"is_private": False, "stats": {}, "raw": None, "error": f"{type(e).__name__}: {e}"}

    playerstats = raw.get("playerstats", {})
    # Manche Antworten haben "error" Field — typischerweise wenn der User Rust nie gespielt hat
    if "error" in playerstats:
        return {"is_private": False, "stats": {}, "raw": raw, "error": playerstats["error"]}
    stats_arr = playerstats.get("stats") or []
    stats = {s["name"]: int(s.get("value", 0)) for s in stats_arr if "name" in s}
    return {"is_private": False, "stats": stats, "raw": raw, "error": None}


def upsert_rust_user_stats(steamid64: int, result: dict) -> None:
    """Speichert Rust-Stats für einen User in rust_player_stats."""
    now = int(time.time())
    cols: dict = {col: result["stats"].get(steam_name) for steam_name, col in RUST_STAT_COLUMNS.items()}
    cols.update({
        "steam_id":   int(steamid64),
        "fetched_at": now,
        "is_private": bool(result.get("is_private")),
        "error":      result.get("error"),
        "raw_json":   json.dumps(result.get("raw"), separators=(",", ":")) if result.get("raw") else None,
    })
    # Build dynamic UPSERT
    col_names = list(cols.keys())
    placeholders = ",".join("%s" for _ in col_names)
    cols_sql = ",".join(col_names)
    updates = ",".join(f"{c}=EXCLUDED.{c}" for c in col_names if c != "steam_id")
    sql = (f"INSERT INTO rust_player_stats ({cols_sql}) VALUES ({placeholders}) "
           f"ON CONFLICT (steam_id) DO UPDATE SET {updates}")
    values = [cols[c] for c in col_names]
    with _Conn() as (conn, cur):
        cur.execute(sql, values)


def get_rust_user_stats(steamid64: int, force_refresh: bool = False) -> dict | None:
    """Liefert Rust-Stats aus Cache (oder fetcht frisch wenn stale/missing).
    Returns dict mit allen Stats-Spalten + Meta, oder None bei totalem Fehler.
    """
    now = int(time.time())
    if not force_refresh:
        with _Conn() as (conn, cur):
            cur.execute(
                "SELECT * FROM rust_player_stats WHERE steam_id=%s", (int(steamid64),))
            r = cur.fetchone()
            if r and (now - r["fetched_at"]) < RUST_STATS_CACHE_TTL:
                return dict(r)
    # Cache-Miss oder Force: fresh fetch
    result = query_rust_user_stats(steamid64)
    upsert_rust_user_stats(steamid64, result)
    with _Conn() as (conn, cur):
        cur.execute("SELECT * FROM rust_player_stats WHERE steam_id=%s", (int(steamid64),))
        r = cur.fetchone()
        return dict(r) if r else None


# ═══════════════════════════════════════════════════════════════════════════
#  GAME SCORES (Rust Flap Minigame)
# ═══════════════════════════════════════════════════════════════════════════

# Rate-Limit fürs Score-Submit (max 10 per User-Minute, gegen botted submissions).
_score_buckets: dict[int, TokenBucket] = {}
_score_buckets_lock = threading.Lock()

def _user_score_bucket(user_id: int) -> TokenBucket:
    with _score_buckets_lock:
        b = _score_buckets.get(user_id)
        if not b:
            # 10 / minute, also 1 token / 6s, capacity 10
            b = TokenBucket(capacity=10, refill_rate=10/60)
            _score_buckets[user_id] = b
    return b


# ── Flap-Minigame: Anti-Cheat-Plausibilitaet ──────────────────────────────
# Jede Pipe braucht ~PIPE_DIST/PIPE_SPD Frames = 220 / 2.3 / 60 ≈ 1.59s.
# Wir verlangen nur ~1.4s pro Punkt (Faktor < 1 erlaubt 'lucky early pipe',
# filtert aber Auto-Submit-Cheats), plus Puffer fuer den Spielstart.
FLAP_MAX_SCORE          = 100_000
FLAP_MIN_MS_PER_POINT   = 1400
FLAP_START_BUFFER_MS    = 300
FLAP_MAX_DURATION_MS    = 12 * 3600 * 1000   # laenger als 12h ist klar Unsinn


def submit_game_score(user_id: int, game: str, score: int,
                      duration_ms: int | None) -> tuple[bool, str | None]:
    """Speichert einen Game-Score nach Plausibilitäts-Checks.
    Returns (ok, error_message_if_not_ok).
    """
    if game not in ("flap",):
        return False, "unknown game"
    if not isinstance(score, int) or not (0 <= score <= FLAP_MAX_SCORE):
        return False, "invalid score range"
    if duration_ms is not None:
        try: dur = int(duration_ms)
        except (TypeError, ValueError): dur = 0
        min_expected_ms = score * FLAP_MIN_MS_PER_POINT - FLAP_START_BUFFER_MS
        if dur > 0 and score > 0 and dur < min_expected_ms:
            return False, "duration implausibly short for score"
        if dur > FLAP_MAX_DURATION_MS:
            return False, "duration implausibly long"
    # Rate-Limit (eigener Bucket pro User)
    if not _user_score_bucket(user_id).take(1):
        return False, "rate limited"
    try:
        with _Conn() as (conn, cur):
            cur.execute(
                "INSERT INTO game_scores (user_id, game, score, duration_ms, played_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (user_id, game, int(score),
                 int(duration_ms) if duration_ms is not None else None,
                 int(time.time())))
    except Exception as e:
        return False, f"db error: {type(e).__name__}: {e}"
    return True, None


def get_game_leaderboard(game: str, limit: int = 10) -> list[dict]:
    """Top-N Spieler nach persönlichem Bestscore (1 Zeile pro User)."""
    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT u.id, u.display_name, u.avatar_url, "
            "       MAX(gs.score) AS best, "
            "       COUNT(*) AS games_played, "
            "       MAX(gs.played_at) AS last_played "
            "FROM game_scores gs JOIN users u ON u.id = gs.user_id "
            "WHERE gs.game = %s "
            "GROUP BY u.id, u.display_name, u.avatar_url "
            "ORDER BY best DESC, MIN(gs.played_at) ASC "
            "LIMIT %s",
            (game, int(limit)))
        return [dict(r) for r in cur.fetchall()]


def get_user_game_best(user_id: int, game: str) -> int:
    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT MAX(score) AS best FROM game_scores WHERE user_id=%s AND game=%s",
            (int(user_id), game))
        r = cur.fetchone()
        return int(r["best"]) if r and r["best"] is not None else 0


def resolve_steam_id_from_bm(bm_player_id: int) -> int | None:
    """Versucht aus BattleMetrics-API die Steam-ID für einen BM-Player rauszuziehen.
    Funktioniert nur wenn der Player sein Steam öffentlich auf BM hat.
    Returns SteamID64 oder None.
    """
    try:
        url = f"{BATTLEMETRICS_PLAYER_URL}/{int(bm_player_id)}?include=identifier"
        data = bm_get(url, timeout=8)
    except Exception as e:
        print(f"[bm_steamid] {bm_player_id}: {e}", file=sys.stderr, flush=True)
        return None
    for inc in (data.get("included") or []):
        if inc.get("type") != "identifier":
            continue
        attrs = inc.get("attributes") or {}
        if attrs.get("type") == "steamID":
            v = attrs.get("identifier")
            try: return int(v)
            except (TypeError, ValueError): pass
    return None


def ensure_player_steam_id(bm_player_id: int) -> int | None:
    """Lazy resolve: liefert players.steam_id wenn vorhanden, sonst aus BM ziehen + cachen."""
    with _Conn() as (conn, cur):
        cur.execute("SELECT steam_id FROM players WHERE bm_id=%s", (int(bm_player_id),))
        r = cur.fetchone()
        if r and r["steam_id"]:
            return int(r["steam_id"])
    # Try BM
    sid = resolve_steam_id_from_bm(bm_player_id)
    if sid:
        try:
            with _Conn() as (conn, cur):
                cur.execute(
                    "UPDATE players SET steam_id=%s WHERE bm_id=%s",
                    (sid, int(bm_player_id)))
        except Exception as e:
            print(f"[steam_id_cache] {e}", file=sys.stderr, flush=True)
    return sid


# ═══════════════════════════════════════════════════════════════════════════
#  SESSIONS
# ═══════════════════════════════════════════════════════════════════════════

def create_session(user_id: int, ip: str = "", ua: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now   = int(time.time())
    with _Conn() as (conn, cur):
        cur.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at, ip, user_agent) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (token, user_id, now, now + SESSION_TTL, ip[:64], ua[:255]),
        )
    return token


def lookup_session(token: str) -> dict | None:
    if not token: return None
    now = int(time.time())
    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT s.user_id, s.expires_at, u.display_name, u.avatar_url, u.is_admin, u.last_seen_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = %s",
            (token,),
        )
        row = cur.fetchone()
        if not row: return None
        if row["expires_at"] < now:
            cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
            return None
        # Aktivitaet fuer /admin/stats: gedrosselt, damit nicht jeder Request schreibt
        if should_touch_last_seen(row["last_seen_at"], now):
            cur.execute("UPDATE users SET last_seen_at=%s WHERE id=%s", (now, row["user_id"]))
        return dict(row)


def delete_session(token: str):
    if not token: return
    with _Conn() as (conn, cur):
        cur.execute("DELETE FROM sessions WHERE token=%s", (token,))


def upsert_user(steamid64: int, profile: dict) -> None:
    now = int(time.time())
    is_admin = str(steamid64) in ADMIN_STEAMIDS
    with _Conn() as (conn, cur):
        cur.execute(
            "INSERT INTO users (id, display_name, avatar_url, profile_url, is_admin, created_at, last_login_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  display_name  = COALESCE(EXCLUDED.display_name,  users.display_name), "
            "  avatar_url    = COALESCE(EXCLUDED.avatar_url,    users.avatar_url), "
            "  profile_url   = COALESCE(EXCLUDED.profile_url,   users.profile_url), "
            "  is_admin      = EXCLUDED.is_admin, "
            "  last_login_at = EXCLUDED.last_login_at",
            (steamid64, profile.get("display_name"), profile.get("avatar_url"),
             profile.get("profile_url"), is_admin, now, now),
        )


# ═══════════════════════════════════════════════════════════════════════════
#  CALENDAR-TOKEN (für ICS-Feed) — pro User ein opakes Token
# ═══════════════════════════════════════════════════════════════════════════

def get_or_create_calendar_token(user_id: int) -> str:
    """Holt das calendar_token des Users — generiert eins falls noch keins existiert."""
    with _Conn() as (conn, cur):
        cur.execute("SELECT calendar_token FROM users WHERE id=%s", (user_id,))
        r = cur.fetchone()
        if r and r["calendar_token"]:
            return r["calendar_token"]
        # Generate new token (urlsafe, 32 bytes = 43 chars)
        new_token = secrets.token_urlsafe(32)
        cur.execute(
            "UPDATE users SET calendar_token=%s WHERE id=%s",
            (new_token, user_id))
    return new_token


def reset_calendar_token(user_id: int) -> str:
    """Generiert ein NEUES Token (invalidiert alte Subscriber-URLs). Gibt das neue zurück."""
    new_token = secrets.token_urlsafe(32)
    with _Conn() as (conn, cur):
        cur.execute(
            "UPDATE users SET calendar_token=%s WHERE id=%s",
            (new_token, user_id))
    return new_token


def lookup_user_by_calendar_token(token: str) -> int | None:
    """Findet den User zu einem Calendar-Token. None wenn unbekannt."""
    if not token or len(token) < 16:
        return None
    with _Conn() as (conn, cur):
        cur.execute("SELECT id FROM users WHERE calendar_token=%s", (token,))
        r = cur.fetchone()
        return r["id"] if r else None


def _ics_escape(s: str) -> str:
    """Escape für ICS-TEXT-Werte (RFC 5545)."""
    if s is None: return ""
    return (str(s)
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
            .replace("\r", ""))


def _ics_format_utc(ts: int) -> str:
    """Unix-TS → ICS UTC-Format YYYYMMDDTHHMMSSZ."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(int(ts)))


def render_wipes_ics(user_id: int) -> str:
    """
    Generiert einen ICS-Feed mit allen Wipes (vergangene + nächste 4) der Server,
    die der User gewatched hat. Format ist RFC 5545.
    """
    now = int(time.time())
    week = 7 * 86400
    out = ["BEGIN:VCALENDAR",
           "VERSION:2.0",
           "PRODID:-//RustMetrics//Wipe Calendar//EN",
           "CALSCALE:GREGORIAN",
           "METHOD:PUBLISH",
           "X-WR-CALNAME:RustMetrics Wipes",
           "X-WR-CALDESC:Upcoming and recent Rust server wipes for your watchlist",
           "X-PUBLISHED-TTL:PT1H",
           "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]

    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT s.id, s.host, s.port, s.name AS srv_name "
            "FROM servers s JOIN watched_servers ws ON ws.server_id=s.id "
            "WHERE ws.user_id=%s",
            (user_id,))
        servers = [dict(r) for r in cur.fetchall()]

        # Letzten Snapshot pro Server holen, daraus born_ts extrahieren
        for s in servers:
            cur.execute(
                "SELECT keywords, name FROM snapshots WHERE server_id=%s "
                "ORDER BY ts DESC LIMIT 1", (s["id"],))
            snap = cur.fetchone()
            if not snap: continue
            kw = (snap.get("keywords") or "")
            srv_label = snap.get("name") or s["srv_name"] or f"{s['host']}:{s['port']}"
            parsed = parse_rust_keywords(kw)
            born_ts = parsed.get("born_ts")
            if not born_ts:
                continue

            # Letzten + nächsten 4 Wipes generieren
            # Wir kennen die genaue Wipe-Stunde nicht, nehmen die Uhrzeit von born_ts
            # (das ist der reale letzte Wipe).
            wipe_ts = born_ts
            wipes: list[tuple[str, int, str]] = []  # (uid_suffix, ts, label)
            # Vergangener Wipe
            wipes.append(("past", wipe_ts, "(actual)"))
            # Nächste 4 — wöchentlich rolling
            for i in range(1, 5):
                t = wipe_ts + i * week
                # Wenn schon vorbei, überspringen (kann passieren wenn unsere
                # born_ts gerade zwischen zwei Wipes liegt — sollte aber selten sein)
                if t < now - 86400:
                    continue
                wipes.append((f"next{i}", t, f"(est. +{i}w)"))

            for suffix, ts, label in wipes:
                # 1h-Event ab Wipe-Zeit
                uid = f"wipe-{s['id']}-{suffix}-{ts}@rustmetrics.eu"
                title = f"Wipe: {srv_label} {label}"
                desc_lines = [
                    f"Server: {srv_label}",
                    f"Address: {s['host']}:{s['port']}",
                    "Estimated wipe time (based on weekly cycle from last known wipe).",
                    "",
                    f"View: https://rustmetrics.eu/server/{s['host']}:{s['port']}",
                ]
                out.extend([
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{_ics_format_utc(now)}",
                    f"DTSTART:{_ics_format_utc(ts)}",
                    f"DTEND:{_ics_format_utc(ts + 3600)}",
                    f"SUMMARY:{_ics_escape(title)}",
                    f"DESCRIPTION:{_ics_escape(chr(10).join(desc_lines))}",
                    f"URL:https://rustmetrics.eu/server/{s['host']}:{s['port']}",
                    f"LOCATION:{_ics_escape(s['host'] + ':' + str(s['port']))}",
                    "END:VEVENT",
                ])

    out.append("END:VCALENDAR")
    # RFC 5545: lines limited to 75 octets, CRLF line endings
    body = []
    for line in out:
        while len(line.encode("utf-8")) > 75:
            # Soft-wrap on character boundary (approximate — works for ASCII)
            body.append(line[:74])
            line = " " + line[74:]
        body.append(line)
    return "\r\n".join(body) + "\r\n"


# ═══════════════════════════════════════════════════════════════════════════
#  RATE LIMITING — Token Bucket pro IP
# ═══════════════════════════════════════════════════════════════════════════

class TokenBucket:
    """Simpler Token-Bucket. capacity tokens, refill_rate tokens/sec."""
    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = capacity
        self.refill   = refill_rate
        self.tokens   = capacity
        self.last     = time.time()
        self._lock    = threading.Lock()

    def take(self, cost: float = 1.0) -> bool:
        with self._lock:
            now = time.time()
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill)
            self.last = now
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


_rl_general: dict[str, TokenBucket] = {}
_rl_browse:  dict[str, TokenBucket] = {}
_rl_lock = threading.Lock()

def _get_bucket(d, ip, capacity, rate) -> TokenBucket:
    with _rl_lock:
        b = d.get(ip)
        if b is None:
            b = TokenBucket(capacity, rate)
            d[ip] = b
            # Lazy-Cleanup: wenn Map zu gross, ältere weg
            if len(d) > 5000:
                old = sorted(d.items(), key=lambda kv: kv[1].last)[:1000]
                for k, _ in old: d.pop(k, None)
        return b

def rate_limit_general(ip: str) -> bool:
    b = _get_bucket(_rl_general, ip, RL_REQ_PER_MIN, RL_REQ_PER_MIN / 60.0)
    return b.take(1.0)

def rate_limit_browse_miss(ip: str) -> bool:
    b = _get_bucket(_rl_browse, ip, RL_BROWSE_PER_H, RL_BROWSE_PER_H / 3600.0)
    return b.take(1.0)


# ═══════════════════════════════════════════════════════════════════════════
#  POLLER — pollt nur Server, die mindestens 1 User in der Watchlist hat
# ═══════════════════════════════════════════════════════════════════════════

_poller_stop = threading.Event()


def _store_snapshot(cur, server_id: int, q: dict) -> None:
    players = q.get("players_list", []) or []
    names_json = json.dumps([p.get("name", "") for p in players])
    # Die Spielerliste aendert sich zwischen zwei Polls meistens nicht, macht
    # aber den Grossteil des Snapshot-Volumens aus. Nur bei Aenderung schreiben
    # (_latest_snapshot und _names_at holen die letzte bekannte Liste nach).
    if players:
        cur.execute(
            "SELECT players_json FROM snapshots WHERE server_id=%s "
            "AND players_json IS NOT NULL ORDER BY ts DESC LIMIT 1", (server_id,))
        prev = cur.fetchone()
        if prev and prev["players_json"] == names_json:
            names_json = None
    cur.execute(
        "INSERT INTO snapshots "
        "(server_id, ts, online, name, map, players_count, max_players, "
        " ping_ms, keywords, players_json, error) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            server_id,
            q.get("queried_at", int(time.time())),
            bool(q.get("online")),
            q.get("name"), q.get("map"),
            q.get("players_count"), q.get("max_players"),
            q.get("ping_ms"), q.get("keywords"),
            names_json,
            q.get("error"),
        ),
    )
    if q.get("name"):
        cur.execute("UPDATE servers SET name=%s WHERE id=%s", (q["name"], server_id))


def _diff_player_sessions(cur, server_id: int, ts: int, names: list[str],
                          name_to_bmid: dict[str, int] | None = None) -> tuple[set, set]:
    """Updates player_sessions; returns (came_online_set, went_offline_set).

    name_to_bmid: optionales Mapping lowercase_name → bm_player_id, das beim
    Erzeugen neuer Sessions die stabile BM-ID mitspeichert. Alte (NULL-)Einträge
    werden nicht rückwirkend angefasst — die Player-Profil-Page matched per Name
    + players.aliases.
    """
    name_to_bmid = name_to_bmid or {}
    cur.execute(
        "SELECT id, player_name FROM player_sessions "
        "WHERE server_id=%s AND end_ts IS NULL", (server_id,))
    open_sess = {r["player_name"]: r["id"] for r in cur.fetchall()}
    now_set = set(names)
    came_online = now_set - set(open_sess.keys())
    went_offline = set(open_sess.keys()) - now_set
    for n in came_online:
        bid = name_to_bmid.get(n.lower())
        cur.execute(
            "INSERT INTO player_sessions (server_id, player_name, start_ts, bm_player_id) "
            "VALUES (%s,%s,%s,%s)",
            (server_id, n, ts, bid))
    for n in went_offline:
        cur.execute("UPDATE player_sessions SET end_ts=%s WHERE id=%s", (ts, open_sess[n]))
    return came_online, went_offline


def upsert_player_meta(cur, pairs: list[tuple[int, str]], ts: int | None = None) -> None:
    """Pflegt die players-Tabelle: bm_id → current_name + aliases.
    Wenn der Name sich geändert hat, wird der alte current_name in aliases verschoben.
    pairs: Liste von (bm_id, name) — bm_id muss > 0 sein, sonst übersprungen.
    """
    if not pairs:
        return
    if ts is None:
        ts = int(time.time())
    seen: set[int] = set()
    for bid, nm in pairs:
        if not bid or not nm or bid in seen:
            continue
        seen.add(bid)
        cur.execute("SELECT current_name, aliases FROM players WHERE bm_id=%s", (bid,))
        r = cur.fetchone()
        if not r:
            cur.execute(
                "INSERT INTO players (bm_id, current_name, aliases, first_seen, last_seen) "
                "VALUES (%s,%s,ARRAY[]::TEXT[],%s,%s)",
                (bid, nm, ts, ts))
        elif r["current_name"] != nm:
            # Name geändert: alten Namen zu aliases hinzufügen wenn neu
            new_aliases = list(r["aliases"] or [])
            if r["current_name"] and r["current_name"] not in new_aliases and r["current_name"] != nm:
                new_aliases.append(r["current_name"])
            # Limit aliases auf 20 Einträge (älteste fliegen raus)
            if len(new_aliases) > 20:
                new_aliases = new_aliases[-20:]
            cur.execute(
                "UPDATE players SET current_name=%s, aliases=%s, last_seen=%s WHERE bm_id=%s",
                (nm, new_aliases, ts, bid))
        else:
            cur.execute(
                "UPDATE players SET last_seen=%s WHERE bm_id=%s",
                (ts, bid))


# ─── Discord-Webhook-Dispatcher ─────────────────────────────────────────
DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(?:ptb\.|canary\.)?(?:discord|discordapp)\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+/?$"
)
_webhook_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook")

def is_valid_discord_webhook(url: str) -> bool:
    return bool(url and DISCORD_WEBHOOK_RE.match(url))

def _post_discord_webhook(url: str, payload: dict) -> tuple[bool, str]:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "RustMetrics/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return (200 <= r.status < 300), f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception: pass
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def fire_discord_webhook_async(url: str, payload: dict):
    """Fire-and-forget Discord-Webhook-POST."""
    if not is_valid_discord_webhook(url): return
    _webhook_executor.submit(_post_discord_webhook, url, payload)

def _make_player_embed(event: str, player: str, server_name: str,
                       players_count: int = None, max_players: int = None,
                       map_name: str = None) -> dict:
    online = (event == "online")
    color = 0x5FC26B if online else 0xD24A3E   # green / red
    fields = []
    if server_name:
        fields.append({"name": "Server", "value": server_name[:200], "inline": False})
    if map_name:
        fields.append({"name": "Map", "value": str(map_name)[:64], "inline": True})
    if players_count is not None and max_players is not None:
        fields.append({"name": "Players", "value": f"{players_count}/{max_players}", "inline": True})
    return {
        "username":  "RustMetrics",
        "avatar_url": "https://rustmetrics.eu/favicon.png",
        "embeds": [{
            "title":       f"🟢 Player online — {player}" if online else f"🔴 Player offline — {player}",
            "description": f"**{player}** {'appeared on' if online else 'left'} {server_name}",
            "color":       color,
            "fields":      fields,
            "footer":      {"text": "rustmetrics.eu"},
        }],
    }

def _notify_player_transitions(cur, server_id: int, server_name: str,
                               came_online: set, went_offline: set,
                               snapshot_pop: int = None, snapshot_max: int = None,
                               map_name: str = None) -> None:
    """Find watchers of these names and fire their Discord webhooks (async)."""
    if not came_online and not went_offline: return
    transitions = [(n, "online") for n in came_online] + [(n, "offline") for n in went_offline]
    for name, event in transitions:
        col = "notify_online" if event == "online" else "notify_offline"
        cur.execute(
            f"SELECT u.discord_webhook FROM watched_players wp "
            f"JOIN users u ON u.id = wp.user_id "
            f"WHERE wp.server_id=%s AND lower(wp.name)=lower(%s) "
            f"  AND u.discord_webhook IS NOT NULL AND u.{col}=TRUE",
            (server_id, name))
        for row in cur.fetchall():
            wh = row["discord_webhook"]
            embed = _make_player_embed(event, name, server_name,
                                        snapshot_pop, snapshot_max, map_name)
            fire_discord_webhook_async(wh, embed)


_a2s_blocklist = A2SBlocklist()

BATTLEMETRICS_CREDIT_HTML = (
    ' · via <a class="via-bm" href="https://www.battlemetrics.com/" '
    'target="_blank" rel="noopener">BattleMetrics</a>')


def _served_by_battlemetrics(host: str, port: int) -> bool:
    return bm_available() and _a2s_blocklist.mode(host, port) is not None


def _bm_credit_html(source: str, host: str, port: int) -> str:
    uses_bm = source == "battlemetrics-live" or _served_by_battlemetrics(host, port)
    return BATTLEMETRICS_CREDIT_HTML if uses_bm else ""


def poll_server_row(row: dict) -> dict:
    host, port = row["host"], row["port"]
    # Bekannte A2S-Blocker direkt ueber BM holen, statt jedes Mal den Timeout abzuwarten
    blocked = _a2s_blocklist.mode(host, port) if bm_available() else None
    if blocked == MODE_FULL:
        q = {"host": host, "port": port, "online": False, "queried_at": int(time.time())}
    else:
        q = query_a2s(host, port, include_players=blocked not in PLAYER_LIST_FROM_BM_MODES)
    a2s_online = bool(q.get("online"))
    a2s_players_failed = "players_error" in q
    a2s_names = [p["name"] for p in q.get("players_list") or []]

    # Wenn A2S nicht alle Daten liefert (Server blockt A2S_INFO oder _PLAYER):
    # BattleMetrics-Fallback. Spart Resourcen indem wir nur dann BM anfragen,
    # wenn A2S nicht das volle Bild liefert.
    a2s_has_pop     = q.get("players_count") is not None and q.get("max_players") is not None
    a2s_has_players = bool(q.get("players_list"))
    bm_attrs = None
    bm_pairs: list[tuple[int, str]] = []
    used_bm_names = False
    if not a2s_has_pop or not a2s_has_players or looks_randomized(a2s_names):
        try:
            bm_attrs, bm_pairs = query_bm_server(row["host"], row["port"])
        except Exception as e:
            print(f"[bm_fallback] {row['host']}:{row['port']} {e}", file=sys.stderr, flush=True)
            bm_attrs, bm_pairs = None, []
        if bm_attrs:
            details = bm_attrs.get("details") or {}
            if not q.get("name"):           q["name"]          = bm_attrs.get("name")
            if not q.get("map"):            q["map"]           = details.get("map")
            if q.get("players_count") is None: q["players_count"] = bm_attrs.get("players")
            if q.get("max_players") is None:   q["max_players"]   = bm_attrs.get("maxPlayers")
            if not q.get("keywords"):
                tags = list(details.get("rust_modded_tags") or [])
                if details.get("rust_type"): tags.append(details["rust_type"])
                kw_extra = []
                if details.get("rust_queued_players"): kw_extra.append(f"qp{details['rust_queued_players']}")
                q["keywords"] = ",".join(tags + kw_extra)
            if bm_attrs.get("status") == "online":
                q["online"] = True
                q.pop("error", None)
            if should_use_bm_names(a2s_names, [nm for _bid, nm in bm_pairs]):
                used_bm_names = True
                q["players_list"] = [
                    {"name": nm, "score": 0, "duration_s": 0, "_bm_id": bid}
                    for bid, nm in bm_pairs
                ]
            q["_data_source"] = "a2s+bm" if (q.get("ping_ms") is not None) else "bm-only"

    if blocked is None:
        new_block = _a2s_blocklist.record(
            host, port,
            a2s_online=a2s_online, a2s_names=a2s_names, a2s_players_failed=a2s_players_failed,
            bm_online=bool(bm_attrs) and bm_attrs.get("status") == "online",
            bm_names=[nm for _bid, nm in bm_pairs])
        if new_block:
            print(f"[a2s-blocklist] {host}:{port} blockt A2S ({new_block}) — BM zuerst bis zum naechsten Recheck",
                  flush=True)

    came_online: set = set()
    went_offline: set = set()
    server_display_name = q.get("name") or row.get("host") + ":" + str(row.get("port"))
    # name → bm_id mapping (case-insensitive), entweder direkt aus dem
    # A2S+BM-Mischpfad (players_list mit _bm_id), oder aus bm_pairs falls
    # nur BM Daten lieferte.
    name_to_bmid: dict[str, int] = {}
    for p in (q.get("players_list") or []):
        bid = p.get("_bm_id") if isinstance(p, dict) else None
        if bid and p.get("name"):
            name_to_bmid[p["name"].lower()] = bid
    for bid, nm in (bm_pairs or []):
        if bid and nm:
            name_to_bmid.setdefault(nm.lower(), bid)
    with _Conn() as (conn, cur):
        _store_snapshot(cur, row["id"], q)
        if q.get("online"):
            names = [p["name"] for p in q.get("players_list", []) or []]
            came_online, went_offline = _diff_player_sessions(
                cur, row["id"], q.get("queried_at", int(time.time())), names,
                name_to_bmid=name_to_bmid)
            # Player-Metadaten pflegen (current_name, aliases) für jeden bekannten BM-Player
            if name_to_bmid:
                meta_pairs = []
                for nm in names:
                    bid = name_to_bmid.get(nm.lower())
                    if bid:
                        meta_pairs.append((bid, nm))
                if meta_pairs:
                    upsert_player_meta(cur, meta_pairs,
                                       ts=q.get("queried_at", int(time.time())))
            _notify_player_transitions(
                cur, row["id"], server_display_name,
                came_online, went_offline,
                snapshot_pop=q.get("players_count"),
                snapshot_max=q.get("max_players"),
                map_name=q.get("map"),
            )
            # Anonymisierung persistieren; echte BM-Namen (RCON-verbundene Server) heben sie wieder auf
            if detect_name_anonymization(names):
                cur.execute(
                    "UPDATE servers SET is_anonymized=TRUE WHERE id=%s AND NOT is_anonymized",
                    (row["id"],))
            elif used_bm_names and names_look_real(names):
                cur.execute(
                    "UPDATE servers SET is_anonymized=FALSE WHERE id=%s AND is_anonymized",
                    (row["id"],))
            # Watched-players: BM-IDs auffüllen / Namen auto-aktualisieren
            if bm_pairs:
                bm_by_name = {nm.lower(): bid for bid, nm in bm_pairs if bid}
                bm_by_id   = {bid: nm for bid, nm in bm_pairs if bid}
                cur.execute(
                    "SELECT id, name, bm_player_id FROM watched_players WHERE server_id=%s",
                    (row["id"],))
                for w in cur.fetchall():
                    if w["bm_player_id"] and w["bm_player_id"] in bm_by_id:
                        # ID gematcht: ggf. neuen Namen übernehmen
                        cur_name = bm_by_id[w["bm_player_id"]]
                        if cur_name != w["name"]:
                            cur.execute(
                                "UPDATE watched_players SET name=%s WHERE id=%s",
                                (cur_name, w["id"]))
                    elif not w["bm_player_id"]:
                        # Noch keine ID: per Name backfillen
                        bid = bm_by_name.get(w["name"].lower())
                        if bid:
                            cur.execute(
                                "UPDATE watched_players SET bm_player_id=%s WHERE id=%s",
                                (bid, w["id"]))
    return q


def _cleanup_old(cur):
    cutoff = int(time.time()) - SNAPSHOT_TTL_DAY * 86400
    cur.execute("DELETE FROM snapshots WHERE ts < %s", (cutoff,))
    # Namensliste aus alten Snapshots entfernen: sie macht ~80% des Volumens
    # aus, wird aber nach ein paar Stunden von nichts mehr gebraucht — der
    # Populationsgraph braucht nur ts/players_count, und wer wann da war
    # steht in player_sessions. Batchweise, damit kein langer Lock entsteht.
    pj_cut = int(time.time()) - PLAYERS_JSON_KEEP_H * 3600
    for _ in range(20):
        cur.execute(
            "UPDATE snapshots SET players_json = NULL WHERE ctid IN ("
            "  SELECT ctid FROM snapshots WHERE ts < %s AND players_json IS NOT NULL"
            "  LIMIT 20000)", (pj_cut,))
        if cur.rowcount < 20000:
            break
    cur.execute("DELETE FROM player_sessions WHERE end_ts IS NOT NULL AND end_ts < %s", (cutoff,))
    cur.execute("DELETE FROM presence_sessions WHERE end_ts IS NOT NULL AND end_ts < %s", (cutoff,))
    # Sicherheitsnetz: nie geschlossene Sessions duerfen die Aufbewahrungsfrist nicht umgehen
    cur.execute("DELETE FROM presence_sessions WHERE end_ts IS NULL AND start_ts < %s", (cutoff,))
    cur.execute("DELETE FROM sessions WHERE expires_at < %s", (int(time.time()),))
    # Verwaiste Server löschen (die niemand mehr watcht und seit 7+ Tagen keine Snapshots)
    cur.execute(
        "DELETE FROM servers s WHERE NOT EXISTS (SELECT 1 FROM watched_servers w WHERE w.server_id = s.id) "
        "AND NOT EXISTS (SELECT 1 FROM snapshots sn WHERE sn.server_id = s.id AND sn.ts > %s)",
        (int(time.time()) - 7 * 86400,)
    )


def presence_ip(addr: str | None) -> str | None:
    """
    'ip:port' → 'ip'. WICHTIG: Steam liefert in gameserverip den GAME-Port
    (z.B. 28055), unsere servers-Tabelle speichert den QUERY-Port (z.B. 28015).
    Die Ports stimmen also systematisch NICHT überein — gematcht wird auf der
    IP. Ein Rust-Server bedient ohnehin genau eine Instanz pro IP-Slot.
    """
    if not addr: return None
    return addr.rsplit(":", 1)[0] if ":" in addr else addr


def presence_transition(old: dict | None, in_rust: bool, server_addr: str | None) -> str | None:
    """
    Entscheidet, ob ein Presence-Wechsel gemeldet werden muss.
    Rückgabe: None (nichts), "online", "offline" oder "switch" (Serverwechsel).
    Erstbeobachtung (old is None) meldet NIE — sonst würde jeder neu getrackte
    Spieler sofort eine Benachrichtigung auslösen.
    """
    if old is None:
        return None
    was_in   = bool(old.get("in_rust"))
    old_ip   = presence_ip(old.get("server_addr"))
    new_ip   = presence_ip(server_addr)
    if in_rust and not was_in:
        return "online"
    if was_in and not in_rust:
        return "offline"
    if in_rust and was_in and new_ip and old_ip and new_ip != old_ip:
        return "switch"
    return None


# ── Alias-Lernen: den zensierten Namen eines Spielers herausfinden ────────
# Auf Servern mit censorplayerlist sind die Namen zwar Platzhalter, aber sie
# sind stabil an den Account gebunden (gemessen: 240/243 Namen ueber 5 Minuten
# identisch, verschiedene Server ueberschneiden sich nur zufaellig).
# Die Zuordnung ist nicht berechenbar — aber beobachtbar: Steam sagt uns
# sekundengenau, wann ein Account einen Server betritt oder verlaesst. Genau
# dann taucht sein Platzhalter in der A2S-Liste auf bzw. verschwindet.
# Ueber mehrere solcher Ereignisse bleibt genau ein Name uebrig.

# Wie viele Join/Leave-Ereignisse noetig sind, bis ein Alias als sicher gilt.
# Gerechnet (Liste ~245 Namen, bis zu 10 Kandidaten pro Ereignis):
#   2 Ereignisse -> bis 33% Falsch-Positiv | 3 -> 1.7% | 4 -> 0.07%
# Darum 3 als Minimum und Kandidaten pro Ereignis hart auf 10 begrenzt.
ALIAS_MIN_EVENTS  = _env("RM_ALIAS_MIN_EVENTS", 3, int)
ALIAS_MAX_CANDS   = _env("RM_ALIAS_MAX_CANDS", 10, int)

def _names_at(cur, server_id: int, ts: int, after: bool) -> set[str]:
    """Namensmenge des Snapshots direkt vor bzw. nach einem Zeitpunkt."""
    if after:
        cur.execute("SELECT players_json FROM snapshots WHERE server_id=%s AND ts > %s "
                    "AND online AND players_json IS NOT NULL ORDER BY ts ASC LIMIT 1",
                    (server_id, ts))
    else:
        cur.execute("SELECT players_json FROM snapshots WHERE server_id=%s AND ts <= %s "
                    "AND online AND players_json IS NOT NULL ORDER BY ts DESC LIMIT 1",
                    (server_id, ts))
    r = cur.fetchone()
    if not r or not r["players_json"]:
        return set()
    try:
        return set(n.lower() for n in json.loads(r["players_json"]) if n)
    except Exception as e:
        # Korruptes players_json in der DB — leise leer zurueckgeben waere ein
        # falsches "keine Spieler" fuers Session-Diffing, also mindestens loggen.
        print(f"[names_at] korruptes players_json server={server_id} ts={ts}: "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return set()


def learn_alias(cur, steam_id: int, server_id: int, ts: int, event: str) -> None:
    """
    Wertet ein Join-/Leave-Ereignis aus: welche Namen kamen genau dann dazu
    (bzw. fielen weg)? Jeder Kandidat bekommt einen Punkt, die Ereigniszahl
    steigt. Der echte Alias ist der einzige, der bei JEDEM Ereignis dabei ist.
    """
    before = _names_at(cur, server_id, ts, after=False)
    after  = _names_at(cur, server_id, ts, after=True)
    if not before or not after:
        return
    cands = (after - before) if event == "online" else (before - after)
    # Zu viele gleichzeitige Wechsel -> das Ereignis trennt schlecht, verwerfen
    if not cands or len(cands) > ALIAS_MAX_CANDS:
        return
    cur.execute("UPDATE alias_candidates SET events = events + 1 "
                "WHERE steam_id=%s AND server_id=%s", (steam_id, server_id))
    for name in cands:
        cur.execute(
            "INSERT INTO alias_candidates (steam_id, server_id, alias, score, events, "
            " first_seen, last_seen) VALUES (%s,%s,%s,1,1,%s,%s) "
            "ON CONFLICT (steam_id, server_id, alias) DO UPDATE SET "
            " score = alias_candidates.score + 1, last_seen = EXCLUDED.last_seen",
            (steam_id, server_id, name, ts, ts))
    print(f"[alias] {event} steam={steam_id} server={server_id}: {len(cands)} Kandidat(en)",
          flush=True)


def confirmed_alias(cur, steam_id: int, server_id: int) -> str | None:
    """
    Bestaetigt, wenn genau ein Kandidat bei allen Ereignissen dabei war und
    es mindestens ALIAS_MIN_EVENTS Ereignisse gab.
    """
    cur.execute(
        "SELECT alias, score, events FROM alias_candidates "
        "WHERE steam_id=%s AND server_id=%s ORDER BY score DESC LIMIT 2",
        (steam_id, server_id))
    rows = cur.fetchall()
    if not rows:
        return None
    top = rows[0]
    if top["events"] < ALIAS_MIN_EVENTS or top["score"] < top["events"]:
        return None
    if len(rows) > 1 and rows[1]["score"] >= top["score"]:
        return None                      # nicht eindeutig
    return top["alias"]


def _server_id_for_ip(cur, ip: str | None) -> int | None:
    """IP → server_id (Port ignorieren, siehe presence_ip)."""
    if not ip: return None
    cur.execute("SELECT id FROM servers WHERE host=%s ORDER BY id LIMIT 1", (ip,))
    r = cur.fetchone()
    return int(r["id"]) if r else None


def _presence_server_name(cur, server_addr: str) -> str:
    """host:port → hübscher Servername falls bei uns bekannt, sonst die Adresse."""
    if not server_addr: return "unknown server"
    ip = presence_ip(server_addr)
    try:
        cur.execute("SELECT name FROM servers WHERE host=%s AND name IS NOT NULL "
                    "ORDER BY id LIMIT 1", (ip,))
        r = cur.fetchone()
        if r and r["name"]: return r["name"]
    except Exception as e:
        # DB-Fehler beim Namens-Lookup: Fallback auf die Adresse ist ok,
        # aber der Fehler selbst darf nicht unsichtbar bleiben.
        print(f"[presence_server_name] DB-Lookup fehlgeschlagen fuer {server_addr}: "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return server_addr


def _notify_steam_presence(cur, steam_id: int, persona: str,
                           in_rust: bool, server_addr: str | None,
                           server_name: str) -> None:
    """Discord-Webhooks aller User feuern, die diese SteamID watchen."""
    col = "notify_online" if in_rust else "notify_offline"
    cur.execute(
        f"SELECT DISTINCT u.discord_webhook FROM watched_players wp "
        f"JOIN users u ON u.id = wp.user_id "
        f"WHERE wp.steam_id=%s AND u.discord_webhook IS NOT NULL AND u.{col}=TRUE",
        (steam_id,))
    label = f"{persona} [steam]" if persona else str(steam_id)
    for row in cur.fetchall():
        embed = _make_player_embed("online" if in_rust else "offline",
                                   label, server_name)
        fire_discord_webhook_async(row["discord_webhook"], embed)


PRESENCE_STALE_SEC = PRESENCE_STALE_POLLS * PRESENCE_POLL_SEC


def _open_presence_sessions(cur, steam_ids: list[int]) -> dict[int, dict]:
    """Offene Presence-Session pro SteamID."""
    cur.execute("SELECT id, steam_id FROM presence_sessions "
                "WHERE end_ts IS NULL AND steam_id = ANY(%s::bigint[])", (steam_ids,))
    return {int(r["steam_id"]): dict(r) for r in cur.fetchall()}


def _sync_presence_session(cur, steam_id: int, open_row: dict | None,
                           in_rust: bool, now: int) -> None:
    """Oeffnet/schliesst presence_sessions gemaess presence_session_actions()."""
    actions = presence_session_actions(open_row is not None, in_rust)
    if ACTION_CLOSE in actions:
        cur.execute("UPDATE presence_sessions SET end_ts=%s WHERE id=%s",
                    (now, open_row["id"]))
    if ACTION_OPEN in actions:
        cur.execute("INSERT INTO presence_sessions (steam_id, start_ts) VALUES (%s,%s)",
                    (steam_id, now))


def _sync_presence_session_safe(cur, steam_id: int, open_row: dict | None,
                                in_rust: bool, now: int) -> None:
    """Wie _sync_presence_session, aber ein Fehler bricht die Runde (Discord-Pings) nicht ab.

    Der Savepoint haelt die gemeinsame Transaktion nach einem SQL-Fehler benutzbar.
    """
    try:
        cur.execute("SAVEPOINT presence_session")
        _sync_presence_session(cur, steam_id, open_row, in_rust, now)
        cur.execute("RELEASE SAVEPOINT presence_session")
    except Exception as e:
        print(f"[presence] session sync {steam_id} fehlgeschlagen: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        try:
            cur.execute("ROLLBACK TO SAVEPOINT presence_session")
        except Exception as e2:
            print(f"[presence] savepoint rollback fehlgeschlagen: {type(e2).__name__}: {e2}",
                  file=sys.stderr, flush=True)


def _close_stale_presence_sessions(cur, now: int) -> None:
    """Offene Sessions ohne aktuelle Beobachtung am letzten Beobachtungszeitpunkt schliessen.

    steam_presence.updated_at wird bei jedem erfolgreichen Poll gesetzt (nicht nur bei
    Aenderung) und markiert damit die letzte Beobachtung. Fehlt die Zeile: Ende = Start.
    """
    cur.execute("SELECT ps.id, ps.start_ts, sp.updated_at FROM presence_sessions ps "
                "LEFT JOIN steam_presence sp ON sp.steam_id = ps.steam_id "
                "WHERE ps.end_ts IS NULL")
    for row in cur.fetchall():
        updated = None if row["updated_at"] is None else int(row["updated_at"])
        end = stale_session_end(int(row["start_ts"]), updated, now, PRESENCE_STALE_SEC)
        if end is not None:
            cur.execute("UPDATE presence_sessions SET end_ts=%s WHERE id=%s", (end, row["id"]))


def _close_untracked_presence_sessions(cur, tracked_ids: list[int], now: int) -> None:
    """Niemand watcht die SteamID mehr -> offene Session beenden, sonst liefe sie ewig."""
    cur.execute("UPDATE presence_sessions SET end_ts=%s "
                "WHERE end_ts IS NULL AND NOT (steam_id = ANY(%s::bigint[]))",
                (now, tracked_ids))


def steam_presence_loop():
    """
    Pollt Steam-Presence für alle per SteamID gewatchten Spieler.
    Erkennt online/offline/Serverwechsel unabhängig von censorplayerlist.
    """
    if not STEAM_API_KEY:
        print("[presence] kein RM_STEAM_API_KEY — Presence-Tracking aus", flush=True)
        return
    print("[presence] gestartet", flush=True)
    while not _poller_stop.is_set():
        try:
            with _Conn() as (conn, cur):
                cur.execute("SELECT DISTINCT steam_id FROM watched_players "
                            "WHERE steam_id IS NOT NULL")
                ids = [int(r["steam_id"]) for r in cur.fetchall()]
                round_start = int(time.time())
                # Vor der Verarbeitung: steam_presence.updated_at ist noch der Stand der Vorrunde
                _close_stale_presence_sessions(cur, round_start)
                _close_untracked_presence_sessions(cur, ids, round_start)
            if ids:
                summaries = fetch_player_summaries(ids)
                now = int(time.time())
                with _Conn() as (conn, cur):
                    cur.execute("SELECT * FROM steam_presence WHERE steam_id = ANY(%s)",
                                (ids,))
                    old = {int(r["steam_id"]): dict(r) for r in cur.fetchall()}
                    open_sessions = _open_presence_sessions(cur, ids)
                    for sid64 in ids:
                        s = summaries.get(sid64)
                        if s is None:
                            continue  # Batch-Fehler / ID nicht geliefert → Status halten
                        in_rust     = str(s.get("gameid") or "") == str(RUST_APPID)
                        server_addr = s.get("gameserverip") if in_rust else None
                        # 'visible' = Profil öffentlich genug, dass Spieldetails kommen
                        # KÖNNTEN. Bei privaten Spieldetails fehlt gameid auch beim
                        # Spielen — dann können wir nur "nicht sichtbar" sagen.
                        visible = s.get("communityvisibilitystate") == 3
                        persona = s.get("personaname")
                        o = old.get(sid64)
                        cur.execute(
                            "INSERT INTO steam_presence "
                            " (steam_id, persona, visible, in_rust, server_addr, updated_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (steam_id) DO UPDATE SET "
                            " persona=EXCLUDED.persona, visible=EXCLUDED.visible, "
                            " in_rust=EXCLUDED.in_rust, server_addr=EXCLUDED.server_addr, "
                            " updated_at=EXCLUDED.updated_at",
                            (sid64, persona, visible, in_rust, server_addr, now))
                        _sync_presence_session_safe(cur, sid64, open_sessions.get(sid64),
                                                    in_rust, now)
                        event = presence_transition(o, in_rust, server_addr)
                        if event:
                            was_addr = o.get("server_addr") if o else None
                            sname = _presence_server_name(
                                cur, server_addr or was_addr or "")
                            _notify_steam_presence(cur, sid64, persona,
                                                   in_rust, server_addr, sname)
                            # Alias-Lernen: Join/Leave sind die einzigen Momente,
                            # in denen sich ein Platzhalter eindeutig zuordnen laesst.
                            try:
                                if event in ("online", "offline"):
                                    addr = server_addr if event == "online" else was_addr
                                    sid_row = _server_id_for_ip(cur, presence_ip(addr))
                                    if sid_row:
                                        learn_alias(cur, sid64, sid_row, now, event)
                                elif event == "switch":
                                    old_sid = _server_id_for_ip(cur, presence_ip(was_addr))
                                    new_sid = _server_id_for_ip(cur, presence_ip(server_addr))
                                    if old_sid: learn_alias(cur, sid64, old_sid, now, "offline")
                                    if new_sid: learn_alias(cur, sid64, new_sid, now, "online")
                            except Exception as e:
                                print(f"[alias] Fehler: {type(e).__name__}: {e}",
                                      file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[presence] loop error: {e}", file=sys.stderr, flush=True)
        _poller_stop.wait(PRESENCE_POLL_SEC)


# Fester Worker-Pool statt "ein Thread pro Server pro Runde". Verhindert
# Thread-Explosion und Erschoepfung des DB-Pools, wenn viele Server beobachtet
# werden (vorher: 500 Server = 500 gleichzeitige Threads + 500 DB-Connections).
POLL_WORKERS = _env("RM_POLL_WORKERS", 16, int)
_poll_pool = ThreadPoolExecutor(max_workers=POLL_WORKERS, thread_name_prefix="poll")
_poll_stats = {"ok": 0, "err": 0}

def _poll_guarded(row: dict) -> None:
    """poll_server_row mit Fehlerfang — sonst stirbt der Worker still."""
    try:
        poll_server_row(row)
        _poll_stats["ok"] += 1
    except Exception as e:
        _poll_stats["err"] += 1
        print(f"[poller] {row.get('host')}:{row.get('port')} {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)


def poller_loop():
    print(f"[poller] gestartet ({POLL_WORKERS} worker)", flush=True)
    last_cleanup = 0.0
    while not _poller_stop.is_set():
        try:
            with _Conn() as (conn, cur):
                cur.execute("SELECT id, host, port FROM v_active_servers")
                rows = [dict(r) for r in cur.fetchall()]
            _poll_stats["ok"] = _poll_stats["err"] = 0
            futs = [_poll_pool.submit(_poll_guarded, r) for r in rows]
            deadline = max(5, POLL_INTERVAL - 2)
            try:
                for _f in as_completed(futs, timeout=deadline):
                    pass
            except _FuturesTimeout:
                pending = sum(1 for f in futs if not f.done())
                print(f"[poller] {pending}/{len(futs)} Server nach {deadline}s noch offen",
                      file=sys.stderr, flush=True)
            if _poll_stats["err"]:
                print(f"[poller] Runde: {_poll_stats['ok']} ok, {_poll_stats['err']} Fehler",
                      file=sys.stderr, flush=True)
            if time.time() - last_cleanup > 3600:
                with _Conn() as (conn, cur):
                    _cleanup_old(cur)
                last_cleanup = time.time()
        except Exception as e:
            print(f"[poller] error: {e}", file=sys.stderr, flush=True)
        for _ in range(POLL_INTERVAL):
            if _poller_stop.is_set(): return
            time.sleep(1)


# ═══════════════════════════════════════════════════════════════════════════
#  API-FUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════════

def _latest_snapshot(cur, server_id: int):
    cur.execute(
        "SELECT * FROM snapshots WHERE server_id=%s ORDER BY ts DESC LIMIT 1",
        (server_id,))
    r = cur.fetchone()
    if not r:
        return None
    snap = dict(r)
    # players_json wird nur bei Aenderung geschrieben (spart ~3/4 des
    # Speichers). Fuer die Anzeige die letzte bekannte Liste nachladen.
    if snap.get("players_json") is None and snap.get("online"):
        cur.execute(
            "SELECT players_json FROM snapshots WHERE server_id=%s AND ts <= %s "
            "AND players_json IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (server_id, snap["ts"]))
        prev = cur.fetchone()
        if prev:
            snap["players_json"] = prev["players_json"]
    return snap


def api_servers_list(user_id: int) -> list[dict]:
    """Liste der Server, die DIESER User beobachtet, mit aktuellem Snapshot
       und seinen beobachteten Spielernamen pro Server."""
    out = []
    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT s.*, ws.added_at AS user_added_at FROM servers s "
            "JOIN watched_servers ws ON ws.server_id = s.id "
            "WHERE ws.user_id=%s ORDER BY ws.added_at ASC",
            (user_id,)
        )
        rows = cur.fetchall()
        for r in rows:
            snap = _latest_snapshot(cur, r["id"])
            players = []
            if snap and snap.get("players_json"):
                try: players = json.loads(snap["players_json"])
                except Exception: players = []
            cur.execute(
                "SELECT id, name, added_at, bm_player_id, steam_id FROM watched_players "
                "WHERE user_id=%s AND server_id=%s ORDER BY name",
                (user_id, r["id"]))
            watched = [dict(w) for w in cur.fetchall()]
            online_set = {p.lower() for p in players}
            # Steam-Presence anreichern (SteamID-gewatchte Spieler): funktioniert
            # auch auf Streamer-Mode-Servern, wo die A2S-Namen fake sind.
            sids = [int(w["steam_id"]) for w in watched if w.get("steam_id")]
            presence = {}
            if sids:
                cur.execute("SELECT * FROM steam_presence WHERE steam_id = ANY(%s)", (sids,))
                presence = {int(p["steam_id"]): dict(p) for p in cur.fetchall()}
            this_ip = r["host"]
            for w in watched:
                w["online"] = w["name"].lower() in online_set
                if w.get("steam_id"):
                    p = presence.get(int(w["steam_id"]))
                    # Match auf IP, nicht host:port — Steam meldet den Game-Port,
                    # wir speichern den Query-Port (siehe presence_ip()).
                    same_box = bool(p and presence_ip(p.get("server_addr")) == this_ip)
                    w["steam"] = ({
                        "persona":        p.get("persona"),
                        "visible":        bool(p.get("visible")),
                        "in_rust":        bool(p.get("in_rust")),
                        "server_addr":    p.get("server_addr"),
                        "on_this_server": bool(p.get("in_rust")) and same_box,
                        "updated_at":     p.get("updated_at"),
                    } if p else None)
                    # Gelernter Platzhaltername auf diesem Server
                    alias = confirmed_alias(cur, int(w["steam_id"]), r["id"])
                    w["alias"] = alias
                    if alias:
                        w["alias_online"] = alias in online_set
                    if p and p.get("in_rust") and same_box:
                        w["online"] = True
                    # JS verliert Präzision bei int64 → als String rausgeben
                    w["steam_id"] = str(w["steam_id"])
            kw = (snap.get("keywords") if snap else "") or ""
            parsed = parse_rust_keywords(kw)
            tier = detect_rust_tier(snap.get("name") if snap else r["name"], parsed.get("tags", []))
            out.append({
                "id": r["id"], "host": r["host"], "port": r["port"],
                "name": (snap.get("name") if snap else None) or r["name"] or f"{r['host']}:{r['port']}",
                "added_at": r["user_added_at"],
                "snapshot": ({
                    "ts": snap["ts"], "online": bool(snap["online"]),
                    "map": snap.get("map"),
                    "players_count": snap.get("players_count"),
                    "max_players": snap.get("max_players"),
                    "ping_ms": snap.get("ping_ms"),
                    "players": players,
                    "error": snap.get("error"),
                } if snap else None),
                "tier": tier,
                "tags": parsed.get("tags", []),
                "queued": parsed.get("queued_kw"),
                "wipe": next_wipe_estimate(parsed.get("born_ts")),
                "max_group": detect_rust_group_limit(
                    (snap.get("name") if snap else None) or r["name"], parsed.get("tags", [])),
                "watched_players": watched,
                "name_anonymized": bool(r.get("is_anonymized")) or detect_name_anonymization(players),
                "via_battlemetrics": _served_by_battlemetrics(r["host"], r["port"]),
            })
    return out


def api_server_detail(user_id: int, server_id: int, hours: int = 24) -> dict | None:
    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT s.* FROM servers s JOIN watched_servers ws "
            "ON ws.server_id=s.id WHERE s.id=%s AND ws.user_id=%s",
            (server_id, user_id))
        r = cur.fetchone()
        if not r: return None
        snap = _latest_snapshot(cur, server_id)
        cutoff = int(time.time()) - hours * 3600
        cur.execute(
            "SELECT ts, players_count, online, ping_ms FROM snapshots "
            "WHERE server_id=%s AND ts >= %s ORDER BY ts ASC", (server_id, cutoff))
        hist = [dict(x) for x in cur.fetchall()]
        cur.execute(
            "SELECT player_name, start_ts FROM player_sessions "
            "WHERE server_id=%s AND end_ts IS NULL ORDER BY start_ts ASC", (server_id,))
        active = [dict(x) for x in cur.fetchall()]
        cur.execute(
            "SELECT player_name, start_ts, end_ts FROM player_sessions "
            "WHERE server_id=%s AND end_ts IS NOT NULL ORDER BY end_ts DESC LIMIT 60",
            (server_id,))
        closed = [dict(x) for x in cur.fetchall()]
        kw_raw = (snap.get("keywords") if snap else "") or ""
        parsed = parse_rust_keywords(kw_raw)
        players_list = []
        if snap and snap.get("players_json"):
            try: players_list = json.loads(snap["players_json"])
            except Exception: players_list = []
        # Gelernte Aliase der auf diesem Server per Steam getrackten Spieler:
        # alias -> echter Name, damit das Frontend die Platzhalter aufloesen kann.
        alias_map: dict[str, str] = {}
        cur.execute("SELECT wp.steam_id, wp.name, sp.persona FROM watched_players wp "
                    "LEFT JOIN steam_presence sp ON sp.steam_id = wp.steam_id "
                    "WHERE wp.server_id=%s AND wp.steam_id IS NOT NULL", (server_id,))
        for w in cur.fetchall():
            a = confirmed_alias(cur, int(w["steam_id"]), server_id)
            if a:
                alias_map[a] = w["persona"] or w["name"]
        return {
            "id": r["id"], "host": r["host"], "port": r["port"],
            "name": (snap.get("name") if snap else None) or r["name"],
            "snapshot": snap, "history": hist,
            "active_sessions": active, "recent_sessions": closed,
            "keywords_raw": kw_raw, "keywords_parsed": parsed,
            "tier": detect_rust_tier(snap.get("name") if snap else r["name"], parsed.get("tags", [])),
            "wipe": next_wipe_estimate(parsed.get("born_ts")),
            "name_anonymized": bool(r.get("is_anonymized")) or detect_name_anonymization(players_list),
            "alias_map": alias_map,
        }


_browse_inflight: dict[str, threading.Lock] = {}
_browse_inflight_lock = threading.Lock()

def browse_refresh(region: str) -> dict:
    """
    Holt die Serverliste und legt sie klassifiziert in den Cache.

    Zwei Dinge passieren hier bewusst:
    1) Single-Flight: Bei gleichzeitigen Cache-Misses fetcht nur EIN Thread,
       die anderen warten und nehmen sein Ergebnis. Vorher holten alle
       parallel je 10-20k Server (je ~25-50 MB) -> OOM-Kandidat.
    2) Klassifikation (Keywords, Tier, Kategorie, Wipe) passiert EINMAL hier
       statt bei jedem Request. Das waren ~0.5s GIL-haltende CPU pro Aufruf.
    """
    with _browse_inflight_lock:
        lock = _browse_inflight.setdefault(region, threading.Lock())
    with lock:
        cached = _cache_get(f"browse:{region}")
        if cached is not None:
            return cached                       # ein anderer Thread war schneller
        t0 = time.time()
        servers, source = fetch_server_list(region=region, limit=BROWSE_MASTER_LIMIT)
        prepared = []
        for s in servers:
            parsed = parse_rust_keywords(s.get("keywords") or "")
            prepared.append({
                "host": s["host"], "port": s["port"],
                "name": s.get("name") or f"{s['host']}:{s['port']}",
                "name_lc": (s.get("name") or "").lower(),
                "map": s.get("map"),
                "players_count": s.get("players_count") or 0,
                "max_players": s.get("max_players") or 0,
                "ping_ms": s.get("ping_ms"),
                "tier": detect_rust_tier(s.get("name"), parsed.get("tags", [])),
                "category": detect_rust_category(s.get("name"), parsed.get("tags", [])),
                "region": detect_rust_region(s.get("name"), parsed.get("tags", [])),
                "tags": parsed.get("tags", []),
                "queued": parsed.get("queued_kw") or 0,
                "wipe": next_wipe_estimate(parsed.get("born_ts")),
                "max_group": detect_rust_group_limit(s.get("name"), parsed.get("tags", [])),
                # Pop-Verdacht: mehr Spieler als Slots OHNE Queue gibt es im
                # echten Rust nicht — das ist praktisch immer Fake-Population.
                "pop_suspect": bool(
                    (s.get("players_count") or 0) > (s.get("max_players") or 0) > 0
                    and not (parsed.get("queued_kw") or 0)),
            })
        raw = {
            "fetched_at":   int(time.time()),
            "addr_count":   len(prepared),
            "online_count": len(prepared),
            "fetch_secs":   round(time.time() - t0, 2),
            "source":       source,
            "servers":      prepared,
        }
        _cache_put(f"browse:{region}", raw)
        return raw


def browse_warmer_loop():
    """
    Haelt den Browse-Cache im Hintergrund warm, damit kein Nutzer-Request je
    auf einen Fetch warten muss. Nur die Regionen, die tatsaechlich abgefragt
    werden (siehe _browse_wanted), damit nicht alle 9 Regionen laufen.
    """
    while not _poller_stop.is_set():
        try:
            for region in list(_browse_wanted):
                if _poller_stop.is_set(): break
                age = None
                e = _browse_cache.get(f"browse:{region}")
                if e: age = time.time() - e["ts"]
                if age is None or age > BROWSE_CACHE_TTL * 0.75:
                    browse_refresh(region)
        except Exception as e:
            print(f"[browse-warm] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        _poller_stop.wait(max(15, BROWSE_CACHE_TTL // 3))


_browse_wanted: set[str] = {"all"}


def api_browse(user_id: int | None,
               region="all", q="", tier="all",
               min_pop=0, max_pop=9999,
               sort="pop_desc", limit=200, offset=0,
               group="all", max_wipe_h=0,
               ip="0.0.0.0") -> dict:
    # Region wechselt NICHT mehr die Datenquelle. Wir holen immer die globale
    # Steam-Liste (enthaelt alle ~10k Server inkl. Rustafied/Rustoria/Moose) und
    # filtern die Region serverseitig ueber den offiziellen Region-Tag (mit
    # Namen-Fallback). Steams eigener \region\-Filter ist fuer Rust unbrauchbar
    # (die meisten Server melden \region\-1), und BM pro Region ergab den
    # Fallback-Salat "source: steam_web_api trotz Europe".
    _browse_wanted.add("all")
    cache_key = "browse:all"
    raw = _cache_get(cache_key)
    if raw is None:
        if not rate_limit_browse_miss(ip):
            raise PermissionError("rate-limit: too many browser refreshes — please wait")
        raw = browse_refresh("all")             # Single-Flight, siehe unten

    # Dropdown-Region → offizieller Rust-Region-Code (Legacy-Werte mitgemappt).
    reg = (region or "all").strip().lower()
    reg = _REGION_FILTER_ALIASES.get(reg, reg)
    cache_age = int(time.time() - raw["fetched_at"])

    # Bereits beobachtete Server (für aktuellen User markieren)
    in_user_watchlist: set[tuple[str, int]] = set()
    if user_id:
        with _Conn() as (conn, cur):
            cur.execute(
                "SELECT s.host, s.port FROM servers s "
                "JOIN watched_servers ws ON ws.server_id=s.id WHERE ws.user_id=%s",
                (user_id,))
            in_user_watchlist = {(r["host"], r["port"]) for r in cur.fetchall()}

    # Die Klassifikation steckt bereits im Cache (browse_refresh) — hier wird
    # nur noch gefiltert. Das spart ~0.5s CPU pro Request.
    ql = (q or "").strip().lower()
    grp = (group or "all").strip().lower()
    _now = int(time.time())
    out = []
    for s in raw["servers"]:
        pc = s["players_count"]
        if pc < min_pop or pc > max_pop: continue
        if ql and ql not in s["name_lc"]: continue
        if grp not in ("", "all"):
            mg = s.get("max_group")
            if grp == "nolimit":
                if mg != 0: continue
            elif grp == "5plus":
                if not (isinstance(mg, int) and mg >= 5): continue
            else:
                try:
                    if mg != int(grp): continue
                except ValueError:
                    pass
        if max_wipe_h and max_wipe_h > 0:
            _lw = (s.get("wipe") or {}).get("last_wipe_ts")
            if not _lw or (_now - _lw) > max_wipe_h * 3600: continue
        if reg != "all":
            _grp = _REGION_GROUPS.get(reg)
            if _grp is not None:
                if s.get("region") not in _grp: continue
            elif s.get("region") != reg:
                continue
        if tier != "all":
            if tier == "pve":
                if s["tier"] != "pve": continue
            elif tier != s["category"]:
                continue
        out.append(s)

    # Sortier-Spezifikationen: (key-Funktion, reverse). Fehlende Werte landen
    # bei JEDER Richtung am Ende (deshalb ±inf bzw. Sentinel-Strings).
    _INF = float("inf")
    def _num(v, missing):
        return v if isinstance(v, (int, float)) else missing
    sort_specs = {
        "pop_desc":  (lambda x: _num(x.get("players_count"), 0), True),
        "pop_asc":   (lambda x: _num(x.get("players_count"), 0), False),
        "name":      (lambda x: (x.get("name") or "\uffff").lower(), False),
        "name_desc": (lambda x: (x.get("name") or "").lower(), True),
        "map":       (lambda x: (x.get("map") or "\uffff").lower(), False),
        "map_desc":  (lambda x: (x.get("map") or "").lower(), True),
        "ping":      (lambda x: _num(x.get("ping_ms"), _INF), False),
        "ping_desc": (lambda x: _num(x.get("ping_ms"), -_INF), True),
        "wipe":      (lambda x: _num((x.get("wipe") or {}).get("days_until_next"), _INF), False),
        "wipe_desc": (lambda x: _num((x.get("wipe") or {}).get("days_until_next"), -_INF), True),
        # Frisch gewipte zuerst (Just-Wiped-Style): neuester last_wipe_ts oben
        "fresh":     (lambda x: _num((x.get("wipe") or {}).get("last_wipe_ts"), -_INF), True),
    }
    keyf, rev = sort_specs.get(sort, sort_specs["pop_desc"])
    out.sort(key=keyf, reverse=rev)

    # Nur die tatsaechlich gelieferten Zeilen kopieren (die Cache-Objekte sind
    # geteilt und duerfen nicht veraendert werden). name_lc ist intern.
    page = []
    for s in out[offset:offset + limit]:
        row = {k: v for k, v in s.items() if k != "name_lc"}
        row["in_watchlist"] = (s["host"], s["port"]) in in_user_watchlist
        page.append(row)

    return {
        "servers": page,
        "offset": offset,
        "has_more": offset + limit < len(out),
        "source": raw.get("source", "unknown"),
        "total_online": raw["online_count"],
        "total_fetched": raw["addr_count"],
        "matched": len(out),
        "returned": len(page),
        "fetched_at": raw["fetched_at"],
        "cache_age_sec": cache_age,
        "fetch_secs": raw.get("fetch_secs"),
        "region": region,
        "authenticated": bool(user_id),
    }


MAX_WATCHED_SERVERS = _env("RM_MAX_WATCHED_SERVERS", 50, int)
MAX_WATCHED_PLAYERS = _env("RM_MAX_WATCHED_PLAYERS", 100, int)


def normalize_server_input(host: str, port: int) -> tuple[str, int]:
    """
    Räumt auf, was Leute tatsaechlich ins Host-Feld tippen (aus den Altdaten):
      "connect eu.server.net:28015"  -> host + Port aus dem Connect-Befehl
      "1.2.3.4:28015"                -> host/Port getrennt
      "steamcommunity.com/profiles/…"-> klare Fehlermeldung statt "loest nicht auf"
      "76561199…"                    -> dito (SteamID gehoert ins Spieler-Feld)
    """
    h = (host or "").strip()
    if h.lower().startswith("connect "):
        h = h[8:].strip()
    if _STEAMID64_RE.match(h) or "steamcommunity.com" in h.lower() or "steamid.uk" in h.lower():
        raise ValueError("that's a Steam profile — add it in the player field of a server, "
                         "not as a server address")
    if h.lower().startswith(("http://", "https://")):
        raise ValueError("that's a URL, not a server address — use IP or hostname")
    # "host:port" im Host-Feld: Port daraus uebernehmen (schlaegt das Formularfeld)
    if h.count(":") == 1:
        left, right = h.rsplit(":", 1)
        if right.isdigit() and 0 < int(right) < 65536 and left:
            h, port = left, int(right)
    return h.strip("/ "), port


def validate_public_host(host: str, port: int) -> None:
    """
    Verhindert, dass ein User interne/private Adressen als 'Rust-Server'
    eintraegt. Ohne diese Pruefung schickt der Poller alle 20s UDP-Pakete an
    ein beliebiges Ziel (DDoS-Proxy) bzw. sondiert das interne Netz (SSRF).
    Hostnamen sind erlaubt, werden aber aufgeloest und jede resultierende
    IP geprueft.
    """
    if not (0 < port < 65536):
        raise ValueError("invalid port")
    host = (host or "").strip()
    if not host or len(host) > 253:
        raise ValueError("invalid host")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_UDP)
    except socket.gaierror:
        raise ValueError("host does not resolve") from None
    if not infos:
        raise ValueError("host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"address not allowed: {ip}")


def add_watch_server(user_id: int, host: str, port: int) -> int:
    host, port = normalize_server_input(host, port)
    validate_public_host(host, port)
    with _Conn() as (conn, cur):
        cur.execute("SELECT COUNT(*) AS c FROM watched_servers WHERE user_id=%s", (user_id,))
        if cur.fetchone()["c"] >= MAX_WATCHED_SERVERS:
            raise PermissionError(f"watchlist limit reached ({MAX_WATCHED_SERVERS} servers)")
    with _Conn() as (conn, cur):
        cur.execute(
            "INSERT INTO servers (host, port, first_seen) VALUES (%s,%s,%s) "
            "ON CONFLICT (host, port) DO NOTHING", (host, port, int(time.time())))
        cur.execute("SELECT id FROM servers WHERE host=%s AND port=%s", (host, port))
        sid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO watched_servers (user_id, server_id, added_at) VALUES (%s,%s,%s) "
            "ON CONFLICT (user_id, server_id) DO NOTHING",
            (user_id, sid, int(time.time())))
    # erstmaliger Sofort-Poll für direktes Feedback
    threading.Thread(
        target=poll_server_row,
        args=({"id": sid, "host": host, "port": port},),
        daemon=True
    ).start()
    return sid


def remove_watch_server(user_id: int, server_id: int):
    with _Conn() as (conn, cur):
        cur.execute("DELETE FROM watched_servers WHERE user_id=%s AND server_id=%s",
                    (user_id, server_id))
        cur.execute("DELETE FROM watched_players WHERE user_id=%s AND server_id=%s",
                    (user_id, server_id))


def add_watch_player(user_id: int, server_id: int, name: str, steam_id: int | None = None):
    with _Conn() as (conn, cur):
        cur.execute("SELECT 1 FROM watched_servers WHERE user_id=%s AND server_id=%s",
                    (user_id, server_id))
        if not cur.fetchone():
            raise PermissionError("server not in your watchlist")
        cur.execute("SELECT COUNT(*) AS c FROM watched_players WHERE user_id=%s", (user_id,))
        if cur.fetchone()["c"] >= MAX_WATCHED_PLAYERS:
            raise PermissionError(f"limit reached ({MAX_WATCHED_PLAYERS} watched players)")
        cur.execute(
            "INSERT INTO watched_players (user_id, server_id, name, added_at, steam_id) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id, server_id, name) DO UPDATE SET steam_id=EXCLUDED.steam_id",
            (user_id, server_id, name, int(time.time()), steam_id))


def remove_watch_player(user_id: int, watch_id: int):
    with _Conn() as (conn, cur):
        cur.execute("DELETE FROM watched_players WHERE id=%s AND user_id=%s",
                    (watch_id, user_id))


RHYTHM_MAX_ROWS = 2000   # pro Quelle; 7 Tage mit Minutentakt-Sessions bleiben weit darunter


def _presence_session_rows(cur, steam_id: int | None, since: int, now: int) -> list[tuple]:
    if not steam_id:
        return []
    cur.execute("SELECT start_ts, end_ts FROM presence_sessions "
                "WHERE steam_id=%s AND start_ts < %s AND (end_ts IS NULL OR end_ts > %s) "
                "ORDER BY start_ts LIMIT %s", (steam_id, now, since, RHYTHM_MAX_ROWS))
    return [(r["start_ts"], r["end_ts"]) for r in cur.fetchall()]


def _server_session_rows(cur, watched: dict, since: int, now: int) -> list[tuple]:
    """player_sessions per Name auf dem gewatchten Server, zusaetzlich per BM-ID (alle Server)."""
    window = "start_ts < %s AND (end_ts IS NULL OR end_ts > %s)"
    cur.execute(f"SELECT start_ts, end_ts FROM player_sessions WHERE server_id=%s "
                f"AND lower(player_name)=lower(%s) AND {window} ORDER BY start_ts LIMIT %s",
                (watched["server_id"], watched["name"], now, since, RHYTHM_MAX_ROWS))
    rows = [(r["start_ts"], r["end_ts"]) for r in cur.fetchall()]
    if not watched.get("bm_player_id"):
        return rows
    cur.execute(f"SELECT start_ts, end_ts FROM player_sessions WHERE bm_player_id=%s "
                f"AND {window} ORDER BY start_ts LIMIT %s",
                (watched["bm_player_id"], now, since, RHYTHM_MAX_ROWS))
    return rows + [(r["start_ts"], r["end_ts"]) for r in cur.fetchall()]


def api_player_rhythm(user_id: int, watch_id: int) -> dict | None:
    """Online-Rhythmus eines gewatchten Spielers; None, wenn er nicht auf DIESER User-Watchlist steht."""
    now = int(time.time())
    since = now - RHYTHM_WINDOW_SEC
    with _Conn() as (conn, cur):
        cur.execute("SELECT server_id, name, steam_id, bm_player_id FROM watched_players "
                    "WHERE id=%s AND user_id=%s", (watch_id, user_id))
        watched = cur.fetchone()
        if not watched:
            return None
        watched = dict(watched)
        steam_rows = _presence_session_rows(cur, watched.get("steam_id"), since, now)
        server_rows = _server_session_rows(cur, watched, since, now)
    return build_rhythm(steam_rows, server_rows, now)


def _esc(s) -> str:
    """HTML-Escape für Template-Substitution."""
    return (str(s) if s is not None else "")\
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")\
        .replace('"', "&quot;").replace("'", "&#39;")


def render_public_server_page(host: str, port: int) -> tuple[str, int]:
    """
    Server-side-rendered HTML für /server/<host>:<port>. Liefert (html, status).
    Daten: bevorzugt aktueller Snapshot aus DB, sonst Live-BM-Lookup, sonst 404.
    """
    name = map_name = None
    players = max_players = ping = None
    online = False
    keywords = ""
    players_list: list[str] = []
    # (name → bm_id) mapping — wenn vorhanden, werden Player-Namen als Links
    # zu /player/<bm_id> gerendert. Quelle: BM-Pairs (live oder cached).
    player_bmid: dict[str, int] = {}
    snap_ts = None
    source = "live"

    # 1. DB-Snapshot wenn vorhanden und frisch
    with _Conn() as (conn, cur):
        cur.execute("SELECT id FROM servers WHERE host=%s AND port=%s", (host, port))
        r = cur.fetchone()
        if r:
            cur.execute("SELECT * FROM snapshots WHERE server_id=%s ORDER BY ts DESC LIMIT 1", (r["id"],))
            s = cur.fetchone()
            if s and (int(time.time()) - s["ts"] < 600):
                name = s["name"]; map_name = s["map"]
                players = s["players_count"]; max_players = s["max_players"]
                ping = s["ping_ms"]
                online = bool(s["online"])
                keywords = s["keywords"] or ""
                try: players_list = json.loads(s["players_json"] or "[]")
                except Exception: players_list = []
                snap_ts = s["ts"]
                source = "rustmetrics-db"
                # BM-IDs für die aktuell sichtbaren Namen aus der players-Tabelle
                # nachziehen (falls dort schon bekannt durch frühere BM-Polls)
                if players_list:
                    lower_names = [n.lower() for n in players_list]
                    cur.execute(
                        "SELECT bm_id, current_name, aliases FROM players "
                        "WHERE LOWER(current_name) = ANY(%s) "
                        "   OR EXISTS (SELECT 1 FROM unnest(aliases) AS a "
                        "              WHERE LOWER(a) = ANY(%s))",
                        (lower_names, lower_names))
                    for pr in cur.fetchall():
                        # current_name + aliases einbeziehen
                        for n in [pr["current_name"]] + list(pr["aliases"] or []):
                            if n and n.lower() in lower_names:
                                player_bmid.setdefault(n.lower(), pr["bm_id"])

    # 2. Live-BM-Lookup falls kein frischer Snapshot
    if name is None:
        bm_attrs, bm_pairs = query_bm_server(host, port)
        if bm_attrs:
            details = bm_attrs.get("details") or {}
            name = bm_attrs.get("name")
            map_name = details.get("map")
            players = bm_attrs.get("players")
            max_players = bm_attrs.get("maxPlayers")
            online = bm_attrs.get("status") == "online"
            tags = list(details.get("rust_modded_tags") or [])
            if details.get("rust_type"): tags.append(details["rust_type"])
            if details.get("rust_queued_players"):
                tags.append(f"qp{details['rust_queued_players']}")
            keywords = ",".join(tags)
            players_list = [nm for _bid, nm in bm_pairs]
            for bid, nm in bm_pairs:
                if bid and nm:
                    player_bmid[nm.lower()] = bid
            # Bei dieser Live-Quelle direkt players-Metadaten pflegen
            if bm_pairs:
                try:
                    with _Conn() as (_c2, cur2):
                        upsert_player_meta(cur2, bm_pairs)
                except Exception as e:
                    print(f"[upsert_meta live] {e}", file=sys.stderr, flush=True)
            source = "battlemetrics-live"

    # 2b. Browse-Cache-Fallback (Steam-Liste). BM ist oft 403-gesperrt und die
    # Sitemap verweist auch auf nicht-gewatchte Server — ohne diesen Fallback
    # liefen alle diese URLs fuer Besucher UND Google auf 404.
    if name is None:
        raw = _cache_get("browse:all", ttl=BROWSE_CACHE_TTL * 4)
        if raw:
            row = next((x for x in raw["servers"]
                        if x["host"] == host and x["port"] == port), None)
            if row:
                name = row.get("name"); map_name = row.get("map")
                players = row.get("players_count"); max_players = row.get("max_players")
                ping = row.get("ping_ms")
                online = True                      # Steam-Liste enthaelt nur Online-Server
                tags = list(row.get("tags") or [])
                if row.get("queued"): tags.append(f"qp{row['queued']}")
                _w = row.get("wipe") or {}
                if _w.get("last_wipe_ts"): tags.append(f"born{_w['last_wipe_ts']}")
                keywords = ",".join(tags)
                snap_ts = raw.get("fetched_at")
                source = "steam-browse-cache"

    if name is None:
        return _public_404_html(host, port), 404

    parsed = parse_rust_keywords(keywords)
    tier = detect_rust_tier(name, parsed.get("tags", []))
    wipe = next_wipe_estimate(parsed.get("born_ts"))
    queued = parsed.get("queued_kw") or 0

    title = f"{name} — RustMetrics" if name else f"{host}:{port} — RustMetrics"
    pop_str = (f"{players}/{max_players}" if players is not None and max_players is not None else "—")
    pop_pct = round(100 * (players or 0) / max_players) if (players and max_players) else 0
    desc_bits = []
    if online:    desc_bits.append("🟢 online")
    else:         desc_bits.append("🔴 offline")
    desc_bits.append(f"{pop_str} players")
    if map_name:  desc_bits.append(f"map: {map_name}")
    if tier:      desc_bits.append(f"tier: {tier}")
    if wipe and wipe.get("days_until_next") is not None:
        desc_bits.append(f"next wipe in {wipe['days_until_next']}d")
    og_description = " · ".join(desc_bits)

    # Player-Liste rendern (max ~80 anzeigen, Rest "…and N more").
    # Wenn wir eine BM-ID für den Namen kennen, wird er zur eigenen Player-Page verlinkt.
    pl_html = ""
    if players_list:
        shown = players_list[:80]
        rest = max(0, len(players_list) - 80)
        items = []
        for n in shown:
            bid = player_bmid.get((n or "").lower())
            if bid:
                items.append(
                    f"<div class='p'><a class='pname plink' "
                    f"href='/player/{int(bid)}'>{_esc(n)}</a></div>")
            else:
                items.append(f"<div class='p'><span class='pname'>{_esc(n)}</span></div>")
        pl_html = "<div class='player-list'>" + "".join(items) + "</div>"
        if rest:
            pl_html += f"<p class='hint'>… and {rest} more</p>"
    else:
        pl_html = "<p class='hint' style='text-align:center'>No players online or list not available.</p>"

    tag_chips = []
    if tier: tag_chips.append(f"<span class='tag tier-{_esc(tier)}'>{_esc(tier)}</span>")
    for t in (parsed.get("tags") or [])[:6]:
        if t == tier: continue
        tag_chips.append(f"<span class='tag'>{_esc(t)}</span>")
    tags_html = "".join(tag_chips)

    wipe_html = ""
    if wipe and wipe.get("days_until_next") is not None:
        wipe_html = f"<dt>Next wipe</dt><dd>~{wipe['days_until_next']} days</dd>"

    updated_html = ""
    if snap_ts:
        diff = max(0, int(time.time()) - snap_ts)
        if diff < 60:    upd = f"{diff}s ago"
        elif diff<3600:  upd = f"{diff//60}m ago"
        else:            upd = f"{diff//3600}h ago"
        updated_html = f"<span style='color:var(--text-dim); font-size:11px'>updated {upd}</span>"
    else:
        updated_html = "<span style='color:var(--text-dim); font-size:11px'>live BM lookup</span>"

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(og_description)}" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="RustMetrics" />
<meta property="og:title" content="{_esc(name or host)}" />
<meta property="og:description" content="{_esc(og_description)}" />
<meta property="og:url" content="https://rustmetrics.eu/server/{_esc(host)}:{port}" />
<meta property="og:image" content="https://rustmetrics.eu/og-image.png" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{_esc(name or host)}" />
<meta name="twitter:description" content="{_esc(og_description)}" />
<meta name="twitter:image" content="https://rustmetrics.eu/og-image.png" />
<link rel="stylesheet" href="/style.css" />
</head><body>

<header class="topbar">
  <div class="brand">
    <a href="/" style="display:flex; align-items:center; gap:12px; text-decoration:none; color:inherit;">
      <span class="brand-mark" aria-hidden="true"></span>
      <span class="brand-name">RUST<span class="brand-name-accent">METRICS</span></span>
    </a>
  </div>
  <div class="topbar-actions" style="margin-left:auto;">
    <a class="btn-steam" href="/auth/steam/login" style="text-decoration:none;">
      <span class="steam-icon" aria-hidden="true">⌬</span> Sign in to watch
    </a>
  </div>
</header>

<article class="legal-page" style="max-width:880px;">
  <a href="/" class="legal-back">← Home</a>
  <h1 style="text-transform:none; font-size:22px;">{_esc(name or host)}</h1>
  <p style="color:var(--text-muted); font-family:'SF Mono',monospace; font-size:13px; margin:0 0 6px 0;">
    {_esc(host)}:{port}
  </p>
  <div style="margin:8px 0 16px 0;">{tags_html}</div>

  <section class="detail-section">
    <h3>Live Status</h3>
    <dl class="detail-meta">
      <dt>Status</dt><dd>{'<span style="color:var(--green)">● ONLINE</span>' if online else '<span style="color:var(--red)">● OFFLINE</span>'}</dd>
      <dt>Players</dt><dd>{_esc(pop_str)}{f' <span style="color:var(--hazmat); font-size:11px;">+{queued} queued</span>' if queued else ''}</dd>
      <dt>Map</dt><dd>{_esc(map_name or "—")}</dd>
      {('<dt>Ping</dt><dd>' + str(ping) + ' ms</dd>') if ping else ''}
      <dt>Tier</dt><dd>{_esc(tier)}</dd>
      {wipe_html}
    </dl>
    <div class="fill-bar" style="margin-top:14px;"><div class="fill-bar-inner" style="width:{pop_pct}%"></div></div>
  </section>

  <section class="detail-section">
    <h3>Currently online ({len(players_list)})</h3>
    {pl_html}
  </section>

  <section class="detail-section">
    <h3>Data source</h3>
    <p style="color:var(--text-muted); font-size:12px;">
      {_esc(source)} · {updated_html}{_bm_credit_html(source, host, port)}
    </p>
  </section>

  <p style="text-align:center; margin-top:30px; color:var(--text-muted); font-size:13px;">
    Want a private watchlist with online/offline notifications for specific players?
    <a href="/auth/steam/login" style="color:var(--accent-bright); font-weight:600;">Sign in with Steam</a> — free, no email required.
  </p>
</article>

<footer class="footer">
  <a href="/">Home</a>
  <span class="footer-divider">·</span>
  <a href="/imprint">Imprint</a>
  <span class="footer-divider">·</span>
  <a href="/privacy">Privacy</a>
  <span class="footer-divider">·</span>
  <a href="https://github.com/swiss-shift-ch/rustmetrics" target="_blank" rel="noopener" title="Source on GitHub">GitHub ★</a>
</footer>

</body></html>"""
    return html, 200


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN STATS
# ═══════════════════════════════════════════════════════════════════════════

def _iter_caddy_log_lines(base_path: str):
    """Yields lines from all still-retained rotated (.gz) log siblings, oldest
    first, then the active log file. Best-effort: unreadable files are skipped."""
    d = os.path.dirname(base_path)
    stem = os.path.splitext(os.path.basename(base_path))[0]
    rotated = sorted(glob.glob(os.path.join(d, f"{stem}-*.log.gz")))
    for gz_path in rotated:
        try:
            with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    yield line
        except Exception:
            continue
    try:
        with open(base_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line
    except Exception:
        pass


_RE_TRAFFIC_IP      = re.compile(r'"(?:client_ip|remote_ip|remote_addr)"\s*:\s*"([^"]+)"')
_RE_TRAFFIC_URI     = re.compile(r'"uri"\s*:\s*"([^"]+)"')
_RE_TRAFFIC_STATUS  = re.compile(r'"status"\s*:\s*(\d+)')
_RE_TRAFFIC_REFERER = re.compile(r'"Referer"\s*:\s*\[\s*"([^"]+)"')
_RE_TRAFFIC_TS      = re.compile(r'"ts"\s*:\s*([\d.]+)')
_RE_TRAFFIC_LOGDATE = re.compile(r'^(\d{4})/(\d{2})/(\d{2})[ \t]')


def _ingest_traffic_history(days_keep: int = 60) -> int:
    """
    Aggregiert das Caddy-Access-Log (aktive Datei + noch vorhandene .gz-Rotationen)
    pro Kalendertag (UTC) in die Postgres-Tabelle traffic_daily.

    Grund: parse_caddy_log_tail() liest nur die letzten paar MB der aktiven Datei
    (Momentaufnahme, "seit wann online" ist nicht beantwortbar). Caddy rotiert
    rustmetrics.log ab 10 MB und behaelt nur die letzten 5 Rotationen
    (roll_keep 5, roll_keep_for 720h) - bei der aktuellen Traffic-Rate sind das
    real nur ca. 2 Tage, aeltere Daten (z.B. ein Reddit-Launch-Peak) sind dann
    unwiderruflich weg.

    Diese Funktion schreibt jeden Tag, fuer den gerade Log-Daten vorliegen,
    komplett neu in traffic_daily (UPSERT, kein Aufsummieren) - dadurch ist ein
    wiederholter Lauf idempotent und es gibt keine Doppelzaehlung. Sie laeuft
    alle 15 Minuten (siehe traffic_history_loop) - deutlich oefter als Caddy
    rotiert - damit jeder Tag mindestens einmal vollstaendig erfasst wird, bevor
    seine Log-Zeilen aus den verbleibenden .gz-Dateien herausfallen. Einmal
    geschriebene Tage bleiben stehen, auch wenn ihre Rohdaten laengst rotiert
    und geloescht sind - das IST die Historie.
    """
    per_day: dict[str, dict] = {}
    cutoff_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_keep * 86400))

    for line in _iter_caddy_log_lines(CADDY_LOG_PATH):
        if not line.strip() or ('"request"' not in line and '"uri"' not in line):
            continue
        # 1) bevorzugt echtes JSON-"ts" (falls Caddy je auf format json umgestellt wird)
        m_ts = _RE_TRAFFIC_TS.search(line)
        if m_ts:
            try:
                day = time.strftime("%Y-%m-%d", time.gmtime(float(m_ts.group(1))))
            except ValueError:
                day = None
        else:
            day = None
        # 2) sonst Klartext-Datum vom Zeilenanfang (aktuelles "console"-Format)
        if day is None:
            m_ld = _RE_TRAFFIC_LOGDATE.match(line)
            if not m_ld:
                continue
            day = f"{m_ld.group(1)}-{m_ld.group(2)}-{m_ld.group(3)}"
        if day < cutoff_day:
            continue
        m_uri = _RE_TRAFFIC_URI.search(line)
        if not m_uri:
            continue
        m_st = _RE_TRAFFIC_STATUS.search(line)
        if m_st and int(m_st.group(1)) >= 400:
            continue

        bucket = per_day.setdefault(
            day, {"hits": 0, "ips": set(), "reddit": 0, "paths": {}, "refs": {}})
        bucket["hits"] += 1

        uri = m_uri.group(1)
        if not (uri.startswith("/api/") or uri.startswith("/favicon")
                or uri.endswith((".css", ".js", ".png", ".svg", ".ico", ".webmanifest"))):
            stem_uri = uri.split("?", 1)[0]
            bucket["paths"][stem_uri] = bucket["paths"].get(stem_uri, 0) + 1

        m_ip = _RE_TRAFFIC_IP.search(line)
        if m_ip:
            bucket["ips"].add(m_ip.group(1))

        m_rf = _RE_TRAFFIC_REFERER.search(line)
        if m_rf and m_rf.group(1):
            try:
                host = urllib.parse.urlparse(m_rf.group(1)).netloc or m_rf.group(1)
                bucket["refs"][host] = bucket["refs"].get(host, 0) + 1
                if "reddit" in host.lower():
                    bucket["reddit"] += 1
            except Exception:
                pass

    if not per_day:
        return 0

    now = int(time.time())
    with _Conn() as (conn, cur):
        for day, b in per_day.items():
            top_paths = sorted(b["paths"].items(), key=lambda x: -x[1])[:15]
            top_refs  = sorted(b["refs"].items(), key=lambda x: -x[1])[:10]
            cur.execute(
                "INSERT INTO traffic_daily "
                "(day, hits_total, unique_ips, reddit_hits, top_paths, top_referers, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (day) DO UPDATE SET "
                "hits_total=EXCLUDED.hits_total, unique_ips=EXCLUDED.unique_ips, "
                "reddit_hits=EXCLUDED.reddit_hits, top_paths=EXCLUDED.top_paths, "
                "top_referers=EXCLUDED.top_referers, updated_at=EXCLUDED.updated_at",
                (day, b["hits"], len(b["ips"]), b["reddit"],
                 json.dumps(top_paths), json.dumps(top_refs), now))
    return len(per_day)


_traffic_ingest_stop = threading.Event()


def traffic_history_loop():
    print("[traffic-history] gestartet", flush=True)
    while not _traffic_ingest_stop.is_set():
        try:
            n = _ingest_traffic_history()
            if n:
                print(f"[traffic-history] {n} Tag(e) aktualisiert", flush=True)
        except Exception as e:
            print(f"[traffic-history] WARN: {type(e).__name__}: {e}", flush=True)
        _traffic_ingest_stop.wait(900)


_skins_price_stop = threading.Event()


def skins_price_loop():
    """Holt Skin-Preise 1x/Stunde und schreibt sie in die eigene DB.

    Frontend/API fragen NIE live gegen Skinport/Steam — siehe Bau-Auftrag
    Abschnitt 4 ("Muss gecached werden").
    """
    print("[skins-price] gestartet", flush=True)
    while not _skins_price_stop.is_set():
        try:
            n = refresh_skin_prices(_Conn)
            print(f"[skins-price] {n} Item(s) aktualisiert", flush=True)
        except Exception as e:
            print(f"[skins-price] WARN: {type(e).__name__}: {e}", flush=True)
        _skins_price_stop.wait(SKINPORT_MIN_INTERVAL_SEC)


def api_skins_items() -> dict:
    """Liest Items+Preise ausschliesslich aus der eigenen DB (kein Live-Call).

    skin_items enthaelt den kompletten Skinport-Katalog (~5000+ Items ueber
    alle Rust-Kategorien: weapon, misc, resource, clothing, ...) — siehe
    rm_skins.refresh_skin_prices(). Nur ein kuratierter Bruchteil davon hat
    einen slot (rm_skin_slots.SLOT_MAP) und ist damit ueberhaupt ein reales
    Outfit-Wearable. Der Outfit Builder ist kein allgemeiner Preisvergleich
    fuer den ganzen Katalog, also WHERE slot IS NOT NULL — sonst ertrinkt
    die Liste in tausenden Waffen-/Deployable-Skins ohne Icon.
    """
    with _Conn() as (conn, cur):
        cur.execute("""
            SELECT i.item_id, i.name, i.slot, i.raw_category, i.image_url,
                   p.platform, p.price, p.currency, p.url, p.quantity, p.updated_at
            FROM skin_items i
            LEFT JOIN skin_prices p ON p.item_id = i.item_id
            WHERE i.slot IS NOT NULL
            ORDER BY i.name
        """)
        rows = cur.fetchall()
    items: dict[str, dict] = {}
    for r in rows:
        item = items.setdefault(r["item_id"], {
            "item_id": r["item_id"],
            "name": r["name"],
            "slot": r["slot"],
            "raw_category": r["raw_category"],
            "image_url": r["image_url"],
            "prices": [],
        })
        if r["platform"]:
            item["prices"].append({
                "platform": r["platform"],
                "price": float(r["price"]) if r["price"] is not None else None,
                "currency": r["currency"],
                "url": r["url"],
                "quantity": r["quantity"],
                "updated_at": r["updated_at"],
            })
    return {"items": list(items.values())}


def parse_caddy_log_tail(path: str = None, max_bytes: int = None) -> dict:
    """
    Liest die letzten max_bytes des Caddy-Access-Logs und extrahiert:
      - unique_ips (Set)
      - top_paths (Counter)
      - top_referers (Counter)
      - reddit_hits (int)
      - hits_total (int)
      - hits_by_hour (dict: "YYYY-MM-DD HH" → count, letzte 24h)
    Best-effort: Caddy log format kann JSON oder console sein, wir nutzen Regex.
    """
    if path is None: path = CADDY_LOG_PATH
    if max_bytes is None: max_bytes = CADDY_LOG_MAX_BYTES
    out = {
        "available":     False,
        "log_path":      path,
        "log_size":      0,
        "hits_total":    0,
        "unique_ips":    0,
        "reddit_hits":   0,
        "top_paths":     [],
        "top_referers":  [],
        "hits_by_hour":  [],
        "error":         None,
    }
    try:
        st = os.stat(path)
        out["log_size"] = st.st_size
    except FileNotFoundError:
        out["error"] = "log file not found"
        return out
    except PermissionError:
        out["error"] = "log file not readable (chown caddy:rustmetrics oder ACL setzen)"
        return out
    except Exception as e:
        out["error"] = f"stat: {type(e).__name__}: {e}"
        return out

    try:
        with open(path, "rb") as f:
            if out["log_size"] > max_bytes:
                f.seek(-max_bytes, 2)
                f.readline()  # erste Zeile ist möglicherweise abgeschnitten — wegwerfen
            data = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        out["error"] = f"read: {type(e).__name__}: {e}"
        return out

    out["available"] = True

    # Regex-basierte Extraction. Caddy in beiden Formaten (console / json) hat
    # die Felder als "key":"value" oder "key":value im JSON-Block jeder Zeile.
    re_ip       = re.compile(r'"(?:client_ip|remote_ip|remote_addr)"\s*:\s*"([^"]+)"')
    re_uri      = re.compile(r'"uri"\s*:\s*"([^"]+)"')
    re_status   = re.compile(r'"status"\s*:\s*(\d+)')
    re_referer  = re.compile(r'"Referer"\s*:\s*\[\s*"([^"]+)"')
    re_ts       = re.compile(r'"ts"\s*:\s*([\d.]+)')

    ips: set[str] = set()
    path_counter: dict[str, int] = {}
    referer_counter: dict[str, int] = {}
    hour_counter: dict[str, int] = {}
    reddit_hits = 0
    total = 0
    now_ts = time.time()
    cutoff_24h = now_ts - 24 * 3600

    for line in data.splitlines():
        if not line.strip() or ('"request"' not in line and '"uri"' not in line):
            continue
        m_ip  = re_ip.search(line)
        m_uri = re_uri.search(line)
        m_st  = re_status.search(line)
        m_rf  = re_referer.search(line)
        m_ts  = re_ts.search(line)
        # Erfolg = mindestens uri da, sonst keine echte Request-Zeile
        if not m_uri:
            continue
        # Nur 2xx/3xx zählen
        if m_st and int(m_st.group(1)) >= 400:
            continue
        total += 1
        uri = m_uri.group(1)
        # API/static rausfiltern für Top-Paths
        if not (uri.startswith("/api/") or uri.startswith("/favicon")
                or uri.endswith(".css") or uri.endswith(".js")
                or uri.endswith(".png") or uri.endswith(".svg")
                or uri.endswith(".ico") or uri.endswith(".webmanifest")):
            # nur den "Pfad-Stamm" (ohne Query)
            stem = uri.split("?", 1)[0]
            path_counter[stem] = path_counter.get(stem, 0) + 1
        if m_ip:
            ips.add(m_ip.group(1))
        if m_rf:
            ref = m_rf.group(1)
            if ref:
                # auf Host-Anteil normalisieren
                try:
                    host = urllib.parse.urlparse(ref).netloc or ref
                    referer_counter[host] = referer_counter.get(host, 0) + 1
                    if "reddit" in host.lower():
                        reddit_hits += 1
                except Exception:
                    pass
        if m_ts:
            try:
                t = float(m_ts.group(1))
                if t >= cutoff_24h:
                    hkey = time.strftime("%Y-%m-%d %H", time.gmtime(t))
                    hour_counter[hkey] = hour_counter.get(hkey, 0) + 1
            except ValueError:
                pass

    out["hits_total"]   = total
    out["unique_ips"]   = len(ips)
    out["reddit_hits"]  = reddit_hits
    out["top_paths"]    = sorted(path_counter.items(), key=lambda x: -x[1])[:15]
    out["top_referers"] = sorted(referer_counter.items(), key=lambda x: -x[1])[:10]
    out["hits_by_hour"] = sorted(hour_counter.items())[-24:]
    return out


def admin_stats_data() -> dict:
    """SQL-Aggregate für /admin/stats. Liefert Dict mit allen Metriken."""
    now = int(time.time())
    data: dict = {"generated_at": now}
    with _Conn() as (conn, cur):
        # User-Counts: Ghost-Spieler (seeded für Leaderboard-Demo) ausschließen
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE NOT is_ghost")
        data["users_total"] = cur.fetchone()["c"]

        cur.execute(
            "SELECT COUNT(*) AS c FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.expires_at > %s AND NOT u.is_ghost", (now,))
        data["sessions_active"] = cur.fetchone()["c"]

        for k, secs in [("active_1h", 3600), ("active_24h", 86400),
                        ("active_7d", 7 * 86400), ("active_30d", 30 * 86400)]:
            cur.execute(
                f"SELECT COUNT(*) AS c FROM users WHERE {ACTIVITY_TS_SQL} > %s AND NOT is_ghost",
                (now - secs,))
            data[k] = cur.fetchone()["c"]

        for k, secs in [("new_24h", 86400), ("new_7d", 7 * 86400),
                        ("new_30d", 30 * 86400)]:
            cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at > %s AND NOT is_ghost",
                (now - secs,))
            data[k] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM watched_servers")
        data["watched_servers_total"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM watched_players")
        data["watched_players_total"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM servers")
        data["servers_total"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM v_active_servers")
        data["servers_active"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM players")
        data["players_tracked"] = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM players WHERE last_seen > %s",
            (now - 86400,))
        data["players_seen_24h"] = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM players WHERE steam_id IS NOT NULL")
        data["players_with_steam_id"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM snapshots")
        data["snapshots_total"] = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM player_sessions WHERE end_ts IS NULL")
        data["open_player_sessions"] = cur.fetchone()["c"]

        # Top-10 meist-gewatchte Server (über alle User hinweg)
        cur.execute(
            "SELECT s.id, s.host, s.port, COALESCE(s.name, s.host || ':' || s.port) AS name, "
            "       COUNT(ws.user_id) AS watchers "
            "FROM servers s JOIN watched_servers ws ON ws.server_id=s.id "
            "GROUP BY s.id ORDER BY watchers DESC, s.id LIMIT 10")
        data["top_watched_servers"] = [dict(r) for r in cur.fetchall()]

        # Top-10 meist-gewatchte Player-Namen
        cur.execute(
            "SELECT name, COUNT(*) AS watchers FROM watched_players "
            "GROUP BY name ORDER BY watchers DESC, name LIMIT 10")
        data["top_watched_players"] = [dict(r) for r in cur.fetchall()]

        # Letzte 10 Signups (echte User, keine Ghosts)
        cur.execute(
            "SELECT id, display_name, created_at, last_login_at "
            "FROM users WHERE NOT is_ghost ORDER BY created_at DESC LIMIT 10")
        data["recent_signups"] = [dict(r) for r in cur.fetchall()]

        # Signups pro Tag, letzte 14 Tage (für Sparkline) — auch ohne Ghosts
        cur.execute(
            "SELECT DATE(to_timestamp(created_at)) AS day, COUNT(*) AS c "
            "FROM users WHERE created_at > %s AND NOT is_ghost "
            "GROUP BY day ORDER BY day",
            (now - 14 * 86400,))
        data["signups_by_day"] = [(str(r["day"]), r["c"]) for r in cur.fetchall()]

    # Persistierte Traffic-Historie (ueberlebt Caddy-Log-Rotation)
    with _Conn() as (conn, cur):
        cur.execute(
            "SELECT day, hits_total, unique_ips, reddit_hits FROM traffic_daily "
            "WHERE day > CURRENT_DATE - INTERVAL '30 days' ORDER BY day")
        data["traffic_history"] = [
            {"day": str(r["day"]), "hits": r["hits_total"],
             "ips": r["unique_ips"], "reddit": r["reddit_hits"]}
            for r in cur.fetchall()]

    # Caddy-Log Stats (best effort, kann fehlen)
    data["caddy"] = parse_caddy_log_tail()
    return data


def render_admin_stats_page(user_session: dict) -> tuple[str, int]:
    """Rendert /admin/stats — nur für is_admin Users."""
    d = admin_stats_data()
    now = d["generated_at"]
    user_name = user_session.get("display_name") or "admin"

    # Helper für hübsche Zahlen
    def fnum(n): return f"{n:,}".replace(",", " ")

    # Top-Server-Tabelle
    server_rows = []
    for s in d["top_watched_servers"]:
        server_rows.append(
            f"<tr><td><a href='/server/{_esc(s['host'])}:{s['port']}' "
            f"style='color:var(--text); text-decoration:none;'>{_esc(s['name'])}</a>"
            f"<div style='font-size:11px; color:var(--text-dim); font-family:\"SF Mono\",monospace;'>"
            f"{_esc(s['host'])}:{s['port']}</div></td>"
            f"<td style='text-align:right; font-variant-numeric:tabular-nums;'>{s['watchers']}</td></tr>")
    server_table = ("<table style='width:100%; border-collapse:collapse;'>"
                    "<thead><tr style='border-bottom:1px solid var(--border); color:var(--text-dim);"
                    " font-size:11px; text-transform:uppercase; letter-spacing:1px;'>"
                    "<th style='text-align:left; padding:6px 8px;'>Server</th>"
                    "<th style='text-align:right; padding:6px 8px;'>Watchers</th>"
                    "</tr></thead><tbody>"
                    + ("".join(server_rows) or "<tr><td colspan=2 style='padding:10px; color:var(--text-dim)'>none yet</td></tr>")
                    + "</tbody></table>")

    # Top-Players-Tabelle
    player_rows = []
    for p in d["top_watched_players"]:
        player_rows.append(
            f"<tr><td>{_esc(p['name'])}</td>"
            f"<td style='text-align:right; font-variant-numeric:tabular-nums;'>{p['watchers']}</td></tr>")
    player_table = ("<table style='width:100%; border-collapse:collapse;'>"
                    "<thead><tr style='border-bottom:1px solid var(--border); color:var(--text-dim);"
                    " font-size:11px; text-transform:uppercase; letter-spacing:1px;'>"
                    "<th style='text-align:left; padding:6px 8px;'>Player</th>"
                    "<th style='text-align:right; padding:6px 8px;'>Watchers</th>"
                    "</tr></thead><tbody>"
                    + ("".join(player_rows) or "<tr><td colspan=2 style='padding:10px; color:var(--text-dim)'>none yet</td></tr>")
                    + "</tbody></table>")

    # Recent Signups
    signup_rows = []
    for u in d["recent_signups"]:
        ago = max(0, now - u["created_at"])
        if   ago < 3600:   ago_str = f"{ago // 60}m ago"
        elif ago < 86400:  ago_str = f"{ago // 3600}h ago"
        else:              ago_str = f"{ago // 86400}d ago"
        signup_rows.append(
            f"<tr><td>{_esc(u['display_name'] or str(u['id']))}</td>"
            f"<td style='font-family:\"SF Mono\",monospace; font-size:11px; color:var(--text-dim);'>{u['id']}</td>"
            f"<td style='text-align:right; color:var(--text-dim); font-size:12px;'>{ago_str}</td></tr>")
    signups_table = ("<table style='width:100%; border-collapse:collapse;'>"
                     "<thead><tr style='border-bottom:1px solid var(--border); color:var(--text-dim);"
                     " font-size:11px; text-transform:uppercase; letter-spacing:1px;'>"
                     "<th style='text-align:left; padding:6px 8px;'>Display name</th>"
                     "<th style='text-align:left; padding:6px 8px;'>SteamID64</th>"
                     "<th style='text-align:right; padding:6px 8px;'>Signed up</th>"
                     "</tr></thead><tbody>"
                     + ("".join(signup_rows) or "<tr><td colspan=3 style='padding:10px; color:var(--text-dim)'>none yet</td></tr>")
                     + "</tbody></table>")

    # Signup-Sparkline (letzte 14 Tage)
    sparkline_html = ""
    if d["signups_by_day"]:
        vals = [v for _, v in d["signups_by_day"]]
        max_v = max(vals) if vals else 1
        bars = []
        for day, v in d["signups_by_day"]:
            h = int(40 * v / max_v) if max_v else 0
            bars.append(
                f"<div title='{day}: {v} signups' style='flex:1; min-width:14px; "
                f"display:flex; flex-direction:column; align-items:center; gap:4px;'>"
                f"<div style='width:100%; height:42px; display:flex; align-items:flex-end;'>"
                f"<div style='width:100%; height:{max(2,h)}px; background:var(--accent); border-radius:2px;'></div>"
                f"</div>"
                f"<div style='font-size:10px; color:var(--text-dim); font-variant-numeric:tabular-nums;'>{v}</div>"
                f"</div>")
        sparkline_html = (
            "<div style='display:flex; gap:3px; align-items:flex-end; padding:8px 0;'>"
            + "".join(bars) + "</div>"
            f"<p class='hint' style='text-align:right; font-size:11px;'>last 14 days · max {max_v}/day</p>")

    # Traffic-History Section (persistiert, ueberlebt Log-Rotation)
    hist = d.get("traffic_history") or []
    if hist:
        hmax = max(h["hits"] for h in hist) or 1
        bars = []
        for h in hist:
            hday, hhits, hips, hreddit = h["day"], h["hits"], h["ips"], h["reddit"]
            bh = int(40 * hhits / hmax) if hmax else 0
            bar_title = f"{hday}: {hhits} hits, {hips} IPs, {hreddit} reddit"
            bars.append(
                f"<div title='{bar_title}' style='flex:1; min-width:6px; "
                f"display:flex; flex-direction:column; align-items:center;'>"
                f"<div style='width:100%; height:42px; display:flex; align-items:flex-end;'>"
                f"<div style='width:100%; height:{max(2,bh)}px; background:var(--accent); border-radius:2px;'></div>"
                f"</div></div>")
        total_hits   = sum(h["hits"] for h in hist)
        total_reddit = sum(h["reddit"] for h in hist)
        history_html = f"""
        <section class="detail-section">
          <h3>Traffic history · persisted daily ({len(hist)} days)</h3>
          <dl class="detail-meta">
            <dt>Total requests in window</dt><dd>{fnum(total_hits)}</dd>
            <dt>Reddit referrals in window</dt><dd>{fnum(total_reddit)}</dd>
            <dt>First day recorded</dt><dd>{_esc(hist[0]['day'])}</dd>
          </dl>
          <div style="display:flex; gap:2px; align-items:flex-end; padding:8px 0;">{"".join(bars)}</div>
          <p class="hint" style="text-align:right; font-size:11px;">last {len(hist)} days · max {fnum(hmax)}/day · survives Caddy log rotation</p>
        </section>"""
    else:
        history_html = """
        <section class="detail-section">
          <h3>Traffic history · persisted daily</h3>
          <p class="hint">Noch keine Tage aggregiert - der Hintergrund-Job (traffic_history_loop) laeuft alle 15 Minuten
             und schreibt nach traffic_daily. Kurz nach Deploy kann diese Liste leer sein.</p>
        </section>"""

    # Caddy-Log Section
    caddy = d["caddy"]
    if caddy["available"]:
        log_size_mb = caddy["log_size"] / (1024 * 1024)
        path_rows = "".join(
            f"<tr><td style='font-family:\"SF Mono\",monospace; font-size:12px;'>{_esc(p)}</td>"
            f"<td style='text-align:right; font-variant-numeric:tabular-nums;'>{c}</td></tr>"
            for p, c in caddy["top_paths"]) or \
            "<tr><td colspan=2 style='padding:10px; color:var(--text-dim)'>none</td></tr>"
        ref_rows = "".join(
            f"<tr><td style='font-family:\"SF Mono\",monospace; font-size:12px;'>{_esc(r)}</td>"
            f"<td style='text-align:right; font-variant-numeric:tabular-nums;'>{c}</td></tr>"
            for r, c in caddy["top_referers"]) or \
            "<tr><td colspan=2 style='padding:10px; color:var(--text-dim)'>none</td></tr>"
        # Hits-by-Hour Mini-Chart
        hits_bars = ""
        if caddy["hits_by_hour"]:
            hv = [v for _, v in caddy["hits_by_hour"]]
            hmax = max(hv) if hv else 1
            bars = []
            for hkey, v in caddy["hits_by_hour"]:
                h = int(40 * v / hmax) if hmax else 0
                bars.append(
                    f"<div title='{hkey}: {v} hits' style='flex:1; min-width:8px; "
                    f"display:flex; flex-direction:column; align-items:center;'>"
                    f"<div style='width:100%; height:42px; display:flex; align-items:flex-end;'>"
                    f"<div style='width:100%; height:{max(2,h)}px; background:var(--hazmat); border-radius:2px;'></div>"
                    f"</div></div>")
            hits_bars = ("<div style='display:flex; gap:2px; align-items:flex-end; padding:8px 0;'>"
                         + "".join(bars) + "</div>"
                         f"<p class='hint' style='text-align:right; font-size:11px;'>last 24h · max {hmax}/h</p>")
        caddy_html = f"""
        <section class="detail-section">
          <h3>Traffic · Caddy access log ({log_size_mb:.2f} MB tail)</h3>
          <dl class="detail-meta">
            <dt>Total requests (parsed)</dt><dd>{fnum(caddy['hits_total'])}</dd>
            <dt>Unique IPs</dt><dd>{fnum(caddy['unique_ips'])}</dd>
            <dt>Reddit referrals</dt><dd>{fnum(caddy['reddit_hits'])}</dd>
          </dl>
          {hits_bars}
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:14px;">
            <div><h4 style="margin:0 0 6px 0; font-size:12px; color:var(--text-dim); text-transform:uppercase; letter-spacing:1px;">Top paths</h4>
              <table style='width:100%; border-collapse:collapse;'><tbody>{path_rows}</tbody></table>
            </div>
            <div><h4 style="margin:0 0 6px 0; font-size:12px; color:var(--text-dim); text-transform:uppercase; letter-spacing:1px;">Top referrers</h4>
              <table style='width:100%; border-collapse:collapse;'><tbody>{ref_rows}</tbody></table>
            </div>
          </div>
        </section>"""
    else:
        caddy_html = f"""
        <section class="detail-section">
          <h3>Traffic · Caddy access log</h3>
          <p class="hint">Log unavailable: <code>{_esc(caddy.get('error') or 'unknown')}</code><br>
             Pfad: <code>{_esc(caddy['log_path'])}</code> — falls die Datei existiert, lies-Rechte für den
             rustmetrics-Service-User per <code>chmod 644</code> oder ACL setzen.</p>
        </section>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex, nofollow" />
<meta http-equiv="refresh" content="60" />
<title>Admin Stats — RustMetrics</title>
<link rel="stylesheet" href="/style.css?v=20260517g" />
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
</head><body>

<header class="topbar">
  <div class="brand">
    <a href="/" style="display:flex; align-items:center; gap:12px; text-decoration:none; color:inherit;">
      <span class="brand-mark" aria-hidden="true"></span>
      <span class="brand-name">RUST<span class="brand-name-accent">METRICS</span></span>
    </a>
  </div>
  <div class="topbar-actions" style="margin-left:auto;">
    <span style="color:var(--text-dim); font-size:12px;">admin · {_esc(user_name)}</span>
    <a class="btn-ghost" href="/app" style="text-decoration:none;">← App</a>
  </div>
</header>

<article class="legal-page" style="max-width:1100px;">
  <h1 style="text-transform:none; font-size:22px; margin-bottom:4px;">Admin Stats</h1>
  <p style="color:var(--text-muted); font-size:12px; margin:0 0 18px 0;">
    auto-refresh every 60s · generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))}
  </p>

  <section class="detail-section">
    <h3>Users</h3>
    <dl class="detail-meta">
      <dt>Total signups (lifetime)</dt><dd>{fnum(d['users_total'])}</dd>
      <dt>Active sessions now</dt><dd>{fnum(d['sessions_active'])}</dd>
      <dt>Active last 1h / 24h / 7d / 30d <small>(last seen, else last login)</small></dt>
      <dd>{fnum(d['active_1h'])} · {fnum(d['active_24h'])} · {fnum(d['active_7d'])} · {fnum(d['active_30d'])}</dd>
      <dt>New signups 24h / 7d / 30d</dt>
      <dd>{fnum(d['new_24h'])} · {fnum(d['new_7d'])} · {fnum(d['new_30d'])}</dd>
    </dl>
    {sparkline_html}
  </section>

  <section class="detail-section">
    <h3>Engagement</h3>
    <dl class="detail-meta">
      <dt>Watched servers (per-user rows)</dt><dd>{fnum(d['watched_servers_total'])}</dd>
      <dt>Watched players (per-user rows)</dt><dd>{fnum(d['watched_players_total'])}</dd>
      <dt>Distinct servers tracked</dt><dd>{fnum(d['servers_total'])} ({fnum(d['servers_active'])} with active watchers)</dd>
      <dt>Player profiles built</dt><dd>{fnum(d['players_tracked'])} ({fnum(d['players_seen_24h'])} seen last 24h)</dd>
      <dt>SteamID resolved</dt><dd>{fnum(d['players_with_steam_id'])}</dd>
      <dt>Open player sessions (currently online somewhere we track)</dt><dd>{fnum(d['open_player_sessions'])}</dd>
      <dt>Snapshots in DB</dt><dd>{fnum(d['snapshots_total'])}</dd>
    </dl>
  </section>

  <section class="detail-section">
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:18px;">
      <div><h3 style="margin-top:0;">Top watched servers</h3>{server_table}</div>
      <div><h3 style="margin-top:0;">Top watched players</h3>{player_table}</div>
    </div>
  </section>

  <section class="detail-section">
    <h3>Recent signups</h3>
    {signups_table}
  </section>

  {history_html}

  {caddy_html}

  <p style="color:var(--text-muted); font-size:11px; margin-top:30px; text-align:center;">
    SQL aggregates from Postgres · Caddy log parsing from {_esc(caddy['log_path'])}<br>
    This page is admin-only and excluded from search engines (robots noindex).
  </p>
</article>

<footer class="footer">
  <a href="/">Home</a>
  <span class="footer-divider">·</span>
  <span style="color:var(--text-dim); font-size:11px;">/admin/stats</span>
</footer>

</body></html>"""
    return html, 200


def _fmt_int(n) -> str:
    """1234567 → '1 234 567' (thin-space). None/0 → '—'."""
    if n is None: return "—"
    if not isinstance(n, (int, float)): return str(n)
    s = f"{int(n):,}"
    return s.replace(",", " ")


def _fmt_duration_secs(s) -> str:
    """120 → '2m'. 7200 → '2h'. 86400 → '1d'. None → '—'."""
    if not s: return "—"
    s = int(s)
    if s < 60: return f"{s}s"
    if s < 3600: return f"{s // 60}m"
    if s < 86400:
        h = s // 3600
        return f"{h}h" if (s % 3600) < 60 else f"{h}h {(s % 3600) // 60:02d}m"
    d = s // 86400
    h = (s % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def _render_rust_stats_block(row: dict) -> str:
    """Generiert das HTML für den Lifetime-Stats-Block einer Rust-Player-Page."""
    # K/D Ratio berechnen
    kills  = row.get("kill_player") or 0
    deaths = row.get("deaths") or 0
    kd     = f"{kills / max(1, deaths):.2f}" if deaths else "—"
    hs     = row.get("headshot") or 0
    hs_pct = f"{100 * hs / max(1, kills):.1f}%" if kills else "—"
    bullets_fired   = row.get("bullet_fired") or 0
    bullets_hit_pl  = row.get("bullet_hit_player") or 0
    accuracy        = f"{100 * bullets_hit_pl / max(1, bullets_fired):.1f}%" if bullets_fired else "—"

    # Layout: Stat-Cards-Grid (numbers + labels)
    def card(label, value, sub=""):
        sub_html = f"<div class='stat-sub'>{_esc(sub)}</div>" if sub else ""
        return (f"<div class='stat-card'>"
                f"<div class='stat-num'>{_esc(value)}</div>"
                f"<div class='stat-label'>{_esc(label)}</div>"
                f"{sub_html}</div>")

    # Top-Row: Combat
    combat_cards = "".join([
        card("Kills",     _fmt_int(kills), sub=f"K/D {kd}"),
        card("Deaths",    _fmt_int(deaths)),
        card("Headshots", _fmt_int(hs),    sub=f"{hs_pct} of kills"),
        card("Hit rate",  accuracy,        sub=f"{_fmt_int(bullets_fired)} fired"),
    ])

    # Mid-Row: Bullets by target
    bullet_hits = [
        ("Players",       row.get("bullet_hit_player")),
        ("Buildings",     row.get("bullet_hit_building")),
        ("Signs",         row.get("bullet_hit_sign")),
        ("Wolves",        row.get("bullet_hit_wolf")),
        ("Bears",         row.get("bullet_hit_bear")),
        ("Boars",         row.get("bullet_hit_boar")),
        ("Stags",         row.get("bullet_hit_stag")),
        ("Horses",        row.get("bullet_hit_horse")),
        ("Corpses",       row.get("bullet_hit_corpse")),
    ]
    bullet_rows = "".join(
        f"<div class='stat-row'><span>{_esc(lbl)}</span>"
        f"<span class='stat-row-v'>{_fmt_int(v)}</span></div>"
        for lbl, v in bullet_hits if v
    )
    bullet_html = (
        "<div class='stat-list'><div class='stat-list-title'>Bullets hit, by target</div>"
        + bullet_rows + "</div>" if bullet_rows else ""
    )

    # Bottom-Row: Harvested
    harvest = [
        ("Wood",       row.get("harvested_wood")),
        ("Stones",     row.get("harvested_stones")),
        ("Cloth",      row.get("harvested_cloth")),
        ("Leather",    row.get("harvested_leather")),
        ("Sulfur ore", row.get("harvested_sulfur_ore")),
        ("Metal ore",  row.get("harvested_metal_ore")),
        ("HQ metal",   row.get("harvested_hq_metal_ore")),
        ("Scrap",      row.get("acquired_scrap")),
        ("Sulfur (gathered)", row.get("acquired_sulfur")),
        ("Metal frags",row.get("acquired_metalfrag")),
        ("Low-grade fuel", row.get("acquired_lowgradefuel")),
    ]
    harvest_rows = "".join(
        f"<div class='stat-row'><span>{_esc(lbl)}</span>"
        f"<span class='stat-row-v'>{_fmt_int(v)}</span></div>"
        for lbl, v in harvest if v
    )
    harvest_html = (
        "<div class='stat-list'><div class='stat-list-title'>Harvested / acquired</div>"
        + harvest_rows + "</div>" if harvest_rows else ""
    )

    # Misc
    misc = [
        ("Playtime (tracked by Rust)", _fmt_duration_secs(row.get("seconds_played"))),
        ("Wounded",        _fmt_int(row.get("wounded"))),
        ("C4 thrown",      _fmt_int(row.get("c4_thrown"))),
        ("Rockets fired",  _fmt_int(row.get("rocket_fired"))),
        ("Melee thrown",   _fmt_int(row.get("melee_thrown"))),
        ("Arrows fired",   _fmt_int(row.get("arrow_fired"))),
        ("Arrows hit",     _fmt_int(row.get("arrow_hit_player"))),
        ("Time cold",      _fmt_duration_secs(row.get("seconds_cold"))),
        ("Time hot",       _fmt_duration_secs(row.get("seconds_hot"))),
        ("Time comfy",     _fmt_duration_secs(row.get("seconds_comfort"))),
    ]
    misc_rows = "".join(
        f"<div class='stat-row'><span>{_esc(lbl)}</span>"
        f"<span class='stat-row-v'>{_esc(v)}</span></div>"
        for lbl, v in misc if v not in ("—", None, 0, "0")
    )
    misc_html = (
        "<div class='stat-list'><div class='stat-list-title'>Other</div>"
        + misc_rows + "</div>" if misc_rows else ""
    )

    fetched = row.get("fetched_at") or 0
    age = max(0, int(time.time()) - fetched) if fetched else None
    if   age is None:        age_str = ""
    elif age < 60:           age_str = "just now"
    elif age < 3600:         age_str = f"{age // 60}m ago"
    else:                    age_str = f"{age // 3600}h ago"
    footer = (f"<p class='hint' style='text-align:right; font-size:11px; margin-top:8px;'>"
              f"counters from Steam · cached {age_str}</p>" if age_str else "")

    return (
        f"<div class='stat-cards'>{combat_cards}</div>"
        f"<div class='stat-lists'>{bullet_html}{harvest_html}{misc_html}</div>"
        f"{footer}"
    )


def render_player_profile_page(bm_id: int) -> tuple[str, int]:
    """
    Server-side-rendered HTML für /player/<bm_id>. Liefert (html, status).
    Zeigt: aktueller Name, Aliases, Recent Activity (letzte 14 Tage über
    alle Server hinweg), Session-History pro Server, Currently-online-Status.
    Vollständig SSR — keine User-Auth nötig, public + indexable.
    """
    now = int(time.time())
    cutoff_14d = now - 14 * 86400
    with _Conn() as (conn, cur):
        # 1. Player-Metadaten
        cur.execute(
            "SELECT bm_id, current_name, aliases, steam_id, first_seen, last_seen "
            "FROM players WHERE bm_id=%s", (bm_id,))
        p = cur.fetchone()

        # Fallback: bm_id ist noch nicht in players-Tabelle, aber existiert in
        # watched_players (User hat ihn manuell hinzugefügt). Wir nehmen den
        # neuesten Namen von dort.
        if not p:
            cur.execute(
                "SELECT name, added_at FROM watched_players "
                "WHERE bm_player_id=%s ORDER BY added_at DESC LIMIT 1", (bm_id,))
            w = cur.fetchone()
            if not w:
                return _public_player_404_html(bm_id), 404
            p = {
                "bm_id":        bm_id,
                "current_name": w["name"],
                "aliases":      [],
                "steam_id":     None,
                "first_seen":   w["added_at"],
                "last_seen":    w["added_at"],
            }

        all_names = [p["current_name"]] + list(p["aliases"] or [])
        # Lowercase-Vergleich + Dedup
        seen_lower = set()
        all_names_dedup = []
        for n in all_names:
            if n and n.lower() not in seen_lower:
                seen_lower.add(n.lower())
                all_names_dedup.append(n)

        # 2. Sessions: Match per bm_player_id ODER per Name-Liste (für Legacy
        # ohne bm_player_id). Letzte 14 Tage.
        cur.execute(
            "SELECT ps.player_name, ps.start_ts, ps.end_ts, ps.bm_player_id, "
            "       s.id AS server_id, s.host, s.port, s.name AS server_name "
            "FROM player_sessions ps "
            "JOIN servers s ON s.id = ps.server_id "
            "WHERE ps.start_ts >= %s AND ("
            "      ps.bm_player_id = %s "
            "   OR (ps.bm_player_id IS NULL AND ps.player_name = ANY(%s))"
            ") "
            "ORDER BY ps.start_ts DESC LIMIT 500",
            (cutoff_14d, bm_id, all_names_dedup))
        sessions = [dict(x) for x in cur.fetchall()]

        # 3. Currently online? — irgendeine offene Session
        currently_on = None
        for s in sessions:
            if s["end_ts"] is None:
                currently_on = s
                break

        # 4. Aggregat pro Server (letzte 14 Tage): total minutes, session-count
        agg: dict[int, dict] = {}
        for s in sessions:
            sid = s["server_id"]
            end = s["end_ts"] or now
            dur = max(0, end - s["start_ts"])
            a = agg.setdefault(sid, {
                "server_id":   sid,
                "host":        s["host"],
                "port":        s["port"],
                "server_name": s["server_name"] or f"{s['host']}:{s['port']}",
                "total_secs":  0,
                "session_count": 0,
                "last_seen":   0,
            })
            a["total_secs"]    += dur
            a["session_count"] += 1
            a["last_seen"]      = max(a["last_seen"], end)
        agg_list = sorted(agg.values(), key=lambda x: -x["total_secs"])[:10]

    # 5. HTML-Render
    name        = p["current_name"]
    aliases     = [a for a in (p["aliases"] or []) if a and a.lower() != name.lower()][-5:]
    first_seen  = p["first_seen"]
    last_seen   = p["last_seen"]
    steam_id    = p.get("steam_id")
    # Lazy steam-id resolve via BM falls noch nicht bekannt — fire-and-forget,
    # damit der erste Page-Render nicht warten muss. Beim 2. Visit ist's da.
    if not steam_id and bm_id:
        try:
            steam_id = ensure_player_steam_id(bm_id)
        except Exception as e:
            print(f"[steam_id_resolve] bm={bm_id}: {e}", file=sys.stderr, flush=True)
    days_known  = max(0, (now - first_seen) // 86400)

    title = f"{name} — RustMetrics player profile"
    desc_bits = []
    if currently_on:
        sname = currently_on["server_name"] or f"{currently_on['host']}:{currently_on['port']}"
        desc_bits.append(f"🟢 online now on {sname}")
    else:
        if last_seen:
            ago = max(0, now - last_seen)
            if   ago < 3600:   desc_bits.append(f"last seen {ago // 60}m ago")
            elif ago < 86400:  desc_bits.append(f"last seen {ago // 3600}h ago")
            else:              desc_bits.append(f"last seen {ago // 86400}d ago")
    desc_bits.append(f"tracked for {days_known}d")
    if agg_list:
        desc_bits.append(f"played on {len(agg_list)} server(s) in last 14 days")
    og_description = " · ".join(desc_bits)

    # Aliases-HTML
    aliases_html = ""
    if aliases:
        chips = "".join(f"<span class='tag'>{_esc(a)}</span>" for a in aliases)
        aliases_html = f"<p class='hint' style='margin:4px 0 0 0;'>also known as: {chips}</p>"

    # Status-HTML
    status_html = ""
    if currently_on:
        sname = currently_on["server_name"] or f"{currently_on['host']}:{currently_on['port']}"
        shost, sport = currently_on["host"], currently_on["port"]
        dur = max(0, now - currently_on["start_ts"])
        if   dur < 3600:  dur_str = f"{dur // 60}m"
        elif dur < 86400: dur_str = f"{dur // 3600}h {(dur % 3600) // 60}m"
        else:             dur_str = f"{dur // 86400}d {(dur % 86400) // 3600}h"
        status_html = (
            f"<div style='padding:16px; background:rgba(80,160,80,0.08); "
            f"border:1px solid rgba(80,160,80,0.3); border-radius:8px;'>"
            f"<span style='color:var(--green); font-weight:700;'>● ONLINE NOW</span> "
            f"on <a href='/server/{_esc(shost)}:{sport}' style='color:var(--accent-bright)'>"
            f"{_esc(sname)}</a> "
            f"<span style='color:var(--text-dim); font-size:13px;'>· {dur_str}</span>"
            f"</div>")
    else:
        if last_seen:
            ago = max(0, now - last_seen)
            if   ago < 3600:   ago_str = f"{ago // 60}m ago"
            elif ago < 86400:  ago_str = f"{ago // 3600}h ago"
            else:              ago_str = f"{ago // 86400}d ago"
        else:
            ago_str = "never"
        status_html = (
            f"<div style='padding:16px; background:rgba(120,120,120,0.05); "
            f"border:1px solid var(--border); border-radius:8px;'>"
            f"<span style='color:var(--text-dim); font-weight:700;'>● OFFLINE</span> "
            f"<span style='color:var(--text-dim); font-size:13px;'>· last seen {ago_str}</span>"
            f"</div>")

    # Activity-Tabelle (Top-Server der letzten 14 Tage)
    activity_html = ""
    if agg_list:
        rows_html = []
        for a in agg_list:
            total_min = a["total_secs"] // 60
            if total_min >= 60:
                total_str = f"{total_min // 60}h {total_min % 60:02d}m"
            else:
                total_str = f"{total_min}m"
            rows_html.append(
                f"<tr>"
                f"<td><a href='/server/{_esc(a['host'])}:{a['port']}' "
                f"style='color:var(--text); text-decoration:none;'>"
                f"{_esc(a['server_name'])}</a>"
                f"<div style='font-size:11px; color:var(--text-dim); font-family:\"SF Mono\",monospace;'>"
                f"{_esc(a['host'])}:{a['port']}</div></td>"
                f"<td style='text-align:right; font-variant-numeric:tabular-nums;'>{total_str}</td>"
                f"<td style='text-align:right; font-variant-numeric:tabular-nums; color:var(--text-dim);'>"
                f"{a['session_count']}</td>"
                f"</tr>")
        activity_html = (
            "<table style='width:100%; border-collapse:collapse; margin-top:8px;'>"
            "<thead><tr style='border-bottom:1px solid var(--border); color:var(--text-dim);"
            " font-size:11px; text-transform:uppercase; letter-spacing:1px;'>"
            "<th style='text-align:left; padding:6px 8px;'>Server</th>"
            "<th style='text-align:right; padding:6px 8px;'>Playtime</th>"
            "<th style='text-align:right; padding:6px 8px;'>Sessions</th>"
            "</tr></thead><tbody>"
            + "".join(rows_html)
            + "</tbody></table>")
    else:
        activity_html = (
            "<p class='hint' style='text-align:center; padding:20px;'>"
            "No tracked activity in the last 14 days.</p>")

    # Steam-Profil-Button (nur wenn wir die SteamID haben)
    steam_button_html = ""
    if steam_id:
        steam_button_html = (
            f"<a href='https://steamcommunity.com/profiles/{int(steam_id)}/' "
            f"target='_blank' rel='noopener nofollow' "
            f"class='btn-steam' style='text-decoration:none; display:inline-flex; "
            f"align-items:center; gap:8px;'>"
            f"<span class='steam-icon' aria-hidden='true'>⌬</span> Steam Profile</a>")

    # Rust Lifetime Stats (Steam GetUserStatsForGame), server-side gerendert wenn vorhanden.
    # Falls steam_id noch nicht resolved, versucht das Client-JS später es nachzuziehen.
    stats_html = ""
    if steam_id:
        try:
            stats_row = get_rust_user_stats(steam_id, force_refresh=False)
        except Exception as e:
            print(f"[player_stats_render] {e}", file=sys.stderr, flush=True)
            stats_row = None
        if stats_row and not stats_row.get("is_private") and (stats_row.get("kill_player") is not None
                                                              or stats_row.get("seconds_played") is not None):
            stats_html = _render_rust_stats_block(stats_row)
        elif stats_row and stats_row.get("is_private"):
            stats_html = ("<p class='hint' style='padding:16px; text-align:center;'>"
                          "🔒 This player's Steam game-details are private — lifetime stats unavailable.</p>")

    # Recent-Sessions-Liste (letzte 8 closed sessions)
    recent_closed = [s for s in sessions if s["end_ts"] is not None][:8]
    recent_html = ""
    if recent_closed:
        items = []
        for s in recent_closed:
            sname = s["server_name"] or f"{s['host']}:{s['port']}"
            dur = max(0, s["end_ts"] - s["start_ts"])
            if   dur < 60:    dur_str = f"{dur}s"
            elif dur < 3600:  dur_str = f"{dur // 60}m"
            elif dur < 86400: dur_str = f"{dur // 3600}h {(dur % 3600) // 60:02d}m"
            else:             dur_str = f"{dur // 86400}d {(dur % 86400) // 3600}h"
            ago = max(0, now - s["end_ts"])
            if   ago < 3600:   ago_str = f"{ago // 60}m ago"
            elif ago < 86400:  ago_str = f"{ago // 3600}h ago"
            else:              ago_str = f"{ago // 86400}d ago"
            items.append(
                f"<div class='p' style='display:flex; justify-content:space-between; "
                f"align-items:center; padding:8px 10px; border-bottom:1px solid var(--border);'>"
                f"<a href='/server/{_esc(s['host'])}:{s['port']}' "
                f"style='color:var(--text); text-decoration:none; flex:1;'>"
                f"{_esc(sname)}</a>"
                f"<span style='color:var(--text-dim); font-size:12px; font-variant-numeric:tabular-nums;'>"
                f"{dur_str} · {ago_str}</span></div>")
        recent_html = "<div class='player-list'>" + "".join(items) + "</div>"

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(og_description)}" />
<meta name="robots" content="{'index, follow' if agg_list else 'noindex, follow'}" />
<link rel="canonical" href="https://rustmetrics.eu/player/{int(bm_id)}" />
<meta property="og:type" content="profile" />
<meta property="og:site_name" content="RustMetrics" />
<meta property="og:title" content="{_esc(name)}" />
<meta property="og:description" content="{_esc(og_description)}" />
<meta property="og:url" content="https://rustmetrics.eu/player/{int(bm_id)}" />
<meta property="og:image" content="https://rustmetrics.eu/og-image.png" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{_esc(name)}" />
<meta name="twitter:description" content="{_esc(og_description)}" />
<meta name="twitter:image" content="https://rustmetrics.eu/og-image.png" />
<link rel="stylesheet" href="/style.css?v=20260517d" />
</head><body>

<header class="topbar">
  <div class="brand">
    <a href="/" style="display:flex; align-items:center; gap:12px; text-decoration:none; color:inherit;">
      <span class="brand-mark" aria-hidden="true"></span>
      <span class="brand-name">RUST<span class="brand-name-accent">METRICS</span></span>
    </a>
  </div>
  <div class="topbar-actions" style="margin-left:auto;">
    <a class="btn-steam" href="/auth/steam/login" data-anon-only style="text-decoration:none;">
      <span class="steam-icon" aria-hidden="true">⌬</span> Sign in to watch
    </a>
    <a class="btn-ghost" href="/app" data-auth-only style="text-decoration:none; display:none;">
      ← My watchlist
    </a>
  </div>
</header>

<article class="legal-page" style="max-width:880px;">
  <a href="/" class="legal-back">← Home</a>
  <h1 style="text-transform:none; font-size:22px; margin-bottom:4px;">{_esc(name)}</h1>
  <p style="color:var(--text-muted); font-family:'SF Mono',monospace; font-size:13px; margin:0;">
    BM #{int(bm_id)} · tracked for {days_known}d
  </p>
  {aliases_html}

  <section class="detail-section" style="margin-top:18px;">
    {status_html}
    {('<div style="margin-top:12px;">' + steam_button_html + '</div>') if steam_button_html else ''}
  </section>

  {('<section class="detail-section"><h3>Lifetime stats · from Steam</h3>' + stats_html + '</section>') if stats_html else ''}

  <section class="detail-section">
    <h3>Recent activity · last 14 days</h3>
    {activity_html}
  </section>

  {('<section class="detail-section"><h3>Recent sessions</h3>' + recent_html + '</section>') if recent_html else ''}

  <p data-anon-only style="text-align:center; margin-top:30px; color:var(--text-muted); font-size:13px;">
    Want to get notified when {_esc(name)} comes online?
    <a href="/auth/steam/login" style="color:var(--accent-bright); font-weight:600;">Sign in with Steam</a>
    and add them to your watchlist — free, no email required.
  </p>
  <p data-auth-only style="text-align:center; margin-top:30px; color:var(--text-muted); font-size:13px; display:none;">
    To watch <strong>{_esc(name)}</strong>, open the server they play on (above) and
    use the <strong>+</strong> button next to their name in <em>Currently online</em>.
  </p>
</article>

<footer class="footer">
  <a href="/">Home</a>
  <span class="footer-divider">·</span>
  <a href="/imprint">Imprint</a>
  <span class="footer-divider">·</span>
  <a href="/privacy">Privacy</a>
  <span class="footer-divider">·</span>
  <a href="https://github.com/swiss-shift-ch/rustmetrics" target="_blank" rel="noopener" title="Source on GitHub">GitHub ★</a>
</footer>

<script src="/player-page.js?v=20260517f"></script>

</body></html>"""
    return html, 200


def _public_player_404_html(bm_id: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" />
<meta name="robots" content="noindex" />
<title>Player not found — RustMetrics</title>
<link rel="stylesheet" href="/style.css" />
</head><body>
<header class="topbar">
  <div class="brand">
    <a href="/" style="display:flex; align-items:center; gap:12px; text-decoration:none; color:inherit;">
      <span class="brand-mark"></span>
      <span class="brand-name">RUST<span class="brand-name-accent">METRICS</span></span>
    </a>
  </div>
</header>
<article class="legal-page">
  <a href="/" class="legal-back">← Home</a>
  <h1>Player not found</h1>
  <p>We don't track BattleMetrics player <code>#{int(bm_id)}</code> yet.
     Players appear on RustMetrics once they've been seen on a server in our watchlist database.</p>
  <p>Try the <a href="/browser">Server Browser</a> to find live servers.</p>
</article>
</body></html>"""


def _public_404_html(host: str, port: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" />
<title>Server not found — RustMetrics</title>
<link rel="stylesheet" href="/style.css" />
</head><body>
<header class="topbar">
  <div class="brand">
    <a href="/" style="display:flex; align-items:center; gap:12px; text-decoration:none; color:inherit;">
      <span class="brand-mark"></span>
      <span class="brand-name">RUST<span class="brand-name-accent">METRICS</span></span>
    </a>
  </div>
</header>
<article class="legal-page">
  <a href="/" class="legal-back">← Home</a>
  <h1>Server not found</h1>
  <p>BattleMetrics doesn't know <code>{_esc(host)}:{port}</code>, and we don't have it
     in our database either. Either the IP/port is wrong, or the server is offline and
     not tracked anywhere.</p>
  <p>Try the <a href="/browser">Server Browser</a> to find live servers.</p>
</article>
</body></html>"""


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP-HANDLER
# ═══════════════════════════════════════════════════════════════════════════

class Handler(SimpleHTTPRequestHandler):
    server_version = "RustMetrics/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        try:
            if args and "/api/" in str(args[0]): return
        except Exception: pass
        super().log_message(fmt, *args)

    # ── Helpers ────────────────────────────────────────────────────────
    def _client_ip(self) -> str:
        return (self.headers.get("X-Real-IP")
                or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or self.client_address[0])

    def _origin_ok(self) -> bool:
        """Defense-in-Depth gegen CSRF: Wenn Origin gesetzt (Browser), muss er ORIGIN matchen.
           Wenn nicht gesetzt (curl, Mobile App), erlauben — Session-Auth schützt dann allein."""
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return True
        return origin == ORIGIN.rstrip("/")

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    MAX_BODY_BYTES = 64 * 1024

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return {}
        if length <= 0: return {}
        if length > self.MAX_BODY_BYTES:
            # Ohne Cap kann ein Client mit Content-Length: 2000000000 den
            # Prozess in eine 2-GB-Allokation treiben.
            return {}
        try: return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception: return {}

    def _fail(self, e: Exception, ctx: str = "") -> None:
        """
        500 ohne interne Details. Die Original-Exception geht mit einer
        Referenz-ID ins Log — vorher landeten psycopg2-Meldungen inkl.
        Tabellen-/Spaltennamen direkt beim Client.
        """
        ref = secrets.token_hex(6)
        print(f"[err {ref}] {ctx}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return self._json({"error": "internal error", "ref": ref}, 500)

    def _read_session(self) -> dict | None:
        cookie_hdr = self.headers.get("Cookie", "")
        if not cookie_hdr: return None
        c = SimpleCookie()
        try: c.load(cookie_hdr)
        except Exception: return None
        if COOKIE_NAME not in c: return None
        return lookup_session(c[COOKIE_NAME].value)

    def _send_html(self, path: str, status: int = 200):
        try:
            with open(path, "rb") as f: body = f.read()
        except FileNotFoundError:
            return self._json({"error": "not found"}, 404)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_browser_page(self):
        try:
            with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
                html = render_browser_page(f.read(), _cached_browse_servers(), ORIGIN)
        except Exception as e:
            return self._fail(e, "browser_page")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=60")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, status: int = 302,
                  set_cookie: str | None = None,
                  clear_cookie: bool = False):
        self.send_response(status)
        self.send_header("Location", location)
        if set_cookie:
            cookie = SimpleCookie()
            cookie[COOKIE_NAME] = set_cookie
            cookie[COOKIE_NAME]["path"] = "/"
            cookie[COOKIE_NAME]["httponly"] = True
            cookie[COOKIE_NAME]["secure"] = True
            cookie[COOKIE_NAME]["samesite"] = "Lax"
            cookie[COOKIE_NAME]["max-age"] = str(SESSION_TTL)
            self.send_header("Set-Cookie", cookie.output(header="").strip())
        if clear_cookie:
            self.send_header("Set-Cookie",
                f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
        self.end_headers()

    # ── Routing ───────────────────────────────────────────────────────
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        # www → kanonischer Host: sonst scheitern POSTs am Origin-Check und Linksignale splitten sich
        location = canonical_redirect_location(self.headers.get("Host"), self.path, ORIGIN)
        if location:
            return self._redirect(location, status=301)
        # Rate-Limit (generell, sehr grosszügig)
        if not rate_limit_general(self._client_ip()):
            return self._json({"error": "rate limited"}, 429)

        # Auth-Endpoints
        if u.path == "/auth/steam/login":
            return self._redirect(steam_login_url())
        if u.path == "/auth/steam/callback":
            return self._auth_steam_callback(u.query)
        if u.path == "/auth/logout":
            sess = self._read_session()
            cookie_hdr = self.headers.get("Cookie", "")
            if cookie_hdr:
                c = SimpleCookie()
                try:
                    c.load(cookie_hdr)
                    if COOKIE_NAME in c:
                        delete_session(c[COOKIE_NAME].value)
                except Exception: pass
            return self._redirect("/", clear_cookie=True)

        # API
        if u.path == "/api/ping": return self._json({"ok": True, "ts": int(time.time())})
        if u.path == "/api/me":   return self._api_me()
        if u.path == "/api/skins/items":
            try:
                return self._json(api_skins_items())
            except Exception as e:
                return self._fail(e, "skins")
        if u.path == "/api/skins/marketplaces":
            return self._json({"marketplaces": MARKETPLACES})
        if u.path == "/api/servers":
            sess = self._read_session()
            if not sess: return self._json({"error": "auth required"}, 401)
            return self._json(api_servers_list(sess["user_id"]))
        if u.path == "/api/me/friends":
            sess = self._read_session()
            if not sess: return self._json({"error": "auth required"}, 401)
            try:
                return self._json(api_friends_in_rust(sess["user_id"]))
            except Exception as e:
                return self._fail(e, "friends")
        if u.path.startswith("/api/me/players/"):
            return self._api_player_rhythm(u.path)
        if u.path.startswith("/api/server/"):
            parts = u.path.strip("/").split("/")
            if len(parts) == 3:
                sess = self._read_session()
                if not sess: return self._json({"error": "auth required"}, 401)
                try: sid = int(parts[2])
                except ValueError: return self._json({"error": "bad id"}, 400)
                qs = urllib.parse.parse_qs(u.query)
                # Ungeprueft konnte ?hours=abc einen 500er ohne Antwort und
                # ?hours=99999999 einen Full-Table-Scan ueber alle Snapshots ausloesen.
                try:
                    hours = max(1, min(720, int(qs.get("hours", ["24"])[0])))
                except (ValueError, TypeError):
                    return self._json({"error": "bad hours"}, 400)
                d = api_server_detail(sess["user_id"], sid, hours)
                if d is None: return self._json({"error": "not found"}, 404)
                return self._json(d)
        # Game leaderboard (public, anonymous can see)
        if u.path == "/api/game/flap/leaderboard":
            try:
                board = get_game_leaderboard("flap", limit=10)
                # Optional: user's own best if authed
                sess = self._read_session()
                my_best = None
                if sess:
                    my_best = get_user_game_best(sess["user_id"], "flap")
                return self._json({
                    "game": "flap",
                    "top": board,
                    "my_best": my_best,
                })
            except Exception as e:
                return self._fail(e)
        if u.path == "/api/game/flap/me":
            sess = self._read_session()
            if not sess: return self._json({"error": "auth required"}, 401)
            return self._json({"best": get_user_game_best(sess["user_id"], "flap")})

        # Logged-in user's own Rust stats (cached, fetched from Steam API)
        if u.path == "/api/me/stats":
            sess = self._read_session()
            if not sess: return self._json({"error": "auth required"}, 401)
            try:
                stats = get_rust_user_stats(sess["user_id"], force_refresh=False)
                return self._json(stats or {"error": "stats unavailable"})
            except Exception as e:
                return self._fail(e)

        # Stats für einen einzelnen Player (Steam-ID wird per BM lazy resolved)
        # /api/player/<bm_id>/stats
        if u.path.startswith("/api/player/") and u.path.endswith("/stats"):
            spec = u.path[len("/api/player/"):-len("/stats")]
            try: bm_id = int(spec)
            except ValueError: return self._json({"error": "bad bm_id"}, 400)
            sid = ensure_player_steam_id(bm_id)
            if not sid:
                return self._json({
                    "available": False,
                    "reason": "no Steam ID known for this player (BattleMetrics didn't expose it)"
                })
            try:
                stats = get_rust_user_stats(sid, force_refresh=False)
                if not stats:
                    return self._json({"available": False, "reason": "fetch failed"})
                stats["available"] = True
                return self._json(stats)
            except Exception as e:
                return self._fail(e)

        if u.path == "/api/browse":
            sess = self._read_session()
            uid = sess["user_id"] if sess else None
            qs = urllib.parse.parse_qs(u.query)
            def g(k, d=""):
                v = qs.get(k, [d]); return v[0] if v else d
            try:
                data = api_browse(
                    user_id=uid,
                    region=g("region", "all"), q=g("q", ""), tier=g("tier", "all"),
                    min_pop=int(g("min_pop", "0") or 0),
                    max_pop=int(g("max_pop", "9999") or 9999),
                    sort=g("sort", "pop_desc"),
                    limit=min(BROWSE_RESULT_CAP, int(g("limit", "200") or 200)),
                    offset=max(0, min(20000, int(g("offset", "0") or 0))),
                    group=g("group", "all"),
                    max_wipe_h=max(0, min(720, int(g("max_wipe_h", "0") or 0))),
                    ip=self._client_ip(),
                )
                return self._json(data)
            except PermissionError as e:
                return self._json({"error": str(e)}, 429)
            except Exception as e:
                return self._fail(e)

        # Legal pages (English now; old DE paths kept as alias)
        if u.path in ("/imprint", "/impressum"):
            return self._send_html(os.path.join(TEMPLATES_DIR, "imprint.html"))
        if u.path in ("/privacy", "/datenschutz"):
            return self._send_html(os.path.join(TEMPLATES_DIR, "privacy.html"))

        # SEO: robots.txt + sitemap
        if u.path == "/robots.txt":
            body = (
                "User-agent: *\n"
                "Allow: /\n"
                "Disallow: /api/\n"
                "Disallow: /auth/\n"
                "\n"
                f"Sitemap: {ORIGIN}/sitemap.xml\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path == "/sitemap.xml":
            # Dynamisch: Statische Seiten + Top-100 öffentliche Server aus Browse-Cache
            urls = [
                (f"{ORIGIN}/",         "daily",   "1.0"),
                (f"{ORIGIN}/browser",  "hourly",  "0.9"),
                (f"{ORIGIN}/imprint",  "yearly",  "0.3"),
                (f"{ORIGIN}/privacy",  "yearly",  "0.3"),
            ]
            # Top-Server aus dem Browse-Cache
            for s in _cached_browse_servers()[:100]:
                host = s.get("host"); port = s.get("port")
                if host and port:
                    urls.append((f"{ORIGIN}/server/{host}:{port}", "hourly", "0.6"))
            # Top-Players nach letzter Aktivität (max 200) — bringt Long-Tail Traffic
            try:
                with _Conn() as (_c, _cur):
                    _cur.execute(
                        "SELECT bm_id FROM players "
                        "WHERE last_seen > %s ORDER BY last_seen DESC LIMIT 200",
                        (int(time.time()) - 7 * 86400,))
                    for r in _cur.fetchall():
                        urls.append((f"{ORIGIN}/player/{int(r['bm_id'])}", "daily", "0.4"))
            except Exception as e:
                print(f"[sitemap players] {e}", file=sys.stderr, flush=True)
            xml = ['<?xml version="1.0" encoding="UTF-8"?>',
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
            for url, freq, prio in urls:
                xml.append(f"  <url><loc>{_esc(url)}</loc>"
                          f"<changefreq>{freq}</changefreq>"
                          f"<priority>{prio}</priority></url>")
            xml.append("</urlset>")
            body = "\n".join(xml).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path in ("/browser", "/browser/"):
            return self._send_browser_page()

        # Public server detail page  /server/<host>:<port>
        if u.path.startswith("/server/"):
            spec = u.path[len("/server/"):].rstrip("/")
            if ":" in spec:
                host, port_str = spec.rsplit(":", 1)
                try: port = int(port_str)
                except ValueError: port = 0
                if host and 0 < port < 65536:
                    try:
                        html, status = render_public_server_page(host, port)
                    except Exception as e:
                        print(f"[public_server] {host}:{port}: {e}", file=sys.stderr, flush=True)
                        return self._fail(e)
                    body = html.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=60")
                    self.end_headers()
                    self.wfile.write(body)
                    return
            return self._json({"error": "use /server/<host>:<port>"}, 400)

        # Calendar-ICS-Feed  /calendar/wipes.ics?token=<calendar_token>
        # Auth via Token (Cookie geht nicht — Calendar-Apps senden keine)
        if u.path == "/calendar/wipes.ics":
            qs = urllib.parse.parse_qs(u.query)
            token = (qs.get("token", [""])[0] or "").strip()
            user_id = lookup_user_by_calendar_token(token)
            if not user_id:
                return self._json({"error": "invalid token"}, 401)
            try:
                ics = render_wipes_ics(user_id)
            except Exception as e:
                print(f"[ics_feed] user={user_id}: {e}", file=sys.stderr, flush=True)
                return self._fail(e)
            body = ics.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Disposition",
                "inline; filename=\"rustmetrics-wipes.ics\"")
            self.send_header("Content-Length", str(len(body)))
            # Cache hour-level. Most calendar clients re-fetch hourly anyway.
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return

        # Admin-Stats-Page  /admin/stats — auth + is_admin required
        if u.path == "/admin/stats":
            sess = self._read_session()
            if not sess:
                return self._redirect("/auth/steam/login")
            if not sess.get("is_admin"):
                return self._json({"error": "admin only"}, 403)
            try:
                html, status = render_admin_stats_page(sess)
            except Exception as e:
                print(f"[admin_stats] {e}", file=sys.stderr, flush=True)
                return self._fail(e)
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, private")
            self.end_headers()
            self.wfile.write(body)
            return

        # Public player profile page  /player/<bm_id>
        if u.path.startswith("/player/"):
            spec = u.path[len("/player/"):].rstrip("/")
            try: bm_id = int(spec)
            except ValueError: bm_id = 0
            if bm_id <= 0:
                return self._json({"error": "use /player/<bm_player_id>"}, 400)
            try:
                html, status = render_player_profile_page(bm_id)
            except Exception as e:
                print(f"[player_profile] bm_id={bm_id}: {e}", file=sys.stderr, flush=True)
                return self._fail(e)
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=60")
            self.end_headers()
            self.wfile.write(body)
            return

        # Root ist immer die Landingpage; die App lebt unter /app
        if u.path == "/":
            return self._send_html(os.path.join(STATIC_DIR, "landing.html"))
        if u.path in ("/app", "/app/"):
            return self._send_html(os.path.join(STATIC_DIR, "index.html"))

        # Default: static files
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not rate_limit_general(self._client_ip()):
            return self._json({"error": "rate limited"}, 429)
        if not self._origin_ok():
            return self._json({"error": "bad origin"}, 403)
        parts = u.path.strip("/").split("/")
        sess = self._read_session()
        if not sess: return self._json({"error": "auth required"}, 401)

        if u.path == "/api/servers":
            body = self._read_json()
            host = (body.get("host") or "").strip()
            try: port = int(body.get("port") or 0)
            except (TypeError, ValueError): port = 0
            if not host or not (0 < port < 65536):
                return self._json({"error": "host and port required"}, 400)
            try:
                sid = add_watch_server(sess["user_id"], host, port)
                return self._json({"ok": True, "id": sid})
            except ValueError as e:          # ungueltige/private Adresse
                return self._json({"error": str(e)}, 400)
            except PermissionError as e:     # Watchlist-Limit erreicht
                return self._json({"error": str(e)}, 403)
            except Exception as e:
                return self._fail(e)

        if (len(parts) == 4 and parts[0] == "api" and parts[1] == "server"
                and parts[3] == "watch"):
            try: sid = int(parts[2])
            except ValueError: return self._json({"error": "bad id"}, 400)
            body = self._read_json()
            name = (body.get("name") or "").strip()
            if not name: return self._json({"error": "name required"}, 400)
            try:
                # SteamID64 / Profil-URL erkannt? → Presence-Tracking statt
                # Namens-Match (funktioniert auch auf Streamer-Mode-Servern).
                steam_id = parse_steam_player_input(name)
                if steam_id:
                    summary = fetch_player_summaries([steam_id]).get(steam_id) or {}
                    display = summary.get("personaname") or str(steam_id)
                    add_watch_player(sess["user_id"], sid, display, steam_id=steam_id)
                    return self._json({"ok": True, "steam_id": str(steam_id),
                                       "persona": display})
                add_watch_player(sess["user_id"], sid, name)
                return self._json({"ok": True})
            except PermissionError as e:
                return self._json({"error": str(e)}, 403)
            except Exception as e:
                return self._fail(e)

        if u.path == "/api/me/settings":
            return self._api_save_settings(sess["user_id"])
        # Game score submission (auth required, rate-limited, plausibility-checked)
        if u.path == "/api/game/flap/score":
            body = self._read_json()
            try:
                score = int(body.get("score") or 0)
            except (TypeError, ValueError):
                return self._json({"error": "bad score"}, 400)
            duration = body.get("duration_ms")
            if duration is not None:
                try: duration = int(duration)
                except (TypeError, ValueError): duration = None
            ok, err = submit_game_score(sess["user_id"], "flap", score, duration)
            if not ok:
                return self._json({"ok": False, "error": err}, 400)
            new_best = get_user_game_best(sess["user_id"], "flap")
            return self._json({"ok": True, "best": new_best})

        if u.path == "/api/me/stats/refresh":
            try:
                stats = get_rust_user_stats(sess["user_id"], force_refresh=True)
                return self._json(stats or {"error": "stats unavailable"})
            except Exception as e:
                return self._fail(e)
        if u.path.startswith("/api/player/") and u.path.endswith("/stats/refresh"):
            spec = u.path[len("/api/player/"):-len("/stats/refresh")]
            try: bm_id = int(spec)
            except ValueError: return self._json({"error": "bad bm_id"}, 400)
            sid = ensure_player_steam_id(bm_id)
            if not sid:
                return self._json({"available": False, "reason": "no Steam ID known"})
            try:
                stats = get_rust_user_stats(sid, force_refresh=True)
                if not stats:
                    return self._json({"available": False, "reason": "fetch failed"})
                stats["available"] = True
                return self._json(stats)
            except Exception as e:
                return self._fail(e)
        if u.path == "/api/me/calendar/reset":
            try:
                new_token = reset_calendar_token(sess["user_id"])
                cal_url    = f"{ORIGIN}/calendar/wipes.ics?token={new_token}"
                cal_webcal = cal_url.replace("https://", "webcal://").replace("http://", "webcal://")
                return self._json({"ok": True, "url": cal_url, "webcal_url": cal_webcal})
            except Exception as e:
                return self._fail(e)
        if u.path == "/api/me/test-webhook":
            return self._api_test_webhook(sess["user_id"])

        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        # PUT alias for /api/me/settings, falls Frontend REST-style PUT will
        if not rate_limit_general(self._client_ip()):
            return self._json({"error": "rate limited"}, 429)
        if not self._origin_ok():
            return self._json({"error": "bad origin"}, 403)
        sess = self._read_session()
        if not sess: return self._json({"error": "auth required"}, 401)
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/me/settings":
            return self._api_save_settings(sess["user_id"])
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        if not rate_limit_general(self._client_ip()):
            return self._json({"error": "rate limited"}, 429)
        if not self._origin_ok():
            return self._json({"error": "bad origin"}, 403)
        sess = self._read_session()
        if not sess: return self._json({"error": "auth required"}, 401)
        parts = u.path.strip("/").split("/")
        # /api/server/:id
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "server":
            try: sid = int(parts[2])
            except ValueError: return self._json({"error": "bad id"}, 400)
            remove_watch_server(sess["user_id"], sid)
            return self._json({"ok": True})
        # /api/server/:id/watch/:wid
        if (len(parts) == 5 and parts[0] == "api" and parts[1] == "server"
                and parts[3] == "watch"):
            try: wid = int(parts[4])
            except ValueError: return self._json({"error": "bad id"}, 400)
            remove_watch_player(sess["user_id"], wid)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    # ── Auth-Endpoint-Impl ────────────────────────────────────────────
    def _auth_steam_callback(self, query_string: str):
        qs = urllib.parse.parse_qs(query_string)
        # parse_qs gibt Listen, OpenID braucht einzelne Values
        params = {k: v[0] if isinstance(v, list) and v else "" for k, v in qs.items()}
        steamid64 = steam_verify(params)
        if not steamid64:
            return self._json({"error": "Steam login failed"}, 400)
        profile = fetch_steam_profile(steamid64)
        upsert_user(steamid64, profile)
        token = create_session(
            steamid64, ip=self._client_ip(),
            ua=self.headers.get("User-Agent", "")[:255]
        )
        return self._redirect("/app", set_cookie=token)

    def _api_me(self):
        sess = self._read_session()
        if not sess:
            return self._json({"authenticated": False})
        # Settings dazuladen
        settings = {"discord_webhook": None, "notify_online": True, "notify_offline": False}
        try:
            with _Conn() as (conn, cur):
                cur.execute(
                    "SELECT discord_webhook, notify_online, notify_offline FROM users WHERE id=%s",
                    (sess["user_id"],))
                r = cur.fetchone()
                if r:
                    settings = {
                        "discord_webhook": r["discord_webhook"],
                        "notify_online":   bool(r["notify_online"]),
                        "notify_offline":  bool(r["notify_offline"]),
                    }
        except Exception as e:
            # Settings fehlen dann in /api/me — tolerierbar, aber loggen,
            # sonst debuggt man ein "leeres Settings-Formular" blind.
            print(f"[api_me] Settings-Query fehlgeschlagen: "
                  f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        # Calendar-URL (lazy generiert beim ersten Mal)
        try:
            cal_token = get_or_create_calendar_token(sess["user_id"])
            cal_url   = f"{ORIGIN}/calendar/wipes.ics?token={cal_token}"
            # webcal:// für 1-tap subscribe in Apple/Google Calendar (kein https)
            cal_webcal = cal_url.replace("https://", "webcal://").replace("http://", "webcal://")
        except Exception:
            cal_url = cal_webcal = None
        return self._json({
            "authenticated": True,
            "user_id":       str(sess["user_id"]),
            "display_name":  sess.get("display_name") or str(sess["user_id"]),
            "avatar_url":    sess.get("avatar_url"),
            "is_admin":      bool(sess.get("is_admin")),
            "settings":      settings,
            "calendar": {
                "url":        cal_url,
                "webcal_url": cal_webcal,
            },
        })

    MAX_PG_INT = 2**31 - 1   # watched_players.id ist SERIAL

    def _api_player_rhythm(self, path: str):
        """GET /api/me/players/<watched_player_id>/rhythm — nur fuer den eigenen Watchlist-Eintrag."""
        parts = path.strip("/").split("/")
        if len(parts) != 5 or parts[4] != "rhythm":
            return self._json({"error": "not found"}, 404)
        sess = self._read_session()
        if not sess: return self._json({"error": "auth required"}, 401)
        try: wid = int(parts[3])
        except ValueError: return self._json({"error": "bad id"}, 400)
        if not 0 < wid <= self.MAX_PG_INT:
            return self._json({"error": "bad id"}, 400)
        try:
            data = api_player_rhythm(sess["user_id"], wid)
        except Exception as e:
            return self._fail(e, "player_rhythm")
        # Fremde und nicht existierende Eintraege gleich behandeln (keine ID-Enumeration)
        if data is None: return self._json({"error": "not found"}, 404)
        return self._json(data)

    def _api_save_settings(self, user_id: int):
        body = self._read_json()
        webhook = (body.get("discord_webhook") or "").strip() or None
        notify_online  = bool(body.get("notify_online",  True))
        notify_offline = bool(body.get("notify_offline", False))
        # Validate Discord webhook
        if webhook and not is_valid_discord_webhook(webhook):
            return self._json({"error": "invalid Discord webhook URL"}, 400)
        try:
            with _Conn() as (conn, cur):
                cur.execute(
                    "UPDATE users SET discord_webhook=%s, notify_online=%s, notify_offline=%s WHERE id=%s",
                    (webhook, notify_online, notify_offline, user_id))
            return self._json({"ok": True})
        except Exception as e:
            return self._fail(e)

    def _api_test_webhook(self, user_id: int):
        with _Conn() as (conn, cur):
            cur.execute("SELECT discord_webhook FROM users WHERE id=%s", (user_id,))
            r = cur.fetchone()
        wh = (r["discord_webhook"] if r else None)
        if not wh:
            return self._json({"error": "no webhook configured"}, 400)
        if not is_valid_discord_webhook(wh):
            return self._json({"error": "invalid webhook URL"}, 400)
        embed = {
            "username":   "RustMetrics",
            "avatar_url": "https://rustmetrics.eu/favicon.png",
            "embeds": [{
                "title": "✓ RustMetrics test webhook",
                "description": "If you can read this, your Discord webhook is wired up correctly.",
                "color":  0xCD412A,
                "footer": {"text": "rustmetrics.eu"},
            }],
        }
        ok, msg = _post_discord_webhook(wh, embed)
        return self._json({"ok": ok, "message": msg}, 200 if ok else 502)


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def _banner():
    print("")
    print("  ██████  ██    ██ ███████ ████████   ███    ███ ███████ ████████ ██████  ██  ██████ ███████")
    print("  ██   ██ ██    ██ ██         ██      ████  ████ ██         ██    ██   ██ ██ ██      ██     ")
    print("  ██████  ██    ██ ███████    ██      ██ ████ ██ █████      ██    ██████  ██ ██      ███████")
    print("  ██   ██ ██    ██      ██    ██      ██  ██  ██ ██         ██    ██   ██ ██ ██           ██")
    print("  ██   ██  ██████  ███████    ██      ██      ██ ███████    ██    ██   ██ ██  ██████ ███████")
    print("                                                                            PUBLIC EDITION   ")
    print("")
    print(f"  Listen:           {HOST}:{PORT}")
    print(f"  Origin:           {ORIGIN}")
    print(f"  PG:               {_env('RM_PGHOST', '127.0.0.1')}:{_env('RM_PGPORT', 5432, int)}/{_env('RM_PGDATABASE', 'rustmetrics')}")
    print(f"  Poll-Intervall:   {POLL_INTERVAL}s")
    print(f"  Browse-Cache:     {BROWSE_CACHE_TTL}s")
    print(f"  Browse-Limit:     {BROWSE_MASTER_LIMIT} Server max vom Master")
    print(f"  Steam-API-Key:    {'gesetzt' if STEAM_API_KEY else 'NICHT gesetzt (Avatar/Name fehlen)'}")
    print(f"  Admin-SteamIDs:   {ADMIN_STEAMIDS or '—'}")
    print(f"  Rate-Limit:       {RL_REQ_PER_MIN} req/min/IP, browse-miss {RL_BROWSE_PER_H}/h/IP")
    print("")


def main():
    init_pool()
    # quick DB-Check
    try:
        with _Conn() as (conn, cur):
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as e:
        print(f"FATAL: Postgres-Verbindung fehlgeschlagen: {e}", file=sys.stderr)
        sys.exit(2)

    ensure_migrations()

    t = threading.Thread(target=poller_loop, daemon=True)
    t.start()
    tp = threading.Thread(target=steam_presence_loop, daemon=True)
    tp.start()
    tw = threading.Thread(target=browse_warmer_loop, daemon=True)
    tw.start()
    th = threading.Thread(target=traffic_history_loop, daemon=True)
    th.start()
    tsk = threading.Thread(target=skins_price_loop, daemon=True)
    tsk.start()

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    _banner()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutdown — bitte einen Moment …")
        _poller_stop.set()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
