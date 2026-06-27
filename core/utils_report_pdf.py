# core/utils_report_pdf.py
import math
import logging
from datetime import datetime
from typing import Any
from math import cos, sin, radians

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.platypus import Paragraph, Image, Table, TableStyle
from reportlab.graphics.shapes import Drawing, Polygon, Line, Circle, Wedge, String
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import VerticalBarChart

from core.utils_report_common import (
    enrich_resource_inventory,
    summarize_alternative_technologies,
    summarize_costs,
    summarize_risks,
)

# Configure logger
logger = logging.getLogger("core.engine.report_pdf")
logger.setLevel(logging.INFO)


def transform_resource_inventory_for_pdf(
    resource_inventory: list, resource_type_mapping: dict[str, Any], report_path: str
) -> list[dict[str, Any]]:
    enriched_resources = enrich_resource_inventory(
        resource_inventory,
        resource_type_mapping,
        report_path=report_path,
    )
    return [
        {
            "id": resource["id"],
            "resource_name": resource["resource_name"],
            "icon_url": resource["icon_url"],
            "location": resource["location"],
            "count": resource["count"],
        }
        for resource in enriched_resources
    ]


def transform_cost_inventory_for_pdf(
    cost_data: list,
) -> tuple[list[str], list[float], str]:
    months, costs, _, _, currency_symbol = summarize_costs(cost_data, last_n=6)
    return months, costs, currency_symbol


def transform_risk_inventory_for_pdf(
    risk_data: list, risk_definitions: list, resource_inventory: list
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    risks, severity_counts = summarize_risks(risk_data, risk_definitions)
    return [
        {
            "name": risk["name"],
            "severity": risk["severity"],
            "impacted_resources_count": risk["impacted_resources_count"] or 0,
        }
        for risk in risks
    ], severity_counts


def transform_alt_tech_for_pdf(
    resource_inventory: list,
    resource_type_mapping: dict[str, Any],
    alternatives: list,
    alternative_technologies: list,
    exit_strategy: int,
    report_path: str,
) -> list[dict[str, Any]]:
    grouped_alt_tech = summarize_alternative_technologies(
        resource_inventory,
        alternatives,
        alternative_technologies,
        exit_strategy,
    )
    alt_tech = []
    for idx, resource in enumerate(resource_inventory):
        rtype_str = str(resource["resource_type"])
        rtype_info = resource_type_mapping.get(rtype_str, {})
        resource_name = rtype_info.get("name", "Unknown Resource")

        icon_path = "/assets" + rtype_info.get("icon", "/icons/default.png")
        icon_url = f"{report_path}{icon_path}"

        count = len(grouped_alt_tech.get(rtype_str, []))

        alt_tech.append(
            {
                "id": idx + 1,
                "resource_name": resource_name,
                "icon_url": icon_url,
                "count": count,
            }
        )

    return alt_tech


def draw_header_footer(report_path: str, canvas, doc) -> None:
    # Save the state of the canvas to not affect the drawing
    canvas.saveState()
    width, height = A4

    # Include the date in the format mm-dd-yyyy
    current_date = datetime.now().strftime("%m-%d-%Y")
    left_text_content1 = "EscapeCloud Community Edition - Report"
    left_text_content2 = f"Date: {current_date}"

    # Define the header content with Paragraphs
    header_style = ParagraphStyle(
        "HeaderStyle", fontSize=10, textColor=HexColor("#9cafae")
    )
    header_data = [
        [Paragraph(left_text_content1, header_style), "", ""],
        [Paragraph(left_text_content2, header_style), "", ""],
    ]

    # Create the header table
    table = Table(
        header_data, colWidths=[width - 188 - doc.rightMargin - doc.leftMargin, 10, 150]
    )

    # Define the style for the table
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 0), (1, 1)),  # Merge Column 2 in both rows
                ("SPAN", (2, 0), (2, 1)),  # Merge Column 3 in both rows
                (
                    "ALIGN",
                    (0, 0),
                    (0, 0),
                    "LEFT",
                ),  # Align left_text_content1 to the left
                (
                    "ALIGN",
                    (0, 1),
                    (0, 1),
                    "LEFT",
                ),  # Align left_text_content2 to the left
                ("ALIGN", (2, 0), (2, 1), "RIGHT"),  # Align logo to the right
                ("VALIGN", (0, 0), (0, 1), "TOP"),  # Vertically align to the top
                ("VALIGN", (2, 0), (2, 1), "MIDDLE"),  # Vertically align to the middle
                # ('GRID', (0, 0), (-1, -1), 0.5, colors.red),  # Temporary borders for visualization
            ]
        )
    )
    # Build the header table and draw it on the canvas
    table.wrapOn(canvas, doc.leftMargin, height - doc.topMargin)
    table.drawOn(canvas, doc.leftMargin, height - doc.topMargin)

    # Add the logo
    logo_path = f"{report_path}/assets/img/logo/report_logo.png"
    logo = Image(logo_path, width=150, height=30)

    # Aligning logo vertically with the text
    logo_y = height - doc.topMargin + 5
    logo.drawOn(canvas, width - 150 - doc.rightMargin, logo_y)

    # Line below the header
    canvas.setStrokeColor(HexColor("#115e59"))
    canvas.setLineWidth(1)
    line_y = height - doc.topMargin - 10
    canvas.line(doc.leftMargin, line_y, width - doc.rightMargin, line_y)

    # Footer
    footer_padding = 15  # Add padding under the page number
    canvas.setStrokeColor(HexColor("#115e59"))
    canvas.line(40, 60 + footer_padding, A4[0] - 40, 60 + footer_padding)

    canvas.setFont("Helvetica", 8)
    canvas.drawString(A4[0] / 2 - 30, 60 + footer_padding - 15, f"Page {doc.page}")

    canvas.setFont("Helvetica-Oblique", 8)
    canvas.setFillColor(HexColor("#9cafae"))
    canvas.drawCentredString(
        A4[0] / 2,
        40,
        "EscapeCloud Community Edition - This report is provided 'As Is,' without any warranty of any kind.",
    )
    canvas.drawCentredString(
        A4[0] / 2,
        30,
        "EscapeCloud makes no warranty that the information contained in this report is complete or error-free. Copyright 2024-2026",
    )

    # Restore the state of the canvas
    canvas.restoreState()


def draw_risk_chart(risk_chart_data: dict[str, int]) -> Drawing:
    # Define colors for each severity and their border colors
    severity_colors = {
        "high": HexColor("#991b1b"),
        "medium": HexColor("#ffae1f"),
        "low": HexColor("#539bff"),
    }

    # Border colors
    border_colors = {
        "high": HexColor("#991b1b"),
        "medium": HexColor("#ffae1f"),
        "low": HexColor("#539bff"),
    }

    # Create a drawing for the Doughnut chart
    d = Drawing(300, 200)

    # Create the Pie (Doughnut) chart
    pie = Pie()
    pie.x = 100
    pie.y = 25
    pie.width = 150
    pie.height = 150
    pie.data = list(risk_chart_data.values())
    pie.innerRadiusFraction = 0.5

    # Assign colors and borders for each severity level
    for i, severity in enumerate(risk_chart_data.keys()):
        pie.slices[i].fillColor = severity_colors[severity]
        pie.slices[i].strokeColor = border_colors[severity]
        pie.slices[i].strokeWidth = 1  # Set the border width

    # Add the Pie chart to the drawing
    d.add(pie)

    # Create a Legend with headers
    legend = Legend()
    legend.x = 280
    legend.y = 130
    legend.dxTextSpace = 10
    legend.columnMaximum = 6
    legend.alignment = "right"
    legend.subCols[0].minWidth = 60
    legend.subCols[1].minWidth = 30
    legend.colorNamePairs = [
        (severity_colors[severity], (severity, str(risk_chart_data[severity])))
        for severity in risk_chart_data.keys()
    ]

    # Configure sub-columns for the legend
    legend.subCols[0].align = "left"
    legend.subCols[1].align = "right"

    # Add the Legend to the drawing
    d.add(legend)

    # Create a Legend Header
    legend_header = Legend()
    legend_header.x = 280
    legend_header.y = 150
    legend_header.dxTextSpace = 10
    legend_header.colorNamePairs = [
        (HexColor("#FFFFFF"), ("Severity", "No."))
    ]  # Corrected line
    legend_header.alignment = "right"
    legend_header.subCols[0].align = "left"
    legend_header.subCols[0].minWidth = 60
    legend_header.subCols[1].align = "right"
    legend_header.subCols[1].minWidth = 30

    # Add the Legend Header to the drawing
    d.add(legend_header)

    return d


def draw_cost_chart(months: list[str], costs: list[float]) -> Drawing:
    # Create a drawing for the bar chart
    d = Drawing(7.5 * cm, 5 * cm)

    # Create a Vertical Bar Chart
    bar_chart = VerticalBarChart()
    bar_chart.x = 20
    bar_chart.y = 20
    bar_chart.width = 6.5 * cm
    bar_chart.height = 4 * cm
    bar_chart.data = [costs]
    bar_chart.barWidth = 0.8 * cm

    # Style the bars
    bar_chart.bars[0].fillColor = HexColor("#055160")
    bar_chart.bars[0].strokeColor = HexColor("#055160")

    # Set the categories (months)
    bar_chart.categoryAxis.categoryNames = months

    # Calculate valueMax
    max_cost = max(costs) if costs else 0
    bar_chart.valueAxis.valueMax = (
        math.ceil(max_cost / 10.0) * 10 if max_cost > 0 else 10
    )
    bar_chart.valueAxis.valueMin = 0

    # Add the bar chart to the drawing
    d.add(bar_chart)

    return d


def draw_exitscore_pie_chart(
    exit_score: int,
    size: float = 7.5 * cm,
    show_title: bool = False,
    ring_bg_color: str = "#E9ECEF",
    value_color: str = "#000000",
) -> Drawing:
    # Clamp value
    val = max(0, min(100, int(exit_score)))

    def score_to_grade(s: int) -> str:
        if s > 83:
            return "A"
        if s > 67:
            return "B"
        if s > 50:
            return "C"
        if s > 33:
            return "D"
        if s > 17:
            return "E"
        return "F"

    # Band palette (matches the 6-grade gauge segments)
    bands = [
        (0, 17, "#ba1c1d", "F"),
        (17, 33, "#d44000", "E"),
        (33, 50, "#ff9533", "D"),
        (50, 67, "#f1ca00", "C"),
        (67, 83, "#76c31d", "B"),
        (83, 100, "#065f43", "A"),
    ]

    def band_color(score: int):
        s = max(0, min(100, int(score)))
        for a, b, hexcol, _ in bands:
            if a <= s <= b:
                return HexColor(hexcol)
        return HexColor("#4a5568")

    # Canvas: a touch taller when showing a title
    d = Drawing(size, size + (20 if show_title else 0))
    w, h = d.width, d.height
    cx = w / 2.0
    cy = (h / 2.0) - (8 if show_title else 0)

    # Geometry
    r_outer = min(w, h) * 0.40
    r_inner = r_outer * 0.62

    # Optional title
    if show_title:
        d.add(
            String(
                cx,
                h - 8,
                "Exit Score",
                textAnchor="middle",
                fontSize=10,
                fillColor=colors.black,
            )
        )

    # Background ring
    d.add(Circle(cx, cy, r_outer, fillColor=HexColor(ring_bg_color), strokeColor=None))

    # Progress arc (start at -90 deg to sweep from top clockwise)
    if val > 0:
        start_deg = -90.0
        sweep_deg = 360.0 * (val / 100.0)
        d.add(
            Wedge(
                cx,
                cy,
                r_outer,
                start_deg,
                start_deg + sweep_deg,
                fillColor=band_color(val),
                strokeColor=None,
            )
        )

    # Punch the inner hole to make a donut
    d.add(Circle(cx, cy, r_inner, fillColor=HexColor("#FFFFFF"), strokeColor=None))

    # Grade label
    d.add(
        String(
            cx,
            cy - 7,
            score_to_grade(val),
            textAnchor="middle",
            fontSize=22,
            fontName="Helvetica-Bold",
            fillColor=HexColor(value_color),
        )
    )

    return d


def draw_vendor_lockin_radar_chart(
    human: int, technology: int, operational: int
) -> Drawing:
    # Create a drawing for the radar chart
    d = Drawing(350, 250)

    # Define the labels and data
    labels = ["Human", "Technology", "Operational"]
    data = [human, technology, operational]

    # Define your hex color with alpha
    bg_color = HexColor("#4BC0C0")
    bg_color.alpha = 0.2  # Set alpha for the fill color
    border_color = HexColor("#4BC0C0")  # Border color with default alpha (1)

    # Normalize data
    max_value = 5
    normalized_data = [i / max_value for i in data]

    # Define the number of facets and calculate the angle of each facet
    num_facets = len(labels)
    angle = 360 / num_facets

    # Adjust the starting angle for pyramid orientation
    start_angle = -30

    # Define the center and radius of the radar chart
    cx = 230
    cy = 125
    radius = 100  # Radius

    # Draw concentric polygons
    for level in range(1, int(max_value) + 1):
        points = []
        for i in range(num_facets):
            x = cx + radius * cos(radians(start_angle + i * angle))
            y = cy + radius * sin(radians(start_angle + i * angle))
            points.extend([x, y])
        d.add(Polygon(points, fillColor=None, strokeColor=colors.grey))

    # Draw lines connecting the vertices of concentric polygons
    for level in range(1, int(max_value)):
        prev_x = None
        prev_y = None
        first_x = None
        first_y = None
        for i in range(num_facets):
            x = cx + (radius * level / max_value) * cos(
                radians(start_angle + i * angle)
            )
            y = cy + (radius * level / max_value) * sin(
                radians(start_angle + i * angle)
            )

            # Store the first x and y coordinates to close the triangle later
            if i == 0:
                first_x = x
                first_y = y

            # If not the first vertex, draw a line from the previous vertex to the current vertex
            if prev_x is not None and prev_y is not None:
                d.add(Line(prev_x, prev_y, x, y, strokeColor=colors.grey))

            prev_x = x
            prev_y = y

        # Close the triangle by drawing a line from the last vertex to the first vertex
        d.add(Line(prev_x, prev_y, first_x, first_y, strokeColor=colors.grey))

    # Draw the data polygon
    points = []
    for i in range(num_facets):
        x = cx + radius * normalized_data[i] * cos(radians(start_angle + i * angle))
        y = cy + radius * normalized_data[i] * sin(radians(start_angle + i * angle))
        points.extend([x, y])
    d.add(Polygon(points, fillColor=bg_color, strokeColor=border_color))

    # Draw labels
    for i in range(num_facets):
        x = cx + radius * cos(radians(start_angle + i * angle))
        y = cy + radius * sin(radians(start_angle + i * angle))
        d.add(Line(cx, cy, x, y, strokeColor=colors.grey))

        # Adjust label position and anchor based on quadrant
        anchor = "middle"  # Default text anchor

        label_text = labels[i]

        # Adjust padding and anchor for different quadrants if necessary
        if i * angle > 90 and i * angle < 270:
            anchor = "end" if i * angle < 180 else "start"  # Adjust text anchor

        label_padding = (
            10  # Additional padding to move the label slightly outward from max_value
        )
        label_x = cx + (radius + label_padding) * cos(radians(start_angle + i * angle))
        label_y = cy + (radius + label_padding) * sin(radians(start_angle + i * angle))

        # Adjustments based on label
        if label_text == "Technology":
            label_x += 25
            label_y -= 10
        elif label_text == "Operational":
            label_x -= 50
            label_y -= 10
        elif label_text == "Human":
            label_y += 5
            label_x += 10

        d.add(
            String(
                label_x,
                label_y,
                label_text,
                fontSize=10,
                fillColor=colors.black,
                textAnchor=anchor,
            )
        )

    return d
