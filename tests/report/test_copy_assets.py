import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from cloudexit.resources import asset_path
from cloudexit.report.assets import copy_assets
from cloudexit.catalogue.store import active_catalogue_path

SOURCE_ICONS = asset_path("icons")


@contextmanager
def staged(cloud_service_provider):
    # The catalogue is downloaded at runtime and is absent in CI, so the
    # assessment.db copy is stubbed out -- it isn't what these tests cover.
    # Replace the module binding inside cloudexit.report.assets rather than the attribute
    # on shutil itself: copytree calls copyfile internally, so patching it
    # globally would break the asset copy this module is testing.
    fake_shutil = mock.MagicMock(wraps=shutil)
    # What the real copy leaves behind is a database; an empty one will do
    fake_shutil.copyfile = mock.MagicMock(
        side_effect=lambda source, target: sqlite3.connect(target).close()
    )

    with tempfile.TemporaryDirectory() as report_dir:
        with mock.patch("cloudexit.report.assets.shutil", fake_shutil):
            copy_assets(report_dir, cloud_service_provider)

        yield Path(report_dir), fake_shutil.copyfile


def icon_dirs(report_path):
    return {p.name for p in (report_path / "assets" / "icons").iterdir() if p.is_dir()}


def relative_pngs(root):
    return {p.relative_to(root) for p in root.rglob("*.png")}


class CopyAssetsProviderScopeTests(unittest.TestCase):
    def test_azure_assessment_copies_only_azure_icons(self):
        with staged(1) as (report_path, _):
            self.assertEqual(icon_dirs(report_path), {"azure"})

    def test_aws_assessment_copies_only_aws_icons(self):
        with staged(2) as (report_path, _):
            self.assertEqual(icon_dirs(report_path), {"aws"})

    def test_unknown_provider_falls_back_to_every_icon_set(self):
        with staged(99) as (report_path, _):
            self.assertEqual(icon_dirs(report_path), {"azure", "aws"})

    def test_provider_icon_set_is_copied_in_full(self):
        # The filter must drop non-PNG files only, never an icon.
        for cloud_service_provider, provider in ((1, "azure"), (2, "aws")):
            with self.subTest(provider=provider):
                with staged(cloud_service_provider) as (report_path, _):
                    self.assertEqual(
                        relative_pngs(report_path / "assets" / "icons" / provider),
                        relative_pngs(SOURCE_ICONS / provider),
                    )


class CopyAssetsFileTypeTests(unittest.TestCase):
    def test_no_svg_is_copied(self):
        for cloud_service_provider in (1, 2):
            with self.subTest(cloud_service_provider=cloud_service_provider):
                with staged(cloud_service_provider) as (report_path, _):
                    self.assertEqual(
                        list((report_path / "assets" / "icons").rglob("*.svg")), []
                    )

    def test_only_png_files_are_copied(self):
        with staged(1) as (report_path, _):
            non_png = [
                p
                for p in (report_path / "assets" / "icons").rglob("*")
                if p.is_file() and p.suffix.lower() != ".png"
            ]

            self.assertEqual(non_png, [])

    def test_category_subfolders_are_preserved(self):
        with staged(2) as (report_path, _):
            copied = report_path / "assets" / "icons" / "aws"

            self.assertEqual(
                {p.name for p in copied.iterdir() if p.is_dir()},
                {p.name for p in (SOURCE_ICONS / "aws").iterdir() if p.is_dir()},
            )


class CopyAssetsUnrelatedAssetTests(unittest.TestCase):
    def test_css_and_img_are_still_copied(self):
        with staged(1) as (report_path, _):
            self.assertTrue((report_path / "assets" / "css").is_dir())
            self.assertTrue((report_path / "assets" / "img").is_dir())

    def test_assessment_db_is_still_copied(self):
        with staged(1) as (report_path, copyfile):
            self.assertTrue((report_path / "data").is_dir())
            copyfile.assert_called_once_with(
                str(active_catalogue_path()),
                os.path.join(str(report_path), "data", "assessment.db"),
            )

    def test_no_shared_icon_folder_is_copied(self):
        # Only the assessed provider's resource icons travel with a report;
        # the report draws a placeholder for a resource type without an icon.
        for cloud_service_provider in (1, 2, 99):
            with self.subTest(cloud_service_provider=cloud_service_provider):
                with staged(cloud_service_provider) as (report_path, _):
                    icons = report_path / "assets" / "icons"

                    self.assertFalse((icons / "severity").exists())
                    self.assertFalse((icons / "misc").exists())


if __name__ == "__main__":
    unittest.main()
