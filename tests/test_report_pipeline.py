import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.utils_report import (
    generate_html_report,
    generate_json_report,
    generate_pdf_report,
)
from core.utils_report_json import transform_cost_inventory_for_json


def build_report_fixture():
    metadata = {
        "name": "Smoke Test Assessment",
        "cloud_service_provider": 2,
        "exit_strategy": 1,
        "assessment_type": 1,
        "timestamp": "2026-05-07 12:00:00 UTC",
    }
    provider_details = {
        "accessKey": "AKIA_TEST",
        "secretKey": "SECRET_TEST",
        "region": "eu-central-1",
    }
    resource_type_mapping = {
        "101": {
            "id": 101,
            "code": "AWS.EC2.DescribeInstances.Reservations",
            "name": "EC2 Instance",
            "icon": "/icons/misc/no_image.png",
        }
    }
    resource_inventory = [
        {"resource_type": 101, "location": "eu-central-1", "count": 2},
    ]
    cost_data = [
        {"month": "2025-11-01", "cost": 10.5, "currency": "USD"},
        {"month": "2025-12-01", "cost": 12.0, "currency": "USD"},
        {"month": "2026-01-01", "cost": 14.75, "currency": "USD"},
        {"month": "2026-02-01", "cost": 11.25, "currency": "USD"},
        {"month": "2026-03-01", "cost": 9.0, "currency": "USD"},
        {"month": "2026-04-01", "cost": 13.4, "currency": "USD"},
    ]
    risk_definitions = [
        {
            "id": "1",
            "name": "Limited Alternatives",
            "description": "There are only a few alternatives available.",
            "severity": "high",
        }
    ]
    risk_data = [
        {"resource_type": "101", "risk": "1"},
    ]
    alternatives = [
        {"resource_type": "101", "strategy_type": "1", "alternative_technology": 1},
    ]
    alternative_technologies = [
        {
            "id": 1,
            "product_name": "OpenStack",
            "product_description": "Open source cloud platform.",
            "product_url": "https://www.openstack.org/",
            "open_source": "t",
            "support_plan": "t",
            "status": "t",
        }
    ]
    return {
        "metadata": metadata,
        "provider_details": provider_details,
        "resource_type_mapping": resource_type_mapping,
        "resource_inventory": resource_inventory,
        "cost_data": cost_data,
        "risk_definitions": risk_definitions,
        "risk_data": risk_data,
        "alternatives": alternatives,
        "alternative_technologies": alternative_technologies,
        "exit_strategy": 1,
    }


def stage_report_assets(report_path: str) -> None:
    report_assets = Path(report_path) / "assets"
    report_assets.mkdir(parents=True, exist_ok=True)

    source_assets = Path("assets")
    for folder in ("css", "img", "icons"):
        shutil.copytree(
            source_assets / folder,
            report_assets / folder,
            dirs_exist_ok=True,
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
