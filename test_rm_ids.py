"""Run: python3 -m unittest test_rm_ids"""

import unittest

from rm_ids import MAX_PG_INT, parse_pg_id


class ParsePgIdTest(unittest.TestCase):
    def test_accepts_plain_positive_id(self):
        self.assertEqual(parse_pg_id("42"), 42)

    def test_accepts_largest_serial_value(self):
        self.assertEqual(parse_pg_id(str(MAX_PG_INT)), MAX_PG_INT)

    def test_rejects_value_beyond_serial_range(self):
        # Vorher: int() ok, Postgres «integer out of range» ausserhalb try → Verbindungsabbruch
        self.assertIsNone(parse_pg_id(str(MAX_PG_INT + 1)))
        self.assertIsNone(parse_pg_id("9" * 30))

    def test_rejects_zero_and_negative(self):
        self.assertIsNone(parse_pg_id("0"))
        self.assertIsNone(parse_pg_id("-5"))

    def test_rejects_forms_int_would_accept(self):
        for raw in ("+5", " 5", "5 ", "1_0", "٣", "5\n"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_pg_id(raw))

    def test_rejects_non_numeric_and_empty(self):
        for raw in ("", "abc", "1.5", None):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_pg_id(raw))


if __name__ == "__main__":
    unittest.main()
