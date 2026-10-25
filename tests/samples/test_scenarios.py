import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.samples.render import EXPECTED_FILES, expected_files
from tests.samples.scenarios import (
    SAMPLES_DIR,
    ScenarioError,
    load_scenario,
    load_scenarios,
)

CASES = {
    "aws_typical",
    "aws_no_egress",
    "azure_typical",
    "azure_uploaded",
    "uploaded_no_link",
    "tfstate_aws",
    "tfstate_azure",
    "no_risks",
    "empty",
    "egress_free_tier",
    "egress_multi_tib",
    "stress",
}
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MADE_UP = "00000000-0000-4000-8000-"
GIB_TOLERANCE = 1e-6


class ScenarioTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.valid = json.loads(
            (SAMPLES_DIR / "aws_typical.json").read_text(encoding="utf-8")
        )

    def write(self, data, name="case.json"):
        path = self.tmp / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def edited(self, **changes):
        data = copy.deepcopy(self.valid)
        data.update(changes)
        return data

    def assertRejected(self, data, *words):
        with self.assertRaises(ScenarioError) as raised:
            load_scenario(self.write(data))
        message = str(raised.exception)
        self.assertTrue(message.startswith("case.json: "), message)
        for word in words:
            self.assertIn(word, message)


class LoaderTests(ScenarioTestCase):
    def test_a_valid_file_loads(self):
        scenario = load_scenario(self.write(self.valid))

        self.assertEqual(scenario.case, "case")
        self.assertEqual(scenario.provider, "aws")
        self.assertEqual(scenario.csp, 2)
        self.assertEqual(scenario.clock.isoformat(), "2026-11-12T09:30:00+00:00")
        self.assertEqual(scenario.provider_details()["region"], "eu-central-1")
        self.assertIsNone(scenario.tfstate)
        self.assertIn("AWS.s3.list_buckets.Buckets", scenario.type_codes())

    def test_a_missing_field_names_the_file_and_the_field(self):
        data = self.edited()
        del data["costs"]

        self.assertRejected(data, "costs", "missing")

    def test_a_missing_scope_field_is_named(self):
        data = self.edited(scope={"accountId": "123456789012"})

        self.assertRejected(data, "scope.region", "missing")

    def test_an_unknown_provider_is_rejected(self):
        self.assertRejected(self.edited(provider="gcp"), "provider", "gcp")

    def test_a_bad_month_key_is_rejected(self):
        costs = {"currency": "USD", "months": {"2026-13": 1.0}}

        self.assertRejected(self.edited(costs=costs), "costs.months.2026-13", "YYYY-MM")

    def test_a_bad_currency_amount_or_override_is_rejected(self):
        self.assertRejected(
            self.edited(costs={"currency": "dollar", "months": {}}), "costs.currency"
        )
        self.assertRejected(
            self.edited(costs={"currency": "USD", "months": {"2026-10": -1}}),
            "costs.months.2026-10",
        )
        self.assertRejected(
            self.edited(
                costs={
                    "currency": "USD",
                    "months": {"2026-10": 1},
                    "currencies": {"2026-09": "EUR"},
                }
            ),
            "costs.currencies.2026-09",
        )

    def test_an_unknown_field_is_rejected(self):
        self.assertRejected(self.edited(colour="red"), "colour", "unknown field")

    def test_a_bad_strategy_clock_or_run_id_is_rejected(self):
        self.assertRejected(self.edited(exit_strategy=4), "exit_strategy")
        self.assertRejected(self.edited(clock="2026-11-12T09:30:00"), "clock")
        self.assertRejected(self.edited(clock="yesterday"), "clock")
        self.assertRejected(self.edited(run_id="not-a-uuid"), "run_id")

    def test_a_resource_listed_twice_or_without_a_count_is_rejected(self):
        resource = {"type": "AWS.x", "region": "eu-central-1", "count": 1}
        self.assertRejected(
            self.edited(resources=[resource, resource]), "resources[1]", "twice"
        )
        self.assertRejected(
            self.edited(resources=[{**resource, "count": 0}]), "resources[0].count"
        )

    def test_a_bad_egress_resource_is_rejected(self):
        egress = {
            "archive_tiers": [],
            "resources": [{"type": "AWS.x", "name": "a", "size_gib": -1}],
        }

        self.assertRejected(self.edited(egress=egress), "egress.resources[0].size_gib")

    def test_a_state_file_has_nothing_else(self):
        self.assertRejected(
            self.edited(scope={"tfstate": "sample_aws.tfstate"}),
            "scope.tfstate",
            "no resources",
        )
        self.assertRejected(
            self.edited(
                scope={"tfstate": "missing.tfstate"},
                resources=[],
                costs=None,
                egress=None,
            ),
            "scope.tfstate",
            "missing.tfstate",
        )

    def test_a_file_that_is_not_json_is_named(self):
        path = self.tmp / "broken.json"
        path.write_text("{", encoding="utf-8")

        with self.assertRaises(ScenarioError) as raised:
            load_scenario(path)
        self.assertIn("broken.json", str(raised.exception))


class SampleFilesTests(unittest.TestCase):
    def test_every_case_of_the_matrix_has_a_valid_file(self):
        scenarios = load_scenarios()

        self.assertEqual(set(scenarios), CASES)

    def test_the_cases_cover_the_matrix(self):
        scenarios = load_scenarios()

        providers = {scenario.provider for scenario in scenarios.values()}
        self.assertEqual(providers, {"aws", "azure"})
        self.assertTrue(any(s.tfstate for s in scenarios.values()))
        self.assertTrue(any(s.upload for s in scenarios.values()))
        self.assertTrue(any(s.upload is None for s in scenarios.values()))
        self.assertTrue(any(s.egress for s in scenarios.values()))
        self.assertFalse(any(s.egress for s in scenarios.values() if s.tfstate))
        self.assertGreater(len(scenarios["stress"].type_codes()), 30)

    def test_names_are_fictional_and_marked(self):
        for case, scenario in load_scenarios().items():
            with self.subTest(case):
                self.assertTrue(scenario.name.startswith("Sample – "), scenario.name)

    def test_no_identifier_is_real(self):
        for case, scenario in load_scenarios().items():
            with self.subTest(case):
                text = scenario.path.read_text(encoding="utf-8")
                for found in GUID.findall(text):
                    self.assertTrue(
                        found.startswith(MADE_UP) or found == scenario.run_id, found
                    )
                for number in re.findall(r"\b\d{12}\b", text):
                    self.assertEqual(number, "123456789012")
                self.assertNotIn("AKIA", text)
        for path in (SAMPLES_DIR / "tfstate").glob("*.tfstate"):
            text = path.read_text(encoding="utf-8")
            for found in GUID.findall(text):
                self.assertTrue(found.startswith(MADE_UP), found)
            for number in re.findall(r"\b\d{12}\b", text):
                self.assertEqual(number, "123456789012")

    def test_the_tiers_of_a_resource_add_up_to_its_size(self):
        for case, scenario in load_scenarios().items():
            for resource in (scenario.egress or {}).get("resources", []):
                if resource.get("tiers"):
                    with self.subTest(case, resource=resource["name"]):
                        self.assertAlmostEqual(
                            sum(resource["tiers"].values()),
                            resource["size_gib"],
                            delta=GIB_TOLERANCE,
                        )

    def test_the_run_ids_and_clocks_are_fixed_and_distinct(self):
        scenarios = load_scenarios().values()

        self.assertEqual(len({s.run_id for s in scenarios}), len(CASES))
        self.assertEqual({s.clock.year for s in scenarios}, {2026})


class ExpectedFilesTests(unittest.TestCase):
    def test_one_list_names_the_files_of_a_case(self):
        scenarios = load_scenarios()

        self.assertEqual(
            expected_files(scenarios["aws_no_egress"]), list(EXPECTED_FILES["always"])
        )
        self.assertEqual(
            expected_files(scenarios["aws_typical"]),
            [*EXPECTED_FILES["always"], *EXPECTED_FILES["egress"]],
        )
        # Every report has its text dump in the same list
        for names in EXPECTED_FILES.values():
            for name in names:
                if name.endswith(".pdf"):
                    self.assertIn(f"texts/{Path(name).stem}.txt", names)
                if name.endswith(".html"):
                    self.assertIn(f"texts/{Path(name).stem}-html.txt", names)


if __name__ == "__main__":
    unittest.main()
