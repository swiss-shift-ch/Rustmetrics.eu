"""Manueller Backfill fuer skin_items.image_url.

Kein Scheduled Job: laut init.sql wird image_url manuell kuratiert, nicht
automatisch befuellt (wie `slot` — siehe rm_skins.py Modul-Docstring). Von
Hand auf dem Server ausfuehren, wenn neue kuratierte Items dazugekommen
sind:

    python3 backfill_skin_icons.py [--limit N]

Holt fuer jedes Item mit gesetztem slot und fehlendem image_url das Icon
von Steams Market-Listing-Seite, spiegelt es nach static/skins/icons/ (Steams
CDN schickt kein Access-Control-Allow-Origin — ohne lokale Kopie scheitert
die Textur im 3D-Viewer an CORS, siehe rm_skins.download_skin_icon) und
schreibt den lokalen Pfad in die DB. Rate-limitiert wie steam_priceoverview
(3s zwischen Requests) und bricht bei einem 429 sofort ab; ein erneuter
Lauf macht dank `image_url IS NULL` nur beim Rest weiter.
"""
from __future__ import annotations

import argparse
import os

from rm_config import STATIC_DIR
from rm_db import _Conn, init_pool
from rm_skins import enrich_skin_image_urls

SKIN_ICONS_DIR = os.path.join(STATIC_DIR, "skins", "icons")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Nur die ersten N Items (Testlauf)")
    args = parser.parse_args()

    init_pool()
    result = enrich_skin_image_urls(_Conn, SKIN_ICONS_DIR, limit=args.limit)
    print(f"backfill done: {result}")
