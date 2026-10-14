"""Run: python3 -m unittest test_rm_rust_category"""

import unittest

from rm_rust import detect_rust_category


class ModHintWordBoundaryTest(unittest.TestCase):
    def test_no_kits_is_not_modded(self):
        self.assertEqual(detect_rust_category("EU Vanilla+ | No Kits | 2x", []), "community")

    def test_workshop_is_not_shop(self):
        self.assertEqual(detect_rust_category("Rustafied Workshop Skins 2x", []), "community")

    def test_kits_still_modded(self):
        self.assertEqual(detect_rust_category("EU 2x | Kits | Shop", []), "modded")

    def test_kitpvp_still_modded(self):
        self.assertEqual(detect_rust_category("KitPvP Arena 2x", []), "modded")

    def test_slash_shop_still_modded(self):
        self.assertEqual(detect_rust_category("Main 3x /shop /tpr", []), "modded")

    def test_no_kits_but_shop_is_modded(self):
        self.assertEqual(detect_rust_category("2x No Kits Shop", []), "modded")


if __name__ == "__main__":
    unittest.main()
