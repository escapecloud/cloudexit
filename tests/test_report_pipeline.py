import json
import tempfile
import unittest
from pathlib import Path

from core.utils_report import (
    generate_html_report,
    generate_json_report,
    generate_pdf_report,
)
from core.utils_report_json import transform_cost_inventory_for_json
from tests.report_fixtures import (
    build_empty_report_fixture,
    build_report_fixture,
    stage_report_assets,
)


class ReportPipelineSmokeTests(unittest.TestCase):
    def test_generate_html_report_creates_expected_output(self):
        fixture = build_report_fixture()

        with tempfile.TemporaryDirectory() as report_dir:
            html_path = generate_html_report(
                report_dir,
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

            self.assertTrue(Path(html_path).exists())
            html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("Smoke Test Assessment", html)
        self.assertIn("Amazon Web Services", html)
        self.assertIn("OpenStack", html)
        self.assertIn("EC2 Instance", html)

    def test_generate_html_report_renders_empty_state_output(self):
        fixture = build_empty_report_fixture()

        with tempfile.TemporaryDirectory() as report_dir:
            html_path = generate_html_report(
                report_dir,
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

            self.assertTrue(Path(html_path).exists())
            html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("Empty State Assessment", html)
        self.assertIn("No risk data available.", html)
        self.assertIn("No cost data available.", html)
        self.assertIn("No exit score data available.", html)
        self.assertIn("No vendor lock-in score data available.", html)
        self.assertIn("No resources were discovered during the assessment.", html)
        self.assertIn("No alternative technologies are available", html)
        self.assertNotIn('id="risksChart"', html)
        self.assertNotIn('id="costsChart"', html)
        self.assertNotIn('id="exitScoreChart"', html)
        self.assertNotIn('id="vendorLockInScoreChart"', html)

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
            stage_report_assets(report_dir)

            pdf_path = generate_pdf_report(
                fixture["provider_details"],
                report_dir,
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
