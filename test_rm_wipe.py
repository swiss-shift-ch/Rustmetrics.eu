"""Run: python3 -m unittest test_rm_wipe"""

import unittest

from rm_rust import next_wipe_estimate

NOW = 1_792_656_000
DAY = 86400
WEEK = 7 * DAY


class NextWipeEstimateTest(unittest.TestCase):
    def test_missing_born_gives_none(self):
        self.assertIsNone(next_wipe_estimate(None, now=NOW))
        self.assertIsNone(next_wipe_estimate(0, now=NOW))

    def test_next_wipe_one_week_after_recent_wipe(self):
        born = NOW - 2 * DAY
        w = next_wipe_estimate(born, now=NOW)
        self.assertEqual(w["last_wipe_ts"], born)
        self.assertEqual(w["next_wipe_estimate_ts"], born + WEEK)
        self.assertEqual(w["days_until_next"], 5)

    def test_skips_whole_weeks_since_old_wipe(self):
        born = NOW - 3 * WEEK - DAY
        w = next_wipe_estimate(born, now=NOW)
        self.assertEqual(w["next_wipe_estimate_ts"], born + 4 * WEEK)
        self.assertEqual(w["days_until_next"], 6)

    def test_born_slightly_in_future_counts_as_just_wiped(self):
        # Serveruhr geht vor: frisch gewipter Server meldet born ein paar Minuten in der Zukunft.
        born = NOW + 300
        w = next_wipe_estimate(born, now=NOW)
        self.assertEqual(w["last_wipe_ts"], born)
        self.assertEqual(w["next_wipe_estimate_ts"], born + WEEK)
        self.assertEqual(w["days_until_next"], 7)

    def test_born_far_in_future_is_implausible(self):
        self.assertIsNone(next_wipe_estimate(NOW + 2 * DAY, now=NOW))
        self.assertIsNone(next_wipe_estimate(NOW * 1000, now=NOW))  # Millisekunden statt Sekunden

    def test_negative_born_is_implausible(self):
        self.assertIsNone(next_wipe_estimate(-5, now=NOW))


if __name__ == "__main__":
    unittest.main()
