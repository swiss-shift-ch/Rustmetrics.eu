"""Run: python3 -m unittest test_rm_rhythm_rows"""

import sys
import types
import unittest
from unittest import mock

for _name in ("psycopg2", "psycopg2.pool", "psycopg2.extras"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2.pool"].ThreadedConnectionPool = object
sys.modules["psycopg2.extras"].RealDictCursor = object

import rustmetrics as rm  # noqa: E402


class _SessionCursor:
    """Emulates ORDER BY start_ts [DESC] LIMIT n over a fixed list of session rows."""

    def __init__(self, rows):
        self.rows = rows
        self.result = []

    def execute(self, sql, params):
        ordered = sorted(self.rows, key=lambda r: r["start_ts"],
                         reverse="ORDER BY start_ts DESC" in sql)
        self.result = ordered[:params[-1]]

    def fetchall(self):
        return self.result


def _sessions(count, start=1_000, step=60):
    return [{"start_ts": start + i * step, "end_ts": start + i * step + 30} for i in range(count)]


class RhythmRowLimitTest(unittest.TestCase):
    def test_presence_rows_keep_newest_sessions_when_capped(self):
        cur = _SessionCursor(_sessions(10))
        with mock.patch.object(rm, "RHYTHM_MAX_ROWS", 3):
            rows = rm._presence_session_rows(cur, 7656, since=0, now=10**9)
        self.assertEqual(sorted(s for s, _ in rows), [1_420, 1_480, 1_540])

    def test_server_rows_keep_newest_sessions_when_capped(self):
        cur = _SessionCursor(_sessions(10))
        watched = {"server_id": 1, "name": "Player", "bm_player_id": None}
        with mock.patch.object(rm, "RHYTHM_MAX_ROWS", 2):
            rows = rm._server_session_rows(cur, watched, since=0, now=10**9)
        self.assertEqual(sorted(s for s, _ in rows), [1_480, 1_540])

    def test_rhythm_still_contains_latest_session_after_cap(self):
        cur = _SessionCursor(_sessions(10))
        with mock.patch.object(rm, "RHYTHM_MAX_ROWS", 3):
            rows = rm._presence_session_rows(cur, 7656, since=0, now=2_000)
        rhythm = rm.build_rhythm(rows, [], now=2_000, days=1)
        self.assertEqual(rhythm["intervals"][-1], [1_540, 1_570])
        self.assertEqual(rhythm["intervals"][0], [1_420, 1_450])

    def test_uncapped_rows_are_unchanged(self):
        cur = _SessionCursor(_sessions(4))
        rows = rm._presence_session_rows(cur, 7656, since=0, now=10**9)
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()
