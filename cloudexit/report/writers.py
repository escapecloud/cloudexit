# cloudexit/report/writers.py
import os
import json
import logging
from typing import Any
from jinja2 import Environment

# ReportLab

# Utils
from cloudexit.report.helpers import initials
from cloudexit.resources import asset_path
from cloudexit.report.html_data import (
    transform_cost_inventory_for_html,
    transform_risk_inventory_for_html,
    transform_alt_tech_for_html,
)
from cloudexit.report.json_data import (
    transform_resource_inventory_for_json,
    transform_cost_inventory_for_json,
    transform_risk_inventory_for_json,
    transform_alt_tech_for_json,
)

# Configure logger
logger = logging.getLogger("core.engine.report")


def anonymize_string(s: str, num_visible: int = 4) -> str:
    if not isinstance(s, str):
        return "N/A"

    if len(s) <= 2 * num_visible:
        return "*" * len(s)

    middle_length = len(s) - 2 * num_visible
    return f"{s[:num_visible]}{'*' * middle_length}{s[-num_visible:]}"


def generate_html_report(
    report_path: str,
    metadata: dict[str, Any],
    resource_type_mapping: dict[str, dict[str, Any]],
    resource_inventory: list[dict[str, Any]],
    cost_data: list[dict[str, Any]],
    scoring_data: dict[str, Any] | None,
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    exit_strategy: int,
    alternative_technology_organizations: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> str:

    # Transform resource inventory
    resource_inventory_dict = {
        str(item["resource_type"]): {
            **item,
            "name": resource_type_mapping.get(str(item["resource_type"]), {}).get(
                "name", "Unknown Resource"
            ),
            "icon": (
                "/assets" + icon
                if (
                    icon := resource_type_mapping.get(
                        str(item["resource_type"]), {}
                    ).get("icon")
                )
                else ""
            ),
        }
        for item in resource_inventory
    }

    # Transform risks
    risks, severity_counts = transform_risk_inventory_for_html(
        risk_data, risk_definitions, resource_inventory_dict
    )

    # Transform costs
    months, cost_values, total_cost, currency, currency_symbol = (
        transform_cost_inventory_for_html(cost_data)
    )

    # Transform resource data with names and icons
    resource_counts = []
    for resource_type, resource in resource_inventory_dict.items():
        count = resource.get("count", 0)
        resource_info = resource_type_mapping.get(str(resource_type), {})
        name = resource_info.get("name", "Unknown Resource")
        icon = (resource_info.get("icon") or "").lstrip("/")

        resource_counts.append(
            {
                "resource_type": resource_type,
                "name": name,
                "icon": icon,
                "initials": initials(name),
                "count": count,
            }
        )

    # Calculate total resources
    total_resources = sum(item["count"] for item in resource_counts)

    # Transform alternative technologies
    alternative_technologies_data = transform_alt_tech_for_html(
        resource_inventory,
        alternatives,
        alternative_technologies,
        exit_strategy,
        alternative_technology_organizations=alternative_technology_organizations,
    )

    # Scoring Data
    scoring_context = {
        "scoring_data": bool(scoring_data),
        "exit_score": scoring_data.get("exit_score", 0) if scoring_data else 0,
        "human": scoring_data.get("human_score", 0) if scoring_data else 0,
        "technology": scoring_data.get("technology_score", 0) if scoring_data else 0,
        "operational": scoring_data.get("operational_score", 0) if scoring_data else 0,
    }

    # Render the HTML template
    template_content = asset_path("template", "index.html").read_text(encoding="utf-8")

    env = Environment(autoescape=True)
    template = env.from_string(template_content)
    html_content = template.render(
        **metadata,
        **scoring_context,
        risks=risks,
        high_risk_count=severity_counts["high"],
        medium_risk_count=severity_counts["medium"],
        low_risk_count=severity_counts["low"],
        total_cost=total_cost,
        months_json=json.dumps(months),
        costs_json=json.dumps(cost_values),
        currency_symbol=currency_symbol,
        total_resources=total_resources,
        resource_inventory=resource_counts,
        alternative_technologies=alternative_technologies_data,
        summary=summary,
    )

    # Save HTML report
    html_path = os.path.join(report_path, "index.html")
    with open(html_path, "w", encoding="utf-8") as report_file:
        report_file.write(html_content)

    return html_path


def generate_json_report(
    raw_data_path: str,
    metadata: dict[str, Any],
    resource_type_mapping: dict[str, dict[str, Any]],
    resource_inventory: list[dict[str, Any]],
    cost_data: list[dict[str, Any]],
    scoring_data: dict[str, Any] | None,
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    exit_strategy: int,
    provenance: dict[str, Any] | None = None,
) -> str:
    # Transform data for JSON
    transformed_resource_inventory = transform_resource_inventory_for_json(
        resource_inventory, resource_type_mapping
    )
    transformed_cost_inventory = transform_cost_inventory_for_json(cost_data)
    transformed_risk_inventory = transform_risk_inventory_for_json(
        risk_data, risk_definitions, resource_inventory
    )
    transformed_alt_tech = transform_alt_tech_for_json(
        resource_inventory, alternatives, alternative_technologies, exit_strategy
    )

    # Build the JSON structure
    report_json = {
        "meta": metadata,
        "data": {
            "resource_inventory": transformed_resource_inventory,
            "cost_inventory": transformed_cost_inventory,
            "risk_inventory": transformed_risk_inventory,
        },
    }

    if provenance:
        report_json["provenance"] = provenance

    # Add scoring_data only if present
    if scoring_data:
        report_json["data"]["scoring_data"] = {
            "exit_score": scoring_data.get("exit_score", 0),
            "human_score": scoring_data.get("human_score", 0),
            "technology_score": scoring_data.get("technology_score", 0),
            "operational_score": scoring_data.get("operational_score", 0),
        }

    # Add alternative technologies
    report_json["data"]["alternative_technologies"] = transformed_alt_tech

    # Save JSON to file
    json_path = os.path.join(raw_data_path, "assessment_result.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(report_json, json_file, indent=4)

    return json_path
