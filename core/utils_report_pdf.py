# core/utils_report_pdf.py
import os
import math
import logging
from datetime import datetime
from typing import List, Dict, Any, Tuple
from math import cos, sin, radians

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.platypus import Paragraph, Image, Table, TableStyle
from reportlab.graphics.shapes import Drawing, Polygon, Line, String
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import VerticalBarChart
# Plotly
import plotly.graph_objects as go

# Configure logger
logger = logging.getLogger("core.engine.report_pdf")
logger.setLevel(logging.INFO)

def transform_resource_inventory_for_pdf(resource_inventory: list, resource_type_mapping: Dict[str, Any], report_path: str) -> List[Dict[str, Any]]:
    resources = []
    for idx, resource in enumerate(resource_inventory):
        # Convert resource_type to string for lookup
        resource_type = str(resource["resource_type"])
        # Fetch resource info from the mapping
        resource_info = resource_type_mapping.get(resource_type, {})

        resource_name = resource_info.get("name", "Unknown Resource")
        # Construct icon_url from the resource_info, default if not found
        icon_path = "/assets" + resource_info.get("icon", "/icons/default.png")

        # Prepend report_storage to form the full path to the icon
        icon_url = f"{report_path}{icon_path}"

        resources.append({
            "id": idx + 1,
            "resource_name": resource_name,
            "icon_url": icon_url,
            "location": resource.get("location", "Unknown"),
            "count": resource.get("count", 0)
        })

    return resources

def transform_cost_inventory_for_pdf(cost_data: list) -> Tuple[List[str], List[float], str]:
    # Map currency codes to their respective symbols
    currency_symbols = {
        "USD": "$",
        "GBP": "£",
        "EUR": "€"
    }

    # Sort cost_data by date ascending
    sorted_cost_data = sorted(cost_data, key=lambda x: datetime.strptime(x["month"], "%Y-%m-%d"))

    # Take the last 6 months
    last_six_cost_data = sorted_cost_data[-6:]

    # Extract months and costs
    months = [datetime.strptime(item["month"], "%Y-%m-%d").strftime('%b') for item in last_six_cost_data]
    costs = [item["cost"] for item in last_six_cost_data]

    # Determine currency symbol
    if last_six_cost_data:
        currency_code = last_six_cost_data[0].get("currency", "USD")
    else:
        currency_code = "USD"
    currency_symbol = currency_symbols.get(currency_code, currency_code)

    return months, costs, currency_symbol

def transform_risk_inventory_for_pdf(risk_data: list, risk_definitions: list,resource_inventory: list) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    # Create a lookup for risk_definitions by their 'id'
    risk_def_map = {rd["id"]: rd for rd in risk_definitions}

    # Group risks by their risk code
    risk_map = {}
    for entry in risk_data:
        risk_code = entry["risk"]
        if risk_code not in risk_map:
            risk_map[risk_code] = {
                "impacted_resources_count": 0,
                "entries": []
            }
        # If resource_type is not null, increment impacted_resources_count
        if entry["resource_type"] is not None and entry["resource_type"] != "null":
            risk_map[risk_code]["impacted_resources_count"] += 1
        risk_map[risk_code]["entries"].append(entry)

    # We'll track severity counts as well
    severity_counts = {'high': 0, 'medium': 0, 'low': 0}

    # Build a final list of risks with name, severity, impacted_resources_count
    risks = []
    for risk_code, data in risk_map.items():
        rd = risk_def_map.get(risk_code)
        if not rd:
            continue  # If no definition found, skip

        severity = rd["severity"]  # 'high', 'medium', or 'low'
        if severity in severity_counts:
            severity_counts[severity] += 1

        risks.append({
            "name": rd["name"],
            "severity": severity,
            "impacted_resources_count": data["impacted_resources_count"]
        })

    return risks, severity_counts

def transform_alt_tech_for_pdf(resource_inventory: list, resource_type_mapping: Dict[str, Any], alternatives: list, alternative_technologies: list, exit_strategy: int, report_path: str) -> List[Dict[str, Any]]:

    # Count how many valid alternatives each resource_type has for the given exit_strategy
    alt_counts = {}
    for alt in alternatives:
        if str(alt.get("strategy_type")) == str(exit_strategy):
            rtype_str = str(alt.get("resource_type"))
            tech = next((t for t in alternative_technologies if t["id"] == alt["alternative_technology"] and t.get("status") == "t"), None)
            if tech:
                alt_counts[rtype_str] = alt_counts.get(rtype_str, 0) + 1

    alt_tech = []
    for idx, resource in enumerate(resource_inventory):
        rtype_str = str(resource["resource_type"])
        rtype_info = resource_type_mapping.get(rtype_str, {})
        resource_name = rtype_info.get("name", "Unknown Resource")

        icon_path = "/assets" + rtype_info.get("icon", "/icons/default.png")
        icon_url = f"{report_path}{icon_path}"

        count = alt_counts.get(rtype_str, 0)

        alt_tech.append({
            "id": idx + 1,
            "resource_name": resource_name,
            "icon_url": icon_url,
            "count": count
        })

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
    header_style = ParagraphStyle('HeaderStyle', fontSize=10, textColor=HexColor("#9cafae"))
    header_data = [
        [Paragraph(left_text_content1, header_style), "", ""],
        [Paragraph(left_text_content2, header_style), "", ""]
    ]

    # Create the header table
    table = Table(header_data, colWidths=[width - 188 - doc.rightMargin - doc.leftMargin, 10, 150])

    # Define the style for the table
    table.setStyle(TableStyle([
        ('SPAN', (1, 0), (1, 1)),  # Merge Column 2 in both rows
        ('SPAN', (2, 0), (2, 1)),  # Merge Column 3 in both rows
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),  # Align left_text_content1 to the left
        ('ALIGN', (0, 1), (0, 1), 'LEFT'),  # Align left_text_content2 to the left
        ('ALIGN', (2, 0), (2, 1), 'RIGHT'),  # Align logo to the right
        ('VALIGN', (0, 0), (0, 1), 'TOP'),  # Vertically align to the top
        ('VALIGN', (2, 0), (2, 1), 'MIDDLE'),  # Vertically align to the middle
        #('GRID', (0, 0), (-1, -1), 0.5, colors.red),  # Temporary borders for visualization
    ]))
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
    canvas.setStrokeColor(HexColor('#115e59'))
    canvas.setLineWidth(1)
    line_y = height - doc.topMargin - 10
    canvas.line(doc.leftMargin, line_y, width - doc.rightMargin, line_y)

    # Footer
    footer_padding = 15  # Add padding under the page number
    canvas.setStrokeColor(HexColor('#115e59'))
    canvas.line(40, 60 + footer_padding, A4[0] - 40, 60 + footer_padding)

    canvas.setFont('Helvetica', 8)
    canvas.drawString(A4[0] / 2 - 30, 60 + footer_padding - 15, f"Page {doc.page}")

    canvas.setFont('Helvetica-Oblique', 8)
    canvas.setFillColor(HexColor('#9cafae'))
    canvas.drawCentredString(A4[0] / 2, 40, "EscapeCloud Community Edition - This report is provided 'As Is,' without any warranty of any kind.")
    canvas.drawCentredString(A4[0] / 2, 30, "EscapeCloud makes no warranty that the information contained in this report is complete or error-free. Copyright 2024-2025")

    # Restore the state of the canvas
    canvas.restoreState()

def draw_risk_chart(risk_chart_data: Dict[str, int]) -> Drawing:
    # Define colors for each severity and their border colors
    severity_colors = {
        'high': HexColor('#991b1b'),
        'medium': HexColor('#ffae1f'),
        'low': HexColor('#539bff')
    }

    # Border colors
    border_colors = {
        'high': HexColor('#991b1b'),
        'medium': HexColor('#ffae1f'),
        'low': HexColor('#539bff')
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
    legend.alignment = 'right'
    legend.subCols[0].minWidth = 60
    legend.subCols[1].minWidth = 30
    legend.colorNamePairs = [(severity_colors[severity], (severity, str(risk_chart_data[severity]))) for severity in risk_chart_data.keys()]

    # Configure sub-columns for the legend
    legend.subCols[0].align = 'left'
    legend.subCols[1].align = 'right'

    # Add the Legend to the drawing
    d.add(legend)

    # Create a Legend Header
    legend_header = Legend()
    legend_header.x = 280
    legend_header.y = 150
    legend_header.dxTextSpace = 10
    legend_header.colorNamePairs = [(HexColor('#FFFFFF'), ('Severity', 'No.'))]  # Corrected line
    legend_header.alignment = 'right'
    legend_header.subCols[0].align = 'left'
    legend_header.subCols[0].minWidth = 60
    legend_header.subCols[1].align = 'right'
    legend_header.subCols[1].minWidth = 30

    # Add the Legend Header to the drawing
    d.add(legend_header)

    return d

def draw_cost_chart(months: List[str], costs: List[float]) -> Drawing:
    # Create a drawing for the bar chart
    d = Drawing(7.5*cm, 5*cm)

    # Create a Vertical Bar Chart
    bar_chart = VerticalBarChart()
    bar_chart.x = 20
    bar_chart.y = 20
    bar_chart.width = 6.5*cm
    bar_chart.height = 4*cm
    bar_chart.data = [costs]
    bar_chart.barWidth = 0.8*cm

    # Style the bars
    bar_chart.bars[0].fillColor = HexColor('#055160')
    bar_chart.bars[0].strokeColor = HexColor('#055160')

    # Set the categories (months)
    bar_chart.categoryAxis.categoryNames = months

    # Calculate valueMax
    max_cost = max(costs) if costs else 0
    bar_chart.valueAxis.valueMax = math.ceil(max_cost / 10.0) * 10 if max_cost > 0 else 10
    bar_chart.valueAxis.valueMin = 0

    # Add the bar chart to the drawing
    d.add(bar_chart)

    return d

def draw_exitscore_chart(exit_score: int, output_path: str, width: int = 750, height: int = 500) -> str:
    # Create the gauge chart
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=exit_score,
        domain={'x': [0, 1], 'y': [0, 1]},
        gauge={
            'axis': {'range': [0, 100], 'tickwidth': 0.2, 'tickcolor': "darkgray"},
            'bar': {
                'color': '#f3f6f6',
                'thickness': 0.2
            },
            'steps': [
                {'range': [0, 20], 'color': "#ba1c1d"},
                {'range': [20, 40], 'color': "#ff9533"},
                {'range': [40, 60], 'color': "#f1ca00"},
                {'range': [60, 80], 'color': "#76c31d"},
                {'range': [80, 100], 'color': "#065f43"}
            ]
        }
    ))

    image_file = os.path.join(output_path, "exit_score_chart.png")
    fig.write_image(image_file, width=width, height=height)

    return image_file

def draw_vendor_lockin_radar_chart(human: int, technology: int, operational: int) -> Drawing:
    # Create a drawing for the radar chart
    d = Drawing(350, 250)

    # Define the labels and data
    labels = [
        'Human',
        'Technology',
        'Operational'
    ]
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
            x = cx + (radius * level / max_value) * cos(radians(start_angle + i * angle))
            y = cy + (radius * level / max_value) * sin(radians(start_angle + i * angle))

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
        padding = 20  # Adjusted padding
        anchor = 'middle'  # Default text anchor

        label_text = labels[i]

        # Adjust padding and anchor for different quadrants if necessary
        if i * angle > 90 and i * angle < 270:
            padding = -20  # Adjusted padding for different quadrants
            anchor = 'end' if i * angle < 180 else 'start'  # Adjust text anchor

        label_padding = 10  # Additional padding to move the label slightly outward from max_value
        label_x = cx + (radius + label_padding) * cos(radians(start_angle + i * angle))
        label_y = cy + (radius + label_padding) * sin(radians(start_angle + i * angle))

        # Adjustments based on label
        if label_text == 'Technology':
            label_x += 25
            label_y -= 10
        elif label_text == 'Operational':
            label_x -= 50
            label_y -= 10
        elif label_text == 'Human':
            label_y += 5
            label_x += 10

        d.add(String(label_x, label_y, label_text, fontSize=10, fillColor=colors.black, textAnchor=anchor))

    return d
