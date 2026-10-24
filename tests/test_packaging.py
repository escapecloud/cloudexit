import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from cloudexit import resources
from core.engine import generate_report, perform_risk_assessment
from cloudexit.report import fonts
from cloudexit.resources import asset_path, find_asset
from cloudexit.report.assets import copy_assets
from tests.support.isolation import isolated_environ
from tests.support.flow import build_flow_run
from tests.report.test_report_flow import AWS_RESOURCES, CONFIG, COSTS
from tests.support.paths import ROOT

SOURCE_FILES = ("pyproject.toml", "README.md", "main.py")
SOURCE_DIRS = ("cloudexit", "core", "utils")


@contextmanager
def working_directory(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def asset_files(root):
    return sorted(
        path.relative_to(root).as_posix()
        for path in Path(root).rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )


class WheelContentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        # Build from a copy so no stale build directory in the checkout can
        # hide a file that the package data does not pick up.
        source = Path(tmp.name) / "source"
        source.mkdir()
        for name in SOURCE_FILES:
            shutil.copy(ROOT / name, source / name)
        for name in SOURCE_DIRS:
            shutil.copytree(
                ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        out = Path(tmp.name) / "wheel"
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out)],
            cwd=source,
            check=True,
            capture_output=True,
        )
        (wheel,) = out.glob("*.whl")
        cls.names = set(zipfile.ZipFile(wheel).namelist())

    def test_the_wheel_contains_every_asset_file(self):
        expected = {f"cloudexit/assets/{name}" for name in asset_files(asset_path())}
        packaged = {n for n in self.names if n.startswith("cloudexit/assets/")}
        self.assertEqual(sorted(expected - packaged), [])
        self.assertEqual(sorted(packaged - expected), [])
        self.assertGreater(len(expected), 1000)

    def test_the_files_a_report_needs_are_in_the_wheel(self):
        for name in (
            "cloudexit/assets/fonts/IBMPlexSans-Regular.ttf",
            "cloudexit/assets/fonts/IBMPlexMono-SemiBold.ttf",
            "cloudexit/assets/fonts/OFL.txt",
            "cloudexit/assets/template/index.html",
            "cloudexit/assets/template/egress.html",
            "cloudexit/assets/css/style.css",
            "cloudexit/assets/img/logo/report_logo.png",
            "cloudexit/assets/icons/aws/Compute/EC2.png",
        ):
            with self.subTest(name):
                self.assertIn(name, self.names)

    def test_the_wheel_ships_no_svg_icons_and_no_unused_assets(self):
        self.assertEqual([n for n in self.names if n.endswith(".svg")], [])
        self.assertNotIn("cloudexit/assets/img/logo/favicon.ico", self.names)
        self.assertNotIn("cloudexit/assets/fonts/IBMPlexSans-Medium.ttf", self.names)

    def test_the_wheel_has_no_loose_assets_directory(self):
        self.assertFalse([n for n in self.names if n.startswith("assets/")])


class AssetLookupTests(unittest.TestCase):
    def test_the_bundled_files_exist(self):
        for parts in (
            ("template", "index.html"),
            ("template", "egress.html"),
            ("fonts", "IBMPlexSans-Regular.ttf"),
            ("img", "logo", "report_logo.png"),
        ):
            with self.subTest(parts=parts):
                self.assertTrue(asset_path(*parts).is_file())

    def test_the_assets_are_found_through_the_package_not_the_current_directory(self):
        self.assertEqual(asset_path(), ROOT / "cloudexit" / "assets")
        with tempfile.TemporaryDirectory() as empty, working_directory(empty):
            self.assertTrue(asset_path("template", "index.html").is_file())
            self.assertTrue(asset_path("fonts", "IBMPlexSans-Regular.ttf").is_file())

    def test_assets_that_are_not_on_the_file_system_are_a_clear_error(self):
        with mock.patch.object(resources, "files", return_value=object()):
            with self.assertRaises(RuntimeError) as ctx:
                asset_path("fonts")
        self.assertIn("not on the file system", str(ctx.exception))

    def test_a_report_copy_wins_over_the_bundled_file(self):
        with tempfile.TemporaryDirectory() as report:
            copied = Path(report) / "assets" / "img" / "logo" / "report_logo.png"
            copied.parent.mkdir(parents=True)
            copied.write_bytes(b"copy")

            self.assertEqual(find_asset(report, "img/logo/report_logo.png"), copied)
            self.assertEqual(
                find_asset(report, "/icons/aws/Compute/EC2.png"),
                asset_path("icons", "aws", "Compute", "EC2.png"),
            )

    def test_find_asset_refuses_paths_that_leave_the_assets(self):
        with tempfile.TemporaryDirectory() as report:
            for relative in ("", "/", "../core/utils.py", "icons/../../main.py"):
                with self.subTest(relative=relative):
                    self.assertIsNone(find_asset(report, relative))
            self.assertIsNone(find_asset(report, "icons/aws/missing.png"))


class RunFromAnotherDirectoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.dict(
            os.environ,
            isolated_environ(self.tmp),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()

    def test_the_html_and_pdf_reports_are_written_from_an_empty_directory(self):
        report = build_flow_run(
            self.tmp / "report", resources=AWS_RESOURCES, costs=COSTS
        )
        with working_directory(self.elsewhere):
            perform_risk_assessment(1, report)
            result = generate_report(
                2,
                CONFIG["providerDetails"],
                1,
                1,
                "Flow",
                report,
                str(Path(report) / "raw_data"),
            )

        self.assertTrue(result["success"], result.get("logs"))
        self.assertGreater((Path(report) / "index.html").stat().st_size, 1000)
        self.assertGreater((Path(report) / "report.pdf").stat().st_size, 5000)
        self.assertEqual(os.listdir(self.elsewhere), [])

    def test_the_report_folder_still_gets_its_own_copy_of_the_assets(self):
        report = self.tmp / "report"
        (report / "data").mkdir(parents=True)
        (report / "data" / "assessment.db").write_bytes(b"")
        with working_directory(self.elsewhere):
            copy_assets(str(report), 2)

        assets = report / "assets"
        self.assertTrue((assets / "css" / "style.css").is_file())
        self.assertTrue((assets / "img" / "logo" / "report_logo.png").is_file())
        self.assertTrue(list((assets / "icons" / "aws").rglob("*.png")))
        self.assertFalse((assets / "icons" / "azure").exists())
        self.assertEqual(os.listdir(self.elsewhere), [])


class MissingFontTests(unittest.TestCase):
    def test_a_missing_font_names_the_file_and_the_incomplete_installation(self):
        with tempfile.TemporaryDirectory() as empty:
            with (
                mock.patch.object(fonts, "FONT_DIR", Path(empty)),
                mock.patch.object(
                    fonts.pdfmetrics, "getRegisteredFontNames", return_value=[]
                ),
            ):
                with self.assertRaises(fonts.MissingFontError) as ctx:
                    fonts.register_fonts()

        message = str(ctx.exception)
        self.assertIn("IBMPlexSans-Regular.ttf", message)
        self.assertIn("installation is incomplete", message)
        self.assertIsInstance(ctx.exception, FileNotFoundError)


class NoCurrentDirectoryLookupTests(unittest.TestCase):
    PATTERNS = (
        re.compile(r"(?:join|Path)\(\s*[\"']assets[\"']"),
        re.compile(r"[\"']assets/"),
        re.compile(r"[\"']\./assets"),
        re.compile(r"parents\[\d\]\s*/\s*[\"']assets[\"']"),
    )
    # Paths inside the report folder, or links inside the HTML report.
    ALLOWED = ()

    def sources(self):
        for name in SOURCE_DIRS:
            for path in sorted((ROOT / name).rglob("*.py")):
                yield path
        yield ROOT / "main.py"

    def test_no_code_reads_assets_relative_to_the_current_directory(self):
        found = []
        for path in self.sources():
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if any(allowed in line for allowed in self.ALLOWED):
                    continue
                if any(pattern.search(line) for pattern in self.PATTERNS):
                    found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual(found, [])

    def test_the_check_would_catch_the_old_lookups(self):
        for line in (
            'os.path.join("assets", "template", "index.html")',
            'Path("assets") / "fonts"',
            'src = "assets/css"',
            'Path(__file__).resolve().parents[2] / "assets" / "fonts"',
        ):
            with self.subTest(line=line):
                self.assertTrue(any(p.search(line) for p in self.PATTERNS))


class VersionFileTests(unittest.TestCase):
    def test_the_release_workflow_edits_the_file_the_build_reads(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('attr = "cloudexit.version.__version__"', pyproject)
        paths = set(re.findall(r"[\w/]+/version\.py", workflow))
        self.assertEqual(paths, {"cloudexit/version.py"})
        text = (ROOT / "cloudexit" / "version.py").read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^__version__ = ".+"$')


if __name__ == "__main__":
    unittest.main()
