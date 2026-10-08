"""RustMetrics — Skin-Checker: manuell kuratierte Slot-Zuordnung.

Ordnet ausgewaehlte, real existierende Rust-Basisitems (keine geratenen
Namen — jedes hier gelistete Item ist ein tatsaechliches Vanilla-Kleidungs-
/Ruestungsitem im Spiel) einem der sechs Playermodel-Slots zu:
hoodie | chest | pants | kilt | boots | mask | hat | backpack

Bewusst manuell und klein gehalten (siehe Bau-Auftrag Abschnitt 2 und die
Diskussion dazu): wir uebernehmen KEINE Slot-Datenbank von Drittseiten,
sondern pflegen das hier selbst, Item fuer Item, erweiterbar bei Bedarf.

Die Skinport-API selbst liefert nur grobe Kategorien (weapon, misc,
clothing, armor, resource) — das reicht nicht fuer unsere sechs Slots,
siehe rm_skins.category_from_market_page(). Diese Datei ist die feinere
Ebene darueber.
"""
from __future__ import annotations

# item_id hier ist der Basis-Item-Name (market_hash_name-Praefix), wie ihn
# auch Skinport/Steam fuer die Vanilla-Variante des Items verwenden.
# slot: hoodie | chest | pants | kilt | boots | mask | hat | backpack
# (chest = Torso-Ruestung UEBER dem Hoodie, kilt = Bein-Ruestung UEBER der
#  Hose — in Rust eigene Slots, im 3D-Viewer eigene Schalen ueber den Basis-Slots)
SLOT_MAP: dict[str, str] = {
    # Kopf / Maske
    "Bandana Mask":        "mask",
    "Metal Facemask":      "mask",
    "Balaclava":           "mask",
    "Bone Helmet":         "hat",
    "Boonie Hat":          "hat",
    "Baseball Cap":        "hat",
    "Bucket Helmet":       "hat",
    "Coffee Can Helmet":   "hat",
    "Riot Helmet":         "hat",
    "Wood Armor Helmet":   "hat",
    "Metal Chest Plate":   "chest",
    "Hoodie":              "hoodie",
    "Jacket":              "hoodie",
    "Wood Armor Jacket":   "chest",
    "Roadsign Jacket":     "chest",
    "Hazmat Suit":         "hoodie",  # deckt technisch mehrere Slots ab,
                                       # hier vereinfachend torso zugeordnet
    "Pants":               "pants",
    "Wood Armor Pants":    "kilt",
    "Roadsign Kilt":       "kilt",
    "Boots":               "boots",
    "Hide Boots":          "boots",
    # Handschuhe (Leather/Tactical Gloves) bewusst NICHT gemappt: es gibt
    # keinen eigenen Handschuh-Slot im Viewer, und ein Mapping auf hoodie
    # wuerde im Outfit-Builder den Hoodie des Traegers ersetzen -- das
    # waere falsch.
    "Small Backpack":      "backpack",
    "Large Backpack":      "backpack",
    "Road Sign Jacket":    "chest",
}

_SLOT_MAP_LONGEST_FIRST = sorted(SLOT_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)


def slot_for_name(market_hash_name: str) -> str | None:
    """Findet den kuratierten Slot fuer ein Item anhand des Basis-Namens.

    Skin-Namen enthalten oft ein Praefix/Suffix ("Abyss Hoodie", "Plywood
    Kilt" fuer "Roadsign Kilt" etc.) — deshalb Teilstring-Match auf die in
    SLOT_MAP hinterlegten Basis-Namen statt exaktem Vergleich. Gibt None
    zurueck, wenn kein bekanntes Basisitem im Namen vorkommt (lieber
    unbekannt lassen als falsch raten). Der laengste passende Basis-Name
    gewinnt, sonst faengt "Jacket" (hoodie) auch "Roadsign Jacket" (chest).
    """
    if not market_hash_name:
        return None
    lname = market_hash_name.lower()
    for base_name, slot in _SLOT_MAP_LONGEST_FIRST:
        if base_name.lower() in lname:
            return slot
    return None


# Slug ist das 4. Pfadsegment einer Skinport-market_page-URL (siehe
# rm_skins.base_item_from_market_page()), z.B. ".../clothing/roadsign-kilt"
# -> "roadsign-kilt". Das ist Skinports EIGENE Zuordnung, welches Vanilla-
# Rust-Item ein Skin darstellt -- deutlich zuverlaessiger als Teilstring-
# Matching auf den (oft irrefuehrenden) Skin-Namen, siehe slot_for_item().
#
# Handschuh-Slugs (burlap-gloves, tactical-gloves) und satchel-explosives
# (von Skinport unter "clothing" einsortiert, aber nicht tragbar) sind
# bewusst NICHT gelistet -> slot_for_slug() gibt fuer sie None zurueck.
SLUG_SLOT_MAP: dict[str, str] = {
    # hoodie
    "hoodie":              "hoodie",
    "jacket":              "hoodie",
    "snow-jacket":         "hoodie",
    "roadsign-jacket":     "chest",
    "metal-torso-plate":   "chest",
    "hide-poncho":         "hoodie",
    "burlap-shirt":        "hoodie",
    "tshirt":              "hoodie",
    "long-tshirt":         "hoodie",
    "collared-shirt":      "hoodie",
    "tank-top":            "hoodie",
    "hide-halterneck":     "hoodie",
    # pants
    "pants":               "pants",
    "shorts":              "pants",
    "roadsign-kilt":       "kilt",
    "hide-skirt":          "pants",
    "hide-pants":          "pants",
    "burlap-trousers":     "pants",
    # boots
    "boots":               "boots",
    "burlap-shoes":        "boots",
    # mask
    "metal-facemask":      "mask",
    "bandana":             "mask",
    "balaclava":           "mask",
    "deer-skull-mask":     "mask",
    # hat
    "coffeecan-helmet":    "hat",
    "bucket-helmet":       "hat",
    "rifle-helmet":        "hat",
    "boonie":              "hat",
    "cap":                 "hat",
    "beenie":              "hat",
    "miner-s-hat":         "hat",
    "burlap-headwrap":     "hat",
}


def slot_for_slug(slug: str | None) -> str | None:
    """Findet den kuratierten Slot fuer Skinports Basis-Item-Slug (exakt).

    Anders als slot_for_name() kein Teilstring-Match: der Slug IST bereits
    das Basisitem, kein Skin-Name mit Praefix/Suffix drumherum. Gibt None
    zurueck bei leerem/unbekanntem Slug (z.B. Handschuhe, satchel-explosives
    -- siehe SLUG_SLOT_MAP-Kommentar).
    """
    if not slug:
        return None
    return SLUG_SLOT_MAP.get(slug.lower())


def slot_for_item(name: str | None, base_item: str | None) -> str | None:
    """Findet den kuratierten Slot: Slug zuerst (evidenzbasiert), sonst Name.

    Der Skinport-Slug ist Skinports eigene Klassifikation und damit
    zuverlaessiger als Teilstring-Matching auf den (teils irrefuehrenden)
    Skin-Namen -- siehe Modul-Docstring. Fallback auf slot_for_name() bleibt
    noetig fuer Items ohne clothing/armor-Slug (z.B. Backpacks, Hazmat
    Suit), die nicht in SLUG_SLOT_MAP vorkommen.
    """
    slot = slot_for_slug(base_item)
    if slot is not None:
        return slot
    return slot_for_name(name)
