"""Run: python3 -m unittest test_rm_bm_ratelimit"""

import email.message
import sys
import types
import unittest
import urllib.error
from unittest import mock

for _name in ("psycopg2", "psycopg2.pool", "psycopg2.extras"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2.pool"].ThreadedConnectionPool = object
sys.modules["psycopg2.extras"].RealDictCursor = object

import rustmetrics as rm  # noqa: E402

HOST, PORT, BM_ID = "203.0.113.20", 28015, 4242
DETAIL = {"data": {"attributes": {"name": "Test", "status": "online"}},
          "included": [{"type": "player", "id": "7", "attributes": {"name": "Player_7"}}]}


class _Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def time(self) -> float:
        return self.now


def _http_429(retry_after: str | None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.battlemetrics.com/x", 429, "Too Many Requests", headers, None)


class _BMStateTestCase(unittest.TestCase):
    def setUp(self):
        rm._bm_state.update(fails=0, blocked_until=0.0, logged=False, paused_until=0.0)
        rm._bm_id_cache.clear()
        rm._bm_detail_cache.clear()
        self.clock = _Clock()
        patcher = mock.patch.object(rm, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        silence = mock.patch("builtins.print")
        silence.start()
        self.addCleanup(silence.stop)


class BmGetPauseTest(_BMStateTestCase):
    def test_429_pauses_requests_without_tripping_breaker(self):
        with mock.patch.object(rm.urllib.request, "urlopen", side_effect=_http_429("30")) as urlopen:
            with self.assertRaises(urllib.error.HTTPError):
                rm.bm_get("https://api.battlemetrics.com/servers")
            self.clock.now += 10
            with self.assertRaises(rm.BMPaused):
                rm.bm_get("https://api.battlemetrics.com/servers")
            self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(rm.bm_available())

    def test_pause_defaults_to_60_seconds_and_then_ends(self):
        with mock.patch.object(rm.urllib.request, "urlopen", side_effect=_http_429(None)):
            with self.assertRaises(urllib.error.HTTPError):
                rm.bm_get("https://api.battlemetrics.com/servers")
        self.assertAlmostEqual(rm._bm_state["paused_until"], self.clock.now + rm.BM_RATE_LIMIT_PAUSE_SEC)


class GetBmIdBackoffTest(_BMStateTestCase):
    def test_failed_lookup_is_retried_only_after_backoff(self):
        with mock.patch.object(rm, "_bm_search_id", side_effect=RuntimeError("boom")) as search:
            self.assertIsNone(rm.get_bm_id(HOST, PORT))
            self.clock.now += rm.BM_ID_RETRY_AFTER_ERROR_SEC - 1
            self.assertIsNone(rm.get_bm_id(HOST, PORT))
            self.assertEqual(search.call_count, 1)
            self.clock.now += 2
            rm.get_bm_id(HOST, PORT)
            self.assertEqual(search.call_count, 2)

    def test_paused_lookup_is_not_cached(self):
        with mock.patch.object(rm, "_bm_search_id", side_effect=rm.BMPaused("pause")) as search:
            rm.get_bm_id(HOST, PORT)
            rm.get_bm_id(HOST, PORT)
            self.assertEqual(search.call_count, 2)

    def test_found_id_is_cached_for_a_day(self):
        with mock.patch.object(rm, "_bm_search_id", return_value=BM_ID) as search:
            self.assertEqual(rm.get_bm_id(HOST, PORT), BM_ID)
            self.clock.now += rm.BM_ID_CACHE_TTL - 1
            self.assertEqual(rm.get_bm_id(HOST, PORT), BM_ID)
            self.assertEqual(search.call_count, 1)


class QueryBmServerCacheTest(_BMStateTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(rm, "get_bm_id", return_value=BM_ID)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_detail_is_cached_for_detail_ttl(self):
        with mock.patch.object(rm, "bm_get", return_value=DETAIL) as bm_get:
            first = rm.query_bm_server(HOST, PORT)
            self.clock.now += rm.BM_DETAIL_CACHE_TTL - 1
            second = rm.query_bm_server(HOST, PORT)
            self.assertEqual(bm_get.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(first[1], [(7, "Player_7")])
            self.clock.now += 2
            rm.query_bm_server(HOST, PORT)
            self.assertEqual(bm_get.call_count, 2)

    def test_pause_returns_last_known_detail_instead_of_nothing(self):
        with mock.patch.object(rm, "bm_get", return_value=DETAIL):
            rm.query_bm_server(HOST, PORT)
        self.clock.now += rm.BM_DETAIL_CACHE_TTL + 5
        with mock.patch.object(rm, "bm_get", side_effect=rm.BMPaused("pause")):
            attrs, pairs = rm.query_bm_server(HOST, PORT)
        self.assertEqual(attrs.get("name"), "Test")
        self.assertEqual(pairs, [(7, "Player_7")])

    def test_pause_without_cache_returns_empty(self):
        with mock.patch.object(rm, "bm_get", side_effect=rm.BMPaused("pause")):
            self.assertEqual(rm.query_bm_server(HOST, PORT), (None, []))


if __name__ == "__main__":
    unittest.main()
