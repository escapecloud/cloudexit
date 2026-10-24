import ast
import unittest

from tests.support.paths import ROOT

PACKAGE = ROOT / "cloudexit"


def imports(path):
    # (module name, inside a function) for every import in the file
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def walk(node, in_function):
        for child in ast.iter_child_nodes(node):
            inside = in_function or isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            )
            if isinstance(child, ast.Import):
                found.extend((alias.name, inside) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                found.append((child.module or "", inside))
            walk(child, inside)

    walk(tree, False)
    return found


class EgressLayeringTests(unittest.TestCase):
    def test_the_model_is_a_leaf_that_knows_no_provider(self):
        for name, _ in imports(PACKAGE / "egress" / "model.py"):
            self.assertFalse(name.startswith("cloudexit.providers"), name)

    def test_the_estimate_reaches_the_providers_only_inside_a_function(self):
        providers = [
            (name, inside)
            for name, inside in imports(PACKAGE / "egress" / "estimate.py")
            if name.startswith("cloudexit.providers")
        ]
        self.assertTrue(providers)
        for name, inside in providers:
            self.assertTrue(inside, f"{name} is imported at module level")

    def test_no_provider_imports_the_estimate(self):
        for path in sorted((PACKAGE / "providers").rglob("*.py")):
            for name, _ in imports(path):
                self.assertNotEqual(
                    name, "cloudexit.egress.estimate", path.relative_to(ROOT)
                )

    def test_the_provider_egress_modules_use_the_model(self):
        for provider in ("aws", "azure"):
            names = [
                n for n, _ in imports(PACKAGE / "providers" / provider / "egress.py")
            ]
            self.assertIn("cloudexit.egress.model", names)

    def test_the_checker_sees_module_level_and_function_level_imports(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text(
                "import a\ndef f():\n    from b import c\n", encoding="utf-8"
            )
            self.assertEqual(imports(path), [("a", False), ("b", True)])


if __name__ == "__main__":
    unittest.main()
