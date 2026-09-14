"""Einmaliger Neuaufbau von skin_items.slot aus dem aktuellen Skinport-Katalog.

refresh_skin_prices() ueberschreibt einen bereits gesetzten slot absichtlich
NICHT (COALESCE, siehe rm_skins.py) — nach einer Aenderung der Slot-Zuordnung
(neue Slots `chest`/`kilt`, Handschuhe ohne Slot) bleiben alte Werte in der
DB stehen. Dieses Script rechnet slot fuer JEDES Item aus Name + Skinport-
Basis-Slug neu und schreibt ihn unbedingt. Von Hand auf dem Server:

    python3 rebuild_skin_slots.py [--dry-run]
"""
from __future__ import annotations

import argparse

from rm_db import _Conn, init_pool
from rm_skin_slots import slot_for_item
from rm_skins import fetch_skinport_items, skinport_item_to_row


def rebuild(conn_ctx, dry_run: bool = False) -> dict:
    rows = [r for r in (skinport_item_to_row(it) for it in fetch_skinport_items()) if r]
    changed = 0
    with conn_ctx() as (conn, cur):
        cur.execute("SELECT item_id, slot FROM skin_items")
        current = {r["item_id"]: r["slot"] for r in cur.fetchall()}
        for row in rows:
            new_slot = slot_for_item(row["name"], row["base_item"])
            if row["item_id"] not in current or current[row["item_id"]] == new_slot:
                continue
            changed += 1
            if not dry_run:
                cur.execute("UPDATE skin_items SET slot = %s WHERE item_id = %s", (new_slot, row["item_id"]))
    return {"catalog": len(rows), "changed": changed, "dry_run": dry_run}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Nur zaehlen, nichts schreiben")
    args = parser.parse_args()
    init_pool()
    print(f"rebuild done: {rebuild(_Conn, dry_run=args.dry_run)}")
