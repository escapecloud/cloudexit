# core/utils_report_json.py
import logging
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any

# Configure logger
logger = logging.getLogger("core.engine.report_json")
logger.setLevel(logging.INFO)


def transform_resource_inventory_for_json(
    resource_inventory: List[Dict[str, Any]],
    resource_type_mapping: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    resource_inventory_json = []
    for idx, resource in enumerate(resource_inventory):
        resource_type = str(resource["resource_type"])
        resource_info = resource_type_mapping.get(resource_type, {})
        resource_name = resource_info.get("name", "Unknown Resource")
        resource_code = resource_info.get("code", "N/A")

        resource_inventory_json.append(
            {
                "id": idx + 1,
                "code": resource_code,
                "resource_name": resource_name,
                "location": resource.get("location", "Unknown"),
                "count": resource.get("count", 0),
            }
        )
    return resource_inventory_json


def transform_cost_inventory_for_json(
    cost_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    # Sort by date before transformation
    sorted_cost_data = sorted(
        cost_data, key=lambda x: datetime.strptime(x["month"], "%Y-%m-%d")
    )

    cost_inventory = [
        {
            "month": item["month"],
            "cost": round(item["cost"], 2),
            "currency": item["currency"],
        }
        for item in sorted_cost_data
    ]
    return cost_inventory


def transform_risk_inventory_for_json(
    risk_data: List[Dict[str, Any]],
    risk_definitions: List[Dict[str, Any]],
    resource_inventory: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    # Map resource_type to their corresponding resource IDs
    resource_id_map = {
        str(value["resource_type"]): key + 1
        for key, value in enumerate(resource_inventory)
    }

    # Group risks by risk.id
    risk_map = defaultdict(
        lambda: {
            "id": None,
            "name": "",
            "description": "",
            "severity": "",
            "impacted_resources": set(),
            "impacted_resources_count": 0,
        }
    )

    for risk_entry in risk_data:
        risk_id = risk_entry["risk"]
        risk_definition = next(
            (rd for rd in risk_definitions if rd["id"] == risk_id), None
        )
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
        risk["impacted_resources_count"] = (
            len(risk["impacted_resources"]) if risk["impacted_resources"] else None
        )

    return list(risk_map.values())


def transform_alt_tech_for_json(
    resource_inventory: List[Dict[str, Any]],
    alternatives: List[Dict[str, Any]],
    alternative_technologies: List[Dict[str, Any]],
    exit_strategy: int,
) -> Dict[int, List[Dict[str, Any]]]:
    # Map resource_type to resource_id
    resource_id_map = {
        str(value["resource_type"]): key + 1
        for key, value in enumerate(resource_inventory)
    }

    # Initialize the grouped alternative technologies
    grouped_alt_tech_data = {
        resource_id: [] for resource_id in resource_id_map.values()
    }

    # Iterate through alternatives to group them by resource_id
    for alt in alternatives:
        if str(alt["strategy_type"]) != str(exit_strategy):
            continue

        tech = next(
            (
                t
                for t in alternative_technologies
                if t["id"] == alt["alternative_technology"] and t["status"] == "t"
            ),
            None,
        )
        if tech:
            resource_id = resource_id_map.get(str(alt["resource_type"]))
            if resource_id:
                grouped_alt_tech_data[resource_id].append(
                    {
                        "id": len(grouped_alt_tech_data[resource_id]) + 1,
                        "product_name": tech["product_name"],
                        "product_description": tech["product_description"],
                        "product_url": tech["product_url"],
                        "open_source": tech["open_source"] == "t",
                        "support_plan": tech["support_plan"] == "t",
                    }
                )

    # Return the grouped alternatives
    return {
        key: grouped_alt_tech_data[key] for key in sorted(grouped_alt_tech_data.keys())
    }
