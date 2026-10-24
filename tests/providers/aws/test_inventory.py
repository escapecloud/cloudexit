# tests/providers/aws/test_inventory.py
import logging
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import botocore.exceptions

from cloudexit.providers.aws.client import MAX_ITEMS_PER_RESOURCE_TYPE


class BuildAwsResourceInventoryErrorTests(unittest.TestCase):
    @patch("cloudexit.providers.aws.inventory.load_data")
    @patch("cloudexit.providers.aws.inventory.boto3.Session")
    def test_outer_exception_is_logged_silently(self, mock_session_cls, mock_load_data):
        """build_aws_resource_inventory catches all outer exceptions silently."""
        mock_load_data.side_effect = RuntimeError("DB unavailable")

        from cloudexit.providers.aws.inventory import build_aws_resource_inventory

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            # Should not raise -- outer except swallows everything
            build_aws_resource_inventory(
                2,
                {"accessKey": "AK", "secretKey": "SK", "region": "us-east-1"},
                report_path,
                raw_data_path,
            )


class BuildAwsResourceInventoryPerServiceTests(unittest.TestCase):
    @patch("cloudexit.providers.aws.inventory.connect")
    @patch("cloudexit.providers.aws.inventory.paginate_or_call")
    @patch("cloudexit.providers.aws.inventory.boto3.Session")
    @patch("cloudexit.providers.aws.inventory.load_data")
    def test_failed_service_is_skipped_and_logged_at_debug(
        self, mock_load_data, mock_session_cls, mock_poc, mock_connect
    ):
        mock_load_data.return_value = [
            {
                "code": "AWS.ec2.describe_instances.Reservations",
                "id": 1,
                "name": "EC2",
                "csp": 2,
                "status": "t",
            },
            {
                "code": "AWS.s3.list_buckets.Buckets",
                "id": 2,
                "name": "S3",
                "csp": 2,
                "status": "t",
            },
        ]
        # First service raises (e.g. AccessDenied); second returns resources.
        mock_poc.side_effect = [
            botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "no"}},
                "DescribeInstances",
            ),
            [{"InstanceId": "i-1"}],
        ]
        mock_connect.return_value.__enter__.return_value = MagicMock()

        from cloudexit.providers.aws.inventory import build_aws_resource_inventory

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            with self.assertLogs("core.engine.aws", level="DEBUG") as cm:
                build_aws_resource_inventory(
                    2,
                    {"accessKey": "AK", "secretKey": "SK", "region": "us-east-1"},
                    report_path,
                    raw_data_path,
                )

        # Loop continued past the failing service to the next one.
        self.assertEqual(mock_poc.call_count, 2)
        # The failure was recorded at DEBUG, naming the failed service...
        self.assertTrue(
            any(
                r.levelno == logging.DEBUG and "ec2" in r.getMessage()
                for r in cm.records
            )
        )
        # ...and never escalated to WARNING/ERROR (stays off the console).
        self.assertFalse(any(r.levelno >= logging.WARNING for r in cm.records))


class BuildAwsResourceInventorySpecTests(unittest.TestCase):
    """The scanner drives the resolved spec, not the raw code string."""

    def _row(self, id_, code, name, params="{}"):
        return {
            "code": code,
            "id": id_,
            "name": name,
            "csp": 2,
            "status": "t",
            "params": params,
        }

    def _run(self, rows, poc_return=None):
        """Run the scanner with load_data/boto3/paginate_or_call mocked out.

        Returns (paginate_or_call mock, captured log records). A plain handler
        is used instead of assertLogs because a clean scan logs nothing at all.
        """
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        aws_logger = logging.getLogger("core.engine.aws")
        handler = _Capture()
        previous_level = aws_logger.level
        aws_logger.addHandler(handler)
        aws_logger.setLevel(logging.DEBUG)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                report_path = os.path.join(tmp, "report")
                raw_data_path = os.path.join(tmp, "raw")
                os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
                os.makedirs(raw_data_path, exist_ok=True)

                with (
                    patch(
                        "cloudexit.providers.aws.inventory.load_data", return_value=rows
                    ),
                    patch("cloudexit.providers.aws.inventory.boto3.Session"),
                    patch("cloudexit.providers.aws.inventory.connect") as mock_connect,
                    patch(
                        "cloudexit.providers.aws.inventory.paginate_or_call"
                    ) as mock_poc,
                ):
                    mock_connect.return_value.__enter__.return_value = MagicMock()
                    mock_poc.return_value = [] if poc_return is None else poc_return

                    from cloudexit.providers.aws.inventory import (
                        build_aws_resource_inventory,
                    )

                    build_aws_resource_inventory(
                        2,
                        {"accessKey": "AK", "secretKey": "SK", "region": "us-east-1"},
                        report_path,
                        raw_data_path,
                    )
                    return mock_poc, records
        finally:
            aws_logger.removeHandler(handler)
            aws_logger.setLevel(previous_level)

    def test_params_drive_the_call_for_nested_result_path(self):
        rows = [
            self._row(
                1,
                "AWS.cloudfront.list_distributions.DistributionList.Items",
                "CloudFront",
                '{"service": "cloudfront", "operation": "list_distributions", '
                '"result_path": ["DistributionList", "Items"]}',
            )
        ]
        mock_poc, records = self._run(rows, poc_return=[{"Id": "E1"}])

        args, kwargs = mock_poc.call_args
        self.assertEqual(args[1], "list_distributions")
        self.assertEqual(args[2], ["DistributionList", "Items"])
        self.assertEqual(kwargs, {})
        self.assertFalse(any(r.levelno >= logging.WARNING for r in records))

    def test_params_kwargs_are_forwarded_to_the_call(self):
        rows = [
            self._row(
                1,
                "AWS.ec2.describe_snapshots.Snapshots",
                "EBS Snapshot",
                '{"service": "ec2", "operation": "describe_snapshots", '
                '"result_path": ["Snapshots"], "kwargs": {"OwnerIds": ["self"]}}',
            )
        ]
        mock_poc, _ = self._run(rows)

        _, kwargs = mock_poc.call_args
        self.assertEqual(kwargs, {"OwnerIds": ["self"]})

    def test_code_is_used_when_params_are_empty(self):
        rows = [self._row(1, "AWS.s3.list_buckets.Buckets", "S3")]
        mock_poc, _ = self._run(rows)

        args, _ = mock_poc.call_args
        self.assertEqual(args[1], "list_buckets")
        self.assertEqual(args[2], ["Buckets"])

    def test_two_part_placeholder_is_skipped_at_debug(self):
        rows = [self._row(1, "AWS.iam", "IAM")]
        mock_poc, records = self._run(rows)

        mock_poc.assert_not_called()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in records))
        self.assertTrue(
            any(
                "AWS.iam" in r.getMessage()
                for r in records
                if r.levelno == logging.DEBUG
            )
        )

    def test_malformed_code_is_skipped_with_warning(self):
        rows = [self._row(1, "AWS.ec2.describe_instances", "Broken")]
        mock_poc, records = self._run(rows)

        mock_poc.assert_not_called()
        self.assertTrue(
            any(
                r.levelno == logging.WARNING
                and "AWS.ec2.describe_instances" in r.getMessage()
                for r in records
            )
        )

    def test_item_cap_hit_logs_warning(self):
        rows = [self._row(1, "AWS.ec2.describe_images.Images", "AMI")]
        with patch("cloudexit.providers.aws.inventory.MAX_ITEMS_PER_RESOURCE_TYPE", 3):
            _, records = self._run(rows, poc_return=[{"ImageId": "ami"}] * 3)

        self.assertTrue(
            any(
                r.levelno == logging.WARNING
                and "describe_images" in r.getMessage()
                and "ec2" in r.getMessage()
                for r in records
            )
        )

    def test_per_row_debug_line_names_the_spec_source(self):
        rows = [
            self._row(
                1,
                "AWS.cloudfront.list_distributions.DistributionList.Items",
                "CloudFront",
                '{"service": "cloudfront", "operation": "list_distributions", '
                '"result_path": ["DistributionList", "Items"]}',
            ),
            self._row(2, "AWS.s3.list_buckets.Buckets", "S3"),
        ]
        _, records = self._run(rows)
        messages = [r.getMessage() for r in records if r.levelno == logging.DEBUG]

        self.assertTrue(
            any("AWS.cloudfront" in m and "from params" in m for m in messages),
            messages,
        )
        self.assertTrue(
            any("AWS.s3.list_buckets" in m and "from code" in m for m in messages),
            messages,
        )

    def test_summary_line_counts_every_resolution_outcome(self):
        rows = [
            self._row(
                1,
                "AWS.cloudfront.list_distributions.DistributionList.Items",
                "CloudFront",
                '{"service": "cloudfront", "operation": "list_distributions", '
                '"result_path": ["DistributionList", "Items"]}',
            ),
            self._row(2, "AWS.s3.list_buckets.Buckets", "S3"),
            self._row(3, "AWS.iam", "IAM"),
            self._row(4, "AWS.ec2.describe_instances", "Broken"),
        ]
        _, records = self._run(rows)
        summary = [
            r.getMessage()
            for r in records
            if r.levelno == logging.INFO
            and "Resource type resolution" in r.getMessage()
        ]

        self.assertEqual(len(summary), 1, records)
        self.assertEqual(
            summary[0],
            "Resource type resolution: 2 of 4 rows scanned (1 from params, "
            "1 from code), 1 placeholders skipped, 1 invalid.",
        )

    def test_summary_is_info_so_it_stays_off_the_default_console(self):
        rows = [self._row(1, "AWS.s3.list_buckets.Buckets", "S3")]
        _, records = self._run(rows)

        summary = [r for r in records if "Resource type resolution" in r.getMessage()]
        self.assertEqual([r.levelno for r in summary], [logging.INFO])

    def test_item_cap_is_passed_to_paginate_or_call(self):

        rows = [self._row(1, "AWS.s3.list_buckets.Buckets", "S3")]
        mock_poc, _ = self._run(rows)

        args, _ = mock_poc.call_args
        self.assertEqual(args[3], MAX_ITEMS_PER_RESOURCE_TYPE)


if __name__ == "__main__":
    unittest.main()
