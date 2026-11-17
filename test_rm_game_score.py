"""Run: python3 -m unittest test_rm_game_score"""

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


class _FakeConn:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        cur = mock.Mock()
        cur.execute.side_effect = lambda sql, params: self.calls.append(params)
        return None, cur

    def __exit__(self, *exc):
        return False


class SubmitGameScoreDurationTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patches = [
            mock.patch.object(rm, "_Conn", lambda: _FakeConn(self.calls)),
            mock.patch.object(rm, "_user_score_bucket", lambda uid: mock.Mock(take=lambda n: True)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_negative_duration_is_rejected_and_not_stored(self):
        ok, err = rm.submit_game_score(1, "flap", 5000, -1)
        self.assertFalse(ok)
        self.assertEqual(err, "invalid duration")
        self.assertEqual(self.calls, [])

    def test_non_numeric_duration_is_rejected_without_db_error(self):
        ok, err = rm.submit_game_score(1, "flap", 10, "abc")
        self.assertFalse(ok)
        self.assertEqual(err, "invalid duration")
        self.assertEqual(self.calls, [])

    def test_too_short_duration_for_score_is_rejected(self):
        ok, err = rm.submit_game_score(1, "flap", 100, 1000)
        self.assertFalse(ok)
        self.assertEqual(err, "duration implausibly short for score")

    def test_plausible_duration_is_stored(self):
        ok, err = rm.submit_game_score(1, "flap", 10, 20_000)
        self.assertTrue(ok, err)
        self.assertEqual(self.calls[0][3], 20_000)

    def test_missing_duration_is_still_accepted(self):
        ok, err = rm.submit_game_score(1, "flap", 3, None)
        self.assertTrue(ok, err)
        self.assertIsNone(self.calls[0][3])


if __name__ == "__main__":
    unittest.main()
