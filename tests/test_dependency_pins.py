import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:==\s*(\S+))?$")


def parse_requirements(lines):
    # Maps lower-cased name -> pinned version (None when unpinned). Blank
    # lines, comments and -r includes are skipped.
    parsed = {}
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = REQUIREMENT.match(line)
        if match is None:
            raise ValueError(f"unsupported requirement line: {line!r}")
        name, version = match.groups()
        parsed[name.lower()] = version
    return parsed


def read_requirements(name):
    return parse_requirements((ROOT / name).read_text().splitlines())


def read_pyproject():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


class DependencyPinsTest(unittest.TestCase):
    # Dependabot bumps requirements.txt and pyproject.toml independently, so a
    # PR that touches only one of them fails here: update the other file in
    # the same PR.

    def test_runtime_pins_match_pyproject(self):
        requirements = read_requirements("requirements.txt")
        declared = parse_requirements(read_pyproject()["dependencies"])

        self.assertEqual(requirements, declared)

    def test_dev_requirements_listed_in_pyproject_extra(self):
        dev = read_requirements("requirements-dev.txt")
        extra = parse_requirements(read_pyproject()["optional-dependencies"]["dev"])

        self.assertEqual(set(dev), set(extra))


if __name__ == "__main__":
    unittest.main()
