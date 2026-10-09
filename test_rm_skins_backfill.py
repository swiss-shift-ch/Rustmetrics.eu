"""Tests fuer den Icon-Backfill-Pfad in rm_skins.py — mockt urlopen/DB statt
echtem Netzwerk (Konvention wie test_rm_bm_ratelimit.py). Ergaenzt
test_rm_skins.py (dort bleiben nur reine Parsing-Tests ohne Mocking).
"""
from __future__ import annotations

import email.message
import urllib.error
from unittest import mock

import pytest

import rm_skins


class _FakeResponse:
    """Minimal Stand-in fuer das, was urlopen()'s Context-Manager liefert."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self, n: int | None = None) -> bytes:
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_429() -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", 429, "Too Many Requests", email.message.Message(), None)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Mimics rm_db._Conn's Shape: `with conn_ctx() as (conn, cur):`."""

    def __init__(self, rows):
        self.cursor = _FakeCursor(rows)

    def __enter__(self):
        return (self, self.cursor)

    def __exit__(self, *exc):
        return False


class TestHttpGetMaxBytes:
    def test_raises_when_response_exceeds_cap(self):
        fake = _FakeResponse(b"x" * 100)
        with mock.patch.object(rm_skins.urllib.request, "urlopen", return_value=fake):
            with pytest.raises(RuntimeError):
                rm_skins._http_get("https://example.com", {}, max_bytes=50)

    def test_allows_response_within_cap(self):
        fake = _FakeResponse(b"x" * 50)
        with mock.patch.object(rm_skins.urllib.request, "urlopen", return_value=fake):
            assert rm_skins._http_get("https://example.com", {}, max_bytes=50) == b"x" * 50

    def test_no_cap_means_no_limit_enforced(self):
        fake = _FakeResponse(b"x" * 10_000)
        with mock.patch.object(rm_skins.urllib.request, "urlopen", return_value=fake):
            assert len(rm_skins._http_get("https://example.com", {})) == 10_000


class TestDownloadSkinIcon:
    def test_rejects_non_png_body(self, tmp_path):
        fake = _FakeResponse(b"not a png, just html or garbage")
        with mock.patch.object(rm_skins.urllib.request, "urlopen", return_value=fake):
            with pytest.raises(ValueError):
                rm_skins.download_skin_icon("https://cdn.example/x", str(tmp_path / "out.png"))
        assert not (tmp_path / "out.png").exists()

    def test_writes_valid_png_bytes(self, tmp_path):
        body = rm_skins._PNG_MAGIC + b"...rest-of-a-real-png..."
        fake = _FakeResponse(body)
        dest = tmp_path / "nested" / "out.png"
        with mock.patch.object(rm_skins.urllib.request, "urlopen", return_value=fake):
            rm_skins.download_skin_icon("https://cdn.example/x", str(dest))
        assert dest.read_bytes() == body

    def test_raises_runtime_error_on_429_instead_of_generic_failure(self):
        with mock.patch.object(rm_skins.urllib.request, "urlopen", side_effect=_http_429()):
            with pytest.raises(RuntimeError):
                rm_skins.download_skin_icon("https://cdn.example/x", "/tmp/wont-be-written.png")

    def test_429_raises_the_dedicated_rate_limit_error(self):
        with mock.patch.object(rm_skins.urllib.request, "urlopen", side_effect=_http_429()):
            with pytest.raises(rm_skins.SteamRateLimited):
                rm_skins.download_skin_icon("https://cdn.example/x", "/tmp/wont-be-written.png")

    def test_oversized_response_is_not_reported_as_rate_limit(self):
        fake = _FakeResponse(b"x" * (rm_skins.MAX_ICON_BYTES + 10))
        with mock.patch.object(rm_skins.urllib.request, "urlopen", return_value=fake):
            with pytest.raises(RuntimeError) as excinfo:
                rm_skins.download_skin_icon("https://cdn.example/x", "/tmp/wont-be-written.png")
        assert not isinstance(excinfo.value, rm_skins.SteamRateLimited)


class TestEnrichSkinImageUrls:
    def test_continues_past_a_non_429_error_instead_of_crashing_the_run(self, tmp_path, monkeypatch):
        # Regression test: a single delisted/unreachable item (e.g. a 404,
        # or a network blip) must not crash the whole backfill — otherwise
        # the poison item blocks every alphabetically-later item forever
        # (SELECT is ORDER BY item_id, image_url IS NULL).
        conn = _FakeConn(rows=[{"item_id": "Broken Item"}, {"item_id": "Good Item"}])

        def fake_icon_url(item_id):
            if item_id == "Broken Item":
                raise urllib.error.URLError("network blip")
            return "https://cdn.example/good.png"

        monkeypatch.setattr(rm_skins, "steam_market_icon_url", fake_icon_url)
        monkeypatch.setattr(rm_skins, "download_skin_icon", lambda url, dest: None)
        monkeypatch.setattr(rm_skins.time, "sleep", lambda s: None)

        result = rm_skins.enrich_skin_image_urls(lambda: conn, str(tmp_path), sleep_sec=0)

        assert result == {"updated": 1, "failed": 1, "remaining": 0}
        updates = [p for sql, p in conn.cursor.executed if sql.strip().startswith("UPDATE")]
        assert updates == [("/skins/icons/" + rm_skins.local_icon_filename("Good Item"), "Good Item")]

    def test_stops_immediately_on_rate_limit_leaving_rest_for_next_run(self, tmp_path, monkeypatch):
        conn = _FakeConn(rows=[{"item_id": "A"}, {"item_id": "B"}, {"item_id": "C"}])

        def fake_icon_url(item_id):
            if item_id == "B":
                raise rm_skins.SteamRateLimited("Steam rate limit (429) — Abfrage stoppen")
            return "https://cdn.example/ok.png"

        monkeypatch.setattr(rm_skins, "steam_market_icon_url", fake_icon_url)
        monkeypatch.setattr(rm_skins, "download_skin_icon", lambda url, dest: None)
        monkeypatch.setattr(rm_skins.time, "sleep", lambda s: None)

        result = rm_skins.enrich_skin_image_urls(lambda: conn, str(tmp_path), sleep_sec=0)

        # A succeeded, B hit the rate limit and stopped the run before C was ever tried.
        assert result == {"updated": 1, "failed": 0, "remaining": 2}

    def test_a_429_from_the_cdn_download_also_stops_the_run(self, tmp_path, monkeypatch):
        conn = _FakeConn(rows=[{"item_id": "A"}, {"item_id": "B"}])
        monkeypatch.setattr(rm_skins, "steam_market_icon_url", lambda item_id: "https://cdn.example/x.png")

        def fake_download(url, dest):
            raise rm_skins.SteamRateLimited("Steam rate limit (429) — Abfrage stoppen")

        monkeypatch.setattr(rm_skins, "download_skin_icon", fake_download)
        monkeypatch.setattr(rm_skins.time, "sleep", lambda s: None)

        result = rm_skins.enrich_skin_image_urls(lambda: conn, str(tmp_path), sleep_sec=0)

        assert result == {"updated": 0, "failed": 0, "remaining": 2}

    def test_oversized_listing_page_counts_as_failed_and_does_not_stop_the_run(self, tmp_path, monkeypatch):
        # Regression test: _http_get() raises a plain RuntimeError for a response over
        # the size cap. That used to be mistaken for a 429 and stopped the run, so the
        # same item blocked every later one on each rerun (ORDER BY item_id).
        conn = _FakeConn(rows=[{"item_id": "Huge Listing"}, {"item_id": "Good Item"}])

        def fake_icon_url(item_id):
            if item_id == "Huge Listing":
                raise RuntimeError("Antwort zu gross (> 2097152 Bytes): https://x")
            return "https://cdn.example/good.png"

        monkeypatch.setattr(rm_skins, "steam_market_icon_url", fake_icon_url)
        monkeypatch.setattr(rm_skins, "download_skin_icon", lambda url, dest: None)
        monkeypatch.setattr(rm_skins.time, "sleep", lambda s: None)

        result = rm_skins.enrich_skin_image_urls(lambda: conn, str(tmp_path), sleep_sec=0)

        assert result == {"updated": 1, "failed": 1, "remaining": 0}

    def test_oversized_icon_download_counts_as_failed_and_does_not_stop_the_run(self, tmp_path, monkeypatch):
        conn = _FakeConn(rows=[{"item_id": "Huge Icon"}, {"item_id": "Good Item"}])
        monkeypatch.setattr(rm_skins, "steam_market_icon_url", lambda item_id: "https://cdn.example/x.png")

        def fake_download(url, dest):
            if dest.endswith(rm_skins.local_icon_filename("Huge Icon")):
                raise RuntimeError("Antwort zu gross (> 1048576 Bytes): https://cdn.example/x.png")

        monkeypatch.setattr(rm_skins, "download_skin_icon", fake_download)
        monkeypatch.setattr(rm_skins.time, "sleep", lambda s: None)

        result = rm_skins.enrich_skin_image_urls(lambda: conn, str(tmp_path), sleep_sec=0)

        assert result == {"updated": 1, "failed": 1, "remaining": 0}
