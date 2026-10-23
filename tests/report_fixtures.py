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
            "organization_id": 10,
        }
    ]
    alternative_technology_organizations = [
        {
            "id": 10,
            "name": "OpenInfra Foundation",
            "url": "https://openinfra.org/",
            "country_code": "US",
            "stability_tier": 5,
            "years_in_business": 14,
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
        "alternative_technology_organizations": alternative_technology_organizations,
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
        "alternative_technology_organizations": [],
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


def build_fixture_context(fixture, report_dir, **overrides):
    # A report context from a fixture, for rendering the Basic report.
    from datetime import datetime, timezone

    from core.report.context import Provenance, build_context

    collected = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    provenance = overrides.pop(
        "provenance",
        Provenance(
            collector_version="2.0.0",
            collected_at=collected,
            generated_at=collected,
            run_id="01939b8e-7c2a-7d41-9a51-3c8f0a6e21d4",
            catalogue_version="2026-11-02",
            catalogue_sequence=41,
            catalogue_sha256="c" * 64,
        ),
    )
    values = dict(
        metadata=fixture["metadata"],
        provider_details=fixture["provider_details"],
        resource_type_mapping=fixture["resource_type_mapping"],
        resource_inventory=fixture["resource_inventory"],
        cost_data=fixture["cost_data"],
        risk_data=fixture["risk_data"],
        risk_definitions=fixture["risk_definitions"],
        alternatives=fixture["alternatives"],
        alternative_technologies=fixture["alternative_technologies"],
        alternative_technology_organizations=fixture[
            "alternative_technology_organizations"
        ],
        exit_strategy=fixture["exit_strategy"],
        tfstate_scope=None,
        provenance=provenance,
        report_path=str(report_dir),
    )
    values.update(overrides)
    return build_context(**values)


def stage_minimal_assets(report_path: str) -> None:
    # What the Basic report needs: the logo and the fallback icon. Copying the
    # whole icon set for every render would dominate the test time.
    report_assets = Path(report_path) / "assets"
    shutil.copytree(Path("assets") / "img", report_assets / "img", dirs_exist_ok=True)
    shutil.copytree(
        Path("assets") / "icons" / "misc",
        report_assets / "icons" / "misc",
        dirs_exist_ok=True,
    )
