import ast
import importlib
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
            "cloudexit.catalogue.store",
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


if __name__ == "__main__":
    unittest.main()
