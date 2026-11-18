"""Run: python3 -m unittest test_rm_ics"""

import time
import unittest

from rm_ics import BORN_FUTURE_TOLERANCE_SEC, ics_wipe_base_ts

NOW = 1_795_000_000  # Nov 2026


class IcsWipeBaseTest(unittest.TestCase):
    def test_recent_born_is_used(self):
        born = NOW - 3 * 86400
        self.assertEqual(ics_wipe_base_ts(born, NOW), born)

    def test_missing_or_zero_born_is_skipped(self):
        self.assertIsNone(ics_wipe_base_ts(None, NOW))
        self.assertIsNone(ics_wipe_base_ts(0, NOW))

    def test_negative_born_is_skipped(self):
        self.assertIsNone(ics_wipe_base_ts(-5, NOW))

    def test_server_clock_slightly_ahead_is_tolerated(self):
        born = NOW + 600
        self.assertEqual(ics_wipe_base_ts(born, NOW), born)

    def test_born_far_in_future_is_skipped(self):
        self.assertIsNone(ics_wipe_base_ts(NOW + BORN_FUTURE_TOLERANCE_SEC + 1, NOW))

    def test_absurd_born_from_server_keywords_is_skipped(self):
        # «born99999999999999999999» in a server's keywords made time.gmtime raise
        # OverflowError and broke the whole wipe calendar of every watcher.
        self.assertIsNone(ics_wipe_base_ts(10**20, NOW))

    def test_used_value_and_estimates_are_formattable(self):
        born = ics_wipe_base_ts(NOW - 86400, NOW)
        for i in range(5):
            time.gmtime(born + i * 7 * 86400 + 3600)


if __name__ == "__main__":
    unittest.main()
