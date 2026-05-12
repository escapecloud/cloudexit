# core/utils_report_json.py
import logging
from typing import Any

from core.utils_report_common import (
    enrich_resource_inventory,
    sort_cost_data,
    summarize_alternative_technologies,
    summarize_risks,
)

# Configure logger
logger = logging.getLogger("core.engine.report_json")
logger.setLevel(logging.INFO)


def transform_resource_inventory_for_json(
    resource_inventory: list[dict[str, Any]],
    resource_type_mapping: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched_resources = enrich_resource_inventory(
        resource_inventory, resource_type_mapping
    )
    return [
        {
            "id": resource["id"],
            "code": resource["code"],
            "resource_name": resource["resource_name"],
            "location": resource["location"],
            "count": resource["count"],
        }
        for resource in enriched_resources
    ]


def transform_cost_inventory_for_json(
    cost_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sorted_cost_data = sort_cost_data(cost_data)

    cost_inventory = [
        {
            "month": item["month"],
            "cost": round(item["cost"], 2),
            "currency": item["currency"],
        }
        for item in sorted_cost_data
    ]
    return cost_inventory


def transform_risk_inventory_for_json(
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    resource_inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resource_id_map = {
        str(value["resource_type"]): key + 1
        for key, value in enumerate(resource_inventory)
    }
    risks, _ = summarize_risks(
        risk_data,
        risk_definitions,
        resource_id_map=resource_id_map,
    )
    return [
        {
            "id": risk["id"],
            "name": risk["name"],
            "description": risk["description"],
            "severity": risk["severity"],
            "impacted_resources": risk["impacted_resource_ids"] or [],
            "impacted_resources_count": risk["impacted_resources_count"],
        }
        for risk in risks
    ]


def transform_alt_tech_for_json(
    resource_inventory: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    exit_strategy: int,
) -> dict[int, list[dict[str, Any]]]:
    resource_id_map = {
        str(value["resource_type"]): key + 1
        for key, value in enumerate(resource_inventory)
    }
    grouped_alt_tech = summarize_alternative_technologies(
        resource_inventory,
        alternatives,
        alternative_technologies,
        exit_strategy,
    )
    grouped_alt_tech_data = {
        resource_id: [] for resource_id in resource_id_map.values()
    }
    for resource_type, technologies in grouped_alt_tech.items():
        resource_id = resource_id_map.get(resource_type)
        if not resource_id:
            continue
        grouped_alt_tech_data[resource_id] = [
            {"id": idx + 1, **tech} for idx, tech in enumerate(technologies)
        ]

    return {
        key: grouped_alt_tech_data[key] for key in sorted(grouped_alt_tech_data.keys())
    }
