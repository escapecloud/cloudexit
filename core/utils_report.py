# core/utils_report.py
import os
import json
import logging
from typing import Any
from jinja2 import Environment

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Image,
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
from core.utils_report_pdf import (
    transform_resource_inventory_for_pdf,
    transform_cost_inventory_for_pdf,
    transform_risk_inventory_for_pdf,
    transform_alt_tech_for_pdf,
    draw_header_footer,
    draw_risk_chart,
    draw_cost_chart,
    draw_vendor_lockin_radar_chart,
    draw_exitscore_chart,
)

# Configure logger
logger = logging.getLogger("core.engine.report")
logger.setLevel(logging.INFO)


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
    return content


def _build_scope_section(metadata, provider_details, styles, content_style):
    """Page 1: Scope of Assessment table."""
    content = []
    content.append(Paragraph("Scope of Assessment", styles["Heading2"]))
    content.append(Paragraph("Defined scope of assessment:", content_style))

    scope_data = [["Name", "Value"]]

    if metadata["cloud_service_provider"] == 1:  # Azure
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


def _build_cost_section(cost_data, styles, content_style):
    """Page 1: Cost chart and table."""
    content = []
    content.append(Paragraph("Costs", styles["Heading2"]))

    tablecontent_style = styles["BodyText"]
    costs_block = "Examining the costs reveals the financial impact of the transition, allowing for more informed decision-making and strategic planning."
    costs_paragraph = Paragraph(costs_block, tablecontent_style)

    months, costs, currency_symbol = transform_cost_inventory_for_pdf(cost_data)
    cost_chart = draw_cost_chart(months, costs)

    costcharts_table_data = [
        [costs_paragraph, "", "", cost_chart, "", ""],
        months,
        [f"{currency_symbol} {cost:.2f}" for cost in costs],
    ]

    costcharts_table = Table(costcharts_table_data, colWidths=[2.58333333333 * cm] * 6)
    costcharts_table_style = TableStyle(
        [
            ("SPAN", (0, 0), (2, 0)),
            ("SPAN", (3, 0), (5, 0)),
            ("VALIGN", (0, 0), (2, 0), "TOP"),
            ("ALIGN", (0, 0), (2, 0), "LEFT"),
            ("LEFTPADDING", (0, 0), (2, 0), 0),
            ("RIGHTPADDING", (0, 0), (2, 0), 0),
            ("TOPPADDING", (0, 0), (2, 0), 0),
            ("BOTTOMPADDING", (0, 0), (2, 0), 0),
            ("BACKGROUND", (0, 1), (-1, 1), HexColor("#115e59")),
            ("TEXTCOLOR", (0, 1), (-1, 1), colors.white),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("ALIGN", (0, 1), (-1, 1), "CENTER"),
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica"),
            ("ALIGN", (0, 2), (-1, 2), "CENTER"),
            ("GRID", (0, 1), (-1, 2), 1, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("VALIGN", (0, 0), (2, 0), "TOP"),
        ]
    )
    costcharts_table.setStyle(costcharts_table_style)
    content.append(costcharts_table)
    content.append(PageBreak())
    return content


def _build_risk_section(
    risk_data, risk_definitions, resource_inventory, report_path, styles, content_style
):
    """Page 2: Risk Assessment chart and table."""
    content = []
    tablecontent_style = styles["BodyText"]

    content.append(Spacer(1, 12))
    content.append(Paragraph("Risk Assessment", styles["Heading1"]))
    content.append(
        Paragraph(
            "The Risk Assessment provides a thorough evaluation of potential risks "
            "associated with the cloud resources utilized in the project and the "
            "alternative technologies available in the market:",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    risks, severity_counts = transform_risk_inventory_for_pdf(
        risk_data, risk_definitions, resource_inventory
    )

    risk_chart_data = {
        "high": severity_counts["high"],
        "medium": severity_counts["medium"],
        "low": severity_counts["low"],
    }
    content.append(draw_risk_chart(risk_chart_data))
    content.append(Spacer(1, 12))

    severity_order = {"high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda r: severity_order[r["severity"]])

    severity_icon_map = {
        "high": (os.path.join(report_path, "assets/icons/severity/high.png"), 22.5, 12),
        "medium": (
            os.path.join(report_path, "assets/icons/severity/medium.png"),
            39,
            12,
        ),
        "low": (os.path.join(report_path, "assets/icons/severity/low.png"), 20.5, 12),
    }

    risk_table_data = [["#", "Risk name", "Impacted", "Severity"]]
    for i, risk in enumerate(risks):
        impacted_str = (
            str(risk["impacted_resources_count"])
            if risk["impacted_resources_count"] > 0
            else "-"
        )
        severity_level = risk["severity"].lower()
        icon_details = severity_icon_map.get(severity_level, None)

        if icon_details:
            icon_path, icon_width, icon_height = icon_details
            if os.path.exists(icon_path):
                severity_icon = Image(icon_path, width=icon_width, height=icon_height)
            else:
                severity_icon = Paragraph("N/A", tablecontent_style)
        else:
            severity_icon = Paragraph("N/A", tablecontent_style)

        risk_table_data.append([str(i + 1), risk["name"], impacted_str, severity_icon])

    total_risks = len(risks)
    risk_table_data.append(["Total Risks", "", "", str(total_risks)])

    risk_table = Table(risk_table_data, colWidths=[0.5 * cm, 10 * cm, 3 * cm, 2 * cm])
    risk_table_style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, -1), (-1, -1), HexColor("#115e59")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        ("ALIGN", (0, 1), (0, -2), "LEFT"),
        ("VALIGN", (0, 1), (0, -2), "MIDDLE"),
        ("ALIGN", (1, 1), (1, -2), "LEFT"),
        ("VALIGN", (1, 1), (1, -2), "MIDDLE"),
        ("ALIGN", (2, 1), (2, -2), "CENTER"),
        ("VALIGN", (2, 1), (2, -2), "MIDDLE"),
        ("ALIGN", (3, 1), (3, -2), "CENTER"),
        ("VALIGN", (3, 1), (3, -2), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "CENTER"),
        ("VALIGN", (-1, 0), (-1, 0), "MIDDLE"),
        ("ALIGN", (-1, -1), (-1, -1), "CENTER"),
        ("VALIGN", (-1, -1), (-1, -1), "MIDDLE"),
    ]
    risk_table.setStyle(TableStyle(risk_table_style_commands))
    content.append(risk_table)
    content.append(PageBreak())
    return content


def _build_scoring_section(scoring_data, report_path, styles, content_style):
    """Page 3: EscapeCloud Scoring (exit score gauge + vendor lock-in radar)."""
    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("EscapeCloud Scoring", styles["Heading1"]))
    content.append(Paragraph("Scoring #1 - Exit Score", styles["Heading2"]))
    content.append(
        Paragraph(
            "The following gauge chart visualizes a combined score that reflects "
            "both risk assessment results and the evaluation of alternative technologies:",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    exit_score = scoring_data.get("exit_score", 0) if scoring_data else 0

    chart_output_path = os.path.join(report_path, "assets/charts")
    os.makedirs(chart_output_path, exist_ok=True)

    exit_score_image_path = draw_exitscore_chart(
        exit_score, chart_output_path, width=750, height=500
    )

    exitscore_table_data = [
        ["", ""],
        ["Complex (0 - 20)", ""],
        ["Challenging (20 - 40)", ""],
        ["Manageable (40 - 60)", ""],
        ["Smooth Transition (60 - 80)", ""],
        ["Seamless (80 - 100)", ""],
    ]
    exitscore_table_data[1][1] = Image(
        exit_score_image_path, width=7.5 * cm, height=5 * cm
    )

    exitscore_table = Table(exitscore_table_data, colWidths=[5 * cm, 10.5 * cm])
    exitscore_table_style = TableStyle(
        [
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), HexColor("#115e59")),
            ("TEXTCOLOR", (0, 0), (1, 0), colors.white),
            ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (1, 0), "CENTER"),
            ("VALIGN", (0, 0), (1, 0), "MIDDLE"),
            ("SPAN", (1, 1), (1, 5)),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("ALIGN", (0, 1), (0, 5), "LEFT"),
            ("VALIGN", (0, 1), (0, 5), "MIDDLE"),
            ("ALIGN", (1, 1), (1, 1), "CENTER"),
            ("VALIGN", (1, 1), (1, 1), "MIDDLE"),
        ]
    )
    exitscore_table.setStyle(exitscore_table_style)
    content.append(exitscore_table)
    content.append(Spacer(1, 12))

    # Vendor Lock-In Score
    content.append(Paragraph("Scoring #2 - Vendor Lock-In Score", styles["Heading2"]))
    content.append(
        Paragraph(
            "The following radar chart visualizes the assessment of alternative "
            "technologies across three dimensions: Human (skills availability), "
            "Technology (maturity and vendor stability), and Operational (ecosystem "
            "and support services) — only where viable alternatives exist:",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    human_score = scoring_data.get("human_score", 0) if scoring_data else 0
    technology_score = scoring_data.get("technology_score", 0) if scoring_data else 0
    operational_score = scoring_data.get("operational_score", 0) if scoring_data else 0

    content.append(
        draw_vendor_lockin_radar_chart(human_score, technology_score, operational_score)
    )

    vendor_lockin_table = Table(
        [
            ["Human", "Technology", "Operational"],
            [human_score, technology_score, operational_score],
        ],
        colWidths=[5 * cm, 5 * cm, 5 * cm],
    )
    vendor_lockin_table_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ]
    )
    vendor_lockin_table.setStyle(vendor_lockin_table_style)
    content.append(vendor_lockin_table)
    content.append(PageBreak())
    return content


def _build_resource_section(
    resource_inventory, resource_type_mapping, report_path, styles, content_style
):
    """Page 4: Resource Inventory table."""
    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("Resource Inventory", styles["Heading1"]))
    content.append(
        Paragraph(
            "The Resource Inventory provides a summary of the cloud resources "
            "provisioned within the defined scope:",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    resources = transform_resource_inventory_for_pdf(
        resource_inventory, resource_type_mapping, report_path
    )
    total_resources = sum(res["count"] for res in resources)

    resource_data = [["#", "Resource type", "", "No."]]
    for res in resources:
        resource_data.append(
            [
                str(res["id"]),
                res["resource_name"],
                Image(res["icon_url"], width=20, height=20),
                str(res["count"]),
            ]
        )
    resource_data.append(["Total Resources", "", "", str(total_resources)])

    res_table = Table(resource_data, colWidths=[1 * cm, 11.5 * cm, 1.5 * cm, 1.5 * cm])
    res_table_style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, -1), (-1, -1), HexColor("#115e59")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        ("ALIGN", (0, 1), (0, -2), "LEFT"),
        ("VALIGN", (0, 1), (0, -2), "MIDDLE"),
        ("ALIGN", (1, 1), (1, -2), "LEFT"),
        ("VALIGN", (1, 1), (1, -2), "MIDDLE"),
        ("ALIGN", (2, 1), (2, -2), "CENTER"),
        ("VALIGN", (2, 1), (2, -2), "MIDDLE"),
        ("ALIGN", (3, 1), (3, -2), "CENTER"),
        ("VALIGN", (3, 1), (3, -2), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "CENTER"),
        ("VALIGN", (-1, 0), (-1, 0), "MIDDLE"),
        ("ALIGN", (-1, -1), (-1, -1), "CENTER"),
        ("VALIGN", (-1, -1), (-1, -1), "MIDDLE"),
    ]
    res_table.setStyle(TableStyle(res_table_style_commands))
    content.append(res_table)
    content.append(PageBreak())
    return content


def _build_alt_tech_section(
    resource_inventory,
    resource_type_mapping,
    alternatives,
    alternative_technologies,
    exit_strategy,
    report_path,
    styles,
    content_style,
):
    """Page 5: Alternative Technologies table."""
    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("Alternative Technologies", styles["Heading1"]))
    content.append(
        Paragraph(
            "The Alternative Technology provides a summary of the alternative technology "
            "landscape for each identified resource in the Resource Inventory, based on "
            "our dataset and market research. It also includes a count of the available "
            "alternative technologies for each resource:",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    alttech = transform_alt_tech_for_pdf(
        resource_inventory,
        resource_type_mapping,
        alternatives,
        alternative_technologies,
        exit_strategy,
        report_path,
    )

    alttech_data = [["#", "Resource type", "", "No."]]
    for res in alttech:
        alttech_data.append(
            [
                str(res["id"]),
                res["resource_name"],
                Image(res["icon_url"], width=20, height=20) if res["icon_url"] else "",
                str(res["count"]),
            ]
        )

    alttech_table = Table(
        alttech_data, colWidths=[1 * cm, 11.5 * cm, 1.5 * cm, 1.5 * cm]
    )
    alttech_table_style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#000000")),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (2, 0), (2, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("VALIGN", (0, 1), (0, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("VALIGN", (1, 1), (1, -1), "MIDDLE"),
        ("ALIGN", (2, 1), (2, -1), "CENTER"),
        ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "CENTER"),
        ("VALIGN", (-1, 0), (-1, 0), "MIDDLE"),
    ]
    alttech_table.setStyle(TableStyle(alttech_table_style_commands))
    content.append(alttech_table)
    content.append(PageBreak())
    return content


def generate_pdf_report(
    provider_details: dict[str, Any],
    report_path: str,
    metadata: dict[str, Any],
    resource_type_mapping: dict[str, Any],
    resource_inventory: list[dict[str, Any]],
    cost_data: list[dict[str, Any]],
    scoring_data: dict[str, Any] | None,
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    exit_strategy: int,
) -> str:
    pdf_path = os.path.join(report_path, "report.pdf")

    def header_footer(canvas, doc):
        draw_header_footer(report_path, canvas, doc)

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4, title="EscapeCloud_-_Cloud_Exit_Assessment"
    )
    styles = getSampleStyleSheet()
    content_style = ParagraphStyle(
        "ContentStyle", fontSize=10, leading=12, spaceAfter=10
    )
    styles["Heading1"].leading = 1.5 * styles["Heading1"].fontSize
    styles["Heading1"].textColor = HexColor("#112726")
    styles["Heading2"].leading = 1.5 * styles["Heading2"].fontSize
    styles["Heading2"].textColor = HexColor("#112726")

    content = []
    content += _build_summary_section(metadata, styles, content_style)
    content += _build_scope_section(metadata, provider_details, styles, content_style)
    content += _build_cost_section(cost_data, styles, content_style)
    content += _build_risk_section(
        risk_data,
        risk_definitions,
        resource_inventory,
        report_path,
        styles,
        content_style,
    )
    if metadata.get("assessment_type") == 2:
        content += _build_scoring_section(
            scoring_data,
            report_path,
            styles,
            content_style,
        )
    content += _build_resource_section(
        resource_inventory,
        resource_type_mapping,
        report_path,
        styles,
        content_style,
    )
    content += _build_alt_tech_section(
        resource_inventory,
        resource_type_mapping,
        alternatives,
        alternative_technologies,
        exit_strategy,
        report_path,
        styles,
        content_style,
    )

    logger.debug("Building the PDF document...")
    doc.build(content, onFirstPage=header_footer, onLaterPages=header_footer)

    return pdf_path
