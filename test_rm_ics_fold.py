"""Run: python3 -m unittest test_rm_ics_fold"""

import sys
import types
import unittest

for _name in ("psycopg2", "psycopg2.pool", "psycopg2.extras"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2.pool"].ThreadedConnectionPool = object
sys.modules["psycopg2.extras"].RealDictCursor = object

import rustmetrics as rm  # noqa: E402


def _unfold(lines: list[str]) -> str:
    return lines[0] + "".join(part[1:] for part in lines[1:])


class IcsFoldTest(unittest.TestCase):
    def test_short_line_is_unchanged(self):
        self.assertEqual(rm._ics_fold("SUMMARY:Wipe"), ["SUMMARY:Wipe"])

    def test_ascii_line_folds_to_75_octets_and_unfolds_losslessly(self):
        line = "DESCRIPTION:" + "x" * 200
        parts = rm._ics_fold(line)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-8")), 75)
        for part in parts[1:]:
            self.assertTrue(part.startswith(" "))
        self.assertEqual(_unfold(parts), line)

    def test_multibyte_line_stays_within_75_octets_without_splitting_chars(self):
        line = "SUMMARY:Wipe — " + "Ü🔥 Rust Server Zürich " * 10
        parts = rm._ics_fold(line)
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-8")), 75)
            part.encode("utf-8").decode("utf-8")
        self.assertEqual(_unfold(parts), line)


if __name__ == "__main__":
    unittest.main()
