"""Tests fuer rm_skins.py — reine Parsing/Mapping-Logik, kein Netzwerk/DB."""
from __future__ import annotations

from rm_skins import category_from_market_page, skinport_item_to_row


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
