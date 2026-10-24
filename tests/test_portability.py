import ast
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import tests
from core import utils_db

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
