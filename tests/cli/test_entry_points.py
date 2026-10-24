import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cloudexit import codes
from cloudexit.cli import app
from cloudexit.version import __version__
from tests.support.paths import ROOT


class EntryPointTests(unittest.TestCase):
    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_the_module_and_the_root_script_print_the_same_version(self):
        by_module = self.run_cli("-m", "cloudexit", "--version")
        by_script = self.run_cli("main.py", "--version")

        self.assertEqual(by_module.returncode, 0, by_module.stderr)
        self.assertEqual(by_script.returncode, 0, by_script.stderr)
        self.assertEqual(by_module.stdout, f"cloudexit v{__version__}\n")
        self.assertEqual(by_script.stdout, by_module.stdout)

    def test_the_root_script_is_only_a_shim(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertEqual(
            text,
            'from cloudexit.cli import run\n\nif __name__ == "__main__":\n    run()\n',
        )

    def test_the_package_exposes_the_two_entry_functions(self):
        from cloudexit import cli

        self.assertIs(cli.main, app.main)
        self.assertIs(cli.run, app.run)
        with self.assertRaises(AttributeError):
            cli.nothing_of_the_kind

    def test_the_console_script_target_resolves(self):
        from importlib.metadata import EntryPoint

        entry = EntryPoint("cloudexit", "cloudexit.cli:main", "console_scripts")
        self.assertIs(entry.load(), app.main)


class ImportOrderTests(unittest.TestCase):
    MODULES = (
        "cloudexit.engine",
        "cloudexit.cli.prompts",
        "cloudexit.cli.app",
        "cloudexit.providers.azure.costs",
        "cloudexit.providers.azure.cli_session",
        "cloudexit.platform.status",
        "cloudexit.report.egress",
        "cloudexit.egress.estimate",
    )

    def test_each_module_can_be_the_first_thing_imported(self):
        for name in self.MODULES:
            with self.subTest(name):
                result = subprocess.run(
                    [sys.executable, "-c", f"import {name}"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-600:])


class LastResortMessageTests(unittest.TestCase):
    def test_run_turns_an_unexpected_error_into_a_message_and_exit_1(self):
        out = io.StringIO()
        with (
            patch.object(app, "main", side_effect=RuntimeError("boom")),
            patch.object(
                app.console, "print", side_effect=lambda *a, **k: out.write(a[0])
            ),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            app.run()

        self.assertEqual(ctx.exception.code, codes.UNEXPECTED)
        self.assertIn("Unexpected error: boom", out.getvalue())

    def test_main_alone_has_no_such_guard(self):
        # The console script calls main() directly, as it always did
        with patch.object(app, "parse_arguments", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                app.main()


if __name__ == "__main__":
    unittest.main()
