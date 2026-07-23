# core/utils_egress_aws.py
import boto3
import logging
from typing import Any
from datetime import datetime, timedelta, timezone
from botocore.exceptions import BotoCoreError, ClientError

from .utils_aws import AWS_RETRY_CONFIG, paginate
from .utils_egress import GIB, format_bytes, new_row

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

EGRESS_RESOURCE_REGISTRY = {
    "AWS.s3.list_buckets.Buckets": {
        "category": "object",
        "label": "S3 Bucket",
        "strategy": "s3_bucket_metrics",
    },
    "AWS.ec2.describe_volumes.Volumes": {
        "category": "block",
        "label": "EBS Volume",
        "strategy": "ebs_volumes",
    },
    "AWS.ec2.describe_snapshots.Snapshots": {
        "category": "block",
        "label": "EBS Snapshot",
        "strategy": "ebs_snapshots",
    },
    "AWS.rds.describe_db_instances.DBInstances": {
        "category": "database",
        "label": "RDS Instance",
        "strategy": "rds_instances",
    },
    "AWS.dynamodb.list_tables.TableNames": {
        "category": "database",
        "label": "DynamoDB Table",
        "strategy": "dynamodb_tables",
    },
    "AWS.backup.list_backup_vaults.BackupVaultList": {
        "category": "backup",
        "label": "Backup Vault",
        "strategy": "backup_vaults",
    },
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


def _bucket_region(s3_client: Any, bucket_name: str) -> str:
    location = s3_client.get_bucket_location(Bucket=bucket_name).get(
        "LocationConstraint"
    )
    if not location:
        return "us-east-1"
    if location == "EU":
        return "eu-west-1"
    return location


def _list_buckets_in_region(s3_client: Any, region: str) -> list[str]:
    try:
        response = s3_client.list_buckets(BucketRegion=region)
        return [bucket["Name"] for bucket in response.get("Buckets", [])]
    except (BotoCoreError, ClientError) as e:
        logger.debug("ListBuckets with BucketRegion filter failed: %s", str(e))

    bucket_names = []
    for bucket in s3_client.list_buckets().get("Buckets", []):
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
    s3_client = session.client("s3", region_name=region, config=AWS_RETRY_CONFIG)
    cloudwatch = session.client(
        "cloudwatch", region_name=region, config=AWS_RETRY_CONFIG
    )

    bucket_names = _list_buckets_in_region(s3_client, region)

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


def _collect_ebs_volumes(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    ec2_client = session.client("ec2", region_name=region, config=AWS_RETRY_CONFIG)
    rows = []
    for volume in paginate(ec2_client, "describe_volumes", "Volumes"):
        name = next(
            (tag["Value"] for tag in volume.get("Tags", []) if tag["Key"] == "Name"),
            volume["VolumeId"],
        )
        row = new_row(volume["VolumeId"], name, code, entry["label"], entry["category"])
        size_gb = volume.get("Size")
        if size_gb:
            row["size_bytes"] = int(size_gb) * GIB
            row["flags"].append("allocated (upper bound)")
        else:
            row["size_unknown"] = True
        rows.append(row)
    return rows


def _collect_ebs_snapshots(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    ec2_client = session.client("ec2", region_name=region, config=AWS_RETRY_CONFIG)
    rows = []
    for snapshot in paginate(
        ec2_client, "describe_snapshots", "Snapshots", OwnerIds=["self"]
    ):
        row = new_row(
            snapshot["SnapshotId"],
            snapshot["SnapshotId"],
            code,
            entry["label"],
            entry["category"],
        )
        size_gb = snapshot.get("VolumeSize")
        if size_gb:
            row["size_bytes"] = int(size_gb) * GIB
            row["flags"].append("allocated (upper bound)")
            row["notes"].append("incremental – shares blocks with sibling snapshots")
        else:
            row["size_unknown"] = True
        rows.append(row)
    return rows


def _collect_rds_instances(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    rds_client = session.client("rds", region_name=region, config=AWS_RETRY_CONFIG)
    cloudwatch = session.client(
        "cloudwatch", region_name=region, config=AWS_RETRY_CONFIG
    )

    rows = []
    specs = []
    spec_rows: dict[str, tuple[dict[str, Any], int]] = {}
    for index, instance in enumerate(
        paginate(rds_client, "describe_db_instances", "DBInstances")
    ):
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
    dynamodb_client = session.client(
        "dynamodb", region_name=region, config=AWS_RETRY_CONFIG
    )
    rows = []
    for table_name in paginate(dynamodb_client, "list_tables", "TableNames"):
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


def _collect_backup_vaults(
    session: Any, region: str, code: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    backup_client = session.client(
        "backup", region_name=region, config=AWS_RETRY_CONFIG
    )
    rows = []
    for vault in paginate(backup_client, "list_backup_vaults", "BackupVaultList"):
        vault_name = vault["BackupVaultName"]
        row = new_row(
            vault.get("BackupVaultArn", vault_name),
            vault_name,
            code,
            entry["label"],
            entry["category"],
        )
        row["flags"].append("backup vault – not sized")
        recovery_points = vault.get("NumberOfRecoveryPoints")
        if recovery_points:
            row["notes"].append(
                f"{recovery_points} recovery points (cannot be exported directly)"
            )
        rows.append(row)
    return rows


_STRATEGY_COLLECTORS = {
    "s3_bucket_metrics": _collect_s3_buckets,
    "ebs_volumes": _collect_ebs_volumes,
    "ebs_snapshots": _collect_ebs_snapshots,
    "rds_instances": _collect_rds_instances,
    "dynamodb_tables": _collect_dynamodb_tables,
    "backup_vaults": _collect_backup_vaults,
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
    for code, entry in EGRESS_RESOURCE_REGISTRY.items():
        collector = _STRATEGY_COLLECTORS[entry["strategy"]]
        try:
            rows.extend(collector(session, region, code, entry))
        except Exception as e:
            logger.debug(
                "Egress collection failed for %s: %s", code, str(e), exc_info=True
            )

    return rows, ARCHIVE_TIERS
