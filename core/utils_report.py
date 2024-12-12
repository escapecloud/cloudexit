#utils_report.py
import os
import json
import logging
import math
from collections import defaultdict
from datetime import datetime
from jinja2 import Template

# ReportLab imports
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                PageBreak, Image, Table, TableStyle)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import VerticalBarChart

# Configure logger for database operations
logger = logging.getLogger("core.engine.report")
logger.setLevel(logging.INFO)

def anonymize_string(s, num_visible=4):
    if not isinstance(s, str):
        return "N/A"

    if len(s) <= 2 * num_visible:
        return '*' * len(s)

    middle_length = len(s) - 2 * num_visible
    return f"{s[:num_visible]}{'*' * middle_length}{s[-num_visible:]}"

def transform_cost_inventory_for_html(cost_data):
    months = []
    cost_values = []
    total_cost = 0

    # Map currency codes to their respective symbols
    currency_symbols = {
        "USD": "$",
        "GBP": "£",
        "EUR": "€"
    }

    # Convert list to dictionary if necessary
    if isinstance(cost_data, list):
        cost_data = {
            item["month"]: {"cost": item["cost"], "currency": item["currency"]}
            for item in cost_data
        }

    # Extract currency from the first entry, assuming all costs use the same currency
    first_entry = next(iter(cost_data.values()), None)
    currency_code = first_entry.get("currency", "USD") if first_entry else "USD"
    currency_symbol = currency_symbols.get(currency_code, currency_code)  # Default to currency_code if no symbol exists

    # Iterate over the cost data, expecting 6 months
    for month, details in sorted(cost_data.items()):
        months.append(datetime.strptime(month, "%Y-%m-%d").strftime('%b'))
        cost_values.append(details["cost"])
        total_cost += details["cost"]

    total_cost = round(total_cost, 2)
    return months, cost_values, total_cost, currency_code, currency_symbol

def transform_risk_inventory_for_html(risk_data, risk_definitions, resource_inventory):
    severity_order = {'high': 1, 'medium': 2, 'low': 3}
    severity_counts = {'high': 0, 'medium': 0, 'low': 0}
    sorted_risks = []

    # Map resource IDs to resource names for quick lookup
    resource_name_map = {str(key): value['name'] for key, value in resource_inventory.items()}

    # Group risks by their risk code and impacted resources
    risk_map = defaultdict(lambda: {"impacted_resources": set(), "count": 0})
    for risk_entry in risk_data:
        risk_code = risk_entry['risk']
        resource_type = str(risk_entry['resource_type']) if risk_entry['resource_type'] != "null" else None

        if resource_type:
            # Handle risks with associated resource types
            resource_name = resource_name_map.get(resource_type, "Unknown Resource")
            risk_map[risk_code]["impacted_resources"].add(resource_name)
            risk_map[risk_code]["count"] += 1
        else:
            # Handle overall risks with no specific resource type
            risk_map[risk_code]["impacted_resources"] = []
            risk_map[risk_code]["count"] = None

    # Process risk definitions
    for risk_code, risk_info in risk_map.items():
        risk_definition = next((rd for rd in risk_definitions if rd["id"] == risk_code), None)
        if not risk_definition:
            continue

        severity = risk_definition['severity']
        severity_counts[severity] += 1

        sorted_risks.append({
            'name': risk_definition['name'],
            'description': risk_definition['description'],
            'impacted_resources': list(risk_info["impacted_resources"]),
            'impacted_resources_count': risk_info["count"],
            'severity': severity
        })

    # Sort risks by severity
    sorted_risks.sort(key=lambda x: severity_order.get(x['severity'], 4))

    return sorted_risks, severity_counts

def transform_alt_tech_for_html(resource_inventory, alternatives, alternative_technologies, exit_strategy):
    alt_tech_data = []
    for resource in resource_inventory:
        resource_type = resource.get("resource_type")
        relevant_alternatives = [
            alt for alt in alternatives
            if str(alt["resource_type"]) == str(resource_type) and str(alt["strategy_type"]) == str(exit_strategy)
        ]
        for alt in relevant_alternatives:
            tech = next(
                (t for t in alternative_technologies if t["id"] == alt["alternative_technology"] and t["status"] == "t"),
                None
            )
            if tech:
                alt_tech_data.append({
                    "resource_type_id": resource_type,
                    "product_name": tech.get("product_name"),
                    "product_description": tech.get("product_description"),
                    "product_url": tech.get("product_url"),
                    "open_source": tech.get("open_source") == "t",
                    "support_plan": tech.get("support_plan") == "t",
                    "status": tech.get("status") == "t",
                })
    return alt_tech_data

def generate_html_report(report_path, metadata, resource_type_mapping, resource_inventory,cost_data, risk_data, risk_definitions, alternatives, alternative_technologies, exit_strategy):
    # Transform resource inventory
    resource_inventory_dict = {
        str(item["resource_type"]): {
            **item,
            "name": resource_type_mapping.get(str(item["resource_type"]), {}).get("name", "Unknown Resource"),
            "icon": resource_type_mapping.get(str(item["resource_type"]), {}).get("icon", "assets/icons/default.png")
        }
        for item in resource_inventory
    }

    # Transform risks
    risks, severity_counts = transform_risk_inventory_for_html(risk_data, risk_definitions, resource_inventory_dict)

    # Transform costs
    months, cost_values, total_cost, currency, currency_symbol = transform_cost_inventory_for_html(cost_data)

    # Transform resource data with names and icons
    resource_counts = []
    for resource_type, resource in resource_inventory_dict.items():
        count = resource.get("count", 0)
        resource_info = resource_type_mapping.get(str(resource_type), {})
        name = resource_info.get("name", "Unknown Resource")
        icon = resource_info.get("icon", "assets/icons/default.png").lstrip('/')

        resource_counts.append({
            "resource_type": resource_type,
            "name": name,
            "icon": icon,
            "count": count
        })

    # Calculate total resources
    total_resources = sum(item["count"] for item in resource_counts)

    # Transform alternative technologies
    alternative_technologies_data = transform_alt_tech_for_html(resource_inventory, alternatives, alternative_technologies, exit_strategy)

    # Render the HTML template
    template_path = os.path.join("assets", "template", "index.html")
    with open(template_path, 'r') as file:
        template_content = file.read()

    template = Template(template_content)
    html_content = template.render(
        **metadata,
        risks=risks,
        high_risk_count=severity_counts['high'],
        medium_risk_count=severity_counts['medium'],
        low_risk_count=severity_counts['low'],
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
    with open(html_path, 'w') as report_file:
        report_file.write(html_content)

    return html_path

def transform_resource_inventory_for_json(resource_inventory, resource_type_mapping):
    resource_inventory_json = []
    for idx, resource in enumerate(resource_inventory):
        resource_type = str(resource["resource_type"])
        resource_info = resource_type_mapping.get(resource_type, {})
        resource_name = resource_info.get("name", "Unknown Resource")
        resource_code = resource_info.get("code", "N/A")  # Fetch the 'code' from the mapping

        resource_inventory_json.append({
            "id": idx + 1,
            "code": resource_code,  # Include the code field
            "resource_name": resource_name,
            "location": resource.get("location", "Unknown"),
            "count": resource.get("count", 0)
        })
    return resource_inventory_json

def transform_cost_inventory_for_json(cost_data):
    # Sort by date before transformation
    sorted_cost_data = sorted(cost_data, key=lambda x: datetime.strptime(x["month"], "%Y-%m-%d"))

    cost_inventory = [
        {
            "month": item["month"],
            "cost": round(item["cost"], 2),
            "currency": item["currency"]
        }
        for item in sorted_cost_data
    ]
    return cost_inventory

def transform_risk_inventory_for_json(risk_data, risk_definitions, resource_inventory):
    # Map resource_type to their corresponding resource IDs
    resource_id_map = {str(value["resource_type"]): key + 1 for key, value in enumerate(resource_inventory)}

    # Group risks by risk.id
    risk_map = defaultdict(lambda: {
        "id": None,
        "name": "",
        "description": "",
        "severity": "",
        "impacted_resources": set(),  # Use a set to avoid duplicates
        "impacted_resources_count": 0
    })

    for risk_entry in risk_data:
        risk_id = risk_entry["risk"]
        risk_definition = next((rd for rd in risk_definitions if rd["id"] == risk_id), None)
        if not risk_definition:
            continue

        resource_type = str(risk_entry["resource_type"])
        resource_id = resource_id_map.get(resource_type)

        # Initialize risk entry if not already in the map
        if risk_map[risk_id]["id"] is None:
            risk_map[risk_id]["id"] = risk_id
            risk_map[risk_id]["name"] = risk_definition["name"]
            risk_map[risk_id]["description"] = risk_definition["description"]
            risk_map[risk_id]["severity"] = risk_definition["severity"]

        # Add impacted resources
        if resource_id:
            risk_map[risk_id]["impacted_resources"].add(resource_id)

    # Convert impacted_resources set to a list and compute counts
    for risk in risk_map.values():
        risk["impacted_resources"] = list(risk["impacted_resources"])
        risk["impacted_resources_count"] = len(risk["impacted_resources"]) if risk["impacted_resources"] else None

    return list(risk_map.values())

def transform_alt_tech_for_json(resource_inventory, alternatives, alternative_technologies, exit_strategy):
    # Map resource_type to resource_id
    resource_id_map = {str(value["resource_type"]): key + 1 for key, value in enumerate(resource_inventory)}

    # Initialize the grouped alternative technologies
    grouped_alt_tech_data = {resource_id: [] for resource_id in resource_id_map.values()}

    # Iterate through alternatives to group them by resource_id
    for alt in alternatives:
        if str(alt["strategy_type"]) != str(exit_strategy):
            continue

        tech = next(
            (t for t in alternative_technologies if t["id"] == alt["alternative_technology"] and t["status"] == "t"),
            None
        )
        if tech:
            resource_id = resource_id_map.get(str(alt["resource_type"]))
            if resource_id:
                grouped_alt_tech_data[resource_id].append({
                    "id": len(grouped_alt_tech_data[resource_id]) + 1,
                    "product_name": tech["product_name"],
                    "product_description": tech["product_description"],
                    "product_url": tech["product_url"],
                    "open_source": tech["open_source"] == "t",
                    "support_plan": tech["support_plan"] == "t"
                })

    # Return the grouped alternatives
    return {key: grouped_alt_tech_data[key] for key in sorted(grouped_alt_tech_data.keys())}

def generate_json_report(raw_data_path, metadata, resource_type_mapping, resource_inventory, cost_data, risk_data, risk_definitions, alternatives, alternative_technologies, exit_strategy):
    # Transform data for JSON
    transformed_resource_inventory = transform_resource_inventory_for_json(resource_inventory, resource_type_mapping)
    transformed_cost_inventory = transform_cost_inventory_for_json(cost_data)
    transformed_risk_inventory = transform_risk_inventory_for_json(risk_data, risk_definitions, resource_inventory)
    transformed_alt_tech = transform_alt_tech_for_json(resource_inventory, alternatives, alternative_technologies, exit_strategy)

    # Build the JSON structure
    report_json = {
        "meta": metadata,
        "data": {
            "resource_inventory": transformed_resource_inventory,
            "cost_inventory": transformed_cost_inventory,
            "risk_inventory": transformed_risk_inventory,
            "alternative_technologies": transformed_alt_tech,
        }
    }

    # Save JSON to file
    json_path = os.path.join(raw_data_path, "assessment_result.json")
    with open(json_path, 'w') as json_file:
        json.dump(report_json, json_file, indent=4)

    return json_path

def transform_resource_inventory_for_pdf(resource_inventory, resource_type_mapping, report_path):
    resources = []
    for idx, resource in enumerate(resource_inventory):
        # Convert resource_type to string for lookup
        resource_type = str(resource["resource_type"])
        # Fetch resource info from the mapping
        resource_info = resource_type_mapping.get(resource_type, {})

        resource_name = resource_info.get("name", "Unknown Resource")
        # Construct icon_url from the resource_info, default if not found
        icon_path = resource_info.get("icon", "/assets/icons/default.png")
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

def transform_cost_inventory_for_pdf(cost_data):
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

def transform_risk_inventory_for_pdf(risk_data, risk_definitions, resource_inventory):
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

def transform_alt_tech_for_pdf(resource_inventory, resource_type_mapping, alternatives, alternative_technologies, exit_strategy, report_path):
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

        icon_path = rtype_info.get("icon", "/assets/icons/default.png")
        icon_url = f"{report_path}{icon_path}"

        count = alt_counts.get(rtype_str, 0)

        alt_tech.append({
            "id": idx + 1,
            "resource_name": resource_name,
            "icon_url": icon_url,
            "count": count
        })

    return alt_tech

def draw_header_footer(report_path,canvas, doc):
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
    canvas.drawCentredString(A4[0] / 2, 30, "EscapeCloud makes no warranty that the information contained in this report is complete or error-free. Copyright 2024")

    # Restore the state of the canvas
    canvas.restoreState()

def draw_risk_chart(risk_chart_data):
    # Define your colors for each severity and their border colors
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

def draw_cost_chart(months, costs):
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

def generate_pdf_report(provider_details, report_path, metadata, resource_type_mapping, resource_inventory, cost_data, risk_data, risk_definitions, alternatives, alternative_technologies, exit_strategy):
    # Define the PDF path
    pdf_path = os.path.join(report_path, "report.pdf")

    # Define a template for the header and footer
    def header_footer(canvas, doc):
        # Make sure draw_header_footer is defined and accessible
        draw_header_footer(report_path, canvas, doc)

    # Create a document template with the header and footer
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, title="EscapeCloud_-_Cloud_Exit_Assessment")
    styles = getSampleStyleSheet()
    content_style = ParagraphStyle('ContentStyle', fontSize=10, leading=12, spaceAfter=10)
    styles['Heading1'].leading = 1.5 * styles['Heading1'].fontSize
    styles['Heading1'].textColor = HexColor('#112726')
    styles['Heading2'].leading = 1.5 * styles['Heading2'].fontSize
    styles['Heading2'].textColor = HexColor('#112726')
    tablecontent_style = styles['BodyText']

    # Define a custom padding value
    header_padding = 12

    content = []

    # --- # Page 1: Summary ---
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Summary", styles['Heading1']))
    summary_block1 = "Quick overview of the assessment:"
    content.append(Paragraph(summary_block1, content_style))

    # Prepare mappings
    cloud_service_provider_map = {
        "1": "Microsoft Azure",
        "2": "Amazon Web Services",
        "3": "Alibaba Cloud",
        "4": "Google Cloud"
    }

    exit_strategy_map = {
        "1": "Repatriation to On-Premises",
        "2": "Hybrid Cloud Adoption",
        "3": "Migration to Alternate Cloud"
    }

    type_map = {
        "1": "Basic",
        "2": "Basic+"
    }

    # Prepare the summary data
    summary_data = [
        ["Name", "Value"],
        ["Cloud Service Provider", cloud_service_provider_map.get(str(metadata["cloud_service_provider"]), "Unknown")],
        ["Exit Strategy", exit_strategy_map.get(str(metadata["exit_strategy"]), "Unknown")],
        ["Assessment Type", type_map.get(str(metadata["assessment_type"]), "Unknown")],
        ["TimeStamp", metadata["timestamp"]]
    ]

    # Column widths
    summary_colWidths = [4*cm, 11.5*cm]

    # Create the summary table
    summary_table = Table(summary_data, colWidths=summary_colWidths)

    # Define the summary table style
    summary_table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#115e59')),  # Header row background color
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),  # Header row text color
        ('GRID', (0, 0), (-1, -1), 1, HexColor("#000000")),  # Grid lines
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),  # Left align all cells
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # Middle vertical alignment for all cells
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold font for header row
        ('FONTSIZE', (0, 0), (-1, 0), 11),  # Font size for header row
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Padding for header row
        ('TOPPADDING', (0, 0), (-1, 0), 12)  # Padding for header row
    ])

    summary_table.setStyle(summary_table_style)

    # Add summary to content
    content.append(summary_table)
    content.append(Spacer(1, 12))

    # --- Page 1: Scope of Assessment ---
    content.append(Paragraph("Scope of Assessment", styles['Heading2']))
    scope_block1 = "Defined scope of assessment:"
    content.append(Paragraph(scope_block1, content_style))

    # Prepare the scope data
    scope_data = [["Name", "Value"]]

    if metadata["cloud_service_provider"] == 1:  # Azure
        scope_data.extend([
            ["Tenant ID", provider_details.get("tenantId", "N/A")],
            ["Client ID", provider_details.get("clientId", "N/A")],
            ["Client Secret", anonymize_string(provider_details.get("clientSecret", "N/A"))],
            ["Subscription ID", provider_details.get("subscriptionId", "N/A")],
            ["Resource Group Name", provider_details.get("resourceGroupName", "N/A")]
        ])
    elif metadata["cloud_service_provider"] == 2:  # AWS
        scope_data.extend([
            ["Access Key", provider_details.get("accessKey", "N/A")],
            ["Secret Key", anonymize_string(provider_details.get("secretKey", "N/A"))],
            ["Region", provider_details.get("region", "N/A")]
        ])
    else:
        scope_data.append(["N/A", "N/A"])

    # Column widths
    scope_colWidths = [4*cm, 11.5*cm]

    # Create the scope table
    scope_table = Table(scope_data, colWidths=scope_colWidths)

    # Define the scope table style
    scope_table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#115e59')),  # Header row background color
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),  # Header row text color
        ('GRID', (0, 0), (-1, -1), 1, HexColor("#000000")),  # Grid lines
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),  # Left align all cells
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # Middle vertical alignment for all cells
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold font for header row
        ('FONTSIZE', (0, 0), (-1, 0), 11),  # Font size for header row
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Padding for header row
        ('TOPPADDING', (0, 0), (-1, 0), 12)  # Padding for header row
    ])

    scope_table.setStyle(scope_table_style)

    # Add scope to content
    content.append(scope_table)
    content.append(Spacer(1, 12))

    # --- # Page 1: Costs ---
    content.append(Paragraph("Costs", styles['Heading2']))
    #costs_block1 = "Overview of the costs for the last 6 months:"
    #content.append(Paragraph(costs_block1, content_style))
    costs_block2 = "Examining the costs reveals the financial impact of the transition, allowing for more informed decision-making and strategic planning."
    costs_paragraph = Paragraph(costs_block2, tablecontent_style)

    # Transform the cost data for the PDF
    months, costs, currency_symbol = transform_cost_inventory_for_pdf(cost_data)

    # Draw the cost chart
    cost_chart = draw_cost_chart(months, costs)

    # Create the data structure for the table
    costcharts_table_data = [
        [costs_paragraph, '', '', cost_chart, '', ''],  # Row 1: Paragraph and Chart
        months,                                           # Row 2: Months
        [f"{currency_symbol} {cost:.2f}" for cost in costs]  # Row 3: Costs
    ]

    # Create the table with 6 columns
    costcharts_table = Table(
        costcharts_table_data,
        colWidths=[2.58333333333*cm] * 6  # Equal width columns
    )

    # Define the table style
    costcharts_table_style = TableStyle([
        # Merge cells for Row 1
        ('SPAN', (0, 0), (2, 0)),  # Merge columns 1, 2, and 3 for the paragraph
        ('SPAN', (3, 0), (5, 0)),  # Merge columns 4, 5, and 6 for the chart

        # Align the merged cell (Row 1, Column 1-2-3) to top-left
        ('VALIGN', (0, 0), (2, 0), 'TOP'),  # Align vertically to top
        ('ALIGN', (0, 0), (2, 0), 'LEFT'),  # Align horizontally to left

        # Remove padding for the merged cell in Row 1, Columns 1-2-3
        ('LEFTPADDING', (0, 0), (2, 0), 0),
        ('RIGHTPADDING', (0, 0), (2, 0), 0),
        ('TOPPADDING', (0, 0), (2, 0), 0),
        ('BOTTOMPADDING', (0, 0), (2, 0), 0),

        # Background and text color for Row 2 (months)
        ('BACKGROUND', (0, 1), (-1, 1), HexColor('#115e59')),  # Row 2 background color
        ('TEXTCOLOR', (0, 1), (-1, 1), colors.white),  # Row 2 text color
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),  # Bold font for Row 2

        # Center alignment for Row 2 (months)
        ('ALIGN', (0, 1), (-1, 1), 'CENTER'),  # Center align -> Row 2 text

        # Font and alignment for Row 3 (costs)
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica'),  # Regular font for Row 3
        ('ALIGN', (0, 2), (-1, 2), 'CENTER'),  # Center align -> Row 3 text

        # Grid lines for Row 2 and Row 3
        ('GRID', (0, 1), (-1, 2), 1, colors.black),  # Grid for months and costs rows

        # Center alignment and vertical alignment for all cells
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # Vertical alignment for all cells
        ('VALIGN', (0, 0), (2, 0), 'TOP'),  # Align vertically to top for the merged cell
    ])

    # Apply the table style
    costcharts_table.setStyle(costcharts_table_style)

    # Add the table to your content
    content.append(costcharts_table)
    content.append(PageBreak())

    # Page 2: Risks
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Risk Assessment", styles['Heading1']))
    risk_block1 = "The Risk Assessment provides a thorough evaluation of potential risks associated with the cloud resources utilized in the project and the alternative technologies available in the market:"
    content.append(Paragraph(risk_block1, content_style))
    content.append(Spacer(1, 12))

    # Transform the risk data for the PDF and get severity counts
    risks, severity_counts = transform_risk_inventory_for_pdf(risk_data, risk_definitions, resource_inventory)

    # severity_counts is a dict like: {'high': X, 'medium': Y, 'low': Z}
    risk_chart_data = {
        'high': severity_counts['high'],
        'medium': severity_counts['medium'],
        'low': severity_counts['low']
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
        "medium": (os.path.join(report_path, "assets/icons/severity/medium.png"), 39, 12),
        "low": (os.path.join(report_path, "assets/icons/severity/low.png"), 20.5, 12)
    }

    # Build the risk table data
    risk_table_data = [["#", "Risk name", "Impacted", "Severity"]]
    for i, risk in enumerate(risks):
        impacted_str = str(risk['impacted_resources_count']) if risk['impacted_resources_count'] > 0 else '-'

        # Get the severity level and corresponding icon details
        severity_level = risk['severity'].lower()
        icon_details = severity_icon_map.get(severity_level, None)

        if icon_details:
            icon_path, icon_width, icon_height = icon_details
            if os.path.exists(icon_path):
                severity_icon = Image(icon_path, width=icon_width, height=icon_height)
            else:
                severity_icon = Paragraph("N/A", tablecontent_style)
        else:
            severity_icon = Paragraph("N/A", tablecontent_style)

        risk_table_data.append([
            str(i + 1),
            risk['name'],
            impacted_str,
            severity_icon
        ])

    # Add the total risks row
    total_risks = len(risks)
    risk_table_data.append(["Total Risks", "", "", str(total_risks)])

    # Define column widths for the risk table
    risk_table_colWidths = [0.5 * cm, 10 * cm, 3 * cm, 2 * cm]
    risk_table = Table(risk_table_data, colWidths=risk_table_colWidths)

    risk_table_style_commands = [
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#115e59')),  # Header row background
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),          # Header text color
        ('BACKGROUND', (0, -1), (-1, -1), HexColor('#115e59')), # Last row background
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),         # Last row text color
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#112726')),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Padding for header row
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        # Remove SPAN if not needed
        # ('SPAN', (-4, -1), (-2, -1)),

        ('ALIGN', (0, 1), (0, -2), 'LEFT'),
        ('VALIGN', (0, 1), (0, -2), 'MIDDLE'),
        ('ALIGN', (1, 1), (1, -2), 'LEFT'),
        ('VALIGN', (1, 1), (1, -2), 'MIDDLE'),
        ('ALIGN', (2, 1), (2, -2), 'CENTER'),
        ('VALIGN', (2, 1), (2, -2), 'MIDDLE'),
        ('ALIGN', (3, 1), (3, -2), 'CENTER'),
        ('VALIGN', (3, 1), (3, -2), 'MIDDLE'),
        ('ALIGN', (-1, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (-1, 0), (-1, 0), 'MIDDLE'),
        ('ALIGN', (-1, -1), (-1, -1), 'CENTER'),
        ('VALIGN', (-1, -1), (-1, -1), 'MIDDLE')
    ]

    risk_table.setStyle(TableStyle(risk_table_style_commands))
    content.append(risk_table)
    content.append(PageBreak())

    # Page 3: Resource Inventory
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Resource Inventory", styles['Heading1']))
    res_block1 = "The Resource Inventory provides a summary of the cloud resources provisioned within the defined scope:"
    content.append(Paragraph(res_block1, content_style))
    content.append(Spacer(1, 12))

    # Transform the resource inventory data for the PDF
    resources = transform_resource_inventory_for_pdf(resource_inventory, resource_type_mapping, report_path)

    # Compute total resources
    total_resources = sum(res["count"] for res in resources)

    # Build the table data
    resource_data = [["#", "Resource type", "", "No."]]

    for res in resources:
        resource_data.append([
            str(res["id"]),
            res["resource_name"],
            Image(res["icon_url"], width=20, height=20),
            str(res["count"])
        ])

    # Add the total resources row
    resource_data.append(["Total Resources", "", "", str(total_resources)])

    res_colWidths = [1 * cm, 11.5 * cm, 1.5 * cm, 1.5 * cm]
    res_table = Table(resource_data, colWidths=res_colWidths)

    res_table_style_commands = [
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#115e59')),  # Header row background color
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),  # Header row text color
        ('BACKGROUND', (0, -1), (-1, -1), HexColor('#115e59')),  # Last row background color
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),  # Last row text color
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#112726')),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Padding for header row
        ('TOPPADDING', (0, 0), (-1, 0), 12),  # Padding for header row
        # If you previously had a SPAN on the last row, remove if not needed now.
        # ('SPAN', (-4, -1), (-2, -1)), # remove if not required

        ('ALIGN', (0, 1), (0, -2), 'LEFT'),  # Aligning the '#' column
        ('VALIGN', (0, 1), (0, -2), 'MIDDLE'),
        ('ALIGN', (1, 1), (1, -2), 'LEFT'),  # Resource name column
        ('VALIGN', (1, 1), (1, -2), 'MIDDLE'),
        ('ALIGN', (2, 1), (2, -2), 'CENTER'), # Icon column
        ('VALIGN', (2, 1), (2, -2), 'MIDDLE'),
        ('ALIGN', (3, 1), (3, -2), 'CENTER'), # Number column
        ('VALIGN', (3, 1), (3, -2), 'MIDDLE'),
        ('ALIGN', (-1, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (-1, 0), (-1, 0), 'MIDDLE'),
        ('ALIGN', (-1, -1), (-1, -1), 'CENTER'),
        ('VALIGN', (-1, -1), (-1, -1), 'MIDDLE')
    ]

    res_table_style = TableStyle(res_table_style_commands)
    res_table.setStyle(res_table_style)

    content.append(res_table)
    content.append(PageBreak())

    # Page 4: Alternative Technologies
    content.append(Spacer(1, header_padding))
    content.append(Paragraph("Alternative Technologies", styles['Heading1']))

    alttech_block = ("The Alternative Technology provides a summary of the alternative technology landscape "
                     "for each identified resource in the Resource Inventory, based on our dataset and market research. "
                     "It also includes a count of the available alternative technologies for each resource:")
    content.append(Paragraph(alttech_block, content_style))
    content.append(Spacer(1, 12))

    # Transform the alternative technologies data for the PDF
    alttech = transform_alt_tech_for_pdf(
        resource_inventory, resource_type_mapping, alternatives, alternative_technologies, exit_strategy, report_path
    )

    # Build the table data
    alttech_data = [["#", "Resource type", "", "No."]]
    for res in alttech:
        alttech_data.append([
            str(res["id"]),
            res["resource_name"],
            Image(res["icon_url"], width=20, height=20) if res["icon_url"] else "",
            str(res["count"])
        ])

    # Define the column widths
    alttech_colWidths = [1*cm, 11.5*cm, 1.5*cm, 1.5*cm]

    # Create and style the alternative technology table
    alttech_table = Table(alttech_data, colWidths=alttech_colWidths)
    alttech_table_style_commands = [
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#115e59')),  # Header row background color
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),  # Header row text color
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#000000")),  # Draw box around the table
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),  # Apply bottom padding to all rows except the header
        ('TOPPADDING', (0, 1), (-1, -1), 6),  # Apply top padding to all rows except the header
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),  # Center align the text in the icon column
        ('VALIGN', (2, 0), (2, -1), 'MIDDLE'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('VALIGN', (0, 1), (0, -1), 'MIDDLE'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
        ('VALIGN', (1, 1), (1, -1), 'MIDDLE'),
        ('ALIGN', (2, 1), (2, -1), 'CENTER'),
        ('VALIGN', (2, 1), (2, -1), 'MIDDLE'),
        ('ALIGN', (3, 1), (3, -1), 'CENTER'),
        ('VALIGN', (3, 1), (3, -1), 'MIDDLE'),
        ('ALIGN', (-1, 0), (-1, 0), 'CENTER'),  # Center align the "No." header
        ('VALIGN', (-1, 0), (-1, 0), 'MIDDLE'),
    ]

    alttech_table.setStyle(TableStyle(alttech_table_style_commands))

    content.append(alttech_table)
    content.append(PageBreak())


    # Build the PDF document
    logger.debug("Building the PDF document...")
    doc.build(content, onFirstPage=header_footer, onLaterPages=header_footer)

    # Return the path of the generated PDF
    return pdf_path
