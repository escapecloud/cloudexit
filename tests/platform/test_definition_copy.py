import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from tests.support.paths import DEFINITION

MANIFEST = "MANIFEST.sha256"


def manifest_problems(directory):
    directory = Path(directory)
    expected = {}
    for line in (directory / MANIFEST).read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        expected[name] = digest

    actual = {
        path.relative_to(directory)
        .as_posix(): hashlib.sha256(path.read_bytes())
        .hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
        and path.name != MANIFEST
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }

    problems = []
    for name in sorted(expected.keys() - actual.keys()):
        problems.append(f"missing: {name}")
    for name in sorted(actual.keys() - expected.keys()):
        problems.append(f"not in the manifest: {name}")
    for name in sorted(expected.keys() & actual.keys()):
        if expected[name] != actual[name]:
            problems.append(f"modified: {name}")
    return problems


class DefinitionCopyTests(unittest.TestCase):
    def test_the_version_file_is_a_semantic_version(self):
        version = (DEFINITION / "VERSION").read_text(encoding="utf-8").strip()
        major, minor, patch = version.split(".")
        self.assertEqual(major, "2")
        self.assertTrue(minor.isdigit() and patch.isdigit())

    def test_every_file_matches_the_manifest(self):
        self.assertEqual(manifest_problems(DEFINITION), [])

    def test_the_manifest_lists_the_version_file(self):
        manifest = (DEFINITION / MANIFEST).read_text(encoding="utf-8")
        self.assertIn("  VERSION\n", manifest)

    def test_a_modified_file_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "api-v2"
            shutil.copytree(DEFINITION, copy)
            (copy / "VERSION").write_text("2.9.9\n", encoding="utf-8")

            self.assertEqual(manifest_problems(copy), ["modified: VERSION"])

    def test_a_missing_or_extra_file_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "api-v2"
            shutil.copytree(DEFINITION, copy)
            (copy / "errors.yaml").unlink()
            (copy / "extra.json").write_text("{}", encoding="utf-8")

            self.assertEqual(
                manifest_problems(copy),
                ["missing: errors.yaml", "not in the manifest: extra.json"],
            )


if __name__ == "__main__":
    unittest.main()
