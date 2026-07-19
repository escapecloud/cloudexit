# tests/test_utils_egress_aws.py
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from core.utils_egress import GIB
from core.utils_egress_aws import (
    EGRESS_RESOURCE_REGISTRY,
    _collect_backup_vaults,
    _collect_dynamodb_tables,
    _collect_ebs_snapshots,
    _collect_ebs_volumes,
    _collect_rds_instances,
    _collect_s3_buckets,
    _list_buckets_in_region,
    collect_aws_egress,
    fetch_latest_metric_values,
)

REGION = "eu-central-1"

S3_CODE = "AWS.s3.list_buckets.Buckets"
VOLUME_CODE = "AWS.ec2.describe_volumes.Volumes"
SNAPSHOT_CODE = "AWS.ec2.describe_snapshots.Snapshots"
RDS_CODE = "AWS.rds.describe_db_instances.DBInstances"
DYNAMODB_CODE = "AWS.dynamodb.list_tables.TableNames"
BACKUP_CODE = "AWS.backup.list_backup_vaults.BackupVaultList"


def _client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _mock_session(clients):
    session = MagicMock()
    session.client.side_effect = lambda service, **kwargs: clients[service]
    return session


def _mock_paginator(client, pages):
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    client.get_paginator.return_value = paginator
    return paginator


class FetchLatestMetricValuesTests(unittest.TestCase):
    def test_returns_latest_value_per_query(self):
        cloudwatch = MagicMock()
        cloudwatch.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": "q0", "Values": [123.0, 50.0]},
                {"Id": "q1", "Values": []},
            ]
        }
        specs = [
            {"id": "q0", "namespace": "AWS/S3", "metric_name": "m", "dimensions": []},
            {"id": "q1", "namespace": "AWS/S3", "metric_name": "m", "dimensions": []},
        ]

        result = fetch_latest_metric_values(cloudwatch, specs)

        # ScanBy=TimestampDescending: the first value is the latest datapoint.
        self.assertEqual(result, {"q0": 123.0, "q1": None})
        kwargs = cloudwatch.get_metric_data.call_args.kwargs
        self.assertEqual(kwargs["ScanBy"], "TimestampDescending")

    def test_merges_values_across_pages(self):
        cloudwatch = MagicMock()
        cloudwatch.get_metric_data.side_effect = [
            {
                "MetricDataResults": [{"Id": "q0", "Values": []}],
                "NextToken": "page-2",
            },
            {"MetricDataResults": [{"Id": "q0", "Values": [42.0]}]},
        ]
        specs = [
            {"id": "q0", "namespace": "AWS/S3", "metric_name": "m", "dimensions": []},
        ]

        result = fetch_latest_metric_values(cloudwatch, specs)

        self.assertEqual(result, {"q0": 42.0})

    def test_empty_specs_return_empty_dict_without_api_call(self):
        cloudwatch = MagicMock()

        self.assertEqual(fetch_latest_metric_values(cloudwatch, []), {})
        cloudwatch.get_metric_data.assert_not_called()

    def test_returns_none_on_client_error(self):
        cloudwatch = MagicMock()
        cloudwatch.get_metric_data.side_effect = _client_error(
            "AccessDenied", "GetMetricData"
        )
        specs = [
            {"id": "q0", "namespace": "AWS/S3", "metric_name": "m", "dimensions": []},
        ]

        self.assertIsNone(fetch_latest_metric_values(cloudwatch, specs))


class ListBucketsInRegionTests(unittest.TestCase):
    def test_falls_back_to_bucket_region_field_when_filter_unsupported(self):
        s3_client = MagicMock()
        s3_client.list_buckets.side_effect = [
            _client_error("InvalidRequest", "ListBuckets"),
            {
                "Buckets": [
                    {"Name": "data-eu", "BucketRegion": REGION},
                    {"Name": "data-us", "BucketRegion": "us-east-1"},
                ]
            },
        ]

        names = _list_buckets_in_region(s3_client, REGION)

        self.assertEqual(names, ["data-eu"])
        s3_client.get_bucket_location.assert_not_called()

    def test_falls_back_to_get_bucket_location_when_field_missing(self):
        s3_client = MagicMock()
        s3_client.list_buckets.side_effect = [
            _client_error("InvalidRequest", "ListBuckets"),
            {"Buckets": [{"Name": "data-eu"}, {"Name": "legacy-us"}]},
        ]
        s3_client.get_bucket_location.side_effect = lambda Bucket: {
            # us-east-1 buckets report None via the legacy API.
            "LocationConstraint": REGION if Bucket == "data-eu" else None
        }

        names = _list_buckets_in_region(s3_client, REGION)

        self.assertEqual(names, ["data-eu"])

    def test_bucket_is_skipped_when_location_lookup_is_denied(self):
        s3_client = MagicMock()
        s3_client.list_buckets.side_effect = [
            _client_error("InvalidRequest", "ListBuckets"),
            {"Buckets": [{"Name": "no-access"}]},
        ]
        s3_client.get_bucket_location.side_effect = _client_error(
            "AccessDenied", "GetBucketLocation"
        )

        self.assertEqual(_list_buckets_in_region(s3_client, REGION), [])


class S3BucketCollectorTests(unittest.TestCase):
    def _clients(self, bucket_locations, metrics, metric_values):
        s3_client = MagicMock()

        def list_buckets(**kwargs):
            names = bucket_locations
            if "BucketRegion" in kwargs:
                names = [
                    name
                    for name, location in bucket_locations.items()
                    if (location or "us-east-1") == kwargs["BucketRegion"]
                ]
            return {"Buckets": [{"Name": name} for name in names]}

        s3_client.list_buckets.side_effect = list_buckets
        s3_client.get_bucket_location.side_effect = lambda Bucket: {
            "LocationConstraint": bucket_locations[Bucket]
        }
        s3_client.get_bucket_replication.side_effect = _client_error(
            "ReplicationConfigurationNotFoundError", "GetBucketReplication"
        )
        cloudwatch = MagicMock()
        cloudwatch.list_metrics.return_value = {"Metrics": metrics}
        cloudwatch.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": query_id, "Values": [value]}
                for query_id, value in metric_values.items()
            ]
        }
        return {"s3": s3_client, "cloudwatch": cloudwatch}

    @staticmethod
    def _size_metric(bucket, storage_type):
        return {
            "Dimensions": [
                {"Name": "BucketName", "Value": bucket},
                {"Name": "StorageType", "Value": storage_type},
            ]
        }

    def test_storage_type_split_archive_flag_and_region_filter(self):
        clients = self._clients(
            bucket_locations={"data-eu": REGION, "data-us": None},
            metrics=[
                self._size_metric("data-eu", "StandardStorage"),
                self._size_metric("data-eu", "GlacierStorage"),
                self._size_metric("data-eu", "DeepArchiveStorage"),
            ],
            metric_values={
                "q0": float(50 * GIB),
                "q1": float(8 * GIB),
                "q2": float(2 * GIB),
            },
        )
        entry = EGRESS_RESOURCE_REGISTRY[S3_CODE]

        rows = _collect_s3_buckets(_mock_session(clients), REGION, S3_CODE, entry)

        # The us-east-1 bucket is outside the assessment region, and the
        # filter must not rely on GetBucketLocation (not in ViewOnlyAccess).
        self.assertEqual([row["name"] for row in rows], ["data-eu"])
        clients["s3"].list_buckets.assert_called_once_with(BucketRegion=REGION)
        clients["s3"].get_bucket_location.assert_not_called()
        row = rows[0]
        self.assertEqual(row["size_bytes"], 60 * GIB)
        self.assertEqual(
            row["tier_bytes"],
            {"Standard": 50 * GIB, "Glacier": 8 * GIB, "Deep Archive": 2 * GIB},
        )
        self.assertTrue(any("restore required" in flag for flag in row["flags"]))

    def test_no_datapoints_records_unknown_size(self):
        clients = self._clients(
            bucket_locations={"fresh-bucket": REGION},
            metrics=[self._size_metric("fresh-bucket", "StandardStorage")],
            metric_values={},
        )
        entry = EGRESS_RESOURCE_REGISTRY[S3_CODE]

        rows = _collect_s3_buckets(_mock_session(clients), REGION, S3_CODE, entry)

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertTrue(rows[0]["size_unknown"])

    def test_replication_configuration_adds_note(self):
        clients = self._clients(
            bucket_locations={"replicated": REGION},
            metrics=[self._size_metric("replicated", "StandardStorage")],
            metric_values={"q0": float(GIB)},
        )
        clients["s3"].get_bucket_replication.side_effect = None
        clients["s3"].get_bucket_replication.return_value = {
            "ReplicationConfiguration": {"Rules": [{}]}
        }
        entry = EGRESS_RESOURCE_REGISTRY[S3_CODE]

        rows = _collect_s3_buckets(_mock_session(clients), REGION, S3_CODE, entry)

        self.assertTrue(any("replication" in note for note in rows[0]["notes"]))


class EbsCollectorTests(unittest.TestCase):
    def test_volume_size_is_allocated_upper_bound(self):
        ec2_client = MagicMock()
        _mock_paginator(
            ec2_client,
            [
                {
                    "Volumes": [
                        {
                            "VolumeId": "vol-1",
                            "Size": 100,
                            "Tags": [{"Key": "Name", "Value": "data-disk"}],
                        }
                    ]
                }
            ],
        )
        entry = EGRESS_RESOURCE_REGISTRY[VOLUME_CODE]

        rows = _collect_ebs_volumes(
            _mock_session({"ec2": ec2_client}), REGION, VOLUME_CODE, entry
        )

        self.assertEqual(rows[0]["name"], "data-disk")
        self.assertEqual(rows[0]["size_bytes"], 100 * GIB)
        self.assertIn("allocated (upper bound)", rows[0]["flags"])

    def test_snapshots_are_owned_only_and_carry_shared_block_note(self):
        ec2_client = MagicMock()
        paginator = _mock_paginator(
            ec2_client,
            [{"Snapshots": [{"SnapshotId": "snap-1", "VolumeSize": 50}]}],
        )
        entry = EGRESS_RESOURCE_REGISTRY[SNAPSHOT_CODE]

        rows = _collect_ebs_snapshots(
            _mock_session({"ec2": ec2_client}), REGION, SNAPSHOT_CODE, entry
        )

        paginator.paginate.assert_called_once_with(OwnerIds=["self"])
        self.assertEqual(rows[0]["size_bytes"], 50 * GIB)
        self.assertTrue(any("shares blocks" in note for note in rows[0]["notes"]))


class RdsCollectorTests(unittest.TestCase):
    def _clients(self, instances, metric_results):
        rds_client = MagicMock()
        _mock_paginator(rds_client, [{"DBInstances": instances}])
        cloudwatch = MagicMock()
        cloudwatch.get_metric_data.return_value = {"MetricDataResults": metric_results}
        return {"rds": rds_client, "cloudwatch": cloudwatch}

    def test_used_space_computed_from_free_storage_space(self):
        clients = self._clients(
            instances=[
                {
                    "DBInstanceIdentifier": "appdb",
                    "Engine": "postgres",
                    "AllocatedStorage": 100,
                }
            ],
            metric_results=[{"Id": "q0", "Values": [float(40 * GIB)]}],
        )
        entry = EGRESS_RESOURCE_REGISTRY[RDS_CODE]

        rows = _collect_rds_instances(_mock_session(clients), REGION, RDS_CODE, entry)

        self.assertEqual(rows[0]["size_bytes"], 60 * GIB)
        self.assertTrue(any("allocated" in note for note in rows[0]["notes"]))
        self.assertNotIn("allocated (upper bound)", rows[0]["flags"])

    def test_missing_metric_falls_back_to_allocated_upper_bound(self):
        clients = self._clients(
            instances=[
                {
                    "DBInstanceIdentifier": "appdb",
                    "Engine": "mysql",
                    "AllocatedStorage": 100,
                }
            ],
            metric_results=[{"Id": "q0", "Values": []}],
        )
        entry = EGRESS_RESOURCE_REGISTRY[RDS_CODE]

        rows = _collect_rds_instances(_mock_session(clients), REGION, RDS_CODE, entry)

        self.assertEqual(rows[0]["size_bytes"], 100 * GIB)
        self.assertIn("allocated (upper bound)", rows[0]["flags"])

    def test_aurora_instances_are_flagged_not_sized(self):
        clients = self._clients(
            instances=[
                {
                    "DBInstanceIdentifier": "aurora-1",
                    "Engine": "aurora-postgresql",
                    "AllocatedStorage": 1,
                }
            ],
            metric_results=[],
        )
        entry = EGRESS_RESOURCE_REGISTRY[RDS_CODE]

        rows = _collect_rds_instances(_mock_session(clients), REGION, RDS_CODE, entry)

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertTrue(any("Aurora" in flag for flag in rows[0]["flags"]))
        clients["cloudwatch"].get_metric_data.assert_not_called()


class DynamoDbCollectorTests(unittest.TestCase):
    def test_table_and_index_sizes_are_summed(self):
        dynamodb_client = MagicMock()
        _mock_paginator(dynamodb_client, [{"TableNames": ["orders"]}])
        dynamodb_client.describe_table.return_value = {
            "Table": {
                "TableArn": "arn:aws:dynamodb:eu-central-1:1:table/orders",
                "TableSizeBytes": 1000,
                "GlobalSecondaryIndexes": [
                    {"IndexSizeBytes": 300},
                    {"IndexSizeBytes": 200},
                ],
            }
        }
        entry = EGRESS_RESOURCE_REGISTRY[DYNAMODB_CODE]

        rows = _collect_dynamodb_tables(
            _mock_session({"dynamodb": dynamodb_client}), REGION, DYNAMODB_CODE, entry
        )

        self.assertEqual(rows[0]["size_bytes"], 1500)

    def test_empty_table_is_zero_not_unknown(self):
        dynamodb_client = MagicMock()
        _mock_paginator(dynamodb_client, [{"TableNames": ["empty"]}])
        dynamodb_client.describe_table.return_value = {"Table": {"TableSizeBytes": 0}}
        entry = EGRESS_RESOURCE_REGISTRY[DYNAMODB_CODE]

        rows = _collect_dynamodb_tables(
            _mock_session({"dynamodb": dynamodb_client}), REGION, DYNAMODB_CODE, entry
        )

        self.assertEqual(rows[0]["size_bytes"], 0)
        self.assertFalse(rows[0]["size_unknown"])


class BackupVaultCollectorTests(unittest.TestCase):
    def test_vault_is_flagged_not_sized_with_recovery_point_note(self):
        backup_client = MagicMock()
        _mock_paginator(
            backup_client,
            [
                {
                    "BackupVaultList": [
                        {"BackupVaultName": "prod-vault", "NumberOfRecoveryPoints": 12}
                    ]
                }
            ],
        )
        entry = EGRESS_RESOURCE_REGISTRY[BACKUP_CODE]

        rows = _collect_backup_vaults(
            _mock_session({"backup": backup_client}), REGION, BACKUP_CODE, entry
        )

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertFalse(rows[0]["size_unknown"])
        self.assertIn("backup vault – not sized", rows[0]["flags"])
        self.assertTrue(any("12 recovery points" in note for note in rows[0]["notes"]))


class CollectAwsEgressTests(unittest.TestCase):
    _PROVIDER_DETAILS = {
        "accessKey": "AKIAIOSFODNN7EXAMPLE",
        "secretKey": "secret",
        "region": REGION,
    }

    def _empty_clients(self):
        s3_client = MagicMock()
        s3_client.list_buckets.return_value = {"Buckets": []}
        clients = {"s3": s3_client, "cloudwatch": MagicMock()}
        for service, result_key in (
            ("ec2", "Volumes"),
            ("rds", "DBInstances"),
            ("dynamodb", "TableNames"),
            ("backup", "BackupVaultList"),
        ):
            client = MagicMock()
            _mock_paginator(client, [{result_key: []}])
            clients[service] = client
        return clients

    @patch("core.utils_egress_aws.boto3")
    def test_returns_rows_and_archive_tiers(self, mock_boto3):
        clients = self._empty_clients()
        mock_boto3.Session.return_value = _mock_session(clients)

        rows, archive_tiers = collect_aws_egress(self._PROVIDER_DETAILS)

        self.assertEqual(rows, [])
        self.assertEqual(archive_tiers, {"Archive", "Glacier", "Deep Archive"})
        mock_boto3.Session.assert_called_once_with(
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="secret",
            aws_session_token=None,
            region_name=REGION,
        )

    @patch("core.utils_egress_aws.boto3")
    def test_one_failing_service_does_not_fail_the_stage(self, mock_boto3):
        clients = self._empty_clients()
        clients["s3"].list_buckets.side_effect = _client_error(
            "AccessDenied", "ListBuckets"
        )
        volume_paginator = MagicMock()
        volume_paginator.paginate.return_value = [
            {"Volumes": [{"VolumeId": "vol-1", "Size": 10}]}
        ]
        clients["ec2"].get_paginator.return_value = volume_paginator
        mock_boto3.Session.return_value = _mock_session(clients)

        rows, _ = collect_aws_egress(self._PROVIDER_DETAILS)

        # Volumes and snapshots share the ec2 paginator mock here; the point
        # is that the S3 failure is contained and other rows still arrive.
        self.assertTrue(any(row["id"] == "vol-1" for row in rows))


if __name__ == "__main__":
    unittest.main()
