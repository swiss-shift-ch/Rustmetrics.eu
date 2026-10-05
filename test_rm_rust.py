"""Unit-Tests fuer rm_rust: Rate-Multiplikatoren in Tier- und Kategorie-Erkennung."""
from rm_rust import detect_rust_category, detect_rust_tier


def test_category_fractional_rate_is_not_high_rate():
    # "1.5x" enthaelt "5x", ist aber eine niedrige Rate -> Community, nicht Modded
    assert detect_rust_category("Rusty Moose 1.5x Monthly", []) == "community"
    assert detect_rust_category("EU 2.5x Weekly", []) == "community"


def test_category_high_rate_stays_modded():
    assert detect_rust_category("Rustoria 5x Main", []) == "modded"
    assert detect_rust_category("Chaos 10x No BP", []) == "modded"
    assert detect_rust_category("Mega 15x", []) == "modded"


def test_category_fractional_and_high_rate_together_is_modded():
    assert detect_rust_category("1.5x Gather 10x Scrap", []) == "modded"


def test_category_low_rate_and_vanilla_unchanged():
    assert detect_rust_category("Solo Duo 2x", []) == "community"
    assert detect_rust_category("Facepunch Official EU", []) == "vanilla"
    assert detect_rust_category("Vanilla 1.5x", []) == "community"


def test_category_bm_tag_wins_over_name():
    assert detect_rust_category("Some 1.5x Server", ["modded"]) == "modded"


def test_tier_fractional_rate_is_not_5x():
    assert detect_rust_tier("Rusty Moose 1.5x Monthly", []) != "5x"
    assert detect_rust_tier("EU 2.5x Weekly", []) != "5x"


def test_tier_whole_rates_unchanged():
    assert detect_rust_tier("Rustoria 5x Main", []) == "5x"
    assert detect_rust_tier("Chaos 10x", []) == "10x"
    assert detect_rust_tier("Solo Duo 2x", []) == "2x"
    assert detect_rust_tier("1.5x Gather 10x Scrap", []) == "10x"
    assert detect_rust_tier("PvE 5x", []) == "pve"
    assert detect_rust_tier("Plain Server", ["oxide"]) == "modded"
    assert detect_rust_tier("Plain Server", []) == "vanilla"
