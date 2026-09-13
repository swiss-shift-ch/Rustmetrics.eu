"""Run: python3 -m unittest test_rm_rhythm"""

import unittest

from rm_rhythm import (
    ACTION_CLOSE,
    ACTION_OPEN,
    RHYTHM_DAYS,
    RHYTHM_WINDOW_SEC,
    build_rhythm,
    clip_interval,
    merge_intervals,
    presence_session_actions,
)

NOW = 1_800_000_000
WINDOW_START = NOW - RHYTHM_WINDOW_SEC


class ClipIntervalTest(unittest.TestCase):
    def test_keeps_interval_fully_inside_window(self):
        self.assertEqual(clip_interval(NOW - 7200, NOW - 3600, WINDOW_START, NOW),
                         (NOW - 7200, NOW - 3600))

    def test_cuts_start_before_window(self):
        self.assertEqual(clip_interval(WINDOW_START - 500, WINDOW_START + 100, WINDOW_START, NOW),
                         (WINDOW_START, WINDOW_START + 100))

    def test_treats_open_session_as_ending_now(self):
        self.assertEqual(clip_interval(NOW - 60, None, WINDOW_START, NOW), (NOW - 60, NOW))

    def test_drops_interval_ending_before_window(self):
        self.assertIsNone(clip_interval(WINDOW_START - 900, WINDOW_START - 1, WINDOW_START, NOW))

    def test_drops_interval_starting_in_future(self):
        self.assertIsNone(clip_interval(NOW + 10, None, WINDOW_START, NOW))

    def test_drops_empty_or_inverted_interval(self):
        self.assertIsNone(clip_interval(NOW - 100, NOW - 100, WINDOW_START, NOW))
        self.assertIsNone(clip_interval(NOW - 100, NOW - 200, WINDOW_START, NOW))


class MergeIntervalsTest(unittest.TestCase):
    def test_returns_empty_list_for_no_input(self):
        self.assertEqual(merge_intervals([]), [])

    def test_merges_overlapping_and_touching_intervals(self):
        merged = merge_intervals([(50, 80), (0, 10), (5, 20), (20, 30)])
        self.assertEqual(merged, [(0, 30), (50, 80)])

    def test_merges_contained_interval(self):
        self.assertEqual(merge_intervals([(0, 100), (10, 20)]), [(0, 100)])

    def test_does_not_mutate_input(self):
        source = [(5, 20), (0, 10)]
        merge_intervals(source)
        self.assertEqual(source, [(5, 20), (0, 10)])


class BuildRhythmTest(unittest.TestCase):
    def test_reports_unavailable_without_data(self):
        result = build_rhythm([], [], NOW)
        self.assertEqual(result, {"available": False, "days": RHYTHM_DAYS, "now": NOW,
                                  "intervals": [], "sources": []})

    def test_merges_both_sources_and_lists_contributing_sources(self):
        steam = [(NOW - 7200, NOW - 3600)]
        server = [(NOW - 5400, None)]
        result = build_rhythm(steam, server, NOW)
        self.assertTrue(result["available"])
        self.assertEqual(result["intervals"], [[NOW - 7200, NOW]])
        self.assertEqual(result["sources"], ["steam", "server"])

    def test_ignores_source_whose_rows_are_all_outside_window(self):
        steam = [(WINDOW_START - 9000, WINDOW_START - 8000)]
        server = [(NOW - 600, NOW - 300)]
        result = build_rhythm(steam, server, NOW)
        self.assertEqual(result["sources"], ["server"])
        self.assertEqual(result["intervals"], [[NOW - 600, NOW - 300]])


class PresenceSessionActionsTest(unittest.TestCase):
    def test_opens_session_when_player_enters_rust(self):
        self.assertEqual(presence_session_actions(False, None, True, "1.2.3.4:28015"), (ACTION_OPEN,))

    def test_closes_open_session_when_player_leaves_rust(self):
        self.assertEqual(presence_session_actions(True, "1.2.3.4:28015", False, None), (ACTION_CLOSE,))

    def test_does_nothing_when_offline_without_open_session(self):
        self.assertEqual(presence_session_actions(False, None, False, None), ())

    def test_does_nothing_while_staying_on_same_server(self):
        # Steam liefert den Game-Port, Wechsel nur des Ports ist kein Serverwechsel.
        self.assertEqual(presence_session_actions(True, "1.2.3.4:28015", True, "1.2.3.4:28016"), ())

    def test_closes_and_reopens_on_server_switch(self):
        self.assertEqual(presence_session_actions(True, "1.2.3.4:28015", True, "5.6.7.8:28015"),
                         (ACTION_CLOSE, ACTION_OPEN))

    def test_keeps_session_when_server_address_is_unknown(self):
        self.assertEqual(presence_session_actions(True, "1.2.3.4:28015", True, None), ())

    def test_open_session_without_address_is_treated_as_open(self):
        # Offene Session ohne bekannte Adresse (z.B. Menue/Lobby) -> nicht doppelt oeffnen.
        self.assertEqual(presence_session_actions(True, None, True, "1.2.3.4:28015"), ())


if __name__ == "__main__":
    unittest.main()
