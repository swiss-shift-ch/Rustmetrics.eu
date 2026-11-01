"""Run: python3 -m unittest test_rm_caddy_top_paths"""

import json
import os
import sys
import tempfile
import types
import unittest

for _name in ("psycopg2", "psycopg2.pool", "psycopg2.extras"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2.pool"].ThreadedConnectionPool = object
sys.modules["psycopg2.extras"].RealDictCursor = object

import rustmetrics as rm  # noqa: E402


def _line(uri: str) -> str:
    fields = {"request": {"client_ip": "198.51.100.7", "uri": uri}, "status": 200}
    return "2026/11/01 05:00:00.000\tINFO\thttp.log.access\thandled request\t" + json.dumps(fields) + "\n"


class TopPathsTest(unittest.TestCase):
    def _parse(self, *uris: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.writelines(_line(u) for u in uris)
        self.addCleanup(os.unlink, f.name)
        return rm.parse_caddy_log_tail(path=f.name)

    def test_static_file_with_query_is_not_a_top_path(self):
        for uri in ("/player-page.js?v=20260914", "/style.css?v=3", "/logo.png?x=1",
                    "/icons.svg?v=2", "/favicon.ico?v=2", "/site.webmanifest?v=2", "/style.css"):
            with self.subTest(uri=uri):
                self.assertEqual(self._parse(uri)["top_paths"], [])

    def test_page_query_counts_towards_its_path(self):
        self.assertEqual(self._parse("/browser?region=EU", "/browser")["top_paths"], [("/browser", 2)])

    def test_api_is_not_a_top_path(self):
        self.assertEqual(self._parse("/api/browse?limit=5")["top_paths"], [])

    def test_extension_inside_query_does_not_make_a_page_static(self):
        self.assertEqual(self._parse("/server?datei=a.js")["top_paths"], [("/server", 1)])

    def test_static_file_still_counts_as_hit(self):
        self.assertEqual(self._parse("/style.css?v=3")["hits_total"], 1)


if __name__ == "__main__":
    unittest.main()
