# core/utils_report.py
import os
import json
from xml.sax.saxutils import escape
import logging
from typing import Any
from jinja2 import Environment

# ReportLab
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# Utils
from core.utils_report_html import (
    transform_cost_inventory_for_html,
    transform_risk_inventory_for_html,
    transform_alt_tech_for_html,
)
from core.utils_report_json import (
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
            "icon": "/assets"
            + resource_type_mapping.get(str(item["resource_type"]), {}).get(
                "icon", "/icons/default.png"
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
        icon = resource_info.get("icon", "assets/icons/default.png").lstrip("/")

        resource_counts.append(
            {"resource_type": resource_type, "name": name, "icon": icon, "count": count}
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
    template_path = os.path.join("assets", "template", "index.html")
    with open(template_path, "r") as file:
        template_content = file.read()

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
    with open(html_path, "w") as report_file:
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
    with open(json_path, "w") as json_file:
        json.dump(report_json, json_file, indent=4)

    return json_path


def _default_table_style() -> TableStyle:
    """Shared header style used by summary and scope tables."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 1, HexColor("#000000")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("TOPPADDING", (0, 0), (-1, 0), 12),
        ]
    )


def _build_summary_section(metadata, styles, content_style):
    """Page 1: Summary table."""
    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("Summary", styles["Heading1"]))
    content.append(Paragraph("Quick overview of the assessment:", content_style))

    cloud_service_provider_map = {
        "1": "Microsoft Azure",
        "2": "Amazon Web Services",
        "3": "Alibaba Cloud",
        "4": "Google Cloud",
    }
    exit_strategy_map = {
        "1": "Repatriation to On-Premises",
        "2": "Hybrid Cloud Adoption",
        "3": "Migration to Alternate Cloud",
    }
    type_map = {"1": "Basic", "2": "Standard"}

    summary_data = [
        ["Name", "Value"],
        [
            "Cloud Service Provider",
            cloud_service_provider_map.get(
                str(metadata["cloud_service_provider"]), "Unknown"
            ),
        ],
        [
            "Exit Strategy",
            exit_strategy_map.get(str(metadata["exit_strategy"]), "Unknown"),
        ],
        ["Assessment Type", type_map.get(str(metadata["assessment_type"]), "Unknown")],
        ["TimeStamp", metadata["timestamp"]],
    ]

    summary_table = Table(summary_data, colWidths=[4 * cm, 11.5 * cm])
    summary_table.setStyle(_default_table_style())
    content.append(summary_table)
    content.append(Spacer(1, 12))

    upload = metadata.get("upload")
    if upload:
        if upload.get("status") == "uploaded":
            note = "Uploaded to the platform."
            if upload.get("url"):
                url = escape(upload["url"])
                note += f' <a href="{url}" color="blue">{url}</a>'
        else:
            note = f"<b>Not uploaded:</b> {escape(upload.get('reason', 'unknown'))}"
        content.append(Paragraph(note, content_style))
    return content


# Cap on how many subscriptions / resource groups / regions a scope row lists
# before it summarises the remainder. The full list stays in the manifest.
MAX_SCOPE_VALUES = 3


def _shorten(value: str, head: int = 4, tail: int = 4) -> str:
    if not isinstance(value, str):
        return "N/A"
    if len(value) <= head + tail + 1:
        return value
    return f"{value[:head]}…{value[-tail:]}"


def _join_scope_values(values) -> str:
    if not values:
        return "Not determinable from state"
    shown = list(values)[:MAX_SCOPE_VALUES]
    remaining = len(values) - len(shown)
    text = ", ".join(shown)
    return f"{text}, +{remaining} more" if remaining > 0 else text


def _build_tfstate_scope_rows(metadata, scope):
    rows = []

    file_name = scope.get("file") or "N/A"
    sha256 = scope.get("sha256")
    if sha256:
        file_name = f"{file_name} (SHA-256 {_shorten(sha256)})"
    rows.append(["File", file_name])

    lineage = scope.get("lineage")
    serial = scope.get("serial")
    if lineage and serial is not None:
        rows.append(["State", f"{lineage} (Serial: {serial})"])
    elif lineage:
        rows.append(["State", lineage])
    elif serial is not None:
        rows.append(["State", f"Serial: {serial}"])

    if metadata["cloud_service_provider"] == 1:  # Azure
        rows.append(["Subscription", _join_scope_values(scope.get("subscriptions"))])
        rows.append(
            ["Resource Group(s)", _join_scope_values(scope.get("resource_groups"))]
        )
    else:  # AWS
        rows.append(["Region(s)", _join_scope_values(scope.get("locations"))])

    return rows


def _build_scope_section(
    metadata, provider_details, styles, content_style, tfstate_scope=None
):
    """Page 1: Scope of Assessment table."""
    content = []
    content.append(Paragraph("Scope of Assessment", styles["Heading2"]))

    tfstate_path = provider_details.get("tfstatePath")
    if tfstate_path:
        content.append(
            Paragraph(
                "Assessed from Terraform state. Resources not managed by "
                "Terraform are not included.",
                content_style,
            )
        )
    else:
        content.append(Paragraph("Defined scope of assessment:", content_style))

    scope_data = [["Name", "Value"]]

    if tfstate_path:
        # Fall back to the little we know from the config when the manifest is
        # unavailable, rather than rendering credential rows that are all N/A.
        scope = tfstate_scope or {"file": os.path.basename(tfstate_path)}
        scope_data.extend(_build_tfstate_scope_rows(metadata, scope))
    elif metadata["cloud_service_provider"] == 1:  # Azure
        scope_data.extend(
            [
                ["Tenant ID", provider_details.get("tenantId", "N/A")],
                ["Client ID", provider_details.get("clientId", "N/A")],
                [
                    "Client Secret",
                    anonymize_string(provider_details.get("clientSecret", "N/A")),
                ],
                ["Subscription ID", provider_details.get("subscriptionId", "N/A")],
                [
                    "Resource Group Name",
                    provider_details.get("resourceGroupName", "N/A"),
                ],
            ]
        )
    elif metadata["cloud_service_provider"] == 2:  # AWS
        scope_data.extend(
            [
                ["Access Key", provider_details.get("accessKey", "N/A")],
                [
                    "Secret Key",
                    anonymize_string(provider_details.get("secretKey", "N/A")),
                ],
                ["Region", provider_details.get("region", "N/A")],
            ]
        )
    else:
        scope_data.append(["N/A", "N/A"])

    scope_table = Table(scope_data, colWidths=[4 * cm, 11.5 * cm])
    scope_table.setStyle(_default_table_style())
    content.append(scope_table)
    content.append(Spacer(1, 12))
    return content
