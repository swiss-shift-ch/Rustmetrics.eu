"""Run: python3 -m unittest test_rm_seo"""

import os
import unittest

import re

from rm_seo import (BROWSER_ROWS_MAX, browser_rows_html, canonical_redirect_location, parse_server_path,
                    render_browser_page)

ORIGIN = "https://rustmetrics.eu"
INDEX_PATH = os.path.join(os.path.dirname(__file__), "static", "index.html")


def _server(n: int, pop: int, **extra) -> dict:
    return {"host": f"203.0.113.{n}", "port": 28015, "name": f"Server {n}",
            "map": "Procedural Map", "players_count": pop, "max_players": 200, **extra}


class CanonicalRedirectTest(unittest.TestCase):
    def test_redirects_www_host_to_origin_keeping_path_and_query(self):
        self.assertEqual(
            canonical_redirect_location("www.rustmetrics.eu", "/server/1.2.3.4:28015?x=1", ORIGIN),
            "https://rustmetrics.eu/server/1.2.3.4:28015?x=1",
        )

    def test_redirects_www_host_with_port_and_mixed_case(self):
        self.assertEqual(canonical_redirect_location("WWW.rustmetrics.eu:443", "/", ORIGIN), "https://rustmetrics.eu/")

    def test_no_redirect_for_canonical_or_missing_host(self):
        self.assertIsNone(canonical_redirect_location("rustmetrics.eu", "/", ORIGIN))
        self.assertIsNone(canonical_redirect_location(None, "/", ORIGIN))
        self.assertIsNone(canonical_redirect_location("localhost:8765", "/", ORIGIN))


class BrowserRowsTest(unittest.TestCase):
    def test_rows_link_to_server_pages_sorted_by_population(self):
        html = browser_rows_html([_server(1, 5), _server(2, 90)])
        self.assertLess(html.index("/server/203.0.113.2:28015"), html.index("/server/203.0.113.1:28015"))

    def test_escapes_server_names_and_skips_entries_without_address(self):
        html = browser_rows_html([_server(1, 5, name="<script>x</script>"), {"name": "no address"}])
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("no address", html)

    def test_caps_row_count(self):
        html = browser_rows_html([_server(i % 250, i) for i in range(BROWSER_ROWS_MAX + 50)])
        self.assertEqual(html.count('class="browse-row"'), BROWSER_ROWS_MAX)


class ParseServerPathTest(unittest.TestCase):
    def test_plain_ipv4_and_hostname(self):
        self.assertEqual(parse_server_path("1.2.3.4:28015"), ("1.2.3.4", 28015))
        self.assertEqual(parse_server_path("eu.example-rust.com:28015/"), ("eu.example-rust.com", 28015))

    def test_decodes_percent_encoded_host_from_browser_links(self):
        self.assertEqual(parse_server_path("2001%3Adb8%3A%3A1:28015"), ("2001:db8::1", 28015))

    def test_round_trip_with_browser_row_link(self):
        html = browser_rows_html([{"host": "2001:db8::7", "port": 28016, "name": "v6", "players_count": 1}])
        spec = re.search(r'href="/server/([^"]+)"', html).group(1)
        self.assertEqual(parse_server_path(spec), ("2001:db8::7", 28016))

    def test_rejects_malformed_specs(self):
        for spec in ("", "1.2.3.4", ":28015", "1.2.3.4:", "1.2.3.4:abc", "1.2.3.4:0", "1.2.3.4:65536",
                     "1.2.3.4:+28015", "1.2.3.4:\u00b2", "bad%00host:28015", "a%20b:28015",
                     "x" * 254 + ":28015"):
            self.assertIsNone(parse_server_path(spec), spec)


class RenderBrowserPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INDEX_PATH, encoding="utf-8") as f:
            cls.index_html = f.read()

    def test_real_index_renders_browser_view_with_own_metadata(self):
        html = render_browser_page(self.index_html, [_server(7, 42)], ORIGIN)
        self.assertIn("<title>Rust Server Browser", html)
        self.assertIn('<link rel="canonical" href="https://rustmetrics.eu/browser" />', html)
        self.assertIn('<section class="view view-browse is-active" data-view="browse">', html)
        self.assertIn('<section class="view view-watchlist" data-view="watchlist" hidden>', html)
        self.assertIn('href="/server/203.0.113.7:28015"', html)
        self.assertEqual(html.count("<h1"), 1)

    def test_raises_when_index_markup_drifted(self):
        with self.assertRaises(ValueError):
            render_browser_page("<html></html>", [], ORIGIN)


if __name__ == "__main__":
    unittest.main()
