# core/utils_report_html.py
import logging
from typing import Any

from core.utils_report_common import (
    summarize_alternative_technologies,
    summarize_costs,
    summarize_risks,
)

# Configure logger
logger = logging.getLogger("core.engine.report_html")
logger.setLevel(logging.INFO)


def transform_cost_inventory_for_html(
    cost_data: list[dict[str, Any]],
) -> tuple[list[str], list[float], float, str, str]:
    return summarize_costs(cost_data)


def transform_risk_inventory_for_html(
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    resource_inventory: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    severity_order = {"high": 1, "medium": 2, "low": 3}
    resource_name_map = {
        str(key): value["name"] for key, value in resource_inventory.items()
    }
    risks, severity_counts = summarize_risks(
        risk_data,
        risk_definitions,
        resource_name_map=resource_name_map,
    )
    risks.sort(key=lambda x: severity_order.get(x["severity"], 4))
    return risks, severity_counts


def transform_alt_tech_for_html(
    resource_inventory: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    exit_strategy: int,
    alternative_technology_organizations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    alt_tech_data = []
    grouped_alt_tech = summarize_alternative_technologies(
        resource_inventory,
        alternatives,
        alternative_technologies,
        exit_strategy,
        alternative_technology_organizations=alternative_technology_organizations,
    )
    for resource in resource_inventory:
        resource_type = str(resource.get("resource_type"))
        for tech in grouped_alt_tech.get(resource_type, []):
            alt_tech_data.append(
                {
                    "resource_type_id": resource.get("resource_type"),
                    **tech,
                }
            )
    return alt_tech_data
