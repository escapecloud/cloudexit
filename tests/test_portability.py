import ast
import io
import logging
import os
import subprocess
import sys
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import main
import tests
from core import utils_db
from tests.isolation import isolated_environ
from tests.make_smoke_dataset import main as make_smoke_dataset

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [
    ROOT / "main.py",
    *(ROOT / "core").rglob("*.py"),
    *(ROOT / "utils").rglob("*.py"),
]


def text_mode_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = node.args[1] if len(node.args) > 1 else keywords.get("mode")
            binary = isinstance(mode, ast.Constant) and "b" in str(mode.value)
            if not binary:
                yield node, keywords
        elif isinstance(node.func, ast.Attribute) and node.func.attr in (
            "read_text",
            "write_text",
        ):
            yield node, keywords


class EncodingTests(unittest.TestCase):
    def test_text_files_are_opened_with_an_explicit_encoding(self):
        # The default encoding on Windows is not UTF-8
        missing = []
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node, keywords in text_mode_calls(tree):
                if "encoding" not in keywords:
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(missing, [])

    def test_the_checker_sees_a_call_without_an_encoding(self):
        tree = ast.parse('open("x", "w")\nopen("x", "rb")\np.read_text()\n')
        found = [node.lineno for node, _ in text_mode_calls(tree)]
        self.assertEqual(found, [1, 3])


class IsolationTests(unittest.TestCase):
    def test_the_tests_run_with_a_home_of_their_own(self):
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.assertTrue(Path(os.environ[name]).is_relative_to(tests._home), name)


class OpenFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "run.db"
        with utils_db.connect(self.path) as conn:
            conn.execute("CREATE TABLE risk (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO risk VALUES (1, 'a')")

    def assert_closed(self, conn):
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_a_with_block_commits_and_closes_the_connection(self):
        with utils_db.connect(self.path) as conn:
            conn.execute("INSERT INTO risk VALUES (2, 'b')")
        self.assert_closed(conn)
        self.assertEqual(len(utils_db.load_data("risk", self.path)), 2)

    def test_a_failed_with_block_rolls_back_and_closes(self):
        with self.assertRaises(RuntimeError):
            with utils_db.connect(self.path) as conn:
                conn.execute("INSERT INTO risk VALUES (3, 'c')")
                raise RuntimeError("stop")
        self.assert_closed(conn)
        self.assertEqual(len(utils_db.load_data("risk", self.path)), 1)

    def test_the_helpers_close_their_connection_on_success_and_on_error(self):
        opened = []
        real = utils_db.connect

        def spy(path=None):
            conn = real(path)
            opened.append(conn)
            return conn

        utils_db.connect, original = spy, utils_db.connect
        try:
            utils_db.load_data("risk", self.path)
            utils_db.fetch_one("SELECT * FROM risk", db_path=self.path)
            utils_db.fetch_all("SELECT * FROM risk", db_path=self.path)
            utils_db.execute_query("UPDATE risk SET name = 'z'", db_path=self.path)
            with self.assertRaises(sqlite3.Error):
                utils_db.fetch_all("SELECT * FROM nothing", db_path=self.path)
        finally:
            utils_db.connect = original
        self.assertEqual(len(opened), 5)
        for conn in opened:
            self.assert_closed(conn)


class OutputEncodingTests(unittest.TestCase):
    def test_a_stream_that_cannot_hold_the_arrows_is_switched_to_utf8(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", write_through=True)
        with unittest.mock.patch.object(sys, "stdout", stream):
            main._use_utf8_output()
            sys.stdout.write("\u21b3 done")
        self.assertEqual(raw.getvalue().decode("utf-8"), "\u21b3 done")

    def test_a_stream_without_an_encoding_is_left_alone(self):
        with unittest.mock.patch.object(sys, "stdout", io.StringIO()):
            main._use_utf8_output()

    def test_a_run_into_a_cp1252_pipe_still_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dataset = tmp / "smoke.db"
            make_smoke_dataset(str(dataset))
            env = {**os.environ, **isolated_environ(tmp)}
            env.update(
                PYTHONIOENCODING="cp1252",
                ESC_EXIT_STRATEGY="1",
                ESC_ASSESSMENT_TYPE="1",
            )
            state = ROOT / "tests" / "fixtures" / "smoke" / "aws.tfstate"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "main.py"),
                    "aws",
                    "--tfstate",
                    str(state),
                    "--catalogue",
                    str(dataset),
                    "--non-interactive",
                    "--dry-run",
                ],
                cwd=tmp,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=120,
            )
        output = (result.stdout + result.stderr).decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("UnicodeEncodeError", output)


class RunLogTests(unittest.TestCase):
    def test_the_run_log_is_closed_when_the_run_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            main.add_run_log_handler(tmp)
            handler = main._run_log_handler
            self.assertIn(handler, logging.getLogger().handlers)
            main.close_run_log_handler()
            self.assertNotIn(handler, logging.getLogger().handlers)
            self.assertIsNone(handler.stream)
            self.assertIsNone(main._run_log_handler)
            (Path(tmp) / "run.log").unlink()

    def test_a_second_run_log_replaces_the_first(self):
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            main.add_run_log_handler(first)
            opened = main._run_log_handler
            main.add_run_log_handler(second)
            self.assertIsNone(opened.stream)
            self.assertNotIn(opened, logging.getLogger().handlers)
            main.close_run_log_handler()


class LineEndingTests(unittest.TestCase):
    def test_the_definition_copy_is_checked_out_with_unix_line_endings(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("docs/api-v2/** text eol=lf", attributes)
