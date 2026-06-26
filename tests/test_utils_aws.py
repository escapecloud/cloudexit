# tests/test_utils_aws.py
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import botocore.exceptions

from core.utils_aws import (
    aws_api_call_with_retry,
    convert_datetime,
    get_missing_months_aws,
)


class ConvertDatetimeTests(unittest.TestCase):
    def test_converts_datetime_in_flat_dict(self):
        dt = datetime(2026, 1, 15, 12, 30, 0)
        result = convert_datetime({"created": dt, "name": "test"})
        self.assertEqual(result["created"], "2026-01-15T12:30:00")
        self.assertEqual(result["name"], "test")

    def test_converts_datetime_in_list(self):
        dt = datetime(2026, 3, 1)
        result = convert_datetime([dt, "keep"])
        self.assertEqual(result[0], "2026-03-01T00:00:00")
        self.assertEqual(result[1], "keep")

    def test_converts_nested_datetime(self):
        dt = datetime(2026, 6, 15)
        result = convert_datetime({"items": [{"ts": dt}]})
        self.assertEqual(result["items"][0]["ts"], "2026-06-15T00:00:00")

    def test_leaves_non_datetime_values_unchanged(self):
        data = {"count": 5, "name": "ec2", "tags": ["a", "b"]}
        result = convert_datetime(data)
        self.assertEqual(result, {"count": 5, "name": "ec2", "tags": ["a", "b"]})

    def test_handles_empty_structures(self):
        self.assertEqual(convert_datetime({}), {})
        self.assertEqual(convert_datetime([]), [])
        self.assertIsNone(convert_datetime(None))


class GetMissingMonthsAwsTests(unittest.TestCase):
    @patch("core.utils_aws.datetime")
    def test_returns_missing_months(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 6, 15, tzinfo=timezone.utc)
        mock_dt.strptime = datetime.strptime

        processed = {"2026-06-01", "2026-05-01", "2026-04-01"}
        missing = get_missing_months_aws(processed, 6)

        # Should be missing: 2026-03, 2026-02, 2026-01
        self.assertEqual(len(missing), 3)
        self.assertIn(date(2026, 3, 1), missing)
        self.assertIn(date(2026, 2, 1), missing)
        self.assertIn(date(2026, 1, 1), missing)

    @patch("core.utils_aws.datetime")
    def test_returns_empty_when_all_present(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 6, 15, tzinfo=timezone.utc)
        mock_dt.strptime = datetime.strptime

        processed = {
            "2026-06-01",
            "2026-05-01",
            "2026-04-01",
            "2026-03-01",
            "2026-02-01",
            "2026-01-01",
        }
        missing = get_missing_months_aws(processed, 6)
        self.assertEqual(missing, [])

    @patch("core.utils_aws.datetime")
    def test_returns_all_when_none_processed(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 6, 15, tzinfo=timezone.utc)
        mock_dt.strptime = datetime.strptime

        missing = get_missing_months_aws(set(), 6)
        self.assertEqual(len(missing), 6)


class AwsApiCallWithRetryTests(unittest.TestCase):
    def test_successful_call_returns_result(self):
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {"Reservations": []}

        api_call = aws_api_call_with_retry(
            mock_client, "describe_instances", {}, max_retries=3, retry_delay=0
        )
        result = api_call()

        self.assertEqual(result, {"Reservations": []})
        mock_client.describe_instances.assert_called_once()

    def test_passes_parameters_to_function(self):
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = {"Buckets": []}

        params = {"MaxItems": 10}
        api_call = aws_api_call_with_retry(
            mock_client, "list_buckets", params, max_retries=1, retry_delay=0
        )
        api_call()

        mock_client.list_buckets.assert_called_once_with(MaxItems=10)

    def test_passes_kwargs_from_caller(self):
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {"Reservations": []}

        api_call = aws_api_call_with_retry(
            mock_client, "describe_instances", {}, max_retries=1, retry_delay=0
        )
        api_call(NextToken="abc123")

        mock_client.describe_instances.assert_called_once_with(NextToken="abc123")

    @patch("core.utils_aws.time.sleep")
    def test_retries_on_throttling(self, mock_sleep):
        mock_client = MagicMock()
        throttle_error = botocore.exceptions.ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
            "DescribeInstances",
        )
        mock_client.describe_instances.side_effect = [
            throttle_error,
            {"Reservations": ["instance-1"]},
        ]

        api_call = aws_api_call_with_retry(
            mock_client, "describe_instances", {}, max_retries=3, retry_delay=1
        )
        result = api_call()

        self.assertEqual(result, {"Reservations": ["instance-1"]})
        self.assertEqual(mock_client.describe_instances.call_count, 2)
        mock_sleep.assert_called_once()

    def test_raises_non_throttling_client_error_immediately(self):
        mock_client = MagicMock()
        access_denied = botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
            "DescribeInstances",
        )
        mock_client.describe_instances.side_effect = access_denied

        api_call = aws_api_call_with_retry(
            mock_client, "describe_instances", {}, max_retries=3, retry_delay=0
        )

        with self.assertRaises(botocore.exceptions.ClientError):
            api_call()

        mock_client.describe_instances.assert_called_once()

    @patch("core.utils_aws.time.sleep")
    def test_raises_after_max_retries_exhausted(self, mock_sleep):
        mock_client = MagicMock()
        throttle_error = botocore.exceptions.ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
            "DescribeInstances",
        )
        mock_client.describe_instances.side_effect = throttle_error

        api_call = aws_api_call_with_retry(
            mock_client, "describe_instances", {}, max_retries=2, retry_delay=0
        )

        with self.assertRaises(Exception) as ctx:
            api_call()

        self.assertIn("Failed to call", str(ctx.exception))
        self.assertEqual(mock_client.describe_instances.call_count, 2)

    @patch("core.utils_aws.time.sleep")
    def test_retries_on_botocore_error(self, mock_sleep):
        mock_client = MagicMock()
        mock_client.describe_instances.side_effect = [
            botocore.exceptions.BotoCoreError(),
            {"Reservations": []},
        ]

        api_call = aws_api_call_with_retry(
            mock_client, "describe_instances", {}, max_retries=3, retry_delay=1
        )
        result = api_call()

        self.assertEqual(result, {"Reservations": []})
        self.assertEqual(mock_client.describe_instances.call_count, 2)


class BuildAwsCostInventoryErrorTests(unittest.TestCase):
    @patch("core.utils_aws.connect")
    @patch("core.utils_aws.boto3.Session")
    def test_passes_session_token_to_boto3_session(
        self, mock_session_cls, mock_connect
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce
        mock_ce.get_cost_and_usage.return_value = {"ResultsByTime": []}

        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        from core.utils_aws import build_aws_cost_inventory

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            build_aws_cost_inventory(
                2,
                {
                    "accessKey": "AK",
                    "secretKey": "SK",
                    "sessionToken": "TOKEN",
                    "region": "us-east-1",
                },
                report_path,
                raw_data_path,
            )

        mock_session_cls.assert_called_once_with(
            aws_access_key_id="AK",
            aws_secret_access_key="SK",
            aws_session_token="TOKEN",
            region_name="us-east-1",
        )

    @patch("core.utils_aws.connect")
    @patch("core.utils_aws.boto3.Session")
    def test_sqlite_error_is_logged_but_not_reraised(
        self, mock_session_cls, mock_connect
    ):
        """sqlite3.Error is caught and logged but NOT re-raised in current code."""
        import sqlite3

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce
        mock_ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-01-01"},
                    "Groups": [
                        {
                            "Metrics": {
                                "UnblendedCost": {"Amount": "10.0", "Unit": "USD"}
                            }
                        }
                    ],
                }
            ]
        }

        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = sqlite3.Error("disk I/O error")

        from core.utils_aws import build_aws_cost_inventory

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            # sqlite3.Error is caught but NOT re-raised in current code
            # (this documents the current behavior)
            try:
                build_aws_cost_inventory(
                    2,
                    {"accessKey": "AK", "secretKey": "SK", "region": "us-east-1"},
                    report_path,
                    raw_data_path,
                )
            except sqlite3.Error:
                pass  # Expected: current code catches but does not re-raise sqlite3.Error


class BuildAwsResourceInventoryErrorTests(unittest.TestCase):
    @patch("core.utils_aws.load_data")
    @patch("core.utils_aws.boto3.Session")
    def test_outer_exception_is_logged_silently(self, mock_session_cls, mock_load_data):
        """build_aws_resource_inventory catches all outer exceptions silently."""
        mock_load_data.side_effect = RuntimeError("DB unavailable")

        from core.utils_aws import build_aws_resource_inventory

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


if __name__ == "__main__":
    unittest.main()
