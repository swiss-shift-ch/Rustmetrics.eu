"""RustMetrics — Skin-Checker: manuell kuratierte Slot-Zuordnung.

Ordnet ausgewaehlte, real existierende Rust-Basisitems (keine geratenen
Namen — jedes hier gelistete Item ist ein tatsaechliches Vanilla-Kleidungs-
/Ruestungsitem im Spiel) einem der sechs Playermodel-Slots zu:
hoodie | pants | boots | mask | hat | backpack

Bewusst manuell und klein gehalten (siehe Bau-Auftrag Abschnitt 2 und die
Diskussion dazu): wir uebernehmen KEINE Slot-Datenbank von Drittseiten
(rusthelp.com/rustmaps.com), sondern pflegen das hier selbst, Item fuer
Item, erweiterbar bei Bedarf.

Die Skinport-API selbst liefert nur grobe Kategorien (weapon, misc,
clothing, armor, resource) — das reicht nicht fuer unsere sechs Slots,
siehe rm_skins.category_from_market_page(). Diese Datei ist die feinere
Ebene darueber.
"""
from __future__ import annotations

# item_id hier ist der Basis-Item-Name (market_hash_name-Praefix), wie ihn
# auch Skinport/Steam fuer die Vanilla-Variante des Items verwenden.
# slot: hoodie | pants | boots | mask | hat | backpack
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
    "Metal Chest Plate":   "hoodie",  # Torso-Ruestung -> hoodie-Slot
    "Hoodie":              "hoodie",
    "Jacket":              "hoodie",
    "Wood Armor Jacket":   "hoodie",
    "Roadsign Jacket":     "hoodie",
    "Hazmat Suit":         "hoodie",  # deckt technisch mehrere Slots ab,
                                       # hier vereinfachend torso zugeordnet
    "Pants":               "pants",
    "Wood Armor Pants":    "pants",
    "Roadsign Kilt":       "pants",
    "Boots":               "boots",
    "Hide Boots":          "boots",
    "Leather Gloves":      "hoodie",  # kein eigener Handschuh-Slot -> torso
    "Tactical Gloves":     "hoodie",
    "Small Backpack":      "backpack",
    "Large Backpack":      "backpack",
    "Road Sign Jacket":    "hoodie",
}


def slot_for_name(market_hash_name: str) -> str | None:
    """Findet den kuratierten Slot fuer ein Item anhand des Basis-Namens.

    Skin-Namen enthalten oft ein Praefix/Suffix ("Abyss Hoodie", "Plywood
    Kilt" fuer "Roadsign Kilt" etc.) — deshalb Teilstring-Match auf die in
    SLOT_MAP hinterlegten Basis-Namen statt exaktem Vergleich. Gibt None
    zurueck, wenn kein bekanntes Basisitem im Namen vorkommt (lieber
    unbekannt lassen als falsch raten).
    """
    if not market_hash_name:
        return None
    lname = market_hash_name.lower()
    for base_name, slot in SLOT_MAP.items():
        if base_name.lower() in lname:
            return slot
    return None
