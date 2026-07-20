# core/utils_egress_azure.py
import logging
import requests
from typing import Any
from datetime import datetime, timedelta, timezone
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient

from .utils_egress import GIB, format_bytes, new_row

logger = logging.getLogger("core.engine.egress.azure")

VAULT_FINDING = (
    "Recovery Services vault detected: backup recovery points cannot be "
    "exported from Azure. An exit means losing restore history or retaining "
    "the vault until retention expires."
)

METRICS_API_VERSION = "2018-01-01"
MANAGEMENT_SCOPE = "https://management.azure.com/.default"
MANAGEMENT_BASE_URL = "https://management.azure.com"

ARCHIVE_TIERS = {"Archive"}

EGRESS_RESOURCE_REGISTRY = {
    "microsoft.storage/storageaccounts": {
        "category": "object",
        "label": "Storage Account",
        "strategy": "storage_account_metrics",
    },
    "microsoft.compute/disks": {
        "category": "block",
        "label": "Managed Disk",
        "strategy": "allocated_size_property",
        "api_version": "2024-03-02",
        "size_property": "diskSizeGB",
    },
    "microsoft.compute/snapshots": {
        "category": "block",
        "label": "Snapshot",
        "strategy": "allocated_size_property",
        "api_version": "2024-03-02",
        "size_property": "diskSizeGB",
    },
    "microsoft.sql/servers/databases": {
        "category": "database",
        "label": "SQL Database",
        "strategy": "monitor_metric",
        "metrics": ["storage"],
    },
    "microsoft.documentdb/databaseaccounts": {
        "category": "database",
        "label": "Cosmos DB Account",
        "strategy": "monitor_metric",
        "metrics": ["DataUsage", "IndexUsage"],
    },
    "microsoft.dbforpostgresql/flexibleservers": {
        "category": "database",
        "label": "PostgreSQL Flexible Server",
        "strategy": "monitor_metric",
        "metrics": ["storage_used"],
    },
    "microsoft.dbformysql/flexibleservers": {
        "category": "database",
        "label": "MySQL Flexible Server",
        "strategy": "monitor_metric",
        "metrics": ["storage_used"],
    },
    "microsoft.recoveryservices/vaults": {
        "category": "backup",
        "label": "Recovery Services Vault",
        "strategy": "vault_warning",
    },
}


def filter_data_bearing_resources(
    resources: list[Any],
) -> list[tuple[Any, dict[str, Any]]]:
    matched = []
    for resource in resources:
        entry = EGRESS_RESOURCE_REGISTRY.get(resource.type.strip().lower())
        if entry:
            matched.append((resource, entry))
    return matched


def _latest_average(datapoints: list[dict[str, Any]]) -> float | None:
    for point in reversed(datapoints):
        value = point.get("average")
        if value is not None:
            return float(value)
    return None


def fetch_monitor_metrics(
    credential: Any,
    resource_id: str,
    metric_names: list[str],
    *,
    dimension: str | None = None,
    timeout: int = 30,
) -> dict[str, list[dict[str, Any]]] | None:
    try:
        token = credential.get_token(MANAGEMENT_SCOPE)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=2)
        params = {
            "api-version": METRICS_API_VERSION,
            "metricnames": ",".join(metric_names),
            "timespan": (
                f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/"
                f"{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            ),
            "aggregation": "Average",
            "interval": "PT1H",
        }
        if dimension:
            params["$filter"] = f"{dimension} eq '*'"
        response = requests.get(
            f"{MANAGEMENT_BASE_URL}{resource_id}/providers/Microsoft.Insights/metrics",
            headers={"Authorization": f"Bearer {token.token}"},
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        logger.debug("Metrics request failed for %s: %s", resource_id, str(e))
        return None
    except Exception as e:
        logger.debug("Metrics lookup failed for %s: %s", resource_id, str(e))
        return None

    result: dict[str, list[dict[str, Any]]] = {name: [] for name in metric_names}
    for metric in payload.get("value", []):
        name = metric.get("name", {}).get("value", "")
        series_values = []
        for series in metric.get("timeseries", []):
            dimension_value = None
            if dimension:
                for metadata in series.get("metadatavalues", []):
                    metadata_name = metadata.get("name", {}).get("value", "")
                    if metadata_name.lower() == dimension.lower():
                        dimension_value = metadata.get("value")
            value = _latest_average(series.get("data", []))
            if value is not None:
                series_values.append({"dimension": dimension_value, "value": value})
        result[name] = series_values
    return result


def _metric_total(
    metrics: dict[str, list[dict[str, Any]]] | None, name: str
) -> float | None:
    if not metrics or not metrics.get(name):
        return None
    return sum(point["value"] for point in metrics[name])


def _base_row(resource: Any, entry: dict[str, Any]) -> dict[str, Any]:
    row = new_row(
        resource.id, resource.name, resource.type, entry["label"], entry["category"]
    )
    row["findings"] = []
    return row


def _unknown_size_finding(resource: Any) -> dict[str, str]:
    return {
        "severity": "warning",
        "resource": resource.name,
        "message": (
            f"{resource.name}: size unknown – no metric datapoints in the last "
            "2 days (capacity metrics update roughly daily); excluded from totals."
        ),
    }


def _collect_storage_account(
    credential: Any, resource_client: Any, resource: Any, entry: dict[str, Any]
) -> dict[str, Any]:
    row = _base_row(resource, entry)

    account_metrics = fetch_monitor_metrics(credential, resource.id, ["UsedCapacity"])
    used_capacity = _metric_total(account_metrics, "UsedCapacity")

    blob_metrics = fetch_monitor_metrics(
        credential,
        f"{resource.id}/blobServices/default",
        ["BlobCapacity"],
        dimension="Tier",
    )
    tier_bytes: dict[str, int] = {}
    if blob_metrics:
        for point in blob_metrics.get("BlobCapacity", []):
            tier = (point["dimension"] or "Unknown").capitalize()
            tier_bytes[tier] = tier_bytes.get(tier, 0) + int(point["value"])

    if used_capacity is not None:
        row["size_bytes"] = int(used_capacity)
    elif tier_bytes:
        row["size_bytes"] = sum(tier_bytes.values())
    row["tier_bytes"] = tier_bytes or None

    archive_bytes = tier_bytes.get("Archive", 0)
    if archive_bytes:
        row["flags"].append(
            f"Archive: {format_bytes(archive_bytes)} (rehydration required)"
        )

    sku_name = getattr(getattr(resource, "sku", None), "name", None)
    if sku_name and any(geo in sku_name.upper() for geo in ("GRS", "GZRS")):
        row["notes"].append(f"geo-replicated ({sku_name})")
        row["findings"].append(
            {
                "severity": "info",
                "resource": resource.name,
                "message": (
                    f"{resource.name}: SKU {sku_name} is geo-replicated; the "
                    "geo-secondary copy is not additional data to egress "
                    "(not double-counted)."
                ),
            }
        )

    if row["size_bytes"] is None:
        row["size_unknown"] = True
        row["findings"].append(_unknown_size_finding(resource))
    return row


def _collect_allocated_size(
    credential: Any, resource_client: Any, resource: Any, entry: dict[str, Any]
) -> dict[str, Any]:
    row = _base_row(resource, entry)
    row["flags"].append("allocated (upper bound)")

    full_resource = resource_client.resources.get_by_id(
        resource.id, entry["api_version"]
    )
    size_gb = (full_resource.properties or {}).get(entry["size_property"])
    if size_gb:
        row["size_bytes"] = int(size_gb) * GIB
    else:
        row["size_unknown"] = True
        row["findings"].append(_unknown_size_finding(resource))
    return row


def _collect_monitor_metric(
    credential: Any, resource_client: Any, resource: Any, entry: dict[str, Any]
) -> dict[str, Any]:
    row = _base_row(resource, entry)

    metrics = fetch_monitor_metrics(credential, resource.id, entry["metrics"])
    values = [
        total
        for name in entry["metrics"]
        if (total := _metric_total(metrics, name)) is not None
    ]
    if values:
        row["size_bytes"] = int(sum(values))
    else:
        row["size_unknown"] = True
        row["findings"].append(_unknown_size_finding(resource))
    return row


def _collect_vault(
    credential: Any, resource_client: Any, resource: Any, entry: dict[str, Any]
) -> dict[str, Any]:
    row = _base_row(resource, entry)
    row["flags"].append("backup vault – not sized")
    row["findings"].append(
        {
            "severity": "warning",
            "resource": resource.name,
            "message": f"{resource.name}: {VAULT_FINDING}",
        }
    )
    return row


_STRATEGY_COLLECTORS = {
    "storage_account_metrics": _collect_storage_account,
    "allocated_size_property": _collect_allocated_size,
    "monitor_metric": _collect_monitor_metric,
    "vault_warning": _collect_vault,
}


def build_egress_inventory(
    credential: Any, resource_client: Any, resources: list[Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows = []
    findings = []
    for resource, entry in filter_data_bearing_resources(resources):
        collector = _STRATEGY_COLLECTORS[entry["strategy"]]
        try:
            row = collector(credential, resource_client, resource, entry)
        except Exception as e:
            logger.debug(
                "Egress sizing failed for %s: %s", resource.id, str(e), exc_info=True
            )
            row = _base_row(resource, entry)
            row["size_unknown"] = True
            row["flags"].append("size unavailable")
            row["findings"].append(
                {
                    "severity": "warning",
                    "resource": resource.name,
                    "message": f"{resource.name}: size lookup failed ({str(e)}).",
                }
            )
        findings.extend(row.pop("findings"))
        rows.append(row)
    return rows, findings


def collect_azure_egress(
    provider_details: dict[str, Any],
) -> tuple[list[dict[str, Any]], set[str]]:
    credential = provider_details.get("credential") or ClientSecretCredential(
        tenant_id=provider_details["tenantId"],
        client_id=provider_details["clientId"],
        client_secret=provider_details["clientSecret"],
    )
    subscription_id = provider_details["subscriptionId"]
    resource_group_name = provider_details["resourceGroupName"]

    resource_client = ResourceManagementClient(credential, subscription_id)
    resources = list(
        resource_client.resources.list_by_resource_group(resource_group_name)
    )

    rows, _ = build_egress_inventory(credential, resource_client, resources)
    return rows, ARCHIVE_TIERS
