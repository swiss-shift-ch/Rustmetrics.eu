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
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import brotli
except ImportError:  # pragma: no cover - nur relevant wenn Dependency fehlt
    brotli = None

from rm_config import _env
from rm_skin_slots import slot_for_item

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


def _http_get(url: str, headers: dict, max_bytes: int | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read(max_bytes + 1) if max_bytes else r.read()
        if max_bytes and len(raw) > max_bytes:
            raise RuntimeError(f"Antwort zu gross (> {max_bytes} Bytes): {url}")
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


def base_item_from_market_page(market_page: str | None) -> str | None:
    """Extrahiert das Basis-Item-Slug-Segment aus einer Skinport-market_page-URL.

    z.B. "https://skinport.com/rust/market/clothing/roadsign-kilt?item=..."
    -> "roadsign-kilt". Das ist Skinports EIGENE Klassifikation, welches
    Vanilla-Rust-Item ein Skin darstellt -- siehe rm_skin_slots.slot_for_slug()
    fuer die weitere Verwendung. Liefert None, wenn das Format nicht passt
    (lieber None als ein geratener Slug), analog zu category_from_market_page().
    """
    if not market_page:
        return None
    try:
        path = urllib.parse.urlparse(market_page).path
        parts = [p for p in path.split("/") if p]
        # erwartet: ["rust", "market", "<category>", "<slug>"]
        if len(parts) >= 4 and parts[0] == "rust" and parts[1] == "market":
            return parts[3]
    except Exception:
        pass
    return None


def _price_or_none(value) -> float | None:
    """Preis als float, oder None wenn der Wert unbrauchbar ist.

    Externe API -> nicht vertrauen: ein einzelner kaputter Wert ("n/a", Liste,
    NaN, negativ) soll nur dieses Item ohne Preis lassen, nicht per Exception
    den ganzen Refresh abbrechen.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price < 0:
        return None
    return price


def skinport_item_to_row(item: dict) -> dict | None:
    """Wandelt einen Skinport-API-Eintrag in unser DB-Zeilenformat.

    Gibt None zurueck fuer Eintraege ohne market_hash_name (sollte laut
    Doku nicht vorkommen, aber externe API -> nicht vertrauen).
    """
    name = item.get("market_hash_name")
    if not name:
        return None
    return {
        "item_id": name,
        "name": name,
        "raw_category": category_from_market_page(item.get("market_page")),
        "base_item": base_item_from_market_page(item.get("market_page")),
        "platform": "skinport",
        "price": _price_or_none(item.get("min_price")),
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
            slot = slot_for_item(row["name"], row["base_item"])
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


# ── Icon-URLs fuer den 3D-Viewer ─────────────────────────────────────────
#
# Weder Skinport noch Steams priceoverview-Endpoint liefern ein Icon-Feld.
# Die einzige oeffentliche Quelle ist die normale Steam-Market-Listing-Seite
# (".../market/listings/<appid>/<name>") — kein JSON-Endpoint, das Icon
# steckt im eingebetteten, mehrfach escapten Render-State der Seiten-HTML.
# Deshalb Regex statt eines HTML-Parsers: ein einzelnes Feld rechtfertigt
# keine neue Dependency, und das Format ist stabil genug fuer einen
# gezielten Regex (siehe Tests mit echtem Live-Fixture).
STEAM_MARKET_LISTING = "https://steamcommunity.com/market/listings/{appid}/{name}"
STEAM_ICON_CDN = "https://community.cloudflare.steamstatic.com/economy/image/{icon_hash}/128x128"
_ICON_HASH_RE = re.compile(r"icon_url\W+([A-Za-z0-9_\-]{20,160})")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Listing-Seite ist normal ~500KB, Icon-PNG ~24KB — beide Caps sind grosszuegig,
# schuetzen aber vor einem MITM/kompromittierten Host oder Redirect, der uns
# unbegrenzt viele Bytes ins RAM/auf die Platte schreiben laesst.
MAX_LISTING_HTML_BYTES = 2 * 1024 * 1024
MAX_ICON_BYTES = 1 * 1024 * 1024


def icon_hash_from_listing_html(html: str) -> str | None:
    """Extrahiert den Steam-CDN-Icon-Hash aus einer Market-Listing-Seite.

    `\\W+` direkt nach "icon_url" traegt bewusst dazu bei, "icon_url_large"
    NICHT zu treffen (das "_" davor ist ein Wortzeichen) — wir wollen die
    kleine Variante, nicht die grosse.
    """
    m = _ICON_HASH_RE.search(html)
    return m.group(1) if m else None


def steam_market_icon_url(market_hash_name: str) -> str | None:
    """Holt die Steam-CDN-Icon-URL fuer ein Item von dessen Market-Listing-Seite.

    Gleiches Rate-Limit-Risiko wie steam_priceoverview() (siehe dort) —
    Aufrufer muss selbst drosseln. Absichtlich NICHT in einen Scheduled Job
    eingebaut: image_url ist laut init.sql manuell kuratiert, nicht
    automatisch befuellt (wie `slot` — siehe Modul-Docstring).
    """
    url = STEAM_MARKET_LISTING.format(
        appid=STEAM_APPID, name=urllib.parse.quote(market_hash_name, safe="")
    )
    try:
        body = _http_get(url, {"User-Agent": _UA}, max_bytes=MAX_LISTING_HTML_BYTES)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError("Steam rate limit (429) — Abfrage stoppen") from e
        raise
    icon_hash = icon_hash_from_listing_html(body.decode("utf-8", errors="ignore"))
    if not icon_hash:
        return None
    return STEAM_ICON_CDN.format(icon_hash=icon_hash)


SKIN_ICON_URL_PREFIX = "/skins/icons"  # servable path under STATIC_DIR


def local_icon_filename(item_id: str) -> str:
    """Kollisionsfreier, dateisystemsicherer Name aus einem item_id/Skin-Namen.

    Skin-Namen enthalten Leerzeichen, Apostrophe und Unicode (z.B.
    "BCHILLZ! Hoodie") — kein sicherer Dateiname ohne Hashing.
    """
    return hashlib.sha1(item_id.encode("utf-8")).hexdigest() + ".png"


def download_skin_icon(cdn_url: str, dest_path: str) -> None:
    """Speichert Icon-Bytes von Steams CDN lokal auf der eigenen Domain.

    Noetig weil Steams CDN kein Access-Control-Allow-Origin schickt: ein
    direkt vom Browser geladenes Steam-Icon scheitert im 3D-Viewer an CORS
    (verifiziert 2026-09-14 gegen echten Chromium — die Textur laedt gar
    nicht erst). Einmal lokal gespiegelt ist es same-origin und funktioniert.

    Prueft die PNG-Magic-Bytes, bevor irgendwas auf die Platte geschrieben
    wird — die Response kommt von aussen (Steam-CDN), nichts garantiert,
    dass sie wirklich ein PNG ist, und die Datei wird spaeter unveraendert
    an Browser ausgeliefert.
    """
    try:
        body = _http_get(cdn_url, {"User-Agent": _UA}, max_bytes=MAX_ICON_BYTES)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError("Steam rate limit (429) — Abfrage stoppen") from e
        raise
    if not body.startswith(_PNG_MAGIC):
        raise ValueError(f"Antwort ist kein PNG (CDN-URL: {cdn_url})")
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(body)


def enrich_skin_image_urls(
    conn_ctx, icons_dir: str, sleep_sec: float = 3.0, limit: int | None = None
) -> dict:
    """Backfill fuer image_url — von Hand angestossen, kein Scheduled Job.

    Nur Items mit gesetztem `slot` (die einzigen, die der 3D-Viewer ueberhaupt
    zeigt) UND `image_url IS NULL` (idempotent: ein zweiter Lauf ueberschreibt
    nichts und macht nur da weiter, wo der vorige aufgehoert hat). Holt die
    Steam-CDN-URL, spiegelt die Bytes nach `icons_dir` (siehe
    download_skin_icon fuer das CORS-Problem) und schreibt den lokalen,
    same-origin servierbaren Pfad in image_url — nicht die rohe Steam-URL.
    Bricht bei einem 429 sofort ab statt zu retryen — der Rest bleibt fuer
    den naechsten Lauf offen.
    """
    with conn_ctx() as (conn, cur):
        cur.execute(
            "SELECT item_id FROM skin_items WHERE slot IS NOT NULL AND image_url IS NULL ORDER BY item_id"
        )
        item_ids = [r["item_id"] for r in cur.fetchall()]
    if limit is not None:
        item_ids = item_ids[:limit]

    updated, failed = 0, 0
    for item_id in item_ids:
        try:
            cdn_url = steam_market_icon_url(item_id)
        except RuntimeError:
            break  # 429 — sofort abbrechen, Rest bleibt fuer den naechsten Lauf offen
        except Exception as e:
            # Alles andere (404 fuer ein delistetes Item, Timeout, ...) darf
            # NICHT den ganzen Lauf crashen: sonst blockiert eine einzelne
            # kaputte Zeile wegen "ORDER BY item_id" + image_url IS NULL
            # jeden weiteren Lauf an genau derselben Stelle.
            print(f"[skin-icons] {item_id!r} -> ERROR {type(e).__name__}: {e}", flush=True)
            failed += 1
            time.sleep(sleep_sec)
            continue
        if not cdn_url:
            failed += 1
            time.sleep(sleep_sec)
            continue

        filename = local_icon_filename(item_id)
        try:
            download_skin_icon(cdn_url, os.path.join(icons_dir, filename))
        except RuntimeError:
            break  # 429 vom CDN — genauso abbrechen wie bei der Listing-Seite
        except Exception as e:
            print(f"[skin-icons] {item_id!r} -> ERROR {type(e).__name__}: {e}", flush=True)
            failed += 1
            time.sleep(sleep_sec)
            continue

        local_url = f"{SKIN_ICON_URL_PREFIX}/{filename}"
        with conn_ctx() as (conn, cur):
            cur.execute(
                "UPDATE skin_items SET image_url = %s WHERE item_id = %s",
                (local_url, item_id),
            )
        updated += 1
        time.sleep(sleep_sec)
    return {"updated": updated, "failed": failed, "remaining": len(item_ids) - updated - failed}
