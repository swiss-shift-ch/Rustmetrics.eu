"""Run: python3 -m unittest test_rm_a2s_blocklist"""

import socket
import struct
import threading
import unittest

from rm_a2s import query_a2s
from rm_a2s_blocklist import (MODE_ANON, MODE_FULL, MODE_PLAYERS, A2SBlocklist,
                              bm_names_confirmed_real, should_use_bm_names)
from rm_config import A2S_HEADER, A2S_INFO_REQUEST, A2S_PLAYER_REQHDR

HOST, PORT = "203.0.113.9", 28015
FAKE_NAMES = ["carmelo", "gena", "otis"] * 6
REAL_NAMES = [f"Player_{i}" for i in range(20)]
FEW_REAL_NAMES = REAL_NAMES[:5]

_INFO_PACKET = (
    b"\xFF\xFF\xFF\xFFI" + bytes([17])
    + b"Test Server\x00Procedural Map\x00rust\x00Rust\x00"
    + struct.pack("<H", 0) + bytes([5, 50, 0]) + b"dl" + bytes([0, 1]) + b"2260\x00"
)


def _record(blocklist: A2SBlocklist, now: float = 1000.0, **facts) -> str | None:
    defaults = {"a2s_online": True, "a2s_names": REAL_NAMES, "a2s_players_failed": False,
                "bm_online": False, "bm_names": []}
    return blocklist.record(HOST, PORT, now=now, **{**defaults, **facts})


class A2SBlocklistTest(unittest.TestCase):
    def test_marks_full_block_when_a2s_fails_but_battlemetrics_says_online(self):
        blocklist = A2SBlocklist()
        self.assertEqual(_record(blocklist, a2s_online=False, a2s_names=[], bm_online=True), MODE_FULL)
        self.assertEqual(blocklist.mode(HOST, PORT, now=1001.0), MODE_FULL)

    def test_does_not_mark_server_that_is_simply_offline(self):
        blocklist = A2SBlocklist()
        self.assertIsNone(_record(blocklist, a2s_online=False, a2s_names=[], bm_online=False))
        self.assertIsNone(blocklist.mode(HOST, PORT, now=1001.0))

    def test_marks_players_block_when_player_query_fails_or_list_is_empty(self):
        self.assertEqual(_record(A2SBlocklist(), a2s_names=[], a2s_players_failed=True, bm_names=REAL_NAMES), MODE_PLAYERS)
        self.assertEqual(_record(A2SBlocklist(), a2s_names=[], bm_names=REAL_NAMES), MODE_PLAYERS)
        self.assertIsNone(_record(A2SBlocklist(), a2s_names=[], bm_names=[]))

    def test_marks_anonymized_when_battlemetrics_has_real_names(self):
        self.assertEqual(_record(A2SBlocklist(), a2s_names=FAKE_NAMES, bm_names=REAL_NAMES), MODE_ANON)

    def test_not_anonymized_mode_when_battlemetrics_also_has_fake_or_too_few_names(self):
        self.assertIsNone(_record(A2SBlocklist(), a2s_names=FAKE_NAMES, bm_names=FAKE_NAMES))
        self.assertIsNone(_record(A2SBlocklist(), a2s_names=FAKE_NAMES, bm_names=FEW_REAL_NAMES))

    def test_clears_entry_when_a2s_works_again(self):
        blocklist = A2SBlocklist()
        _record(blocklist, a2s_online=False, a2s_names=[], bm_online=True)
        self.assertIsNone(_record(blocklist))
        self.assertIsNone(blocklist.mode(HOST, PORT, now=1001.0))

    def test_entry_expires_after_recheck_window(self):
        blocklist = A2SBlocklist(recheck_after_sec=60)
        _record(blocklist, now=1000.0, a2s_online=False, a2s_names=[], bm_online=True)
        self.assertEqual(blocklist.mode(HOST, PORT, now=1059.0), MODE_FULL)
        self.assertIsNone(blocklist.mode(HOST, PORT, now=1060.0))


class NameSourceTest(unittest.TestCase):
    def test_uses_battlemetrics_names_when_a2s_has_none(self):
        self.assertTrue(should_use_bm_names([], FEW_REAL_NAMES))

    def test_replaces_fake_a2s_names_only_with_confirmed_real_battlemetrics_names(self):
        self.assertTrue(should_use_bm_names(FAKE_NAMES, REAL_NAMES))
        self.assertFalse(should_use_bm_names(FAKE_NAMES, FAKE_NAMES))
        self.assertFalse(should_use_bm_names(FAKE_NAMES, FEW_REAL_NAMES))

    def test_keeps_real_a2s_names_and_ignores_empty_battlemetrics(self):
        self.assertFalse(should_use_bm_names(REAL_NAMES, REAL_NAMES))
        self.assertFalse(should_use_bm_names([], []))

    def test_confirmed_real_needs_enough_names(self):
        self.assertTrue(bm_names_confirmed_real(REAL_NAMES))
        self.assertFalse(bm_names_confirmed_real(FEW_REAL_NAMES))
        self.assertFalse(bm_names_confirmed_real(FAKE_NAMES))


class _FakeA2SServer:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.player_requests = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                return
            if data.startswith(A2S_INFO_REQUEST):
                self.sock.sendto(_INFO_PACKET, addr)
            elif data.startswith(A2S_HEADER + A2S_PLAYER_REQHDR):
                self.player_requests += 1
                self.sock.sendto(b"\xFF\xFF\xFF\xFFD\x00", addr)

    def close(self):
        self.sock.close()


class QueryA2SPlayersFlagTest(unittest.TestCase):
    def setUp(self):
        self.server = _FakeA2SServer()

    def tearDown(self):
        self.server.close()

    def test_skips_player_query_when_disabled(self):
        q = query_a2s("127.0.0.1", self.server.port, timeout=1.0, include_players=False)
        self.assertTrue(q["online"])
        self.assertEqual(q["players_count"], 5)
        self.assertNotIn("players_list", q)
        self.assertEqual(self.server.player_requests, 0)

    def test_queries_players_by_default(self):
        q = query_a2s("127.0.0.1", self.server.port, timeout=1.0)
        self.assertEqual(q["players_list"], [])
        self.assertNotIn("players_error", q)
        self.assertEqual(self.server.player_requests, 1)


if __name__ == "__main__":
    unittest.main()
