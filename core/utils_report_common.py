from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

CURRENCY_SYMBOLS = {
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
}


def sort_cost_data(cost_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(cost_data, key=lambda x: datetime.strptime(x["month"], "%Y-%m-%d"))


def summarize_costs(
    cost_data: List[Dict[str, Any]], *, last_n: Optional[int] = None
) -> Tuple[List[str], List[float], float, str, str]:
    sorted_costs = sort_cost_data(cost_data)
    if last_n is not None:
        sorted_costs = sorted_costs[-last_n:]

    months = [
        datetime.strptime(item["month"], "%Y-%m-%d").strftime("%b")
        for item in sorted_costs
    ]
    values = [item["cost"] for item in sorted_costs]
    total_cost = round(sum(values), 2)

    if sorted_costs:
        currency_code = sorted_costs[0].get("currency", "USD")
    else:
        currency_code = "USD"
    currency_symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code)

    return months, values, total_cost, currency_code, currency_symbol


def summarize_risks(
    risk_data: List[Dict[str, Any]],
    risk_definitions: List[Dict[str, Any]],
    *,
    resource_name_map: Optional[Dict[str, str]] = None,
    resource_id_map: Optional[Dict[str, int]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    risk_def_map = {rd["id"]: rd for rd in risk_definitions}
    severity_counts = {"high": 0, "medium": 0, "low": 0}

    grouped_risks = defaultdict(
        lambda: {
            "impacted_resource_types": set(),
            "impacted_resources_count": 0,
            "has_overall_risk": False,
        }
    )

    for entry in risk_data:
        risk_code = entry["risk"]
        resource_type = entry["resource_type"]

        if resource_type is None or resource_type == "null":
            grouped_risks[risk_code]["has_overall_risk"] = True
            continue

        resource_type = str(resource_type)
        grouped_risks[risk_code]["impacted_resource_types"].add(resource_type)
        grouped_risks[risk_code]["impacted_resources_count"] += 1

    summarized_risks = []
    for risk_code, risk_info in grouped_risks.items():
        risk_definition = risk_def_map.get(risk_code)
        if not risk_definition:
            continue

        severity = risk_definition["severity"]
        if severity in severity_counts:
            severity_counts[severity] += 1

        resource_types = sorted(risk_info["impacted_resource_types"])
        resource_names = None
        if resource_name_map is not None:
            resource_names = [
                resource_name_map.get(resource_type, "Unknown Resource")
                for resource_type in resource_types
            ]

        resource_ids = None
        if resource_id_map is not None:
            resource_ids = [
                resource_id_map[resource_type]
                for resource_type in resource_types
                if resource_type in resource_id_map
            ]

        impacted_resources_count = (
            None
            if risk_info["has_overall_risk"]
            else risk_info["impacted_resources_count"]
        )

        summarized_risks.append(
            {
                "id": risk_code,
                "name": risk_definition["name"],
                "description": risk_definition["description"],
                "severity": severity,
                "impacted_resource_types": resource_types,
                "impacted_resources": resource_names,
                "impacted_resource_ids": resource_ids,
                "impacted_resources_count": impacted_resources_count,
            }
        )

    return summarized_risks, severity_counts


def summarize_alternative_technologies(
    resource_inventory: List[Dict[str, Any]],
    alternatives: List[Dict[str, Any]],
    alternative_technologies: List[Dict[str, Any]],
    exit_strategy: int,
) -> Dict[str, List[Dict[str, Any]]]:
    active_technologies = {
        tech["id"]: tech
        for tech in alternative_technologies
        if tech.get("status") == "t"
    }

    grouped_alt_tech: Dict[str, List[Dict[str, Any]]] = {
        str(resource["resource_type"]): [] for resource in resource_inventory
    }

    for alt in alternatives:
        if str(alt["strategy_type"]) != str(exit_strategy):
            continue

        resource_type = str(alt["resource_type"])
        tech = active_technologies.get(alt["alternative_technology"])
        if not tech or resource_type not in grouped_alt_tech:
            continue

        grouped_alt_tech[resource_type].append(
            {
                "product_name": tech["product_name"],
                "product_description": tech["product_description"],
                "product_url": tech["product_url"],
                "open_source": tech["open_source"] == "t",
                "support_plan": tech["support_plan"] == "t",
                "status": tech["status"] == "t",
            }
        )

    return grouped_alt_tech


def enrich_resource_inventory(
    resource_inventory: List[Dict[str, Any]],
    resource_type_mapping: Dict[str, Dict[str, Any]],
    *,
    report_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    enriched_resources = []
    for idx, resource in enumerate(resource_inventory):
        resource_type = str(resource["resource_type"])
        resource_info = resource_type_mapping.get(resource_type, {})
        icon = resource_info.get("icon", "/icons/default.png")

        entry = {
            "id": idx + 1,
            "resource_type": resource_type,
            "code": resource_info.get("code", "N/A"),
            "resource_name": resource_info.get("name", "Unknown Resource"),
            "icon": icon,
            "location": resource.get("location", "Unknown"),
            "count": resource.get("count", 0),
        }

        if report_path is not None:
            entry["icon_url"] = f"{report_path}/assets{icon}"

        enriched_resources.append(entry)

    return enriched_resources
