import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from cloudexit.cli import app
from cloudexit.private_files import restrict_tree, write_private

MOMENT = datetime(2026, 11, 12, 9, 30, 0)
POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "POSIX file modes")


def frozen():
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return MOMENT

    return FrozenDatetime


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "reports"


class UniqueFolderTests(FolderTestCase):
    def test_two_runs_in_the_same_second_get_different_folders(self):
        with patch("cloudexit.cli.app.datetime", frozen()):
            first, first_raw = app.create_directory(str(self.base))
            second, second_raw = app.create_directory(str(self.base))

        self.assertNotEqual(first, second)
        self.assertNotEqual(first_raw, second_raw)
        for folder, raw in ((first, first_raw), (second, second_raw)):
            self.assertTrue(Path(raw).is_dir())
            self.assertEqual(Path(raw).parent, Path(folder))
            self.assertTrue(Path(folder).name.startswith("20261112093000-"))
        self.assertEqual(len(list(self.base.iterdir())), 2)

    def test_a_folder_that_exists_is_never_reused(self):
        seen = set()
        with patch("cloudexit.cli.app.datetime", frozen()):
            for _ in range(25):
                folder, _raw = app.create_directory(str(self.base))
                self.assertNotIn(folder, seen)
                seen.add(folder)
        self.assertEqual(len(seen), 25)

    def test_what_an_earlier_run_left_behind_is_untouched(self):
        old = self.base / "20261112093000-abcdefgh"
        (old / "raw_data").mkdir(parents=True)
        (old / "raw_data" / "sync.json").write_text("earlier")

        with patch("cloudexit.cli.app.datetime", frozen()):
            folder, _raw = app.create_directory(str(self.base))

        self.assertNotEqual(Path(folder), old)
        self.assertEqual((old / "raw_data" / "sync.json").read_text(), "earlier")
        self.assertEqual(list(Path(folder).glob("**/*.json")), [])


@POSIX_ONLY
class PrivateFolderTests(FolderTestCase):
    def setUp(self):
        super().setUp()
        previous = os.umask(0)
        self.addCleanup(os.umask, previous)

    def test_the_folder_and_its_raw_data_are_for_the_owner_only(self):
        folder, raw = app.create_directory(str(self.base))

        self.assertEqual(mode(folder), 0o700)
        self.assertEqual(mode(raw), 0o700)
        self.assertEqual(mode(self.base), 0o700)

    def test_a_private_file_is_not_readable_by_others_and_keeps_a_stricter_umask(self):
        path = self.base.parent / "a.json"
        write_private(path, b"{}")
        self.assertEqual(mode(path), 0o600)

        os.umask(0o277)
        stricter = self.base.parent / "b.json"
        write_private(stricter, b"{}")
        self.assertEqual(mode(stricter), 0o400)

    def test_restricting_a_tree_removes_group_and_other_and_never_loosens(self):
        root = self.base / "run"
        (root / "assets").mkdir(parents=True)
        files = {
            root / "index.html": 0o644,
            root / "assets" / "logo.svg": 0o664,
            root / "report.pdf": 0o600,
            root / "read-only.json": 0o400,
        }
        for path, wanted in files.items():
            path.write_text("x")
            path.chmod(wanted)
        os.chmod(root, 0o755)
        os.chmod(root / "assets", 0o775)

        restrict_tree(root)

        self.assertEqual(mode(root), 0o700)
        self.assertEqual(mode(root / "assets"), 0o700)
        self.assertEqual(mode(root / "index.html"), 0o600)
        self.assertEqual(mode(root / "assets" / "logo.svg"), 0o600)
        self.assertEqual(mode(root / "report.pdf"), 0o600)
        self.assertEqual(mode(root / "read-only.json"), 0o400)

    def test_a_link_is_left_alone(self):
        root = self.base / "run"
        root.mkdir(parents=True)
        outside = self.base.parent / "outside.txt"
        outside.write_text("x")
        outside.chmod(0o644)
        (root / "link").symlink_to(outside)

        restrict_tree(root)

        self.assertEqual(mode(outside), 0o644)

    def test_a_missing_folder_is_not_an_error(self):
        restrict_tree(self.base / "nothing")


if __name__ == "__main__":
    unittest.main()
