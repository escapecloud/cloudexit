#utils.py
import os
import shutil
import logging
from datetime import datetime

def copy_assets(report_path):
    assets_folders = ["css", "img", "icons"]
    assets_path = os.path.join(report_path, "assets")

    # Create the 'assets' directory if it doesn't exist
    os.makedirs(assets_path, exist_ok=True)

    for folder in assets_folders:
        src_path = os.path.join("assets", folder)
        dest_path = os.path.join(assets_path, folder)

        # Only copy if the destination doesn't already exist
        if not os.path.exists(dest_path):
            shutil.copytree(src_path, dest_path, dirs_exist_ok=True)


def get_cost_summary(cost_data):
    months = []
    cost_values = []
    total_cost = 0

    # Map currency codes to their respective symbols
    currency_symbols = {
        "USD": "$",
        "GBP": "£",
        "EUR": "€"
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
    return months, cost_values, total_cost, currency_symbol

def get_risk_summary(risk_data, risk_definitions, resource_inventory):
    logger = logging.getLogger(__name__)

    severity_order = {'high': 1, 'medium': 2, 'low': 3}
    severity_counts = {'high': 0, 'medium': 0, 'low': 0}
    sorted_risks = []

    # Map resource IDs to resource names for quick lookup
    resource_name_map = {str(item['resource_type']): item['resource_name'] for item in resource_inventory.values()}

    # Log the resource_name_map to verify it has been built correctly
    #logger.info(f"Resource Name Map: {resource_name_map}")

    # Log the risk_data to verify its structure
    #logger.info(f"Risk Data: {risk_data}")

    # Group risks by their risk code and track impacted resources
    risk_map = {}
    for risk_entry in risk_data:
        risk_code = risk_entry['risk']
        resource_type = str(risk_entry['resource_type'])  # Convert to string to match the map keys

        # Initialize risk entry in the map if it doesn't exist
        if risk_code not in risk_map:
            risk_map[risk_code] = {
                "impacted_resources": set(),  # To store unique resources
                "count": 0
            }

        # If resource_type is not "null", add it to impacted resources
        if resource_type != "null":
            resource_name = resource_name_map.get(resource_type, "Unknown Resource")
            risk_map[risk_code]["impacted_resources"].add(resource_name)
            risk_map[risk_code]["count"] += 1
        else:
            # Mark this entry as a general risk without specific resources
            risk_map[risk_code]["impacted_resources"] = []
            risk_map[risk_code]["count"] = None

    # Log the intermediate risk_map to verify resource processing
    #logger.info(f"Risk Map After Processing: {risk_map}")

    # Process each risk code in the map to populate sorted_risks
    for risk_code, risk_info in risk_map.items():
        # Look up the risk definition from risk_definitions
        risk_definition = next((rd for rd in risk_definitions if rd["id"] == risk_code), None)
        if not risk_definition:
            continue

        severity = risk_definition['severity']
        severity_counts[severity] += 1

        # Format the impacted resources and count
        impacted_resources = list(risk_info["impacted_resources"]) if risk_info["impacted_resources"] else []
        impacted_resources_count = risk_info["count"]

        # Append detailed risk information
        sorted_risks.append({
            'name': risk_definition['name'],
            'description': risk_definition['description'],
            'impacted_resources': impacted_resources,
            'impacted_resources_count': impacted_resources_count,
            'severity': severity
        })

    # Sort risks by severity level
    sorted_risks.sort(key=lambda x: severity_order.get(x['severity'], 4))

    # Log the final sorted risks for verification
    #logger.info(f"Sorted Risks: {sorted_risks}")
    #logger.info(f"Severity Counts: {severity_counts}")

    return sorted_risks, severity_counts
