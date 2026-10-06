"""Run: python3 -m unittest test_rm_a2s_query"""

import socket
import struct
import threading
import unittest

from rm_a2s import query_a2s

_INFO_PACKET = (
    b"\xFF\xFF\xFF\xFFI" + bytes([17])
    + b"Test Server\x00Procedural Map\x00rust\x00Rust\x00"
    + struct.pack("<H", 0) + bytes([5, 50, 0]) + b"dl" + bytes([0, 1]) + b"2260\x00"
)


def _serve_once(reply: bytes) -> tuple[str, int, threading.Thread]:
    """Lokaler UDP-Server, der auf die erste Anfrage mit `reply` antwortet."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(2)

    def run():
        try:
            _, addr = srv.recvfrom(2048)
            srv.sendto(reply, addr)
        except OSError:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    host, port = srv.getsockname()
    return host, port, t


class QueryA2SInfoTest(unittest.TestCase):
    def _query(self, reply: bytes) -> dict:
        host, port, t = _serve_once(reply)
        result = query_a2s(host, port, timeout=1, include_players=False)
        t.join(2)
        return result

    def test_valid_info_reply_is_online(self):
        result = self._query(_INFO_PACKET)
        self.assertTrue(result["online"])
        self.assertEqual(result["name"], "Test Server")
        self.assertNotIn("error", result)

    def test_reply_that_is_not_an_info_packet_is_not_online(self):
        result = self._query(b"\xFF\xFF\xFF\xFFXgarbage")
        self.assertFalse(result["online"])
        self.assertEqual(result["error"], "invalid response")

    def test_too_short_reply_is_not_online(self):
        result = self._query(b"\xFF\xFF")
        self.assertFalse(result["online"])
        self.assertEqual(result["error"], "invalid response")


if __name__ == "__main__":
    unittest.main()
