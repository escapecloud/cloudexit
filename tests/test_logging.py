import ast
import importlib
import logging
import tempfile
import unittest
from pathlib import Path

from cloudexit.cli import app
from tests.support.paths import ROOT

PACKAGE = ROOT / "cloudexit"
# Loggers of other libraries that the package sets a level on


def module_loggers(path):
    # `logger = logging.getLogger(...)` at module level
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "logger" for t in node.targets)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "logging.getLogger"
        ):
            yield node


class LoggerNameTests(unittest.TestCase):
    def test_every_logger_of_the_package_is_named_after_its_module(self):
        found = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in module_loggers(path):
                if ast.unparse(node.value) != "logging.getLogger(__name__)":
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        self.assertEqual(found, [])

    def test_a_module_logger_carries_the_dotted_path_of_its_module(self):
        for name in (
            "cloudexit.platform.client",
            "cloudexit.providers.aws.inventory",
            "cloudexit.report.egress",
            "cloudexit.catalogue.download",
            "cloudexit.settings",
            "cloudexit.engine",
        ):
            with self.subTest(name):
                module = importlib.import_module(name)
                self.assertEqual(module.logger.name, name)

    def test_the_run_log_names_the_module_that_wrote_each_line(self):
        from cloudexit.platform import client

        with tempfile.TemporaryDirectory() as tmp:
            app.add_run_log_handler(tmp)
            try:
                client.logger.warning("a line for the run log")
            finally:
                app.close_run_log_handler()
            text = (Path(tmp) / "run.log").read_text(encoding="utf-8")
            self.assertIn("cloudexit.platform.client - WARNING - a line", text)
            self.assertNotIn("core.engine", text)


class ThirdPartyLevelTests(unittest.TestCase):
    NAMES = ("botocore", "boto3", "azure", "urllib3", "msal", "PIL")

    def setUp(self):
        root = logging.getLogger()
        saved = (root.level, list(root.handlers))
        levels = {name: logging.getLogger(name).level for name in self.NAMES}

        def restore():
            root.setLevel(saved[0])
            root.handlers[:] = saved[1]
            for name, level in levels.items():
                logging.getLogger(name).setLevel(level)

        self.addCleanup(restore)

    def level(self, name):
        return logging.getLogger(name).getEffectiveLevel()

    def test_msal_is_never_below_warning_because_it_logs_the_client_id(self):
        for verbose in (0, 1, 2, 3):
            with self.subTest(verbose=verbose):
                app.configure_logging(verbose)
                self.assertEqual(self.level("msal"), logging.WARNING)
                self.assertEqual(self.level("msal.telemetry"), logging.WARNING)

    def test_urllib3_shows_connection_lines_only_at_the_deepest_verbosity(self):
        for verbose, expected in (
            (0, logging.WARNING),
            (1, logging.WARNING),
            (2, logging.DEBUG),
        ):
            with self.subTest(verbose=verbose):
                app.configure_logging(verbose)
                self.assertEqual(self.level("urllib3"), expected)

    def test_the_other_libraries_keep_their_levels(self):
        app.configure_logging(0)
        for name in ("botocore", "boto3", "azure", "PIL"):
            self.assertEqual(self.level(name), logging.WARNING, name)
        app.configure_logging(2)
        for name in ("botocore", "boto3", "azure"):
            self.assertEqual(self.level(name), logging.DEBUG, name)
        self.assertEqual(self.level("PIL"), logging.WARNING)


if __name__ == "__main__":
    unittest.main()
