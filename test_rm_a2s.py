"""Run: python3 -m unittest test_rm_a2s"""

import struct
import unittest

from rm_a2s import _parse_a2s_info, _parse_a2s_players, _read_cstring

HEADER = b"\xFF\xFF\xFF\xFF"
RUST_APPID_SHORT = 0  # Rust meldet im 16-Bit-Feld 0, die echte ID steht im GameID-Feld
RUST_GAMEID = 252490


def cstr(text: str) -> bytes:
    return text.encode("utf-8") + b"\x00"


def info_packet(name: str = "Rusty Moose |EU Main|", players: int = 120, max_players: int = 250,
                extra: bytes = b"") -> bytes:
    return (HEADER + b"I" + bytes([17])
            + cstr(name) + cstr("Procedural Map") + cstr("rust") + cstr("Rust")
            + struct.pack("<H", RUST_APPID_SHORT)
            + bytes([players, max_players, 0]) + b"dl" + bytes([0, 1])
            + cstr("2593") + extra)


def player_row(index: int, name: str, score: int, duration: float) -> bytes:
    return bytes([index]) + cstr(name) + struct.pack("<i", score) + struct.pack("<f", duration)


def players_packet(rows: list[bytes], count: int | None = None) -> bytes:
    return HEADER + b"D" + bytes([len(rows) if count is None else count]) + b"".join(rows)


class ReadCStringTest(unittest.TestCase):
    def test_returns_text_and_position_after_terminator(self):
        self.assertEqual(_read_cstring(b"abc\x00def\x00", 0), ("abc", 4))

    def test_reads_from_given_offset(self):
        self.assertEqual(_read_cstring(b"abc\x00def\x00", 4), ("def", 8))

    def test_replaces_invalid_utf8_instead_of_raising(self):
        text, pos = _read_cstring(b"a\xffb\x00", 0)
        self.assertEqual((text, pos), ("a�b", 4))

    def test_raises_when_terminator_is_missing(self):
        with self.assertRaises(ValueError):
            _read_cstring(b"abc", 0)


class ParseA2SInfoTest(unittest.TestCase):
    def test_parses_fixed_fields(self):
        info = _parse_a2s_info(info_packet())
        self.assertEqual(info["protocol"], 17)
        self.assertEqual(info["name"], "Rusty Moose |EU Main|")
        self.assertEqual(info["map"], "Procedural Map")
        self.assertEqual(info["folder"], "rust")
        self.assertEqual(info["game"], "Rust")
        self.assertEqual(info["players_count"], 120)
        self.assertEqual(info["max_players"], 250)
        self.assertEqual(info["bots"], 0)
        self.assertEqual(info["server_type"], "d")
        self.assertEqual(info["environment"], "l")
        self.assertEqual(info["visibility"], 0)
        self.assertEqual(info["vac"], 1)
        self.assertEqual(info["version"], "2593")

    def test_defaults_keywords_and_gameid_without_extra_data(self):
        info = _parse_a2s_info(info_packet())
        self.assertEqual((info["keywords"], info["gameid"]), ("", 0))

    def test_reads_keywords_and_gameid_behind_all_optional_fields(self):
        extra = (bytes([0x80 | 0x10 | 0x40 | 0x20 | 0x01])
                 + struct.pack("<H", 28015)                 # 0x80 Spielport
                 + struct.pack("<Q", 90071992547409920)     # 0x10 Server-SteamID
                 + struct.pack("<H", 28020) + cstr("SourceTV")  # 0x40
                 + cstr("mp250,cp120,pt,qp0,v2593")         # 0x20 Keywords
                 + struct.pack("<Q", RUST_GAMEID))          # 0x01 GameID
        info = _parse_a2s_info(info_packet(extra=extra))
        self.assertEqual(info["keywords"], "mp250,cp120,pt,qp0,v2593")
        self.assertEqual(info["gameid"], RUST_GAMEID)

    def test_reads_keywords_when_only_keywords_flag_is_set(self):
        info = _parse_a2s_info(info_packet(extra=bytes([0x20]) + cstr("mp100,cp3")))
        self.assertEqual((info["keywords"], info["gameid"]), ("mp100,cp3", 0))

    def test_returns_empty_dict_for_other_response_type(self):
        self.assertEqual(_parse_a2s_info(HEADER + b"A" + b"\x01\x02\x03\x04"), {})

    def test_returns_empty_dict_for_too_short_packet(self):
        self.assertEqual(_parse_a2s_info(HEADER + b"I"), {})


class ParseA2SPlayersTest(unittest.TestCase):
    def test_parses_name_score_and_whole_seconds(self):
        packet = players_packet([player_row(0, "alice", 12, 3600.9), player_row(1, "bob", 0, 59.2)])
        self.assertEqual(_parse_a2s_players(packet), [
            {"name": "alice", "score": 12, "duration_s": 3600},
            {"name": "bob", "score": 0, "duration_s": 59},
        ])

    def test_returns_empty_list_for_zero_players(self):
        self.assertEqual(_parse_a2s_players(players_packet([]) + b"\x00"), [])

    def test_returns_empty_list_for_other_response_type(self):
        self.assertEqual(_parse_a2s_players(HEADER + b"A" + b"\x01\x02\x03\x04"), [])

    def test_stops_at_end_of_data_when_count_is_too_high(self):
        packet = players_packet([player_row(0, "alice", 1, 10.0)], count=5)
        self.assertEqual(_parse_a2s_players(packet), [{"name": "alice", "score": 1, "duration_s": 10}])

    def test_keeps_complete_rows_before_a_truncated_one(self):
        packet = players_packet([player_row(0, "alice", 1, 10.0), player_row(1, "bob", 2, 20.0)])
        self.assertEqual(_parse_a2s_players(packet[:-3]), [{"name": "alice", "score": 1, "duration_s": 10}])

    def test_keeps_empty_names_of_connecting_players(self):
        packet = players_packet([player_row(0, "", 0, 5.0)])
        self.assertEqual(_parse_a2s_players(packet), [{"name": "", "score": 0, "duration_s": 5}])


if __name__ == "__main__":
    unittest.main()
