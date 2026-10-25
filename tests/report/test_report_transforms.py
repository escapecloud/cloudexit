import unittest

from cloudexit.report.json_data import (
    transform_alt_tech_for_json,
    transform_resource_inventory_for_json,
    transform_risk_inventory_for_json,
)


def build_resource_type_mapping():
    return {
        "101": {
            "id": 101,
            "code": "AWS.EC2.DescribeInstances.Reservations",
            "name": "EC2 Instance",
            "icon": "/icons/aws/Compute/EC2.png",
        },
        "202": {
            "id": 202,
            "code": "AWS.S3.ListBuckets.Buckets",
            "name": "S3 Bucket",
            "icon": "/icons/aws/Storage/Simple-Storage-Service.png",
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
            "organization_id": 10,
        },
        {
            "id": 2,
            "product_name": "MinIO",
            "product_description": "Object storage platform.",
            "product_url": "https://min.io/",
            "open_source": "t",
            "support_plan": "f",
            "status": "t",
            "organization_id": 20,
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


def build_alternative_technology_organizations():
    return [
        {
            "id": 10,
            "name": "OpenInfra Foundation",
            "url": "https://openinfra.org/",
            "country_code": "US",
        },
        {
            "id": 20,
            "name": "European Storage Collective",
            "url": "https://example.eu/",
            "country_code": "DE",
        },
    ]


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


if __name__ == "__main__":
    unittest.main()
