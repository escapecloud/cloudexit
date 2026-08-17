# tests/test_utils_egress_aws.py
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from core.utils_egress import GIB
from core.utils_egress_aws import (
    _collect_dynamodb_tables,
    _collect_list_item_size,
    _collect_not_sizeable,
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
# The catalogue row enumerates backup plans; sizing overrides it with vaults.
BACKUP_CODE = "AWS.backup.list_backup_plans.BackupPlansList"

# Stands in for what load_egress_registry() builds out of resourcetype_data,
# so these tests exercise the engine rather than the shipped master data.
TEST_REGISTRY = {
    S3_CODE: {
        "category": "object",
        "label": "S3 (Simple Storage Service)",
        "strategy": "s3_bucket_metrics",
        "enumeration": {
            "service": "s3",
            "operation": "list_buckets",
            "result_path": ["Buckets"],
        },
    },
    VOLUME_CODE: {
        "category": "block",
        "label": "Elastic Block Store (EBS)",
        "strategy": "ebs_volumes",
        "enumeration": {
            "service": "ec2",
            "operation": "describe_volumes",
            "result_path": ["Volumes"],
        },
        "sizing": {
            "id_field": "VolumeId",
            "name_tag": "Name",
            "size_field": "Size",
            "size_unit": "GiB",
            "flags": ["allocated (upper bound)"],
        },
    },
    SNAPSHOT_CODE: {
        "category": "block",
        "label": "EBS Snapshots",
        "strategy": "ebs_snapshots",
        "enumeration": {
            "service": "ec2",
            "operation": "describe_snapshots",
            "result_path": ["Snapshots"],
            "kwargs": {"OwnerIds": ["self"]},
        },
        "sizing": {
            "id_field": "SnapshotId",
            "size_field": "VolumeSize",
            "size_unit": "GiB",
            "flags": ["allocated (upper bound)"],
            "notes": ["incremental – shares blocks with sibling snapshots"],
        },
    },
    RDS_CODE: {
        "category": "database",
        "label": "RDS (Relational Database Service)",
        "strategy": "rds_instances",
        "enumeration": {
            "service": "rds",
            "operation": "describe_db_instances",
            "result_path": ["DBInstances"],
        },
    },
    DYNAMODB_CODE: {
        "category": "database",
        "label": "DynamoDB",
        "strategy": "dynamodb_tables",
        "enumeration": {
            "service": "dynamodb",
            "operation": "list_tables",
            "result_path": ["TableNames"],
        },
    },
    BACKUP_CODE: {
        "category": "backup",
        "label": "Backup",
        "strategy": "backup_vaults",
        "enumeration": {
            "service": "backup",
            "operation": "list_backup_vaults",
            "result_path": ["BackupVaultList"],
        },
        "sizing": {
            "id_field": "BackupVaultName",
            "arn_field": "BackupVaultArn",
            "flags": ["backup vault – not sized"],
            "count_field": "NumberOfRecoveryPoints",
            "count_note": "{count} recovery points (cannot be exported directly)",
        },
    },
}

_REGISTRY_PATCHER = None


def setUpModule():
    global _REGISTRY_PATCHER
    _REGISTRY_PATCHER = patch(
        "core.utils_egress_aws.load_egress_registry",
        return_value=TEST_REGISTRY,
    )
    _REGISTRY_PATCHER.start()


def tearDownModule():
    _REGISTRY_PATCHER.stop()


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

        names = _list_buckets_in_region(
            s3_client, REGION, TEST_REGISTRY[S3_CODE]["enumeration"]
        )

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

        names = _list_buckets_in_region(
            s3_client, REGION, TEST_REGISTRY[S3_CODE]["enumeration"]
        )

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

        self.assertEqual(
            _list_buckets_in_region(
                s3_client, REGION, TEST_REGISTRY[S3_CODE]["enumeration"]
            ),
            [],
        )


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
        entry = TEST_REGISTRY[S3_CODE]

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
        entry = TEST_REGISTRY[S3_CODE]

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
        entry = TEST_REGISTRY[S3_CODE]

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
        entry = TEST_REGISTRY[VOLUME_CODE]

        rows = _collect_list_item_size(
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
        entry = TEST_REGISTRY[SNAPSHOT_CODE]

        rows = _collect_list_item_size(
            _mock_session({"ec2": ec2_client}), REGION, SNAPSHOT_CODE, entry
        )

        paginator.paginate.assert_called_once_with(OwnerIds=["self"])
        self.assertEqual(rows[0]["size_bytes"], 50 * GIB)
        self.assertTrue(any("shares blocks" in note for note in rows[0]["notes"]))

    def test_owner_filter_comes_from_entry_kwargs_not_the_source(self):
        # The master data and the old hardcoded filter agree today, so only a
        # changed entry proves the call is actually data-driven.
        ec2_client = MagicMock()
        paginator = _mock_paginator(ec2_client, [{"Snapshots": []}])
        entry = {
            **TEST_REGISTRY[SNAPSHOT_CODE],
            "enumeration": {
                **TEST_REGISTRY[SNAPSHOT_CODE]["enumeration"],
                "kwargs": {"OwnerIds": ["123456"]},
            },
        }

        _collect_list_item_size(
            _mock_session({"ec2": ec2_client}), REGION, SNAPSHOT_CODE, entry
        )

        paginator.paginate.assert_called_once_with(OwnerIds=["123456"])

    def test_entry_without_kwargs_passes_no_extra_arguments(self):
        ec2_client = MagicMock()
        paginator = _mock_paginator(ec2_client, [{"Snapshots": []}])
        entry = {
            **TEST_REGISTRY[SNAPSHOT_CODE],
            "enumeration": {
                key: value
                for key, value in TEST_REGISTRY[SNAPSHOT_CODE]["enumeration"].items()
                if key != "kwargs"
            },
        }

        _collect_list_item_size(
            _mock_session({"ec2": ec2_client}), REGION, SNAPSHOT_CODE, entry
        )

        paginator.paginate.assert_called_once_with()


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
        entry = TEST_REGISTRY[RDS_CODE]

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
        entry = TEST_REGISTRY[RDS_CODE]

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
        entry = TEST_REGISTRY[RDS_CODE]

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
        entry = TEST_REGISTRY[DYNAMODB_CODE]

        rows = _collect_dynamodb_tables(
            _mock_session({"dynamodb": dynamodb_client}), REGION, DYNAMODB_CODE, entry
        )

        self.assertEqual(rows[0]["size_bytes"], 1500)

    def test_empty_table_is_zero_not_unknown(self):
        dynamodb_client = MagicMock()
        _mock_paginator(dynamodb_client, [{"TableNames": ["empty"]}])
        dynamodb_client.describe_table.return_value = {"Table": {"TableSizeBytes": 0}}
        entry = TEST_REGISTRY[DYNAMODB_CODE]

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
        entry = TEST_REGISTRY[BACKUP_CODE]

        rows = _collect_not_sizeable(
            _mock_session({"backup": backup_client}), REGION, BACKUP_CODE, entry
        )

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertFalse(rows[0]["size_unknown"])
        self.assertIn("backup vault – not sized", rows[0]["flags"])
        self.assertTrue(any("12 recovery points" in note for note in rows[0]["notes"]))

    def test_call_comes_from_entry_enumeration_not_the_catalogue_code(self):
        # The catalogue row enumerates backup plans for the inventory scan;
        # sizing needs the vaults, so the entry overrides the call.
        backup_client = MagicMock()
        _mock_paginator(backup_client, [{"BackupVaultList": []}])
        session = _mock_session({"backup": backup_client})

        _collect_not_sizeable(session, REGION, BACKUP_CODE, TEST_REGISTRY[BACKUP_CODE])

        self.assertEqual(session.client.call_args.args[0], "backup")
        backup_client.get_paginator.assert_called_once_with("list_backup_vaults")

    def test_enumeration_drives_a_different_service_and_operation(self):
        other_client = MagicMock()
        _mock_paginator(other_client, [{"Vaults": [{"BackupVaultName": "v1"}]}])
        session = _mock_session({"otherservice": other_client})
        entry = {
            **TEST_REGISTRY[BACKUP_CODE],
            "enumeration": {
                "service": "otherservice",
                "operation": "list_vaults",
                "result_path": ["Vaults"],
            },
        }

        rows = _collect_not_sizeable(session, REGION, BACKUP_CODE, entry)

        self.assertEqual(session.client.call_args.args[0], "otherservice")
        other_client.get_paginator.assert_called_once_with("list_vaults")
        self.assertEqual([row["name"] for row in rows], ["v1"])

    def test_enumeration_kwargs_are_forwarded(self):
        backup_client = MagicMock()
        paginator = _mock_paginator(backup_client, [{"BackupVaultList": []}])
        entry = {
            **TEST_REGISTRY[BACKUP_CODE],
            "enumeration": {
                **TEST_REGISTRY[BACKUP_CODE]["enumeration"],
                "kwargs": {"ByVaultType": "BACKUP_VAULT"},
            },
        }

        _collect_not_sizeable(
            _mock_session({"backup": backup_client}), REGION, BACKUP_CODE, entry
        )

        paginator.paginate.assert_called_once_with(ByVaultType="BACKUP_VAULT")

    def test_nested_result_path_is_walked(self):
        backup_client = MagicMock()
        _mock_paginator(
            backup_client, [{"Outer": {"Inner": [{"BackupVaultName": "nested"}]}}]
        )
        entry = {
            **TEST_REGISTRY[BACKUP_CODE],
            "enumeration": {
                "service": "backup",
                "operation": "list_backup_vaults",
                "result_path": ["Outer", "Inner"],
            },
        }

        rows = _collect_not_sizeable(
            _mock_session({"backup": backup_client}), REGION, BACKUP_CODE, entry
        )

        self.assertEqual([row["name"] for row in rows], ["nested"])


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


class UnknownStrategyTests(unittest.TestCase):
    @patch("core.utils_egress_aws.boto3")
    def test_unknown_strategy_warns_and_skips_without_raising(self, mock_boto3):
        registry = {
            "AWS.future.list_things.Things": {
                "category": "object",
                "label": "Something New",
                "strategy": "not_implemented_yet",
            },
            VOLUME_CODE: TEST_REGISTRY[VOLUME_CODE],
        }
        ec2_client = MagicMock()
        _mock_paginator(ec2_client, [{"Volumes": [{"VolumeId": "vol-1", "Size": 10}]}])
        mock_boto3.Session.return_value = _mock_session({"ec2": ec2_client})

        with patch("core.utils_egress_aws.load_egress_registry", return_value=registry):
            with self.assertLogs("core.engine.egress.aws", level="WARNING") as captured:
                rows, _ = collect_aws_egress(
                    {
                        "accessKey": "AKIAIOSFODNN7EXAMPLE",
                        "secretKey": "secret",
                        "region": REGION,
                    }
                )

        # The known strategy still runs; only the unknown one is skipped.
        self.assertEqual([row["id"] for row in rows], ["vol-1"])
        self.assertIn("not_implemented_yet", captured.output[0])


class CollectorFailureVisibilityTests(unittest.TestCase):
    @patch("core.utils_egress_aws.boto3")
    def test_a_dropped_resource_type_is_warned_not_buried_at_debug(self, mock_boto3):
        # A master-data row missing its sizing block removes a whole resource
        # type from the estimate; that must be visible in run.log.
        registry = {VOLUME_CODE: {**TEST_REGISTRY[VOLUME_CODE]}}
        del registry[VOLUME_CODE]["sizing"]
        ec2_client = MagicMock()
        _mock_paginator(ec2_client, [{"Volumes": [{"VolumeId": "vol-1", "Size": 10}]}])
        mock_boto3.Session.return_value = _mock_session({"ec2": ec2_client})

        with patch("core.utils_egress_aws.load_egress_registry", return_value=registry):
            with self.assertLogs("core.engine.egress.aws", level="WARNING") as captured:
                rows, _ = collect_aws_egress(
                    {
                        "accessKey": "AKIAIOSFODNN7EXAMPLE",
                        "secretKey": "secret",
                        "region": REGION,
                    }
                )

        self.assertEqual(rows, [])
        self.assertIn(VOLUME_CODE, captured.output[0])
        self.assertIn("KeyError", captured.output[0])
        self.assertIn("missing from the estimate", captured.output[0])


class ParameterisedSizingTests(unittest.TestCase):
    def _entry(self, sizing, result_key="FileSystems"):
        return {
            "category": "block",
            "label": "Elastic File System",
            "strategy": "ebs_volumes",
            "enumeration": {
                "service": "elasticfilesystem",
                "operation": "describe_file_systems",
                "result_path": [result_key],
            },
            "sizing": sizing,
        }

    def _collect(self, entry, items, result_key="FileSystems"):
        client = MagicMock()
        _mock_paginator(client, [{result_key: items}])
        return _collect_list_item_size(
            _mock_session({"elasticfilesystem": client}), REGION, "AWS.efs", entry
        )

    def test_nested_size_field_and_byte_unit(self):
        entry = self._entry(
            {
                "id_field": "FileSystemId",
                "name_field": "Name",
                "size_field": ["SizeInBytes", "Value"],
                "size_unit": "B",
                "flags": ["metered size"],
            }
        )

        rows = self._collect(
            entry,
            [
                {
                    "FileSystemId": "fs-1",
                    "Name": "shared",
                    "SizeInBytes": {"Value": 4096},
                }
            ],
        )

        self.assertEqual(rows[0]["size_bytes"], 4096)
        self.assertEqual(rows[0]["name"], "shared")
        self.assertEqual(rows[0]["flags"], ["metered size"])

    def test_missing_nested_size_is_unknown_not_zero(self):
        entry = self._entry(
            {
                "id_field": "FileSystemId",
                "size_field": ["SizeInBytes", "Value"],
                "size_unit": "B",
            }
        )

        rows = self._collect(entry, [{"FileSystemId": "fs-1"}])

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertTrue(rows[0]["size_unknown"])

    def test_flags_and_notes_are_withheld_when_size_is_unknown(self):
        entry = self._entry(
            {
                "id_field": "FileSystemId",
                "size_field": "Size",
                "flags": ["allocated"],
                "notes": ["incremental"],
            }
        )

        rows = self._collect(entry, [{"FileSystemId": "fs-1"}])

        self.assertEqual(rows[0]["flags"], [])
        self.assertEqual(rows[0]["notes"], [])

    def test_name_tag_falls_back_to_the_identifier(self):
        entry = self._entry(
            {"id_field": "FileSystemId", "name_tag": "Name", "size_field": "Size"}
        )

        rows = self._collect(
            entry, [{"FileSystemId": "fs-1", "Tags": [{"Key": "env", "Value": "prod"}]}]
        )

        self.assertEqual(rows[0]["name"], "fs-1")

    def test_arn_field_falls_back_to_the_identifier_when_absent(self):
        client = MagicMock()
        _mock_paginator(client, [{"Vaults": [{"VaultName": "v1"}]}])
        entry = {
            "category": "backup",
            "label": "Some Vault",
            "strategy": "backup_vaults",
            "enumeration": {
                "service": "glacier",
                "operation": "list_vaults",
                "result_path": ["Vaults"],
            },
            "sizing": {
                "id_field": "VaultName",
                "arn_field": "VaultARN",
                "flags": ["not sized"],
                "count_field": "NumberOfArchives",
                "count_note": "{count} archives held",
            },
        }

        rows = _collect_not_sizeable(
            _mock_session({"glacier": client}), REGION, "AWS.glacier", entry
        )

        self.assertEqual(rows[0]["id"], "v1")
        self.assertEqual(rows[0]["flags"], ["not sized"])
        self.assertEqual(rows[0]["notes"], [])

    def test_count_note_template_is_filled_from_the_item(self):
        client = MagicMock()
        _mock_paginator(
            client, [{"Vaults": [{"VaultName": "v1", "NumberOfArchives": 7}]}]
        )
        entry = {
            "category": "backup",
            "label": "Some Vault",
            "strategy": "backup_vaults",
            "enumeration": {
                "service": "glacier",
                "operation": "list_vaults",
                "result_path": ["Vaults"],
            },
            "sizing": {
                "id_field": "VaultName",
                "flags": [],
                "count_field": "NumberOfArchives",
                "count_note": "{count} archives held",
            },
        }

        rows = _collect_not_sizeable(
            _mock_session({"glacier": client}), REGION, "AWS.glacier", entry
        )

        self.assertEqual(rows[0]["notes"], ["7 archives held"])


if __name__ == "__main__":
    unittest.main()
