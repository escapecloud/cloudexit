# cloudexit/report/writers.py
import os
import json
import logging
from typing import Any
from jinja2 import Environment

from cloudexit.report.context import ReportContext
from cloudexit.report.html_sections import html_sections
from cloudexit.resources import asset_path
from cloudexit.report.json_data import (
    transform_resource_inventory_for_json,
    transform_cost_inventory_for_json,
    transform_risk_inventory_for_json,
    transform_alt_tech_for_json,
)

# Configure logger
logger = logging.getLogger(__name__)


def generate_html_report(
    context: ReportContext, egress_estimate: dict[str, Any] | None = None
) -> str:
    template_content = asset_path("template", "index.html").read_text(encoding="utf-8")

    env = Environment(autoescape=True)
    template = env.from_string(template_content)
    html_content = template.render(page=html_sections(context, egress_estimate))

    html_path = os.path.join(context.report_path, "index.html")
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
