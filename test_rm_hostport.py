"""Run: python3 -m unittest test_rm_hostport"""

import unittest

from rm_hostport import parse_port, split_host_port


class SplitHostPortTest(unittest.TestCase):
    def test_port_in_text_wins_over_form_field(self):
        self.assertEqual(split_host_port("79.137.98.10:28015", 79), ("79.137.98.10", 28015))

    def test_host_without_port_keeps_form_port(self):
        self.assertEqual(split_host_port("eu.moose.gg", 28016), ("eu.moose.gg", 28016))

    def test_trailing_slash_from_copied_link(self):
        # Previously "1.2.3.4:28015/" kept the colon in the host -> "host does not resolve".
        self.assertEqual(split_host_port("1.2.3.4:28015/", 28015), ("1.2.3.4", 28015))
        self.assertEqual(split_host_port("/eu.moose.gg/ ", 28015), ("eu.moose.gg", 28015))

    def test_non_ascii_digit_port_is_not_taken(self):
        # "²".isdigit() is True but int("²") raises -> cryptic error for the user.
        self.assertEqual(split_host_port("1.2.3.4:²", 28015), ("1.2.3.4:²", 28015))

    def test_out_of_range_or_empty_port_is_not_taken(self):
        for text in ("1.2.3.4:0", "1.2.3.4:65536", "1.2.3.4:", ":28015"):
            self.assertEqual(split_host_port(text, 28015), (text, 28015))

    def test_bare_ipv6_is_left_alone(self):
        self.assertEqual(split_host_port("2a01:4f8::1", 28015), ("2a01:4f8::1", 28015))


class ParsePortTest(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertEqual(parse_port("28015"), 28015)
        self.assertEqual(parse_port("65535"), 65535)
        for text in ("", "0", "65536", "-1", "+1", " 1", "²", "١٢"):
            self.assertIsNone(parse_port(text), text)


if __name__ == "__main__":
    unittest.main()
