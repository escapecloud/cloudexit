#utils.py
import os
import shutil
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger("core.engine.utils")

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

    # Copy datasets/data.db to data/assessment.db
    db_src_path = "datasets/data.db"
    db_dest_dir = os.path.join(report_path, "data")
    db_dest_path = os.path.join(db_dest_dir, "assessment.db")

    # Create the 'data' directory if it doesn't exist
    os.makedirs(db_dest_dir, exist_ok=True)

    # Only copy if the destination doesn't already exist
    if not os.path.exists(db_dest_path):
        shutil.copyfile(db_src_path, db_dest_path)


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
    return months, cost_values, total_cost, currency_symbol

def get_risk_summary(risk_data, risk_definitions, resource_inventory):
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

def prepare_alternative_technologies(resource_inventory, alternatives, alternative_technologies, exit_strategy):
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
