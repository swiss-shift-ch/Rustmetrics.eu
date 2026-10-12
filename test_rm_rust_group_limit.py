"""Run: python3 -m unittest test_rm_rust_group_limit"""

import unittest

from rm_rust import detect_rust_group_limit


class ExplicitLimitTest(unittest.TestCase):
    def test_max_group_number_beats_group_words(self):
        self.assertEqual(detect_rust_group_limit("Vital EU Trio - Max Group 4", []), 4)

    def test_team_limit_number(self):
        self.assertEqual(detect_rust_group_limit("Chill server team limit 3", []), 3)

    def test_number_at_end_of_sentence_still_counts(self):
        self.assertEqual(detect_rust_group_limit("Friendly PvE. Max 4. Weekly", []), 4)

    def test_no_group_limit_is_zero(self):
        self.assertEqual(detect_rust_group_limit("Mega Clans no group limit", []), 0)


class FractionalRateTest(unittest.TestCase):
    def test_fractional_rate_after_team_is_not_a_limit(self):
        self.assertIsNone(detect_rust_group_limit("Rusty Team 2.5x Monthly", []))

    def test_fractional_rate_after_max_is_not_a_limit(self):
        self.assertIsNone(detect_rust_group_limit("EU Max 1.5x Gather", []))

    def test_comma_fraction_is_not_a_limit(self):
        self.assertIsNone(detect_rust_group_limit("DE Clan 2,5x Wipe Freitag", []))

    def test_group_word_still_wins_next_to_fractional_rate(self):
        self.assertEqual(detect_rust_group_limit("Team 2.5x | Solo/Duo/Trio", []), 3)

    def test_whole_rate_is_not_a_limit(self):
        self.assertIsNone(detect_rust_group_limit("EU 3x Monthly", []))


if __name__ == "__main__":
    unittest.main()
