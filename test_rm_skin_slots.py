"""Tests fuer rm_skin_slots.py — reine Lookup-Logik, keine DB/Netzwerk."""
from __future__ import annotations

from rm_skin_slots import SLOT_MAP, slot_for_name


class TestSlotForName:
    def test_returns_none_for_empty_string(self):
        assert slot_for_name("") is None

    def test_returns_none_for_none_input(self):
        assert slot_for_name(None) is None

    def test_matches_exact_base_item_name(self):
        assert slot_for_name("Hoodie") == "hoodie"

    def test_matches_skin_name_containing_base_item(self):
        # echte Skin-Namen haben ein Praefix/Suffix um den Basis-Item-Namen
        assert slot_for_name("Abyss Hoodie") == "hoodie"

    def test_is_case_insensitive(self):
        assert slot_for_name("abyss hoodie") == "hoodie"

    def test_returns_none_for_unknown_item(self):
        assert slot_for_name("Some Completely Unknown Weapon Skin") is None

    def test_every_slot_map_entry_maps_to_valid_slot(self):
        valid_slots = {"hoodie", "pants", "boots", "mask", "hat", "backpack"}
        for name, slot in SLOT_MAP.items():
            assert slot in valid_slots, f"{name!r} maps to invalid slot {slot!r}"

    def test_boots_item(self):
        assert slot_for_name("Hide Boots") == "boots"

    def test_backpack_item(self):
        assert slot_for_name("Large Backpack") == "backpack"

    def test_mask_item(self):
        assert slot_for_name("Metal Facemask") == "mask"

    def test_hat_item(self):
        assert slot_for_name("Boonie Hat") == "hat"
