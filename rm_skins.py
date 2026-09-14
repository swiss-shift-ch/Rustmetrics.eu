"""RustMetrics — Skin-Preise.

Holt Rust-Skin-Preise von Drittanbieter-Marktplaetzen und cached sie in der
eigenen DB. Reine Fetch/Parse/Upsert-Logik, kein HTTP-Server-Code hier.

Wichtig (siehe Bau-Auftrag Abschnitt 4): Preise werden NIE live pro
Seitenaufruf abgefragt, sondern per Scheduled Job in die eigene DB
geschrieben. Das Frontend fragt ausschliesslich die eigene DB.

Slot-Zuordnung (hoodie/pants/boots/mask/hat/backpack/...) wird bewusst NICHT
aus der Skinport-Kategorie geraten: wir speichern nur die rohe Kategorie aus
der Skinport-URL (z.B. "clothing", "misc") und lassen `slot` NULL, bis sie
manuell kuratiert wird. Automatisches Raten würde falsche Slots im 3D-Viewer
erzeugen.
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import brotli
except ImportError:  # pragma: no cover - nur relevant wenn Dependency fehlt
    brotli = None

from rm_config import _env
from rm_skin_slots import slot_for_name

STEAM_APPID = 252490  # Rust auf Steam
SKINPORT_API = "https://api.skinport.com/v1/items"
SKINPORT_APP_ID = STEAM_APPID

STEAM_PRICEOVERVIEW = "https://steamcommunity.com/market/priceoverview/"
STEAM_CURRENCY_EUR = 3

# Direktanbieter-Liste aus dem Bau-Auftrag (Abschnitt 6 + 6b) — Basis-URLs
# fest vom Auftrag vorgegeben, nicht geraten. Per-Item-Deeplinks (statt nur
# Shop-Startseite) brauchen pro Plattform verifizierte Such-URL-Syntax; das
# ist noch offen (siehe Abnahme-Checkliste Punkt 4) und wird nachgezogen,
# sobald jede Syntax einzeln bestaetigt ist. Bis dahin zeigt das Frontend
# hier die Shop-Basis-URL, nicht raten wir uns eine Query-Param-Syntax
# zusammen, die falsch sein koennte.
MARKETPLACES: list[dict] = [
    {"id": "steam",       "name": "Steam Community Market", "url": "https://steamcommunity.com/market/search?appid=252490", "is_gambling": False},
    {"id": "skinport",    "name": "Skinport",                "url": "https://skinport.com/rust/market",                     "is_gambling": False},
    {"id": "dmarket",     "name": "DMarket",                 "url": "https://dmarket.com/ingame-items/item-list/rust-skins", "is_gambling": False},
    {"id": "waxpeer",     "name": "Waxpeer",                 "url": "https://waxpeer.com",                                  "is_gambling": False},
    {"id": "lis-skins",   "name": "Lis-Skins",                "url": "https://lis-skins.com/market/rust/",                   "is_gambling": False},
    {"id": "tradeit",     "name": "Tradeit.gg",               "url": "https://tradeit.gg/rust/store",                        "is_gambling": False},
    {"id": "skinsmonkey", "name": "SkinsMonkey",              "url": "https://skinsmonkey.com/trade-rust-skins",             "is_gambling": False},
    {"id": "rapidskins",  "name": "RapidSkins",               "url": "https://www.rapidskins.com/buy/",                      "is_gambling": False},
    {"id": "skinland",    "name": "Skin.Land",                "url": "https://skin.land/market/rust/",                       "is_gambling": False},
    {"id": "swapgg",      "name": "Swap.gg",                  "url": "https://swap.gg/rust/trade",                           "is_gambling": False},
    {"id": "skinswap",    "name": "SkinSwap",                 "url": "https://skinswap.com/buy-rust-skins",                  "is_gambling": False},
    {"id": "itrade",      "name": "iTrade.gg",                "url": "https://itrade.gg/trade/rust",                         "is_gambling": False},
    {"id": "rustskins",   "name": "RustSkins.com",            "url": "https://rustskins.com/",                               "is_gambling": False},
    {"id": "rustcasino",  "name": "RustCasino",               "url": "https://rustcasino.com/market",                        "is_gambling": True},
    {"id": "hellcase",    "name": "Hellcase",                 "url": "https://hellcase.com/rust",                            "is_gambling": True},
]

# Skinport erlaubt laut eigener Doku 8 Requests / 5 Minuten. Wir holen mit
# einem einzigen Call ALLE Rust-Items auf einmal (kein Loop noetig), also
# reicht ein grosszuegiger Mindestabstand zwischen zwei Runs.
SKINPORT_MIN_INTERVAL_SEC = _env("RM_SKINPORT_MIN_INTERVAL_SEC", 3600, int)

_UA = "Mozilla/5.0 (compatible; RustMetrics/1.0; +https://rustmetrics.eu)"


def _http_get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
        if enc == "br":
            if brotli is None:
                raise RuntimeError("brotli-Paket fehlt, siehe requirements.txt")
            return brotli.decompress(raw)
        if enc == "gzip":
            return gzip.decompress(raw)
        return raw


def fetch_skinport_items(currency: str = "EUR") -> list[dict]:
    """Holt ALLE Rust-Items von Skinport in einem einzigen Request.

    Verifiziert am 2026-09-14 live gegen app_id=252490: liefert echte
    market_hash_name/Preise (5380 Items zum Testzeitpunkt).
    """
    qs = urllib.parse.urlencode({"app_id": SKINPORT_APP_ID, "currency": currency})
    url = f"{SKINPORT_API}?{qs}"
    body = _http_get(url, {"Accept-Encoding": "br", "User-Agent": _UA})
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError(f"unerwartetes Skinport-Response-Format: {type(data)}")
    return data


def category_from_market_page(market_page: str | None) -> str | None:
    """Extrahiert das Kategorie-Segment aus einer Skinport-market_page-URL.

    z.B. "https://skinport.com/rust/market/clothing/roadsign-kilt?item=..."
    -> "clothing". Liefert None, wenn das Format nicht passt (lieber None
    als eine geratene Kategorie).
    """
    if not market_page:
        return None
    try:
        path = urllib.parse.urlparse(market_page).path
        parts = [p for p in path.split("/") if p]
        # erwartet: ["rust", "market", "<category>", "<slug>"]
        if len(parts) >= 3 and parts[0] == "rust" and parts[1] == "market":
            return parts[2]
    except Exception:
        pass
    return None


def skinport_item_to_row(item: dict) -> dict | None:
    """Wandelt einen Skinport-API-Eintrag in unser DB-Zeilenformat.

    Gibt None zurueck fuer Eintraege ohne market_hash_name (sollte laut
    Doku nicht vorkommen, aber externe API -> nicht vertrauen).
    """
    name = item.get("market_hash_name")
    if not name:
        return None
    price = item.get("min_price")
    return {
        "item_id": name,
        "name": name,
        "raw_category": category_from_market_page(item.get("market_page")),
        "platform": "skinport",
        "price": float(price) if price is not None else None,
        "currency": item.get("currency") or "EUR",
        "url": item.get("market_page") or item.get("item_page"),
        "quantity": item.get("quantity"),
    }


def refresh_skin_prices(conn_ctx) -> int:
    """Holt Skinport-Preise und schreibt sie in die eigene DB.

    `conn_ctx` ist die `_Conn`-Context-Manager-Klasse aus rm_db (als Parameter
    statt Import, damit dieses Modul unabhaengig testbar bleibt ohne DB).
    Gibt die Anzahl aktualisierter Items zurueck.
    """
    raw_items = fetch_skinport_items()
    rows = [r for r in (skinport_item_to_row(it) for it in raw_items) if r]
    now = int(time.time())
    updated = 0
    with conn_ctx() as (conn, cur):
        for row in rows:
            # skin_items: neue Items anlegen, bestehende NICHT ueberschreiben
            # bei slot/image_url/texture_ref (das ist kuratiert von Hand).
            slot = slot_for_name(row["name"])
            cur.execute(
                """
                INSERT INTO skin_items (item_id, name, raw_category, slot, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (item_id) DO UPDATE
                    SET raw_category = COALESCE(skin_items.raw_category, EXCLUDED.raw_category),
                        slot         = COALESCE(skin_items.slot, EXCLUDED.slot)
                """,
                (row["item_id"], row["name"], row["raw_category"], slot, now),
            )
            cur.execute(
                """
                INSERT INTO skin_prices (item_id, platform, price, currency, url, quantity, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (item_id, platform) DO UPDATE
                    SET price = EXCLUDED.price,
                        currency = EXCLUDED.currency,
                        url = EXCLUDED.url,
                        quantity = EXCLUDED.quantity,
                        updated_at = EXCLUDED.updated_at
                """,
                (row["item_id"], row["platform"], row["price"], row["currency"],
                 row["url"], row["quantity"], now),
            )
            updated += 1
    return updated


def steam_priceoverview(market_hash_name: str, currency: int = STEAM_CURRENCY_EUR) -> dict | None:
    """Einzelabfrage gegen Steams inoffiziellen priceoverview-Endpoint.

    Absichtlich NICHT in refresh_skin_prices() eingebaut: Community-Richtwert
    ist ca. 20 req/min, bei > 5000 Items wuerde ein Full-Sync Stunden dauern
    und Steam sperrt IP-weise. Aufrufer muss selbst rate-limitieren (siehe
    Bau-Auftrag Abschnitt 4: "Muss gecached werden").
    """
    qs = urllib.parse.urlencode({
        "appid": STEAM_APPID,
        "currency": currency,
        "market_hash_name": market_hash_name,
    })
    url = f"{STEAM_PRICEOVERVIEW}?{qs}"
    try:
        body = _http_get(url, {"User-Agent": _UA})
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError("Steam rate limit (429) — Abfrage stoppen") from e
        raise
    data = json.loads(body)
    if not data.get("success"):
        return None
    return {
        "lowest_price": data.get("lowest_price"),
        "median_price": data.get("median_price"),
        "volume": data.get("volume"),
    }
