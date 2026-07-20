# core/utils_egress.py
import json
import logging
import os
from typing import Any
from datetime import datetime, timezone

logger = logging.getLogger("core.engine.egress")

GIB = 1024**3


def new_row(
    resource_id: str, name: str, resource_type: str, label: str, category: str
) -> dict[str, Any]:
    return {
        "id": resource_id,
        "name": name,
        "type": resource_type,
        "label": label,
        "category": category,
        "size_bytes": None,
        "size_unknown": False,
        "tier_bytes": None,
        "flags": [],
        "notes": [],
    }


def format_bytes(size_bytes: int | float | None) -> str:
    if size_bytes is None:
        return "n/a"
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def compute_totals(
    rows: list[dict[str, Any]], archive_tiers: set[str]
) -> dict[str, Any]:
    known_size_bytes = sum(
        row["size_bytes"] for row in rows if row["size_bytes"] is not None
    )
    archive_tier_bytes = sum(
        size
        for row in rows
        for tier, size in (row["tier_bytes"] or {}).items()
        if tier in archive_tiers
    )
    unknown_count = sum(1 for row in rows if row["size_unknown"])
    return {
        "known_size_bytes": known_size_bytes,
        "archive_tier_bytes": archive_tier_bytes,
        "resources_discovered": len(rows),
        "resources_with_unknown_size": unknown_count,
    }


def estimate_egress(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    raw_data_path: str,
    *,
    name: str,
    exit_strategy: int,
    assessment_type: int,
) -> dict[str, Any]:
    try:
        if cloud_service_provider == 1:  # Azure
            from .utils_egress_azure import collect_azure_egress

            rows, archive_tiers = collect_azure_egress(provider_details)
        elif cloud_service_provider == 2:  # AWS
            from .utils_egress_aws import collect_aws_egress

            rows, archive_tiers = collect_aws_egress(provider_details)
        else:
            raise ValueError(
                f"Unsupported cloud service provider: {cloud_service_provider}"
            )

        json_payload = {
            "meta": {
                "name": name,
                "cloud_service_provider": cloud_service_provider,
                "exit_strategy": exit_strategy,
                "assessment_type": assessment_type,
                "timestamp": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
            },
            "data": {
                "resources": rows,
                "totals": compute_totals(rows, archive_tiers),
            },
        }
        json_path = os.path.join(raw_data_path, "egress_estimate.json")
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(json_payload, json_file, indent=4)

        return {
            "success": True,
            "logs": "Egress estimation completed successfully.",
            "json_path": json_path,
        }

    except Exception as e:
        logger.error(f"Error estimating egress: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}
