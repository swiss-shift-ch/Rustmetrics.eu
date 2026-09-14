"""Tests fuer rm_skins.py — reine Parsing/Mapping-Logik, kein Netzwerk/DB."""
from __future__ import annotations

import rm_skins
from rm_skins import (
    STEAM_ICON_CDN,
    base_item_from_market_page,
    category_from_market_page,
    icon_hash_from_listing_html,
    local_icon_filename,
    skinport_item_to_row,
)


class TestCategoryFromMarketPage:
    def test_extracts_category_segment_from_valid_url(self):
        url = "https://skinport.com/rust/market/clothing/roadsign-kilt?item=Plywood%20Kilt"
        assert category_from_market_page(url) == "clothing"

    def test_extracts_misc_category(self):
        url = "https://skinport.com/rust/market/misc/sleeping-bag?item=Pitstop%20Bag"
        assert category_from_market_page(url) == "misc"

    def test_returns_none_for_none_input(self):
        assert category_from_market_page(None) is None

    def test_returns_none_for_empty_string(self):
        assert category_from_market_page("") is None

    def test_returns_none_for_url_with_too_few_path_segments(self):
        assert category_from_market_page("https://skinport.com/rust") is None

    def test_returns_none_for_unrelated_domain_shape(self):
        assert category_from_market_page("https://example.com/foo") is None

    def test_returns_none_for_malformed_url(self):
        # urlparse doesn't raise on most garbage, but guard stays defensive
        assert category_from_market_page("not a url at all::::") is None


class TestBaseItemFromMarketPage:
    def test_extracts_slug_segment_from_valid_url(self):
        url = "https://skinport.com/rust/market/clothing/roadsign-kilt?item=Plywood%20Kilt"
        assert base_item_from_market_page(url) == "roadsign-kilt"

    def test_returns_none_for_none_input(self):
        assert base_item_from_market_page(None) is None

    def test_returns_none_for_url_with_too_few_path_segments(self):
        assert base_item_from_market_page("https://skinport.com/rust/market/clothing") is None

    def test_returns_none_for_malformed_url(self):
        assert base_item_from_market_page("not a url at all::::") is None


class TestSkinportItemToRow:
    def test_maps_full_item_to_row(self):
        item = {
            "market_hash_name": "Plywood Kilt",
            "currency": "EUR",
            "min_price": 4.96,
            "market_page": "https://skinport.com/rust/market/clothing/roadsign-kilt?item=Plywood%20Kilt",
            "item_page": "https://skinport.com/rust/item/plywood-kilt",
            "quantity": 8,
        }
        row = skinport_item_to_row(item)
        assert row == {
            "item_id": "Plywood Kilt",
            "name": "Plywood Kilt",
            "raw_category": "clothing",
            "base_item": "roadsign-kilt",
            "platform": "skinport",
            "price": 4.96,
            "currency": "EUR",
            "url": "https://skinport.com/rust/market/clothing/roadsign-kilt?item=Plywood%20Kilt",
            "quantity": 8,
        }

    def test_returns_none_when_market_hash_name_missing(self):
        assert skinport_item_to_row({"currency": "EUR", "min_price": 1.0}) is None

    def test_falls_back_to_item_page_when_market_page_missing(self):
        item = {
            "market_hash_name": "Test Item",
            "min_price": 1.0,
            "item_page": "https://skinport.com/rust/item/test-item",
        }
        row = skinport_item_to_row(item)
        assert row["url"] == "https://skinport.com/rust/item/test-item"
        assert row["raw_category"] is None

    def test_handles_missing_price_as_none(self):
        item = {"market_hash_name": "No Price Item"}
        row = skinport_item_to_row(item)
        assert row["price"] is None
        assert row["currency"] == "EUR"  # default fallback

    def test_price_is_cast_to_float(self):
        item = {"market_hash_name": "X", "min_price": "3"}
        row = skinport_item_to_row(item)
        assert row["price"] == 3.0
        assert isinstance(row["price"], float)


class TestIconHashFromListingHtml:
    # Realer (gekuerzter) Ausschnitt einer Steam-Market-Listing-Seite,
    # 2026-09-14 live gegen appid=252490 verifiziert: das Icon steckt im
    # mehrfach escapten Render-State der normalen HTML-Seite, nicht in
    # einem sauberen JSON-Feld.
    REAL_FIXTURE = (
        r':\\\"42413e\\\",\\\"icon_url\\\":\\\"6TMcQ7eX6E0EZl2byXi7vaVKyDk_zQLX'
        r'05x6eLCFM9neAckxGDf7qU2e2gu64OnAeQ7835Fd4WPEfCk4nReh8DEiv5daPKo2rL0xR'
        r'PG-mc9EvQ8\\\",\\\"icon_url_large\\\":\\\"6TMcQ7eX6E0EZl2byXi7vaVKyDk'
        r'_zQLX05x6eLCFM9neAckxGDf7qU2e2gu64OnAeQ7835Fd4WPEfDY0jhyo8DEiv5daPKo2'
        r'rL0xRPG-VGIUR0s\\\"'
    )
    EXPECTED_HASH = (
        "6TMcQ7eX6E0EZl2byXi7vaVKyDk_zQLX05x6eLCFM9neAckxGDf7qU2e2gu64OnAeQ7835"
        "Fd4WPEfCk4nReh8DEiv5daPKo2rL0xRPG-mc9EvQ8"
    )

    def test_extracts_hash_from_real_escaped_fixture(self):
        assert icon_hash_from_listing_html(self.REAL_FIXTURE) == self.EXPECTED_HASH

    def test_does_not_match_icon_url_large_instead(self):
        # icon_url_large's hash differs (ends "-VGIUR0s") — a bad regex
        # that treats icon_url as a substring-anywhere match could grab
        # the wrong (larger) variant instead of the plain one.
        result = icon_hash_from_listing_html(self.REAL_FIXTURE)
        assert not result.endswith("VGIUR0s")

    def test_returns_none_when_icon_url_absent(self):
        assert icon_hash_from_listing_html("<html>no listings for this item</html>") is None

    def test_returns_none_for_empty_string(self):
        assert icon_hash_from_listing_html("") is None


class TestSteamIconCdnUrl:
    def test_cdn_template_builds_expected_shape(self):
        url = STEAM_ICON_CDN.format(icon_hash="abc123_-XYZ")
        assert url == "https://community.cloudflare.steamstatic.com/economy/image/abc123_-XYZ/128x128"


class TestLocalIconFilename:
    # Steams CDN schickt kein Access-Control-Allow-Origin — direktes Laden
    # als WebGL-Textur scheitert an CORS (verifiziert 2026-09-14 gegen
    # echten Chromium). Icons muessen deshalb lokal (same-origin) gespiegelt
    # werden; item_id/Skin-Namen enthalten Leerzeichen/Apostrophe/Unicode
    # und sind daher kein sicherer Dateiname.
    def test_returns_png_filename(self):
        assert local_icon_filename("Stalker Hoodie").endswith(".png")

    def test_is_filesystem_safe_for_names_with_spaces_and_apostrophes(self):
        name = local_icon_filename("BCHILLZ! Hoodie's Édition")
        assert all(c.isalnum() or c in "._-" for c in name)

    def test_is_deterministic(self):
        assert local_icon_filename("Stalker Hoodie") == local_icon_filename("Stalker Hoodie")

    def test_different_names_produce_different_filenames(self):
        assert local_icon_filename("Stalker Hoodie") != local_icon_filename("Stalker Pants")


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class _FakeConn:
    """Mimics rm_db._Conn's Shape: `with conn_ctx() as (conn, cur):`."""

    def __init__(self):
        self.cursor = _FakeCursor()

    def __enter__(self):
        return (self, self.cursor)

    def __exit__(self, *exc):
        return False


class TestRefreshSkinPrices:
    def test_passes_slug_derived_slot_into_skin_items_insert(self, monkeypatch):
        # "Ornate Vest" would be unmapped by name-only matching, but its
        # base-item slug ("roadsign-jacket") is authoritative and known.
        raw_items = [
            {
                "market_hash_name": "Ornate Vest",
                "min_price": 12.5,
                "market_page": "https://skinport.com/rust/market/clothing/roadsign-jacket?item=Ornate%20Vest",
                "quantity": 3,
            }
        ]
        monkeypatch.setattr(rm_skins, "fetch_skinport_items", lambda: raw_items)
        conn = _FakeConn()

        updated = rm_skins.refresh_skin_prices(lambda: conn)

        assert updated == 1
        insert_calls = [
            params for sql, params in conn.cursor.executed if sql.strip().startswith("INSERT INTO skin_items")
        ]
        assert len(insert_calls) == 1
        item_id, name, raw_category, slot, _created_at = insert_calls[0]
        assert (item_id, name, raw_category, slot) == ("Ornate Vest", "Ornate Vest", "clothing", "chest")
