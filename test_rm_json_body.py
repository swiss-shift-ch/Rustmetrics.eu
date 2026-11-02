"""Request-Bodies: nur JSON-Objekte zaehlen, Einstellungen bleiben bei unlesbarem Body unveraendert."""
import io
import json
import unittest
from unittest import mock

import rustmetrics


def stub_handler(raw: bytes) -> mock.Mock:
    handler = mock.Mock(spec=rustmetrics.Handler)
    handler.headers = {"Content-Length": str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    handler.MAX_BODY_BYTES = rustmetrics.Handler.MAX_BODY_BYTES
    handler._read_json = lambda: rustmetrics.Handler._read_json(handler)
    return handler


class ReadJsonTest(unittest.TestCase):
    def test_object_is_returned(self):
        self.assertEqual(stub_handler(b'{"a": 1}')._read_json(), {"a": 1})

    def test_non_object_json_gives_empty_dict(self):
        for raw in (b"[]", b"[1, 2]", b"null", b"1", b'"text"', b"true"):
            with self.subTest(raw=raw):
                self.assertEqual(stub_handler(raw)._read_json(), {})

    def test_invalid_json_gives_empty_dict(self):
        self.assertEqual(stub_handler(b"{kaputt")._read_json(), {})


class SaveSettingsTest(unittest.TestCase):
    def save(self, raw: bytes):
        handler = stub_handler(raw)
        with mock.patch.object(rustmetrics, "_Conn") as conn:
            conn.return_value.__enter__.return_value = (mock.Mock(), mock.Mock())
            rustmetrics.Handler._api_save_settings(handler, 7)
        return handler, conn

    def test_unreadable_body_changes_nothing(self):
        for raw in (b"", b"{kaputt", b"[]", b"null", b"{}"):
            with self.subTest(raw=raw):
                handler, conn = self.save(raw)
                conn.assert_not_called()
                self.assertEqual(handler._json.call_args.args[1], 400)

    def test_non_string_webhook_is_rejected(self):
        handler, conn = self.save(b'{"discord_webhook": 5}')
        conn.assert_not_called()
        self.assertEqual(handler._json.call_args.args[1], 400)

    def test_null_webhook_clears_it(self):
        handler, conn = self.save(b'{"discord_webhook": null}')
        conn.assert_called_once()
        handler._json.assert_called_once_with({"ok": True})

    def test_empty_webhook_clears_it(self):
        handler, conn = self.save(json.dumps({"discord_webhook": "  "}).encode())
        conn.assert_called_once()
        handler._json.assert_called_once_with({"ok": True})


class PostFieldTest(unittest.TestCase):
    def post(self, path: str, raw: bytes) -> mock.Mock:
        handler = stub_handler(raw)
        handler.path = path
        handler._client_ip.return_value = "203.0.113.1"
        handler._origin_ok.return_value = True
        handler._read_session.return_value = {"user_id": 7}
        with mock.patch.object(rustmetrics, "rate_limit_general", return_value=True):
            rustmetrics.Handler.do_POST(handler)
        return handler

    def test_non_string_host_gives_400(self):
        handler = self.post("/api/servers", b'{"host": 5, "port": 28015}')
        self.assertEqual(handler._json.call_args.args, ({"error": "host and port required"}, 400))

    def test_non_string_name_gives_400(self):
        handler = self.post("/api/server/1/watch", b'{"name": ["a"]}')
        self.assertEqual(handler._json.call_args.args, ({"error": "name required"}, 400))

    def test_non_object_body_gives_400(self):
        handler = self.post("/api/servers", b"[]")
        self.assertEqual(handler._json.call_args.args[1], 400)


if __name__ == "__main__":
    unittest.main()
