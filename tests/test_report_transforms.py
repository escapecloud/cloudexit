import tempfile
import unittest

from core.utils_report_html import (
    transform_alt_tech_for_html,
    transform_cost_inventory_for_html,
    transform_risk_inventory_for_html,
)
from core.utils_report_json import (
    transform_alt_tech_for_json,
    transform_resource_inventory_for_json,
    transform_risk_inventory_for_json,
)
from core.utils_report_pdf import (
    transform_alt_tech_for_pdf,
    transform_cost_inventory_for_pdf,
    transform_resource_inventory_for_pdf,
    transform_risk_inventory_for_pdf,
)


def build_resource_type_mapping():
    return {
        "101": {
            "id": 101,
            "code": "AWS.EC2.DescribeInstances.Reservations",
            "name": "EC2 Instance",
            "icon": "/icons/misc/no_image.png",
        },
        "202": {
            "id": 202,
            "code": "AWS.S3.ListBuckets.Buckets",
            "name": "S3 Bucket",
            "icon": "/icons/misc/no_image.png",
        },
    }


def build_resource_inventory():
    return [
        {"resource_type": 101, "location": "eu-central-1", "count": 2},
        {"resource_type": 202, "location": "eu-central-1", "count": 1},
    ]


def build_risk_definitions():
    return [
        {
            "id": "1",
            "name": "Limited Alternatives",
            "description": "There are only a few alternatives available.",
            "severity": "high",
        },
        {
            "id": "7",
            "name": "Large Service Footprint",
            "description": "The service footprint is broad.",
            "severity": "medium",
        },
    ]


def build_risk_data():
    return [
        {"resource_type": "101", "risk": "1"},
        {"resource_type": "202", "risk": "1"},
        {"resource_type": "null", "risk": "7"},
    ]


def build_alternatives():
    return [
        {"resource_type": 101, "strategy_type": 1, "alternative_technology": 1},
        {"resource_type": 101, "strategy_type": 3, "alternative_technology": 2},
        {"resource_type": 202, "strategy_type": 1, "alternative_technology": 2},
    ]


def build_alternative_technologies():
    return [
        {
            "id": 1,
            "product_name": "OpenStack",
            "product_description": "Open source cloud platform.",
            "product_url": "https://www.openstack.org/",
            "open_source": "t",
            "support_plan": "t",
            "status": "t",
        },
        {
            "id": 2,
            "product_name": "MinIO",
            "product_description": "Object storage platform.",
            "product_url": "https://min.io/",
            "open_source": "t",
            "support_plan": "f",
            "status": "t",
        },
        {
            "id": 3,
            "product_name": "Inactive Tech",
            "product_description": "Should be ignored.",
            "product_url": "https://example.com/",
            "open_source": "t",
            "support_plan": "t",
            "status": "f",
        },
    ]


class HtmlTransformTests(unittest.TestCase):
    def test_transform_cost_inventory_for_html_sorts_and_sums_costs(self):
        months, cost_values, total_cost, currency_code, currency_symbol = (
            transform_cost_inventory_for_html(
                [
                    {"month": "2026-02-01", "cost": 11.25, "currency": "USD"},
                    {"month": "2026-01-01", "cost": 14.75, "currency": "USD"},
                ]
            )
        )

        self.assertEqual(months, ["Jan", "Feb"])
        self.assertEqual(cost_values, [14.75, 11.25])
        self.assertEqual(total_cost, 26.0)
        self.assertEqual(currency_code, "USD")
        self.assertEqual(currency_symbol, "$")

    def test_transform_risk_inventory_for_html_counts_overall_and_resource_risks(self):
        resource_inventory = {
            "101": {"name": "EC2 Instance"},
            "202": {"name": "S3 Bucket"},
        }

        risks, severity_counts = transform_risk_inventory_for_html(
            build_risk_data(),
            build_risk_definitions(),
            resource_inventory,
        )

        self.assertEqual([risk["severity"] for risk in risks], ["high", "medium"])
        self.assertEqual(risks[0]["impacted_resources_count"], 2)
        self.assertCountEqual(
            risks[0]["impacted_resources"], ["EC2 Instance", "S3 Bucket"]
        )
        self.assertIsNone(risks[1]["impacted_resources_count"])
        self.assertEqual(severity_counts, {"high": 1, "medium": 1, "low": 0})

    def test_transform_alt_tech_for_html_filters_by_strategy_and_status(self):
        transformed = transform_alt_tech_for_html(
            build_resource_inventory(),
            build_alternatives(),
            build_alternative_technologies(),
            exit_strategy=1,
        )

        self.assertEqual(len(transformed), 2)
        self.assertEqual(transformed[0]["product_name"], "OpenStack")
        self.assertEqual(transformed[1]["product_name"], "MinIO")
        self.assertTrue(transformed[0]["open_source"])
        self.assertFalse(transformed[1]["support_plan"])


class JsonTransformTests(unittest.TestCase):
    def test_transform_resource_inventory_for_json_maps_names_and_codes(self):
        transformed = transform_resource_inventory_for_json(
            build_resource_inventory(),
            build_resource_type_mapping(),
        )

        self.assertEqual(transformed[0]["resource_name"], "EC2 Instance")
        self.assertEqual(
            transformed[1]["code"],
            "AWS.S3.ListBuckets.Buckets",
        )

    def test_transform_risk_inventory_for_json_maps_impacted_resource_ids(self):
        transformed = transform_risk_inventory_for_json(
            build_risk_data(),
            build_risk_definitions(),
            build_resource_inventory(),
        )

        transformed_by_id = {item["id"]: item for item in transformed}
        self.assertCountEqual(transformed_by_id["1"]["impacted_resources"], [1, 2])
        self.assertEqual(transformed_by_id["1"]["impacted_resources_count"], 2)
        self.assertIsNone(transformed_by_id["7"]["impacted_resources_count"])

    def test_transform_alt_tech_for_json_groups_by_resource_id(self):
        transformed = transform_alt_tech_for_json(
            build_resource_inventory(),
            build_alternatives(),
            build_alternative_technologies(),
            exit_strategy=1,
        )

        self.assertEqual(list(transformed.keys()), [1, 2])
        self.assertEqual(transformed[1][0]["product_name"], "OpenStack")
        self.assertEqual(transformed[2][0]["product_name"], "MinIO")


class PdfTransformTests(unittest.TestCase):
    def test_transform_cost_inventory_for_pdf_limits_to_last_six_months(self):
        months, costs, currency_symbol = transform_cost_inventory_for_pdf(
            [
                {"month": "2025-10-01", "cost": 8.0, "currency": "USD"},
                {"month": "2025-11-01", "cost": 10.5, "currency": "USD"},
                {"month": "2025-12-01", "cost": 12.0, "currency": "USD"},
                {"month": "2026-01-01", "cost": 14.75, "currency": "USD"},
                {"month": "2026-02-01", "cost": 11.25, "currency": "USD"},
                {"month": "2026-03-01", "cost": 9.0, "currency": "USD"},
                {"month": "2026-04-01", "cost": 13.4, "currency": "USD"},
            ]
        )

        self.assertEqual(months, ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr"])
        self.assertEqual(costs, [10.5, 12.0, 14.75, 11.25, 9.0, 13.4])
        self.assertEqual(currency_symbol, "$")

    def test_transform_risk_inventory_for_pdf_counts_resource_backed_risks(self):
        risks, severity_counts = transform_risk_inventory_for_pdf(
            build_risk_data(),
            build_risk_definitions(),
            build_resource_inventory(),
        )

        risks_by_name = {item["name"]: item for item in risks}
        self.assertEqual(
            risks_by_name["Limited Alternatives"]["impacted_resources_count"], 2
        )
        self.assertEqual(
            risks_by_name["Large Service Footprint"]["impacted_resources_count"], 0
        )
        self.assertEqual(severity_counts, {"high": 1, "medium": 1, "low": 0})

    def test_transform_resource_inventory_for_pdf_builds_report_relative_icon_paths(
        self,
    ):
        with tempfile.TemporaryDirectory() as report_dir:
            transformed = transform_resource_inventory_for_pdf(
                build_resource_inventory(),
                build_resource_type_mapping(),
                report_dir,
            )

        self.assertEqual(transformed[0]["resource_name"], "EC2 Instance")
        self.assertTrue(
            transformed[0]["icon_url"].endswith("/assets/icons/misc/no_image.png")
        )

    def test_transform_alt_tech_for_pdf_counts_matching_alternatives(self):
        with tempfile.TemporaryDirectory() as report_dir:
            transformed = transform_alt_tech_for_pdf(
                build_resource_inventory(),
                build_resource_type_mapping(),
                build_alternatives(),
                build_alternative_technologies(),
                exit_strategy=1,
                report_path=report_dir,
            )

        self.assertEqual(transformed[0]["count"], 1)
        self.assertEqual(transformed[1]["count"], 1)
        self.assertTrue(
            transformed[0]["icon_url"].endswith("/assets/icons/misc/no_image.png")
        )


if __name__ == "__main__":
    unittest.main()
