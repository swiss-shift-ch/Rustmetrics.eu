"""Run: python3 -m unittest test_rm_openid"""

import unittest

from rm_openid import openid_precheck, steamid_from_claimed_id

ORIGIN = "https://rustmetrics.eu"
RETURN_TO = f"{ORIGIN}/auth/steam/callback"
CLAIMED = "https://steamcommunity.com/openid/id/76561198000000001"


def _query(**overrides):
    q = {
        "openid.mode": "id_res",
        "openid.claimed_id": CLAIMED,
        "openid.return_to": RETURN_TO,
        "openid.response_nonce": "2026-11-22T06:00:00ZabcDEF",
    }
    q.update(overrides)
    return q


class SteamIdFromClaimedIdTest(unittest.TestCase):
    def test_accepts_plain_steam_identity(self):
        self.assertEqual(steamid_from_claimed_id(CLAIMED), 76561198000000001)

    def test_rejects_trailing_newline(self):
        # re.match + "$" liess ein abschliessendes "\n" durch.
        self.assertIsNone(steamid_from_claimed_id(CLAIMED + "\n"))

    def test_rejects_non_ascii_digits(self):
        # \d passt auch auf arabisch-indische Ziffern, int() wandelt sie still um.
        self.assertIsNone(steamid_from_claimed_id("https://steamcommunity.com/openid/id/٧٦٥"))

    def test_rejects_foreign_host(self):
        self.assertIsNone(steamid_from_claimed_id("https://evil.example/openid/id/7656119"))

    def test_rejects_missing_value(self):
        self.assertIsNone(steamid_from_claimed_id(None))


class OpenIdPrecheckTest(unittest.TestCase):
    def test_valid_response_yields_steamid(self):
        self.assertEqual(openid_precheck(_query(), ORIGIN), 76561198000000001)

    def test_requires_id_res_mode(self):
        self.assertIsNone(openid_precheck(_query(**{"openid.mode": "cancel"}), ORIGIN))

    def test_requires_return_to_on_our_origin(self):
        q = _query(**{"openid.return_to": "https://evil.example/auth/steam/callback"})
        self.assertIsNone(openid_precheck(q, ORIGIN))

    def test_requires_nonce(self):
        # Ohne Nonce griff der Replay-Schutz bisher gar nicht.
        q = _query()
        del q["openid.response_nonce"]
        self.assertIsNone(openid_precheck(q, ORIGIN))

    def test_rejects_empty_nonce(self):
        self.assertIsNone(openid_precheck(_query(**{"openid.response_nonce": ""}), ORIGIN))


if __name__ == "__main__":
    unittest.main()
