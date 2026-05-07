# core/utils_report.py
import os
import json
import logging
from typing import List, Dict, Any, Optional
from jinja2 import Template

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
    metadata: Dict[str, Any],
    resource_type_mapping: Dict[str, Dict[str, Any]],
    resource_inventory: List[Dict[str, Any]],
    cost_data: List[Dict[str, Any]],
    scoring_data: Optional[Dict[str, Any]],
    risk_data: List[Dict[str, Any]],
    risk_definitions: List[Dict[str, Any]],
    alternatives: List[Dict[str, Any]],
    alternative_technologies: List[Dict[str, Any]],
    exit_strategy: int,
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
        resource_inventory, alternatives, alternative_technologies, exit_strategy
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

    template = Template(template_content)
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
    metadata: Dict[str, Any],
    resource_type_mapping: Dict[str, Dict[str, Any]],
    resource_inventory: List[Dict[str, Any]],
    cost_data: List[Dict[str, Any]],
    scoring_data: Optional[Dict[str, Any]],
    risk_data: List[Dict[str, Any]],
    risk_definitions: List[Dict[str, Any]],
    alternatives: List[Dict[str, Any]],
    alternative_technologies: List[Dict[str, Any]],
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


def generate_pdf_report(
    provider_details: Dict[str, Any],
    report_path: str,
    metadata: Dict[str, Any],
    resource_type_mapping: Dict[str, Any],
    resource_inventory: List[Dict[str, Any]],
    cost_data: List[Dict[str, Any]],
    scoring_data: Optional[Dict[str, Any]],
    risk_data: List[Dict[str, Any]],
    risk_definitions: List[Dict[str, Any]],
    alternatives: List[Dict[str, Any]],
    alternative_technologies: List[Dict[str, Any]],
    exit_strategy: int,
) -> str:
    # Define the PDF path
    pdf_path = os.path.join(report_path, "report.pdf")

    # Define a template for the header and footer
    def header_footer(canvas, doc):
        # Make sure draw_header_footer is defined and accessible
        draw_header_footer(report_path, canvas, doc)

    # Create a document template with the header and footer
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
    tablecontent_style = styles["BodyText"]

    # Define a custom padding value
    header_padding = 12

    content = []

    # --- # Page 1: Summary ---
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Summary", styles["Heading1"]))
    summary_block1 = "Quick overview of the assessment:"
    content.append(Paragraph(summary_block1, content_style))

    # Prepare mappings
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

    # Prepare the summary data
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

    # Column widths
    summary_colWidths = [4 * cm, 11.5 * cm]

    # Create the summary table
    summary_table = Table(summary_data, colWidths=summary_colWidths)

    # Define the summary table style
    summary_table_style = TableStyle(
        [
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                HexColor("#115e59"),
            ),  # Header row background color
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),  # Header row text color
            ("GRID", (0, 0), (-1, -1), 1, HexColor("#000000")),  # Grid lines
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),  # Left align all cells
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),  # Middle vertical alignment for all cells
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),  # Bold font for header row
            ("FONTSIZE", (0, 0), (-1, 0), 11),  # Font size for header row
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
            ("TOPPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
        ]
    )

    summary_table.setStyle(summary_table_style)

    # Add summary to content
    content.append(summary_table)
    content.append(Spacer(1, 12))

    # --- Page 1: Scope of Assessment ---
    content.append(Paragraph("Scope of Assessment", styles["Heading2"]))
    scope_block1 = "Defined scope of assessment:"
    content.append(Paragraph(scope_block1, content_style))

    # Prepare the scope data
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

    # Column widths
    scope_colWidths = [4 * cm, 11.5 * cm]

    # Create the scope table
    scope_table = Table(scope_data, colWidths=scope_colWidths)

    # Define the scope table style
    scope_table_style = TableStyle(
        [
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                HexColor("#115e59"),
            ),  # Header row background color
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),  # Header row text color
            ("GRID", (0, 0), (-1, -1), 1, HexColor("#000000")),  # Grid lines
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),  # Left align all cells
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),  # Middle vertical alignment for all cells
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),  # Bold font for header row
            ("FONTSIZE", (0, 0), (-1, 0), 11),  # Font size for header row
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
            ("TOPPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
        ]
    )

    scope_table.setStyle(scope_table_style)

    # Add scope to content
    content.append(scope_table)
    content.append(Spacer(1, 12))

    # --- # Page 1: Costs ---
    content.append(Paragraph("Costs", styles["Heading2"]))
    # costs_block1 = "Overview of the costs for the last 6 months:"
    # content.append(Paragraph(costs_block1, content_style))
    costs_block2 = "Examining the costs reveals the financial impact of the transition, allowing for more informed decision-making and strategic planning."
    costs_paragraph = Paragraph(costs_block2, tablecontent_style)

    # Transform the cost data for the PDF
    months, costs, currency_symbol = transform_cost_inventory_for_pdf(cost_data)

    # Draw the cost chart
    cost_chart = draw_cost_chart(months, costs)

    # Create the data structure for the table
    costcharts_table_data = [
        [costs_paragraph, "", "", cost_chart, "", ""],  # Row 1: Paragraph and Chart
        months,  # Row 2: Months
        [f"{currency_symbol} {cost:.2f}" for cost in costs],  # Row 3: Costs
    ]

    # Create the table with 6 columns
    costcharts_table = Table(
        costcharts_table_data, colWidths=[2.58333333333 * cm] * 6  # Equal width columns
    )

    # Define the table style
    costcharts_table_style = TableStyle(
        [
            # Merge cells for Row 1
            ("SPAN", (0, 0), (2, 0)),  # Merge columns 1, 2, and 3 for the paragraph
            ("SPAN", (3, 0), (5, 0)),  # Merge columns 4, 5, and 6 for the chart
            # Align the merged cell (Row 1, Column 1-2-3) to top-left
            ("VALIGN", (0, 0), (2, 0), "TOP"),  # Align vertically to top
            ("ALIGN", (0, 0), (2, 0), "LEFT"),  # Align horizontally to left
            # Remove padding for the merged cell in Row 1, Columns 1-2-3
            ("LEFTPADDING", (0, 0), (2, 0), 0),
            ("RIGHTPADDING", (0, 0), (2, 0), 0),
            ("TOPPADDING", (0, 0), (2, 0), 0),
            ("BOTTOMPADDING", (0, 0), (2, 0), 0),
            # Background and text color for Row 2 (months)
            (
                "BACKGROUND",
                (0, 1),
                (-1, 1),
                HexColor("#115e59"),
            ),  # Row 2 background color
            ("TEXTCOLOR", (0, 1), (-1, 1), colors.white),  # Row 2 text color
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),  # Bold font for Row 2
            # Center alignment for Row 2 (months)
            ("ALIGN", (0, 1), (-1, 1), "CENTER"),  # Center align -> Row 2 text
            # Font and alignment for Row 3 (costs)
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica"),  # Regular font for Row 3
            ("ALIGN", (0, 2), (-1, 2), "CENTER"),  # Center align -> Row 3 text
            # Grid lines for Row 2 and Row 3
            (
                "GRID",
                (0, 1),
                (-1, 2),
                1,
                colors.black,
            ),  # Grid for months and costs rows
            # Center alignment and vertical alignment for all cells
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),  # Vertical alignment for all cells
            (
                "VALIGN",
                (0, 0),
                (2, 0),
                "TOP",
            ),  # Align vertically to top for the merged cell
        ]
    )

    # Apply the table style
    costcharts_table.setStyle(costcharts_table_style)

    # Add the table to your content
    content.append(costcharts_table)
    content.append(PageBreak())

    # Page 2: Risks
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Risk Assessment", styles["Heading1"]))
    risk_block1 = "The Risk Assessment provides a thorough evaluation of potential risks associated with the cloud resources utilized in the project and the alternative technologies available in the market:"
    content.append(Paragraph(risk_block1, content_style))
    content.append(Spacer(1, 12))

    # Transform the risk data for the PDF and get severity counts
    risks, severity_counts = transform_risk_inventory_for_pdf(
        risk_data, risk_definitions, resource_inventory
    )

    # severity_counts is a dict like: {'high': X, 'medium': Y, 'low': Z}
    risk_chart_data = {
        "high": severity_counts["high"],
        "medium": severity_counts["medium"],
        "low": severity_counts["low"],
    }
    risk_chart = draw_risk_chart(risk_chart_data)
    content.append(risk_chart)
    content.append(Spacer(1, 12))

    # Sort risks by severity
    severity_order = {"high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda r: severity_order[r["severity"]])

    # Define the path to severity icons
    severity_icon_map = {
        "high": (os.path.join(report_path, "assets/icons/severity/high.png"), 22.5, 12),
        "medium": (
            os.path.join(report_path, "assets/icons/severity/medium.png"),
            39,
            12,
        ),
        "low": (os.path.join(report_path, "assets/icons/severity/low.png"), 20.5, 12),
    }

    # Build the risk table data
    risk_table_data = [["#", "Risk name", "Impacted", "Severity"]]
    for i, risk in enumerate(risks):
        impacted_str = (
            str(risk["impacted_resources_count"])
            if risk["impacted_resources_count"] > 0
            else "-"
        )

        # Get the severity level and corresponding icon details
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

    # Add the total risks row
    total_risks = len(risks)
    risk_table_data.append(["Total Risks", "", "", str(total_risks)])

    # Define column widths for the risk table
    risk_table_colWidths = [0.5 * cm, 10 * cm, 3 * cm, 2 * cm]
    risk_table = Table(risk_table_data, colWidths=risk_table_colWidths)

    risk_table_style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),  # Header row background
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),  # Header text color
        ("BACKGROUND", (0, -1), (-1, -1), HexColor("#115e59")),  # Last row background
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),  # Last row text color
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        # Remove SPAN if not needed
        # ('SPAN', (-4, -1), (-2, -1)),
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

    # Page 3: EscapeCloud Scoring
    if metadata.get("assessment_type") == 2:
        content.append(Spacer(1, header_padding))
        content.append(Paragraph("EscapeCloud Scoring", styles["Heading1"]))
        content.append(Paragraph("Scoring #1 - Exit Score", styles["Heading2"]))

        scoring_block1 = "The following gauge chart visualizes a combined score that reflects both risk assessment results and the evaluation of alternative technologies:"

        content.append(Paragraph(scoring_block1, content_style))
        content.append(Spacer(1, 12))
        exit_score = scoring_data.get("exit_score", 0) if scoring_data else 0

        # Define output path for charts
        chart_output_path = os.path.join(report_path, "assets/charts")
        os.makedirs(chart_output_path, exist_ok=True)

        exit_score_image_path = draw_exitscore_chart(
            exit_score, chart_output_path, width=750, height=500
        )

        # Define the table data
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

        # Column widhts
        exitscore_colWidths = [5 * cm, 10.5 * cm]

        # Create the table
        exitscore_table = Table(exitscore_table_data, colWidths=exitscore_colWidths)

        # Style the table
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

        content.append(
            Paragraph("Scoring #2 - Vendor Lock-In Score", styles["Heading2"])
        )
        scoring_block2 = "The following radar chart visualizes the assessment of alternative technologies across three dimensions: Human (skills availability), Technology (maturity and vendor stability), and Operational (ecosystem and support services) — only where viable alternatives exist:"
        content.append(Paragraph(scoring_block2, content_style))
        content.append(Spacer(1, 12))

        human_score = scoring_data.get("human_score", 0) if scoring_data else 0
        technology_score = (
            scoring_data.get("technology_score", 0) if scoring_data else 0
        )
        operational_score = (
            scoring_data.get("operational_score", 0) if scoring_data else 0
        )

        vendor_lockin_chart = draw_vendor_lockin_radar_chart(
            human_score, technology_score, operational_score
        )
        content.append(vendor_lockin_chart)

        # Define the table data
        vendor_lockin_table_data = [
            ["Human", "Technology", "Operational"],
            [human_score, technology_score, operational_score],
        ]

        # Column widhts
        vendor_lockin_colWidths = [5 * cm, 5 * cm, 5 * cm]

        # Create the table
        vendor_lockin_table = Table(
            vendor_lockin_table_data, colWidths=vendor_lockin_colWidths
        )

        # Style the table
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

    # Page 4: Resource Inventory
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Resource Inventory", styles["Heading1"]))
    res_block1 = "The Resource Inventory provides a summary of the cloud resources provisioned within the defined scope:"
    content.append(Paragraph(res_block1, content_style))
    content.append(Spacer(1, 12))

    # Transform the resource inventory data for the PDF
    resources = transform_resource_inventory_for_pdf(
        resource_inventory, resource_type_mapping, report_path
    )

    # Compute total resources
    total_resources = sum(res["count"] for res in resources)

    # Build the table data
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

    # Add the total resources row
    resource_data.append(["Total Resources", "", "", str(total_resources)])

    res_colWidths = [1 * cm, 11.5 * cm, 1.5 * cm, 1.5 * cm]
    res_table = Table(resource_data, colWidths=res_colWidths)

    res_table_style_commands = [
        (
            "BACKGROUND",
            (0, 0),
            (-1, 0),
            HexColor("#115e59"),
        ),  # Header row background color
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),  # Header row text color
        (
            "BACKGROUND",
            (0, -1),
            (-1, -1),
            HexColor("#115e59"),
        ),  # Last row background color
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),  # Last row text color
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
        ("TOPPADDING", (0, 0), (-1, 0), 12),  # Padding for header row
        # If you previously had a SPAN on the last row, remove if not needed now.
        # ('SPAN', (-4, -1), (-2, -1)), # remove if not required
        ("ALIGN", (0, 1), (0, -2), "LEFT"),  # Aligning the '#' column
        ("VALIGN", (0, 1), (0, -2), "MIDDLE"),
        ("ALIGN", (1, 1), (1, -2), "LEFT"),  # Resource name column
        ("VALIGN", (1, 1), (1, -2), "MIDDLE"),
        ("ALIGN", (2, 1), (2, -2), "CENTER"),  # Icon column
        ("VALIGN", (2, 1), (2, -2), "MIDDLE"),
        ("ALIGN", (3, 1), (3, -2), "CENTER"),  # Number column
        ("VALIGN", (3, 1), (3, -2), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "CENTER"),
        ("VALIGN", (-1, 0), (-1, 0), "MIDDLE"),
        ("ALIGN", (-1, -1), (-1, -1), "CENTER"),
        ("VALIGN", (-1, -1), (-1, -1), "MIDDLE"),
    ]

    res_table_style = TableStyle(res_table_style_commands)
    res_table.setStyle(res_table_style)

    content.append(res_table)
    content.append(PageBreak())

    # Page 5: Alternative Technologies
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Alternative Technologies", styles["Heading1"]))

    alttech_block = (
        "The Alternative Technology provides a summary of the alternative technology landscape "
        "for each identified resource in the Resource Inventory, based on our dataset and market research. "
        "It also includes a count of the available alternative technologies for each resource:"
    )
    content.append(Paragraph(alttech_block, content_style))
    content.append(Spacer(1, 12))

    # Transform the alternative technologies data for the PDF
    alttech = transform_alt_tech_for_pdf(
        resource_inventory,
        resource_type_mapping,
        alternatives,
        alternative_technologies,
        exit_strategy,
        report_path,
    )

    # Build the table data
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

    # Define the column widths
    alttech_colWidths = [1 * cm, 11.5 * cm, 1.5 * cm, 1.5 * cm]

    # Create and style the alternative technology table
    alttech_table = Table(alttech_data, colWidths=alttech_colWidths)
    alttech_table_style_commands = [
        (
            "BACKGROUND",
            (0, 0),
            (-1, 0),
            HexColor("#115e59"),
        ),  # Header row background color
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),  # Header row text color
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#000000")),  # Draw box around the table
        (
            "BOTTOMPADDING",
            (0, 1),
            (-1, -1),
            6,
        ),  # Apply bottom padding to all rows except the header
        (
            "TOPPADDING",
            (0, 1),
            (-1, -1),
            6,
        ),  # Apply top padding to all rows except the header
        (
            "ALIGN",
            (2, 0),
            (2, -1),
            "CENTER",
        ),  # Center align the text in the icon column
        ("VALIGN", (2, 0), (2, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("VALIGN", (0, 1), (0, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("VALIGN", (1, 1), (1, -1), "MIDDLE"),
        ("ALIGN", (2, 1), (2, -1), "CENTER"),
        ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "CENTER"),  # Center align the "No." header
        ("VALIGN", (-1, 0), (-1, 0), "MIDDLE"),
    ]

    alttech_table.setStyle(TableStyle(alttech_table_style_commands))

    content.append(alttech_table)
    content.append(PageBreak())

    # Build the PDF document
    logger.debug("Building the PDF document...")
    doc.build(content, onFirstPage=header_footer, onLaterPages=header_footer)

    # Return the path of the generated PDF
    return pdf_path
