"""Run: python3 -m unittest test_rm_activity"""

import unittest

from rm_activity import LAST_SEEN_TOUCH_INTERVAL_SEC, should_touch_last_seen

NOW = 1_800_000_000


class ShouldTouchLastSeenTest(unittest.TestCase):
    def test_touches_when_user_was_never_seen(self):
        self.assertTrue(should_touch_last_seen(None, NOW))

    def test_skips_write_within_interval(self):
        self.assertFalse(should_touch_last_seen(NOW - LAST_SEEN_TOUCH_INTERVAL_SEC + 1, NOW))

    def test_touches_once_interval_has_elapsed(self):
        self.assertTrue(should_touch_last_seen(NOW - LAST_SEEN_TOUCH_INTERVAL_SEC, NOW))

    def test_touches_when_stored_value_lies_in_the_future(self):
        # Nach einer Uhrkorrektur darf ein Zukunftswert das Tracking nicht dauerhaft blockieren.
        self.assertTrue(should_touch_last_seen(NOW + 86400, NOW))


if __name__ == "__main__":
    unittest.main()
