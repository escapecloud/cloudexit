import shutil
from pathlib import Path


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


def build_empty_report_fixture():
    metadata = {
        "name": "Empty State Assessment",
        "cloud_service_provider": 2,
        "exit_strategy": 1,
        "assessment_type": 2,
        "timestamp": "2026-05-08 10:00:00 UTC",
    }
    provider_details = {
        "accessKey": "AKIA_EMPTY",
        "secretKey": "SECRET_EMPTY",
        "region": "eu-central-1",
    }
    return {
        "metadata": metadata,
        "provider_details": provider_details,
        "resource_type_mapping": {},
        "resource_inventory": [],
        "cost_data": [],
        "risk_definitions": [],
        "risk_data": [],
        "alternatives": [],
        "alternative_technologies": [],
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
