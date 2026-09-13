"""Run: python3 -m unittest test_rm_bm_match"""

import unittest

from rm_rust import pick_bm_server_id

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


if __name__ == "__main__":
    unittest.main()
