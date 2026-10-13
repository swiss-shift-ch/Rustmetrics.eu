"""Run: python3 -m unittest test_rm_bm_match"""

import unittest

from rm_rust import match_presence_server, pick_bm_server_id, presence_query_port

HOST = "203.0.113.50"


def _srv(sid, ip=HOST, port=28010, port_query=28015) -> dict:
    return {"id": str(sid), "attributes": {"ip": ip, "port": port, "portQuery": port_query}}


class PickBmServerIdTest(unittest.TestCase):
    def test_prefers_query_port_match_among_servers_on_same_ip(self):
        results = [_srv(1, port=28000, port_query=28001), _srv(2, port=28010, port_query=28015)]
        self.assertEqual(pick_bm_server_id(results, HOST, 28015), 2)

    def test_matches_game_port_when_user_added_it(self):
        results = [_srv(1, port=28000, port_query=28001), _srv(2, port=28010, port_query=28015)]
        self.assertEqual(pick_bm_server_id(results, HOST, 28010), 2)

    def test_uses_only_server_on_ip_when_ports_differ(self):
        results = [_srv(7, port=28010, port_query=28016), _srv(8, ip="198.51.100.1")]
        self.assertEqual(pick_bm_server_id(results, HOST, 28015), 7)

    def test_refuses_to_guess_between_several_servers_on_same_ip(self):
        results = [_srv(1, port=28000, port_query=28001), _srv(2, port=28020, port_query=28021)]
        self.assertIsNone(pick_bm_server_id(results, HOST, 28015))

    def test_ignores_other_ips_and_bad_ids(self):
        self.assertIsNone(pick_bm_server_id([_srv(3, ip="198.51.100.1")], HOST, 28015))
        self.assertIsNone(pick_bm_server_id([{"id": "x", "attributes": {"ip": HOST, "portQuery": 28015}}], HOST, 28015))
        self.assertIsNone(pick_bm_server_id([], HOST, 28015))


class MatchPresenceServerTest(unittest.TestCase):
    ROWS = [
        {"id": 8, "host": HOST, "port": 28016, "watched": True},
        {"id": 9, "host": HOST, "port": 28021, "watched": True},
        {"id": 5, "host": "198.51.100.5", "port": 28016, "watched": False},
    ]

    def test_matches_query_port_row_from_game_port_presence(self):
        self.assertEqual(match_presence_server(self.ROWS, HOST, f"{HOST}:28020")["id"], 9)
        self.assertEqual(match_presence_server(self.ROWS, HOST, f"{HOST}:28015")["id"], 8)

    def test_matches_row_stored_with_game_port(self):
        rows = [{"id": 1, "host": HOST, "port": 28015, "watched": True}, {"id": 2, "host": HOST, "port": 28020, "watched": True}]
        self.assertEqual(match_presence_server(rows, HOST, f"{HOST}:28015")["id"], 1)

    def test_single_server_on_ip_matches_even_with_other_port(self):
        self.assertEqual(match_presence_server(self.ROWS, "198.51.100.5", "198.51.100.5:27015")["id"], 5)

    def test_does_not_guess_between_servers_on_same_ip(self):
        self.assertIsNone(match_presence_server(self.ROWS, HOST, f"{HOST}:27015"))

    def test_presence_query_port_is_game_port_plus_one(self):
        self.assertEqual(presence_query_port("194.54.88.71:28010"), 28011)
        self.assertIsNone(presence_query_port(None))
        self.assertIsNone(presence_query_port("194.54.88.71"))

    def test_presence_query_port_is_none_outside_the_valid_port_range(self):
        self.assertIsNone(presence_query_port("0.0.0.0:0"))
        self.assertIsNone(presence_query_port("194.54.88.71:65535"))
        self.assertIsNone(presence_query_port("194.54.88.71:99999"))
        self.assertEqual(presence_query_port("194.54.88.71:65534"), 65535)
        self.assertEqual(presence_query_port("194.54.88.71:1"), 2)

    def test_handles_missing_host_or_address(self):
        self.assertIsNone(match_presence_server(self.ROWS, None, None))
        self.assertIsNone(match_presence_server(self.ROWS, "203.0.113.99", "203.0.113.99:28015"))


if __name__ == "__main__":
    unittest.main()
