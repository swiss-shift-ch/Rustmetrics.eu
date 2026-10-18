"""detect_rust_tier: Rate muss als ganze Zahl erkannt werden."""
from rm_rust import detect_rust_tier


def test_exact_tiers_unchanged():
    assert detect_rust_tier("Rustoria 2x Monthly", []) == "2x"
    assert detect_rust_tier("[EU] 10x Solo/Duo", []) == "10x"
    assert detect_rust_tier("1000x KITS", []) == "100x"


def test_two_digit_rate_not_read_as_last_digit():
    assert detect_rust_tier("EU 12x Trio", []) == "10x"
    assert detect_rust_tier("Moose 25x", []) == "20x"
    assert detect_rust_tier("15x Quad", []) == "10x"


def test_fractional_rate_not_read_as_5x():
    assert detect_rust_tier("Chill 1.5x Vanilla+", []) == "vanilla"
    assert detect_rust_tier("2.5x Weekly", []) == "2x"


def test_highest_rate_wins_and_tags_count():
    assert detect_rust_tier("2x gather 5x loot", []) == "5x"
    assert detect_rust_tier("Some Server", ["3x", "biweekly"]) == "3x"


def test_rate_must_stand_alone():
    assert detect_rust_tier("Server 2xp event", []) == "vanilla"
    assert detect_rust_tier("PVE 10x", []) == "pve"
    assert detect_rust_tier("Modded Fun", []) == "modded"
