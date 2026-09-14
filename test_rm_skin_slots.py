"""Tests fuer rm_skin_slots.py — reine Lookup-Logik, keine DB/Netzwerk."""
from __future__ import annotations

from rm_skin_slots import SLOT_MAP, SLUG_SLOT_MAP, slot_for_item, slot_for_name, slot_for_slug


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


class TestSlotForSlug:
    def test_returns_none_for_empty_string(self):
        assert slot_for_slug("") is None

    def test_returns_none_for_none_input(self):
        assert slot_for_slug(None) is None

    def test_hoodie_slug(self):
        assert slot_for_slug("roadsign-jacket") == "hoodie"

    def test_pants_slug(self):
        assert slot_for_slug("roadsign-kilt") == "pants"

    def test_boots_slug(self):
        assert slot_for_slug("burlap-shoes") == "boots"

    def test_mask_slug(self):
        assert slot_for_slug("deer-skull-mask") == "mask"

    def test_hat_slug(self):
        assert slot_for_slug("miner-s-hat") == "hat"

    def test_is_case_insensitive(self):
        assert slot_for_slug("Roadsign-Jacket") == "hoodie"

    def test_returns_none_for_unknown_slug(self):
        assert slot_for_slug("some-unknown-slug") is None

    def test_satchel_explosives_is_deliberately_unmapped(self):
        # Skinport files it under clothing, but it isn't wearable.
        assert slot_for_slug("satchel-explosives") is None

    def test_burlap_gloves_is_deliberately_unmapped(self):
        # No glove slot in the viewer; mapping to hoodie would clobber outfits.
        assert slot_for_slug("burlap-gloves") is None

    def test_tactical_gloves_is_deliberately_unmapped(self):
        assert slot_for_slug("tactical-gloves") is None

    def test_every_slug_slot_map_entry_maps_to_valid_slot(self):
        valid_slots = {"hoodie", "pants", "boots", "mask", "hat", "backpack"}
        for slug, slot in SLUG_SLOT_MAP.items():
            assert slot in valid_slots, f"{slug!r} maps to invalid slot {slot!r}"


class TestSlotForItem:
    def test_prefers_slug_over_misleading_name(self):
        # "Ornate Vest" sounds like a hoodie by name-matching heuristics,
        # but Skinport's own base-item slug says it's a roadsign-jacket skin.
        assert slot_for_item("Ornate Vest", "roadsign-jacket") == "hoodie"

    def test_falls_back_to_name_when_base_item_is_none(self):
        # Backpacks aren't in the clothing/armor slugs, so name fallback
        # must still cover them.
        assert slot_for_item("Large Backpack", None) == "backpack"

    def test_falls_back_to_name_when_slug_is_unknown(self):
        assert slot_for_item("Abyss Hoodie", "some-unknown-slug") == "hoodie"

    def test_returns_none_when_both_name_and_slug_are_unknown(self):
        assert slot_for_item("Some Completely Unknown Weapon Skin", "some-unknown-slug") is None

    def test_returns_none_when_both_inputs_are_none(self):
        assert slot_for_item(None, None) is None
