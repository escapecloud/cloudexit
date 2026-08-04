import tempfile
import unittest
from pathlib import Path

from core.utils import copy_assets

SOURCE_ICONS = Path("assets/icons")


def staged(cloud_service_provider):
    report_path = tempfile.mkdtemp()
    copy_assets(report_path, cloud_service_provider)
    return Path(report_path)


def icon_dirs(report_path):
    return {p.name for p in (report_path / "assets" / "icons").iterdir() if p.is_dir()}


def relative_pngs(root):
    return {p.relative_to(root) for p in root.rglob("*.png")}


class CopyAssetsProviderScopeTests(unittest.TestCase):
    def test_azure_assessment_copies_only_azure_icons(self):
        report_path = staged(1)

        self.assertEqual(icon_dirs(report_path), {"azure", "severity", "misc"})

    def test_aws_assessment_copies_only_aws_icons(self):
        report_path = staged(2)

        self.assertEqual(icon_dirs(report_path), {"aws", "severity", "misc"})

    def test_unknown_provider_falls_back_to_every_icon_set(self):
        report_path = staged(99)

        self.assertEqual(icon_dirs(report_path), {"azure", "aws", "severity", "misc"})

    def test_provider_icon_set_is_copied_in_full(self):
        # The filter must drop non-PNG files only, never an icon.
        for cloud_service_provider, provider in ((1, "azure"), (2, "aws")):
            with self.subTest(provider=provider):
                report_path = staged(cloud_service_provider)

                self.assertEqual(
                    relative_pngs(report_path / "assets" / "icons" / provider),
                    relative_pngs(SOURCE_ICONS / provider),
                )


class CopyAssetsFileTypeTests(unittest.TestCase):
    def test_no_svg_is_copied(self):
        for cloud_service_provider in (1, 2):
            with self.subTest(cloud_service_provider=cloud_service_provider):
                report_path = staged(cloud_service_provider)

                self.assertEqual(
                    list((report_path / "assets" / "icons").rglob("*.svg")), []
                )

    def test_only_png_files_are_copied(self):
        report_path = staged(1)

        non_png = [
            p
            for p in (report_path / "assets" / "icons").rglob("*")
            if p.is_file() and p.suffix.lower() != ".png"
        ]

        self.assertEqual(non_png, [])

    def test_category_subfolders_are_preserved(self):
        report_path = staged(2)
        copied = report_path / "assets" / "icons" / "aws"

        self.assertEqual(
            {p.name for p in copied.iterdir() if p.is_dir()},
            {p.name for p in (SOURCE_ICONS / "aws").iterdir() if p.is_dir()},
        )


class CopyAssetsUnrelatedAssetTests(unittest.TestCase):
    def test_css_img_and_assessment_db_are_still_copied(self):
        report_path = staged(1)

        self.assertTrue((report_path / "assets" / "css").is_dir())
        self.assertTrue((report_path / "assets" / "img").is_dir())
        self.assertTrue((report_path / "data" / "assessment.db").is_file())

    def test_shared_icons_are_copied_for_every_provider(self):
        # no_image.png is the fallback icon and the severity icons are used by
        # the PDF renderer, so both must travel with any report.
        for cloud_service_provider in (1, 2):
            with self.subTest(cloud_service_provider=cloud_service_provider):
                icons = staged(cloud_service_provider) / "assets" / "icons"

                self.assertTrue((icons / "misc" / "no_image.png").is_file())
                for severity in ("high", "medium", "low"):
                    self.assertTrue((icons / "severity" / f"{severity}.png").is_file())


if __name__ == "__main__":
    unittest.main()
