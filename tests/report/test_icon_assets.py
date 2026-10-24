import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.resources import asset_path
from core.utils_report_egress import FALLBACK_ICONS
from tests.make_smoke_dataset import main as make_smoke_dataset


def icon_file(icon):
    return asset_path(*icon.lstrip("/").split("/"))


class IconAssetTests(unittest.TestCase):
    def dataset_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smoke.db"
            make_smoke_dataset(str(path))
            conn = sqlite3.connect(path)
            try:
                return [row[0] for row in conn.execute("SELECT icon FROM resourcetype")]
            finally:
                conn.close()

    def test_every_png_icon_a_dataset_points_at_is_in_the_package(self):
        icons = [icon for icon in self.dataset_icons() if icon.endswith(".png")]

        self.assertTrue(icons)
        for icon in icons:
            with self.subTest(icon=icon):
                self.assertTrue(icon_file(icon).is_file())

    def test_the_icons_the_egress_report_hardcodes_are_in_the_package(self):
        for code, icon in FALLBACK_ICONS.items():
            with self.subTest(code=code):
                self.assertTrue(icon_file(icon).is_file())

    def test_the_dataset_points_at_png_icons_only(self):
        for icon in self.dataset_icons():
            self.assertTrue(not icon or icon.endswith(".png"), icon)

    def test_no_svg_icon_is_shipped(self):
        self.assertEqual(sorted(asset_path("icons").rglob("*.svg")), [])

    def test_every_provider_icon_folder_holds_png_files(self):
        for provider in ("aws", "azure"):
            with self.subTest(provider=provider):
                icons = list(asset_path("icons", provider).rglob("*.png"))
                self.assertGreater(len(icons), 300)

    def test_the_assets_nothing_uses_are_not_shipped(self):
        self.assertFalse(asset_path("img", "logo", "favicon.ico").exists())
        self.assertFalse(asset_path("fonts", "IBMPlexSans-Medium.ttf").exists())

    def test_the_icon_licence_notice_stays(self):
        notice = asset_path("icons", "README.md").read_text(encoding="utf-8")
        self.assertIn("NOT covered by this project's LICENSE", notice)


if __name__ == "__main__":
    unittest.main()
