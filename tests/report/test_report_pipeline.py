import json
import tempfile
import unittest
from pathlib import Path

from cloudexit.report.executive_summary import generate_executive_summary
from cloudexit.report.writers import (
    generate_html_report,
    generate_json_report,
)
from cloudexit.report.json_data import transform_cost_inventory_for_json
from tests.support.report import (
    build_empty_report_fixture,
    build_fixture_context,
    build_report_fixture,
    stage_minimal_assets,
)


class ReportPipelineSmokeTests(unittest.TestCase):
    def html(self, fixture, **overrides):
        with tempfile.TemporaryDirectory() as report_dir:
            context = build_fixture_context(fixture, report_dir, **overrides)
            html_path = generate_html_report(context)

            self.assertTrue(Path(html_path).exists())
            self.assertEqual(Path(html_path).name, "index.html")
            return Path(html_path).read_text(encoding="utf-8")

    def test_generate_html_report_creates_expected_output(self):
        html = self.html(build_report_fixture())

        self.assertIn("Smoke Test Assessment", html)
        self.assertIn("Amazon Web Services", html)
        self.assertIn("OpenStack", html)
        self.assertIn("OpenInfra Foundation", html)
        self.assertIn("EC2 Instance", html)
        self.assertIn('data-os="1"', html)

    def test_generate_html_report_renders_empty_state_output(self):
        html = self.html(build_empty_report_fixture())

        self.assertIn("Empty State Assessment", html)
        self.assertIn("No exit risks were identified.", html)
        self.assertIn("No cost data available for this assessment.", html)
        self.assertIn("No resources were found in the defined scope.", html)
        self.assertIn("No resource types to list alternatives for.", html)
        # No filters when there is nothing to filter
        self.assertNotIn('class="toolbar"', html)

    def test_generate_json_report_creates_expected_structure(self):
        fixture = build_report_fixture()

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = Path(tmp_dir) / "raw_data"
            raw_data_path.mkdir()

            json_path = generate_json_report(
                str(raw_data_path),
                fixture["metadata"],
                fixture["resource_type_mapping"],
                fixture["resource_inventory"],
                fixture["cost_data"],
                None,
                fixture["risk_data"],
                fixture["risk_definitions"],
                fixture["alternatives"],
                fixture["alternative_technologies"],
                fixture["exit_strategy"],
            )

            payload = json.loads(Path(json_path).read_text(encoding="utf-8"))

        self.assertEqual(payload["meta"]["name"], "Smoke Test Assessment")
        self.assertEqual(
            payload["data"]["resource_inventory"][0]["resource_name"], "EC2 Instance"
        )
        self.assertEqual(payload["data"]["cost_inventory"][0]["month"], "2025-11-01")
        self.assertEqual(
            payload["data"]["alternative_technologies"]["1"][0]["product_name"],
            "OpenStack",
        )

    def test_generate_pdf_report_creates_non_empty_file(self):
        fixture = build_report_fixture()

        with tempfile.TemporaryDirectory() as report_dir:
            stage_minimal_assets(report_dir)

            pdf_path = generate_executive_summary(
                build_fixture_context(fixture, report_dir)
            )

            pdf_file = Path(pdf_path)

            self.assertEqual(pdf_file.name, "report.pdf")
            self.assertTrue(pdf_file.exists())
            self.assertGreater(pdf_file.stat().st_size, 0)


class EmptyCostInventoryReportTests(unittest.TestCase):
    """tfstate mode skips Stage 4, so reports must render with resources but no costs."""

    def test_html_report_renders_resources_without_cost_data(self):
        fixture = build_report_fixture()
        with tempfile.TemporaryDirectory() as report_dir:
            context = build_fixture_context(fixture, report_dir, cost_data=[])
            html = Path(generate_html_report(context)).read_text(encoding="utf-8")

        self.assertIn("EC2 Instance", html)
        self.assertIn("No cost data available for this assessment.", html)
        self.assertNotIn('class="bars"', html)

    def test_json_report_has_empty_cost_inventory(self):
        fixture = build_report_fixture()

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = Path(tmp_dir) / "raw_data"
            raw_data_path.mkdir()

            json_path = generate_json_report(
                str(raw_data_path),
                fixture["metadata"],
                fixture["resource_type_mapping"],
                fixture["resource_inventory"],
                [],
                None,
                fixture["risk_data"],
                fixture["risk_definitions"],
                fixture["alternatives"],
                fixture["alternative_technologies"],
                fixture["exit_strategy"],
            )

            payload = json.loads(Path(json_path).read_text(encoding="utf-8"))

        self.assertEqual(payload["data"]["cost_inventory"], [])
        self.assertEqual(
            payload["data"]["resource_inventory"][0]["resource_name"], "EC2 Instance"
        )

    def test_pdf_report_generates_without_cost_data(self):
        fixture = build_report_fixture()

        with tempfile.TemporaryDirectory() as report_dir:
            stage_minimal_assets(report_dir)

            pdf_path = generate_executive_summary(
                build_fixture_context(fixture, report_dir, cost_data=[])
            )

            pdf_file = Path(pdf_path)

            self.assertTrue(pdf_file.exists())
            self.assertGreater(pdf_file.stat().st_size, 0)


class ReportTransformTests(unittest.TestCase):
    def test_transform_cost_inventory_for_json_sorts_months(self):
        unsorted_costs = [
            {"month": "2026-03-01", "cost": 9.0, "currency": "USD"},
            {"month": "2026-01-01", "cost": 14.75, "currency": "USD"},
            {"month": "2026-02-01", "cost": 11.25, "currency": "USD"},
        ]

        transformed = transform_cost_inventory_for_json(unsorted_costs)

        self.assertEqual(
            [item["month"] for item in transformed],
            ["2026-01-01", "2026-02-01", "2026-03-01"],
        )


if __name__ == "__main__":
    unittest.main()
