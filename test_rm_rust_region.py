"""Region-Erkennung aus dem Servernamen (Wort-Fallback ohne offiziellen Region-Tag)."""
from rm_rust import detect_rust_region


def test_south_america_is_not_north_america():
    assert detect_rust_region("[South America] Rusty 2x", []) == "sa"


def test_latin_america_maps_to_south_america():
    assert detect_rust_region("Rust Latin America Vanilla", []) == "sa"


def test_plain_america_stays_north_america():
    assert detect_rust_region("North America Main", []) == "na"
    assert detect_rust_region("America Long 3x", []) == "na"


def test_separate_region_words_keep_fixed_priority():
    assert detect_rust_region("Europe | America", []) == "eu"
    assert detect_rust_region("South America & North America", []) == "na"


def test_official_region_tag_still_wins_over_name():
    assert detect_rust_region("South America Vanilla", ["eu"]) == "eu"
