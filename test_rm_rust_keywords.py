"""Run: python3 -m unittest test_rm_rust_keywords"""

import unittest

from rm_rust import parse_rust_keywords


class ParseRustKeywordsTest(unittest.TestCase):
    def test_reads_numeric_fields_from_standard_keywords(self):
        parsed = parse_rust_keywords("mp200,cp57,qp3,v2580,born1759312800,gmrust,h1a2b,stok,weekly")
        self.assertEqual(parsed["max_players_kw"], 200)
        self.assertEqual(parsed["current_players_kw"], 57)
        self.assertEqual(parsed["queued_kw"], 3)
        self.assertEqual(parsed["version_kw"], 2580)
        self.assertEqual(parsed["born_ts"], 1759312800)
        self.assertEqual(parsed["game_mode"], "rust")
        self.assertTrue(parsed["server_tokenized"])
        self.assertEqual(parsed["tags"], ["h1a2b", "weekly"])

    def test_keeps_tags_that_only_share_a_field_prefix(self):
        parsed = parse_rust_keywords("mp100,mpvp,cpu,qpro,bornagain,monthly")
        self.assertEqual(parsed["max_players_kw"], 100)
        self.assertEqual(parsed["tags"], ["mpvp", "cpu", "qpro", "bornagain", "monthly"])
        self.assertNotIn("current_players_kw", parsed)
        self.assertNotIn("queued_kw", parsed)
        self.assertNotIn("born_ts", parsed)

    def test_returns_empty_tags_for_missing_keywords(self):
        self.assertEqual(parse_rust_keywords(""), {"tags": []})
        self.assertEqual(parse_rust_keywords(None), {"tags": []})


if __name__ == "__main__":
    unittest.main()
