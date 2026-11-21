"""Run: python3 -m unittest test_rm_discord_webhook"""

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

BASE = "https://discord.com/api/webhooks/123456789/abc_DEF-123"


class DiscordWebhookTest(unittest.TestCase):
    def test_accepts_valid_urls(self):
        for url in (BASE, BASE + "/",
                    "https://ptb.discord.com/api/webhooks/1/x",
                    "https://canary.discordapp.com/api/webhooks/1/x"):
            with self.subTest(url=url):
                self.assertTrue(rm.is_valid_discord_webhook(url))

    def test_rejects_trailing_newline(self):
        # re `$` passt auch vor einem abschliessenden "\n" – fullmatch nicht
        self.assertFalse(rm.is_valid_discord_webhook(BASE + "\n"))

    def test_rejects_other_hosts_and_empty(self):
        for url in ("", None, "http://discord.com/api/webhooks/1/x",
                    "https://discord.com.evil.example/api/webhooks/1/x",
                    "https://discord.com/api/webhooks/1/x?wait=true",
                    "https://discord.com/api/webhooks/abc/x"):
            with self.subTest(url=url):
                self.assertFalse(rm.is_valid_discord_webhook(url))


if __name__ == "__main__":
    unittest.main()
