import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from cloudexit.report.cover import LOGO_FILE, logo_reader
from cloudexit.report.helpers import report_icon
from cloudexit.resources import find_asset, resolve_inside, safe_relative

REFUSED = (
    "",
    "/",
    "..",
    "../x.png",
    "icons/../../x.png",
    "/../x.png",
    "//host/share/x.png",
    "///x.png",
    "\\\\host\\share\\x.png",
    "\\\\host\\share\\x.png".replace("\\", "/"),
    "C:\\x.png",
    "C:/x.png",
    "c:x.png",
    "icons\\aws\\EC2.png",
    "/icons\\aws.png",
    "x.png:stream",
    "x\x00.png",
)


class AssetPathTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.report = self.tmp / "report"
        self.assets = self.report / "assets"
        (self.assets / "icons" / "aws").mkdir(parents=True)
        (self.assets / "icons" / "aws" / "EC2.png").write_bytes(b"png")
        self.outside = self.tmp / "outside.png"
        self.outside.write_bytes(b"secret")


class SafeRelativeTests(unittest.TestCase):
    def test_a_normal_relative_path_is_kept(self):
        self.assertEqual(
            safe_relative("icons/aws/EC2.png"), PurePosixPath("icons/aws/EC2.png")
        )

    def test_the_catalogue_form_with_one_leading_slash_means_the_assets_folder(self):
        self.assertEqual(
            safe_relative("/icons/aws/EC2.png"), PurePosixPath("icons/aws/EC2.png")
        )

    def test_everything_that_could_leave_the_folder_is_refused(self):
        for relative in REFUSED:
            with self.subTest(relative=relative):
                self.assertIsNone(safe_relative(relative))
        self.assertIsNone(safe_relative(None))


class FindAssetTests(AssetPathTestCase):
    def test_the_report_logo_is_found_by_its_own_name_on_every_system(self):
        self.assertNotIn("\\", LOGO_FILE)
        self.assertEqual(safe_relative(LOGO_FILE), PurePosixPath(LOGO_FILE))
        self.assertIsNotNone(find_asset(self.report, LOGO_FILE))
        self.assertIsNotNone(logo_reader(self.report))

    def test_a_normal_icon_still_resolves(self):
        found = find_asset(self.report, "/icons/aws/EC2.png")

        self.assertEqual(found, self.assets / "icons" / "aws" / "EC2.png")

    def test_refused_paths_find_nothing_and_touch_no_file(self):
        with (
            patch.object(Path, "is_file", side_effect=AssertionError("looked")),
            patch.object(Path, "resolve", side_effect=AssertionError("looked")),
            patch.object(os.path, "isfile", side_effect=AssertionError("looked")),
            patch.object(os, "stat", side_effect=AssertionError("looked")),
        ):
            for relative in REFUSED:
                with self.subTest(relative=relative):
                    self.assertIsNone(find_asset(self.report, relative))

    def test_an_absolute_path_is_read_inside_the_assets_only(self):
        looked = []
        real = Path.is_file

        def spy(path):
            looked.append(path)
            return real(path)

        with patch.object(Path, "is_file", spy):
            found = find_asset(self.report, str(self.outside))

        self.assertIsNone(found)
        for path in looked:
            self.assertNotEqual(path, self.outside)

    @unittest.skipIf(sys.platform == "win32", "needs symbolic links")
    def test_a_link_out_of_the_assets_is_not_followed(self):
        (self.assets / "icons" / "link.png").symlink_to(self.outside)

        self.assertIsNone(find_asset(self.report, "icons/link.png"))
        self.assertIsNone(resolve_inside(self.assets, "icons/link.png"))


class ReportIconTests(AssetPathTestCase):
    def test_a_normal_icon_points_into_the_folder(self):
        self.assertEqual(
            report_icon(self.report, "icons/aws/EC2.png"), "assets/icons/aws/EC2.png"
        )
        self.assertEqual(
            report_icon(self.report, "/icons/aws/EC2.png"), "assets/icons/aws/EC2.png"
        )

    def test_a_missing_icon_is_none(self):
        self.assertIsNone(report_icon(self.report, "icons/aws/missing.png"))
        self.assertIsNone(report_icon(self.report, None))
        self.assertIsNone(report_icon(self.report, ""))

    def test_refused_paths_give_the_placeholder_and_touch_no_file(self):
        with (
            patch.object(Path, "is_file", side_effect=AssertionError("looked")),
            patch.object(Path, "resolve", side_effect=AssertionError("looked")),
            patch.object(os.path, "isfile", side_effect=AssertionError("looked")),
            patch.object(os, "stat", side_effect=AssertionError("looked")),
        ):
            for relative in REFUSED:
                with self.subTest(relative=relative):
                    self.assertIsNone(report_icon(self.report, relative))

    def test_a_file_outside_the_folder_is_never_pointed_at(self):
        for relative in (
            str(self.outside),
            "../outside.png",
            "..\\outside.png",
            f"/{self.outside}",
        ):
            with self.subTest(relative=relative):
                self.assertIsNone(report_icon(self.report, relative))


if __name__ == "__main__":
    unittest.main()
