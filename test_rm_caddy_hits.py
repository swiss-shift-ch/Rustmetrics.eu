"""Run: python3 -m unittest test_rm_caddy_hits"""

import os
import sys
import tempfile
import time
import types
import unittest

for _name in ("psycopg2", "psycopg2.pool", "psycopg2.extras"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2.pool"].ThreadedConnectionPool = object
sys.modules["psycopg2.extras"].RealDictCursor = object

import rustmetrics as rm  # noqa: E402


def _console_line(ts: float, uri: str = "/browser", status: int = 200) -> str:
    stamp = time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(ts))
    return (
        f'{stamp}.301\t\x1b[34mINFO\x1b[0m\thttp.log.access.log2\thandled request\t'
        f'{{"request": {{"client_ip": "198.51.100.7", "uri": "{uri}"}}, "status": {status}}}\n'
    )


class HitsByHourTest(unittest.TestCase):
    def _parse(self, *lines: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.writelines(lines)
        self.addCleanup(os.unlink, f.name)
        return rm.parse_caddy_log_tail(f.name, 1024 * 1024)

    def test_console_format_lines_fill_the_hour_chart(self):
        now = time.time()
        hour = time.strftime("%Y-%m-%d %H", time.gmtime(now - 60))

        result = self._parse(_console_line(now - 60), _console_line(now - 60))

        self.assertEqual(result["hits_by_hour"], [(hour, 2)])

    def test_console_lines_older_than_24h_stay_out_of_the_chart(self):
        result = self._parse(_console_line(time.time() - 25 * 3600))

        self.assertEqual(result["hits_total"], 1)
        self.assertEqual(result["hits_by_hour"], [])

    def test_json_ts_still_wins(self):
        now = time.time()
        hour = time.strftime("%Y-%m-%d %H", time.gmtime(now - 60))
        line = f'{{"ts": {now - 60}, "request": {{"uri": "/browser"}}, "status": 200}}\n'

        result = self._parse(line)

        self.assertEqual(result["hits_by_hour"], [(hour, 1)])

    def test_line_without_any_timestamp_counts_but_not_in_chart(self):
        result = self._parse('{"request": {"uri": "/browser"}, "status": 200}\n')

        self.assertEqual(result["hits_total"], 1)
        self.assertEqual(result["hits_by_hour"], [])

    def test_impossible_log_date_is_ignored(self):
        result = self._parse(
            '2026/13/45 99:00:00.000\tINFO\t{"request": {"uri": "/browser"}, "status": 200}\n')

        self.assertEqual(result["hits_total"], 1)
        self.assertEqual(result["hits_by_hour"], [])


if __name__ == "__main__":
    unittest.main()
