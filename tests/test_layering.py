import ast
import unittest

from tests.support.paths import ROOT

PACKAGE = ROOT / "cloudexit"

# What each area of the package may import from another area. Imports inside an
# area are free. A new edge fails the test: either it is a mistake, or it needs
# a deliberate entry here (and a line in the pull request that adds it).
ALLOWED = {
    "codes": set(),
    "version": set(),
    "resources": set(),
    "private_files": set(),
    "terminal": {"codes"},
    "settings": set(),
    "catalogue": {"settings"},
    "runconfig": {"codes", "terminal"},
    "tfstate": {"catalogue"},
    "providers": {"catalogue", "egress", "terminal"},
    # egress.estimate reaches the providers inside a function, on purpose
    "egress": {"catalogue", "providers"},
    "platform": {"catalogue", "settings", "version", "codes", "private_files"},
    "report": {"catalogue", "egress", "providers", "resources", "version", "codes"},
    "engine": {"catalogue", "providers", "report", "tfstate", "terminal", "codes"},
}
# The command line sits on top of everything
UNRESTRICTED = {"cli", "__main__"}


def area_of(module):
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else None


def module_name(path):
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module_name(path).split(".")
    if path.name != "__init__.py":
        package.pop()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            yield ".".join([*base, *([node.module] if node.module else [])])


def edges():
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        source = area_of(module_name(path))
        for name in imported_modules(path):
            target = area_of(name) if name.startswith("cloudexit.") else None
            if target and target != source:
                found.add((source, target, path.relative_to(ROOT).as_posix()))
    return found


class LayeringTests(unittest.TestCase):
    def test_every_area_of_the_package_is_known(self):
        areas = {area_of(module_name(p)) for p in PACKAGE.rglob("*.py")}
        areas.discard(None)
        self.assertEqual(areas - UNRESTRICTED - set(ALLOWED), set())

    def test_no_area_imports_another_that_it_is_not_allowed_to(self):
        violations = sorted(
            f"{path}: {source} -> {target}"
            for source, target, path in edges()
            if source not in UNRESTRICTED and target not in ALLOWED[source]
        )
        self.assertEqual(violations, [])

    def test_only_the_command_line_imports_the_command_line(self):
        violations = sorted(
            path
            for source, target, path in edges()
            if target == "cli" and source not in UNRESTRICTED
        )
        self.assertEqual(violations, [])

    def test_platform_and_providers_do_not_know_each_other(self):
        for source, target, path in edges():
            self.assertFalse(
                {source, target} == {"platform", "providers"}, f"{path}: {source}"
            )

    def test_the_leaves_import_nothing_but_the_exit_codes(self):
        for leaf in ("codes", "version", "resources", "terminal"):
            for source, target, path in edges():
                if source == leaf:
                    self.assertEqual(target, "codes", path)

    def test_the_checker_finds_an_edge(self):
        self.assertIn(
            ("providers", "terminal", "cloudexit/providers/azure/costs.py"), edges()
        )


if __name__ == "__main__":
    unittest.main()
