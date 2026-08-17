# core/utils_egress_aws.py
import boto3
import logging
from typing import Any
from datetime import datetime, timedelta, timezone
from botocore.exceptions import BotoCoreError, ClientError

from .utils_aws import (
    AWS_RETRY_CONFIG,
    extract_result_path,
    paginate_or_call,
)
from .utils_egress import GIB, format_bytes, load_egress_registry, new_row

logger = logging.getLogger("core.engine.egress.aws")

METRIC_DATA_BATCH_SIZE = 500

METRICS_LOOKBACK_DAYS = 3

ARCHIVE_TIERS = {"Archive", "Glacier", "Deep Archive"}

S3_STORAGE_TYPE_TIERS = {
    "StandardStorage": "Standard",
    "ReducedRedundancyStorage": "Standard",
    "ExpressOneZone": "Standard",
    "StandardIAStorage": "Standard-IA",
    "StandardIASizeOverhead": "Standard-IA",
    "OneZoneIAStorage": "One Zone-IA",
    "OneZoneIASizeOverhead": "One Zone-IA",
    "IntelligentTieringFAStorage": "Intelligent-Tiering",
    "IntelligentTieringIAStorage": "Intelligent-Tiering",
    "IntelligentTieringAIAStorage": "Intelligent-Tiering",
    "IntelligentTieringAAStorage": "Archive",
    "IntelligentTieringDAAStorage": "Deep Archive",
    "GlacierInstantRetrievalStorage": "Glacier Instant Retrieval",
    "GlacierInstantRetrievalSizeOverhead": "Glacier Instant Retrieval",
    "GlacierStorage": "Glacier",
    "GlacierStagingStorage": "Glacier",
    "GlacierObjectOverhead": "Glacier",
    "GlacierS3ObjectOverhead": "Glacier",
    "DeepArchiveStorage": "Deep Archive",
    "DeepArchiveObjectOverhead": "Deep Archive",
    "DeepArchiveS3ObjectOverhead": "Deep Archive",
    "DeepArchiveStagingStorage": "Deep Archive",
}


def fetch_latest_metric_values(
    cloudwatch: Any,
    metric_specs: list[dict[str, Any]],
    *,
    lookback_days: int = METRICS_LOOKBACK_DAYS,
    period: int = 86400,
) -> dict[str, float | None] | None:
    if not metric_specs:
        return {}
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        results: dict[str, float | None] = {spec["id"]: None for spec in metric_specs}

        for batch_start in range(0, len(metric_specs), METRIC_DATA_BATCH_SIZE):
            batch = metric_specs[batch_start : batch_start + METRIC_DATA_BATCH_SIZE]
            queries = [
                {
                    "Id": spec["id"],
                    "MetricStat": {
                        "Metric": {
                            "Namespace": spec["namespace"],
                            "MetricName": spec["metric_name"],
                            "Dimensions": spec["dimensions"],
                        },
                        "Period": period,
                        "Stat": spec.get("stat", "Average"),
                    },
                    "ReturnData": True,
                }
                for spec in batch
            ]
            kwargs = {
                "MetricDataQueries": queries,
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampDescending",
            }
            while True:
                response = cloudwatch.get_metric_data(**kwargs)
                for series in response.get("MetricDataResults", []):
                    values = series.get("Values") or []
                    if values and results.get(series["Id"]) is None:
                        results[series["Id"]] = float(values[0])
                next_token = response.get("NextToken")
                if not next_token:
                    break
                kwargs["NextToken"] = next_token
        return results
    except (BotoCoreError, ClientError) as e:
        logger.debug("CloudWatch metrics request failed: %s", str(e))
        return None


SIZE_UNITS = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": GIB,
    "TiB": 1024**4,
}


def _nested_get(item: dict[str, Any], path: Any) -> Any:
    if isinstance(path, str):
        path = [path]
    value: Any = item
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _resource_identity(item: dict[str, Any], sizing: dict[str, Any]) -> tuple[str, str]:
    # The row id prefers an ARN when the service reports one; the display name
    # comes from a tag when the service tags resources, else from a field.
    identifier = item[sizing["id_field"]]
    name_tag = sizing.get("name_tag")
    if name_tag:
        name = next(
            (
                tag["Value"]
                for tag in item.get(sizing.get("tags_field", "Tags"), [])
                if tag["Key"] == name_tag
            ),
            identifier,
        )
    else:
        name = item.get(sizing.get("name_field") or sizing["id_field"], identifier)
    arn_field = sizing.get("arn_field")
    row_id = item.get(arn_field, identifier) if arn_field else identifier
    return row_id, name


def _enumeration_client(session: Any, region: str, entry: dict[str, Any]) -> Any:
    return session.client(
        entry["enumeration"]["service"], region_name=region, config=AWS_RETRY_CONFIG
    )


def _enumerate(session: Any, region: str, entry: dict[str, Any]) -> tuple[Any, list]:
    # service/operation/result_path/kwargs all come from resourcetype_data, so a
    # catalogue change does not need a code change. Sizing stays in the strategy.
    enumeration = entry["enumeration"]
    client = _enumeration_client(session, region, entry)
    items = paginate_or_call(
        client,
        enumeration["operation"],
        enumeration["result_path"],
        **enumeration.get("kwargs", {}),
    )
    return client, items


def _bucket_region(s3_client: Any, bucket_name: str) -> str:
    location = s3_client.get_bucket_location(Bucket=bucket_name).get(
        "LocationConstraint"
    )
    if not location:
        return "us-east-1"
    if location == "EU":
        return "eu-west-1"
    return location


def _list_buckets_in_region(
    s3_client: Any, region: str, enumeration: dict[str, Any]
) -> list[str]:
    # The region filter is strategy logic, but the call itself is master data.
    list_buckets = getattr(s3_client, enumeration["operation"])
    result_path = enumeration["result_path"]
    kwargs = enumeration.get("kwargs", {})
    try:
        response = list_buckets(BucketRegion=region, **kwargs)
        return [bucket["Name"] for bucket in extract_result_path(response, result_path)]
    except (BotoCoreError, ClientError) as e:
        logger.debug("ListBuckets with BucketRegion filter failed: %s", str(e))

    bucket_names = []
    for bucket in extract_result_path(list_buckets(**kwargs), result_path):
        name = bucket["Name"]
        bucket_region = bucket.get("BucketRegion")
        if bucket_region is None:
            try:
                bucket_region = _bucket_region(s3_client, name)
            except (BotoCoreError, ClientError) as e:
                logger.debug("Skipping bucket %s: %s", name, str(e))
                continue
        if bucket_region == region:
            bucket_names.append(name)
    return bucket_names


def _collect_s3_buckets(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    s3_client = _enumeration_client(session, region, entry)
    cloudwatch = session.client(
        "cloudwatch", region_name=region, config=AWS_RETRY_CONFIG
    )

    bucket_names = _list_buckets_in_region(s3_client, region, entry["enumeration"])

    rows = []
    for name in bucket_names:
        row = new_row(
            f"arn:aws:s3:::{name}", name, code, entry["label"], entry["category"]
        )

        try:
            metrics = cloudwatch.list_metrics(
                Namespace="AWS/S3",
                MetricName="BucketSizeBytes",
                Dimensions=[{"Name": "BucketName", "Value": name}],
            ).get("Metrics", [])
        except (BotoCoreError, ClientError) as e:
            logger.debug("Listing metrics failed for bucket %s: %s", name, str(e))
            metrics = []

        specs = []
        spec_tiers = {}
        for index, metric in enumerate(metrics):
            storage_type = next(
                (
                    dimension["Value"]
                    for dimension in metric.get("Dimensions", [])
                    if dimension["Name"] == "StorageType"
                ),
                "Unknown",
            )
            spec_id = f"q{index}"
            specs.append(
                {
                    "id": spec_id,
                    "namespace": "AWS/S3",
                    "metric_name": "BucketSizeBytes",
                    "dimensions": metric.get("Dimensions", []),
                }
            )
            spec_tiers[spec_id] = S3_STORAGE_TYPE_TIERS.get(storage_type, storage_type)

        values = fetch_latest_metric_values(cloudwatch, specs)
        tier_bytes: dict[str, int] = {}
        if values:
            for spec_id, value in values.items():
                if value is None:
                    continue
                tier = spec_tiers[spec_id]
                tier_bytes[tier] = tier_bytes.get(tier, 0) + int(value)

        if tier_bytes:
            row["size_bytes"] = sum(tier_bytes.values())
            row["tier_bytes"] = tier_bytes
            archive_bytes = sum(
                size for tier, size in tier_bytes.items() if tier in ARCHIVE_TIERS
            )
            if archive_bytes:
                row["flags"].append(
                    f"Archive-class: {format_bytes(archive_bytes)} (restore required)"
                )
        else:
            row["size_unknown"] = True

        try:
            s3_client.get_bucket_replication(Bucket=name)
            row["notes"].append(
                "replication configured (replica buckets are counted separately)"
            )
        except (BotoCoreError, ClientError):
            pass

        rows.append(row)
    return rows


def _collect_list_item_size(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    sizing = entry["sizing"]
    multiplier = SIZE_UNITS[sizing.get("size_unit", "GiB")]
    _, items = _enumerate(session, region, entry)
    rows = []
    for item in items:
        row_id, name = _resource_identity(item, sizing)
        row = new_row(row_id, name, code, entry["label"], entry["category"])
        size = _nested_get(item, sizing["size_field"])
        if size:
            row["size_bytes"] = int(size) * multiplier
            row["flags"].extend(sizing.get("flags", []))
            row["notes"].extend(sizing.get("notes", []))
        else:
            row["size_unknown"] = True
        rows.append(row)
    return rows


def _collect_not_sizeable(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    sizing = entry["sizing"]
    _, items = _enumerate(session, region, entry)
    rows = []
    for item in items:
        row_id, name = _resource_identity(item, sizing)
        row = new_row(row_id, name, code, entry["label"], entry["category"])
        row["flags"].extend(sizing.get("flags", []))
        count_field = sizing.get("count_field")
        count = _nested_get(item, count_field) if count_field else None
        if count:
            row["notes"].append(sizing["count_note"].format(count=count))
        rows.append(row)
    return rows


def _collect_rds_instances(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    _, instances = _enumerate(session, region, entry)
    cloudwatch = session.client(
        "cloudwatch", region_name=region, config=AWS_RETRY_CONFIG
    )

    rows = []
    specs = []
    spec_rows: dict[str, tuple[dict[str, Any], int]] = {}
    for index, instance in enumerate(instances):
        identifier = instance["DBInstanceIdentifier"]
        row = new_row(
            instance.get("DBInstanceArn", identifier),
            identifier,
            code,
            entry["label"],
            entry["category"],
        )
        if (instance.get("Engine") or "").startswith("aurora"):
            row["flags"].append("Aurora – cluster-level storage not sized")
            rows.append(row)
            continue

        allocated_bytes = int(instance.get("AllocatedStorage") or 0) * GIB
        spec_id = f"q{index}"
        specs.append(
            {
                "id": spec_id,
                "namespace": "AWS/RDS",
                "metric_name": "FreeStorageSpace",
                "dimensions": [{"Name": "DBInstanceIdentifier", "Value": identifier}],
            }
        )
        spec_rows[spec_id] = (row, allocated_bytes)
        rows.append(row)

    values = fetch_latest_metric_values(cloudwatch, specs, period=300)
    for spec_id, (row, allocated_bytes) in spec_rows.items():
        free_bytes = values.get(spec_id) if values else None
        if free_bytes is not None and allocated_bytes:
            row["size_bytes"] = max(int(allocated_bytes - free_bytes), 0)
            row["notes"].append(f"allocated: {format_bytes(allocated_bytes)}")
        elif allocated_bytes:
            row["size_bytes"] = allocated_bytes
            row["flags"].append("allocated (upper bound)")
        else:
            row["size_unknown"] = True
    return rows


def _collect_dynamodb_tables(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    dynamodb_client, table_names = _enumerate(session, region, entry)
    rows = []
    for table_name in table_names:
        try:
            table = dynamodb_client.describe_table(TableName=table_name).get(
                "Table", {}
            )
        except (BotoCoreError, ClientError) as e:
            logger.debug("Describing table %s failed: %s", table_name, str(e))
            row = new_row(
                table_name, table_name, code, entry["label"], entry["category"]
            )
            row["size_unknown"] = True
            rows.append(row)
            continue

        row = new_row(
            table.get("TableArn", table_name),
            table_name,
            code,
            entry["label"],
            entry["category"],
        )
        size_bytes = int(table.get("TableSizeBytes") or 0)
        size_bytes += sum(
            int(index.get("IndexSizeBytes") or 0)
            for index in table.get("GlobalSecondaryIndexes", [])
        )
        row["size_bytes"] = size_bytes
        rows.append(row)
    return rows


# Strategy names stay stable so master data and engine can be deployed
# independently; several of them share one parameterised collector. The
# service-neutral names are the ones to use for new master-data rows.
_STRATEGY_COLLECTORS = {
    "s3_bucket_metrics": _collect_s3_buckets,
    "rds_instances": _collect_rds_instances,
    "dynamodb_tables": _collect_dynamodb_tables,
    "list_item_size": _collect_list_item_size,
    "not_sizeable": _collect_not_sizeable,
    # Kept so existing rows keep working; prefer the two names above.
    "ebs_volumes": _collect_list_item_size,
    "ebs_snapshots": _collect_list_item_size,
    "backup_vaults": _collect_not_sizeable,
}


def collect_aws_egress(
    provider_details: dict[str, Any],
) -> tuple[list[dict[str, Any]], set[str]]:
    region = provider_details["region"]
    session = boto3.Session(
        aws_access_key_id=provider_details["accessKey"],
        aws_secret_access_key=provider_details["secretKey"],
        aws_session_token=provider_details.get("sessionToken"),
        region_name=region,
    )

    rows = []
    for code, entry in load_egress_registry(2).items():
        collector = _STRATEGY_COLLECTORS.get(entry["strategy"])
        if collector is None:
            # Master data can ship a strategy ahead of the engine; skip that one
            # resource type rather than aborting the whole egress run.
            logger.warning(
                "Unknown egress strategy %r for %s; skipping.",
                entry["strategy"],
                code,
            )
            continue
        try:
            rows.extend(collector(session, region, code, entry))
        except Exception as e:
            # One failing service must not abort the run, but a whole resource
            # type dropping out of the estimate has to be visible in run.log.
            logger.warning(
                "Egress collection failed for %s (%s: %s); "
                "this resource type is missing from the estimate.",
                code,
                type(e).__name__,
                str(e),
                exc_info=True,
            )

    return rows, ARCHIVE_TIERS
