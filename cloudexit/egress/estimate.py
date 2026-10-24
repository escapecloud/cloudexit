# cloudexit/egress/estimate.py
import json
import logging
import os
from typing import Any
from datetime import datetime, timezone

from cloudexit.egress.model import (
    compute_totals,
    load_egress_registry,
    public_row,
    write_egress_inventory,
)

logger = logging.getLogger(__name__)


def estimate_egress(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    raw_data_path: str,
    *,
    report_path: str,
    name: str,
    exit_strategy: int,
    assessment_type: int,
) -> dict[str, Any]:
    try:
        if cloud_service_provider == 1:  # Azure
            from cloudexit.providers.azure.egress import collect_azure_egress

            rows, archive_tiers = collect_azure_egress(provider_details)
        elif cloud_service_provider == 2:  # AWS
            from cloudexit.providers.aws.egress import collect_aws_egress

            rows, archive_tiers = collect_aws_egress(provider_details)
        else:
            raise ValueError(
                f"Unsupported cloud service provider: {cloud_service_provider}"
            )

        resource_type_ids = {
            key: entry["resource_type_id"]
            for key, entry in load_egress_registry(cloud_service_provider).items()
        }

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
                "resources": [public_row(row) for row in rows],
                "totals": compute_totals(rows, archive_tiers),
            },
        }
        # The estimate is stored per assessment; the JSON stays as the raw
        # artifact alongside the other raw dumps.
        write_egress_inventory(
            rows,
            resource_type_ids,
            archive_tiers,
            os.path.join(report_path, "data", "assessment.db"),
        )

        json_path = os.path.join(raw_data_path, "egress_inventory_raw_data.json")
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
