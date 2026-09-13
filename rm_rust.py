"""RustMetrics — Rust-Domaenenlogik.

Pure Funktionen: Keyword-Parsing, Tier/Kategorie/Region/Gruppenlimit-
Erkennung, Streamer-Mode-Heuristik, Wipe-Schaetzung. Kein I/O.
"""
from __future__ import annotations

import re
import time

def parse_rust_keywords(kw: str) -> dict:
    out: dict = {"tags": []}
    if not kw: return out
    for part in [p.strip() for p in kw.split(",") if p.strip()]:
        if   part.startswith("mp"):
            try: out["max_players_kw"] = int(part[2:])
            except ValueError: pass
        elif part.startswith("cp"):
            try: out["current_players_kw"] = int(part[2:])
            except ValueError: pass
        elif part.startswith("qp"):
            try: out["queued_kw"] = int(part[2:])
            except ValueError: pass
        elif part.startswith("v") and part[1:].isdigit():
            out["version_kw"] = int(part[1:])
        elif part.startswith("born"):
            try: out["born_ts"] = int(part[4:])
            except ValueError: pass
        elif part.startswith("gm"): out["game_mode"] = part[2:]
        elif part.startswith("h") and part[1:].isdigit(): out["hash"] = part[1:]
        elif part == "stok": out["server_tokenized"] = True
        else: out["tags"].append(part)
    return out


def detect_rust_tier(name, tags) -> str:
    n = (name or "").lower() + " " + " ".join(tags).lower()
    if "pve" in n: return "pve"
    for mult in ("100x","50x","20x","10x","5x","3x","2x"):
        if mult in n: return mult
    if "modded" in n or "oxide" in tags: return "modded"
    if "vanilla" in n: return "vanilla"
    return "vanilla"


# In-Game-Kategorien wie im Rust-Server-Browser: Vanilla / Community / Modded.
#
# Datenlage: Steams GetServerList liefert pro Server nur den 'modded'-Flag
# zuverlässig (im gametype-String). Ein sauberes official-vs-community wie im
# Spiel-Client gibt es dort NICHT — nur BattleMetrics hat rust_type. Für die
# globale Liste bilden wir daher so ab:
#   • modded    → hat 'modded'/'oxide'-Tag oder 'modded' im Namen
#                 (= exakt der In-Game-Modded-Flag)
#   • vanilla   → NICHT modded, KEIN Rate-Multiplikator (1x) und explizit als
#                 vanilla/official erkennbar (Facepunch-Official-Server)
#   • community → alles andere Nicht-Modded (der Normalfall: die meisten
#                 Spieler-Server, inkl. 2x/3x/monthly ohne Modded-Flag)
# Wenn die Quelle BattleMetrics ist, überschreibt deren rust_type-Tag
# ('official'/'community'/'modded') die Heuristik direkt.
_OFFICIAL_RE = re.compile(r"\b(official|facepunch)\b", re.I)
_RATE_MULTS   = ("100x", "50x", "20x", "10x", "5x", "4x", "3x", "2x", "1.5x")
_HIGH_RATES   = ("100x", "50x", "20x", "10x", "5x")   # ab 5x zählt als modded
# Mod-Stichwörter: gesetzte Plugins/Gameplay-Änderungen → modded, auch ohne Flag
_MOD_HINTS = ("kit", "shop", "/home", "teleport", " tp ", "skinbox", "bgrade",
              "raidable", "loot+", "lootx", "instacraft", "instant craft",
              "no bp", "nobp", "no blueprint", "battlefield", "bedwars",
              "aimtrain", "arena", "ffa", "gungame", "creative", "juggernaut",
              "zombie", "npc", "bots ")

def detect_rust_category(name, tags) -> str:
    tl = [str(t).lower() for t in (tags or [])]
    n = (name or "").lower()
    blob = n + " " + " ".join(tl)
    # 0) BattleMetrics rust_type (falls vorhanden) ist maßgeblich
    if "modded" in tl:      return "modded"
    if "community" in tl:   return "community"
    if "official" in tl:    return "vanilla"
    # 1) Modded: Flag / Oxide / im Namen / hohe Rate (≥5x) / typische Mod-Features
    if ("modded" in blob or "oxide" in tl
            or any(r in blob for r in _HIGH_RATES)
            or any(h in blob for h in _MOD_HINTS)):
        return "modded"
    # 2) Vanilla/Official: kein Rate-Multiplikator und explizit vanilla/official
    has_mult = any(m in blob for m in _RATE_MULTS)
    if not has_mult and ("vanilla" in blob or _OFFICIAL_RE.search(blob)):
        return "vanilla"
    # 3) Rest (inkl. 2x/3x-Community-Server ohne Flag) → Community
    return "community"


# ── Region-Erkennung ──────────────────────────────────────────────────────
# Rust hat OFFIZIELLE Region-Tags (via `server.tags`): NA, SA, EU, WA, EA, OC, AF.
# Sie kommen im Steam-gametype-String und landen via parse_rust_keywords in tags.
# Das ist die zuverlaessige Quelle. Namen-Tokens nur als Fallback fuer Server,
# die keinen Region-Tag gesetzt haben. Quelle: wiki.facepunch.com/rust/server-browser-tags
_RUST_REGION_TAGS = {"na", "sa", "eu", "wa", "ea", "oc", "af"}

# Namen-Fallback. Kurze Codes werden nur GROSS im Original-Namen gematcht
# (Region-Kuerzel sind praktisch immer uppercase: "US", "EU", "SEA" …), sonst
# gaebe es Falschtreffer wie 'us' im Wort "join us" oder 'me' in "for me".
_REGION_NAME_UPPER = {
    "eu": ("EU",),
    "na": ("NA", "US", "USA", "USE", "USW", "USEAST", "USWEST"),
    "sa": ("SA", "BR"),
    "wa": ("WA", "ME"),
    "ea": ("EA", "AS", "SEA", "SG", "JP", "HK", "KR"),
    "oc": ("OC", "OCE", "AU", "AUS", "NZ"),
    "af": ("AF", "ZA"),
}
# Lange Woerter: case-insensitive, unkritisch.
_REGION_NAME_WORDS = {
    "eu": ("europe", "european"),
    "na": ("america", "canada"),
    "sa": ("south america", "brazil", "brasil"),
    "wa": ("middle east", "mideast"),
    "ea": ("asia", "singapore", "japan", "korea"),
    "oc": ("oceania", "australia"),
    "af": ("africa",),
}
# Feste Prioritaet, falls ein Name mehrere Regionen enthaelt (selten).
_REGION_ORDER = ("eu", "na", "sa", "wa", "ea", "oc", "af")


def _name_has_upper_token(name: str, token: str) -> bool:
    """Eigenstaendiges GROSS-Token im Original-Namen (von Nicht-Buchstaben umgeben)."""
    return re.search(r"(?<![A-Za-z])" + re.escape(token) + r"(?![A-Za-z])", name) is not None


def detect_rust_region(name, tags) -> str | None:
    # 1) Offizieller Region-Tag hat Vorrang.
    for t in (tags or []):
        tl = str(t).strip().lower()
        if tl in _RUST_REGION_TAGS:
            return tl
    # 2) Fallback: langes Wort (case-insensitive).
    n_lc = (name or "").lower()
    for region in _REGION_ORDER:
        if any(w in n_lc for w in _REGION_NAME_WORDS[region]):
            return region
    # 3) Fallback: kurzes GROSS-Token im Original.
    n = name or ""
    for region in _REGION_ORDER:
        if any(_name_has_upper_token(n, tok) for tok in _REGION_NAME_UPPER[region]):
            return region
    return None


# Alte Dropdown-Werte / Steam-Region-Namen → offizielle Rust-Region-Codes.
# Rust kennt kein US-East/West und kein separates "Middle East": NA ist NA,
# Middle East faellt unter WA (West Asia), Ozeanien ist OC.
_REGION_FILTER_ALIASES = {
    "us-e": "na", "us-w": "na", "us": "na", "usa": "na", "nae": "na", "naw": "na",
    "as": "ea", "sea": "ea",
    "me": "wa", "middle-east": "wa",
    "au": "oc", "aus": "oc", "oce": "oc", "oceania": "oc",
    "europe": "eu",
}
# Sammel-Regionen: ein Dropdown-Wert, der mehrere offizielle Codes abdeckt.
# Rust trennt Asien in WA (West) + EA (East); Nutzer denken aber "Asia".
_REGION_GROUPS = {
    "asia": frozenset({"wa", "ea"}),
}


# ── Gruppenlimit-Erkennung ────────────────────────────────────────────────
# r/playrust-Realitaet: Leute suchen Server nach Max-Group (solo/duo/trio/quad).
# Weder Steam noch A2S liefern das Feld — es steht (wenn ueberhaupt) im Namen
# oder in den Custom-Tags. Heuristik, konservativ: lieber None (unbekannt) als
# falsch. Rueckgabe: 1..12 = Max-Gruppengroesse, 0 = explizit kein Limit,
# None = unbekannt.
_GROUP_WORD_VALUES = {"solo": 1, "duo": 2, "trio": 3, "quad": 4, "quint": 5}
_GROUP_WORD_RE    = re.compile(r"\b(solo|duo|trio|quad|quint)s?\b", re.I)
_GROUP_NOLIMIT_RE = re.compile(
    r"\bno\s*(?:group|team|clan)\s*(?:limit|size|max)?\b"
    r"|\bunlimited\s*(?:group|team|clan)s?\b", re.I)
_GROUP_MAX_RE = re.compile(
    r"\b(?:max|group|team|clan)[\s._\-]*(?:group|team|clan|size|limit)?"
    r"[\s:._\-]*([1-9]|1[0-2])\b", re.I)
_GROUP_NMAN_RE = re.compile(r"\b([2-9])[\s\-]*man\b", re.I)


def detect_rust_group_limit(name, tags) -> int | None:
    blob = (name or "") + " " + " ".join(str(t) for t in (tags or []))
    # 1) Explizit "no group limit" u.ae. -> 0 (kein Limit)
    if _GROUP_NOLIMIT_RE.search(blob):
        return 0
    # 2) Explizites "max 4" / "group limit 3" / "team 2" schlaegt alles
    m = _GROUP_MAX_RE.search(blob)
    if m:
        return int(m.group(1))
    # 3) Wort-Kette: "solo/duo/trio" -> das GROESSTE gefundene Wort zaehlt.
    # Achtung Unicode: "TRİO" (tuerkisches İ) lowercased zu "tri̇o" (i + combining
    # dot) — daher erst NFKD-normalisieren und Combining-Marks strippen, sonst
    # KeyError auf echten Servernamen.
    words = _GROUP_WORD_RE.findall(blob)
    if words:
        import unicodedata
        vals = []
        for w in words:
            key = "".join(c for c in unicodedata.normalize("NFKD", w.lower())
                          if not unicodedata.combining(c))
            v = _GROUP_WORD_VALUES.get(key)
            if v: vals.append(v)
        if vals:
            return max(vals)
    # 4) "5-man" / "3 man"
    m = _GROUP_NMAN_RE.search(blob)
    if m:
        return int(m.group(1))
    return None


def pick_bm_server_id(results: list[dict], host: str, port: int) -> int | None:
    """BattleMetrics server id for host:port; a different-port match on the same IP counts only if it is the only one."""
    same_ip = [s for s in results if (s.get("attributes") or {}).get("ip") == host]
    by_query_port = [s for s in same_ip if s["attributes"].get("portQuery") == port]
    by_game_port = [s for s in same_ip if s["attributes"].get("port") == port]
    candidates = by_query_port or by_game_port or (same_ip if len(same_ip) == 1 else [])
    if not candidates:
        return None
    try:
        return int(candidates[0].get("id"))
    except (TypeError, ValueError):
        return None


ANON_DETECTION_MIN_NAMES = 15


def is_pool_style_name(name: str) -> bool:
    """Looks like an entry from Facepunch's randomized pool: short, lowercase, letters only."""
    return 3 <= len(name) <= 12 and name.islower() and name.isalpha()


def detect_name_anonymization(player_names: list[str]) -> bool:
    """
    Erkennt Facepunch-style Name-Randomisierung. Heuristik fingerprintet:
      - Mehrere Duplikate (im echten Rust ungewöhnlich)
      - PLUS: hoher Anteil pure-lowercase-alpha-kurze-Namen (Facepunch zieht aus einem
        Pool von ~500 generischen kleinbuchstabigen Vornamen wie 'carmelo','gena')
    Beide Bedingungen zusammen sind nötig — Reddit/Stevious-style Server mit echten
    Mixed-Case-Namen + 1-2 Zufalls-Duplikaten dürfen nicht fälschlich anschlagen.
    """
    real_names = [n for n in player_names if (n or "").strip()]
    if len(real_names) < ANON_DETECTION_MIN_NAMES:
        return False
    seen = set()
    dupes = 0
    lower_alpha = 0
    for n in real_names:
        key = n.strip().lower()
        if key in seen: dupes += 1
        else: seen.add(key)
        # "Pure lowercase short alpha" = look like Facepunch's randomized pool entries
        if is_pool_style_name(n):
            lower_alpha += 1
    lower_ratio = lower_alpha / len(real_names)
    # Strenger Filter: ≥3 Duplikate UND ≥70% pure-lowercase-alpha-short
    return dupes >= 3 and lower_ratio >= 0.70


def next_wipe_estimate(born_ts):
    if not born_ts: return None
    now = int(time.time())
    week = 7 * 86400
    if born_ts > now:
        return {"last_wipe_ts": born_ts, "next_wipe_estimate_ts": born_ts, "days_until_next": 0}
    weeks_since = (now - born_ts) // week
    next_wipe = born_ts + (weeks_since + 1) * week
    return {
        "last_wipe_ts": born_ts,
        "next_wipe_estimate_ts": next_wipe,
        "days_until_next": max(0, (next_wipe - now) // 86400),
    }
