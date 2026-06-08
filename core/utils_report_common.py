from collections import defaultdict
from datetime import datetime
from typing import Any

CURRENCY_SYMBOLS = {
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
}

EU_COUNTRY_CODES = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
}

REGION_LABELS = {
    "european-union": "European Union",
    "united-kingdom": "United Kingdom",
    "switzerland": "Switzerland",
    "united-states": "United States",
    "other": "Other",
}


def normalize_country_code(country_code: Any) -> str | None:
    if not isinstance(country_code, str):
        return None
    normalized = country_code.strip().upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def country_code_to_region(country_code: Any) -> str:
    normalized = normalize_country_code(country_code)
    if not normalized:
        return "other"
    if normalized in EU_COUNTRY_CODES:
        return "european-union"
    if normalized == "GB":
        return "united-kingdom"
    if normalized == "CH":
        return "switzerland"
    if normalized == "US":
        return "united-states"
    return "other"


def country_code_to_flag(country_code: Any) -> str:
    normalized = normalize_country_code(country_code)
    if not normalized:
        return ""
    return chr(127397 + ord(normalized[0])) + chr(127397 + ord(normalized[1]))


def sort_cost_data(cost_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(cost_data, key=lambda x: datetime.strptime(x["month"], "%Y-%m-%d"))


def summarize_costs(
    cost_data: list[dict[str, Any]], *, last_n: int | None = None
) -> tuple[list[str], list[float], float, str, str]:
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
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    *,
    resource_name_map: dict[str, str] | None = None,
    resource_id_map: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
    resource_inventory: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    exit_strategy: int,
    alternative_technology_organizations: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    active_technologies = {
        tech["id"]: tech
        for tech in alternative_technologies
        if tech.get("status") == "t"
    }

    grouped_alt_tech: dict[str, list[dict[str, Any]]] = {
        str(resource["resource_type"]): [] for resource in resource_inventory
    }
    organization_by_id = {
        org["id"]: org for org in (alternative_technology_organizations or [])
    }

    for alt in alternatives:
        if str(alt["strategy_type"]) != str(exit_strategy):
            continue

        resource_type = str(alt["resource_type"])
        tech = active_technologies.get(alt["alternative_technology"])
        if not tech or resource_type not in grouped_alt_tech:
            continue
        organization = organization_by_id.get(tech.get("organization_id"))
        organization_country_code = normalize_country_code(
            organization.get("country_code") if organization else None
        )
        organization_region = country_code_to_region(organization_country_code)

        grouped_alt_tech[resource_type].append(
            {
                "product_name": tech["product_name"],
                "product_description": tech["product_description"],
                "product_url": tech["product_url"],
                "open_source": tech["open_source"] == "t",
                "support_plan": tech["support_plan"] == "t",
                "status": tech["status"] == "t",
                "organization_name": (
                    organization.get("name") if organization else "Unknown Organization"
                ),
                "organization_url": organization.get("url") if organization else None,
                "organization_country_code": organization_country_code or "N/A",
                "organization_region": organization_region,
                "organization_region_label": REGION_LABELS.get(
                    organization_region, "Other"
                ),
                "organization_flag": country_code_to_flag(organization_country_code),
            }
        )

    return grouped_alt_tech


def enrich_resource_inventory(
    resource_inventory: list[dict[str, Any]],
    resource_type_mapping: dict[str, dict[str, Any]],
    *,
    report_path: str | None = None,
) -> list[dict[str, Any]]:
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
