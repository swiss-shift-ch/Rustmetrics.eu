"""Run: python3 -m unittest test_rm_params"""

import unittest

from rm_params import int_param


class IntParamTest(unittest.TestCase):
    def test_parses_value_inside_bounds(self):
        self.assertEqual(int_param("50", 200, 1, 500), 50)

    def test_missing_or_empty_gives_default(self):
        self.assertEqual(int_param(None, 200, 1, 500), 200)
        self.assertEqual(int_param("", 200, 1, 500), 200)
        self.assertEqual(int_param("  ", 200, 1, 500), 200)

    def test_negative_limit_is_raised_to_lower_bound(self):
        self.assertEqual(int_param("-1", 200, 1, 500), 1)

    def test_zero_limit_is_raised_to_lower_bound(self):
        self.assertEqual(int_param("0", 200, 1, 500), 1)

    def test_large_value_is_capped(self):
        self.assertEqual(int_param("99999999", 200, 1, 500), 500)

    def test_default_is_clamped_too(self):
        self.assertEqual(int_param(None, 9999, 0, 500), 500)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(int_param(" 7 ", 0, 0, 720), 7)

    def test_non_integer_raises_value_error(self):
        for raw in ("abc", "1.5", "1e3", "0x10"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                int_param(raw, 0, 0, 100)


if __name__ == "__main__":
    unittest.main()
