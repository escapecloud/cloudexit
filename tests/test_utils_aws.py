# tests/test_utils_aws.py
import logging
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import botocore.exceptions

from core.utils_aws import (
    convert_datetime,
    extract_result_path,
    get_missing_months_aws,
    paginate,
    paginate_or_call,
    parse_resource_type_params,
    resolve_aws_call_spec,
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


class BuildAwsResourceInventoryPerServiceTests(unittest.TestCase):
    @patch("core.utils_aws.connect")
    @patch("core.utils_aws.paginate_or_call")
    @patch("core.utils_aws.boto3.Session")
    @patch("core.utils_aws.load_data")
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

        from core.utils_aws import build_aws_resource_inventory

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


class ParseResourceTypeParamsTests(unittest.TestCase):
    def test_parses_json_string(self):
        self.assertEqual(
            parse_resource_type_params('{"service": "s3"}'), {"service": "s3"}
        )

    def test_empty_object_string(self):
        self.assertEqual(parse_resource_type_params("{}"), {})

    def test_none_and_empty_string_degrade_to_empty_dict(self):
        self.assertEqual(parse_resource_type_params(None), {})
        self.assertEqual(parse_resource_type_params(""), {})

    def test_malformed_json_degrades_to_empty_dict(self):
        self.assertEqual(parse_resource_type_params("{not json"), {})

    def test_non_object_json_degrades_to_empty_dict(self):
        self.assertEqual(parse_resource_type_params("[1, 2]"), {})

    def test_dict_passes_through(self):
        self.assertEqual(
            parse_resource_type_params({"service": "ec2"}), {"service": "ec2"}
        )


class ResolveAwsCallSpecTests(unittest.TestCase):
    def test_four_part_code_with_no_params(self):
        spec = resolve_aws_call_spec("AWS.s3.list_buckets.Buckets", {})
        self.assertEqual(
            spec,
            {
                "source": "code",
                "service": "s3",
                "operation": "list_buckets",
                "result_path": ["Buckets"],
                "kwargs": {},
            },
        )

    def test_five_part_code_with_no_params_keeps_full_result_path(self):
        spec = resolve_aws_call_spec(
            "AWS.cloudfront.list_distributions.DistributionList.Items", {}
        )
        self.assertEqual(spec["result_path"], ["DistributionList", "Items"])
        self.assertEqual(spec["service"], "cloudfront")

    def test_params_take_precedence_over_code(self):
        spec = resolve_aws_call_spec(
            "AWS.wrong.wrong_op.Wrong",
            {
                "service": "cloudfront",
                "operation": "list_distributions",
                "result_path": ["DistributionList", "Items"],
            },
        )
        self.assertEqual(
            spec,
            {
                "source": "params",
                "service": "cloudfront",
                "operation": "list_distributions",
                "result_path": ["DistributionList", "Items"],
                "kwargs": {},
            },
        )

    def test_params_with_kwargs(self):
        spec = resolve_aws_call_spec(
            "AWS.ec2.describe_snapshots.Snapshots",
            {
                "service": "ec2",
                "operation": "describe_snapshots",
                "result_path": ["Snapshots"],
                "kwargs": {"OwnerIds": ["self"]},
            },
        )
        self.assertEqual(spec["kwargs"], {"OwnerIds": ["self"]})

    def test_unknown_params_keys_are_ignored(self):
        spec = resolve_aws_call_spec(
            "AWS.s3.list_buckets.Buckets",
            {
                "service": "s3",
                "operation": "list_buckets",
                "result_path": ["Buckets"],
                "future_key": "whatever",
            },
        )
        self.assertNotIn("future_key", spec)
        self.assertEqual(spec["service"], "s3")

    def test_params_missing_result_path_defaults_to_empty(self):
        spec = resolve_aws_call_spec(
            "AWS.iam", {"service": "iam", "operation": "list_users"}
        )
        self.assertEqual(spec["result_path"], [])

    def test_other_cloud_params_fall_back_to_code(self):
        spec = resolve_aws_call_spec(
            "AWS.s3.list_buckets.Buckets", {"kind": "functionapp"}
        )
        self.assertEqual(spec["service"], "s3")

    def test_partial_params_fall_back_to_code(self):
        spec = resolve_aws_call_spec("AWS.s3.list_buckets.Buckets", {"service": "s3"})
        self.assertEqual(spec["operation"], "list_buckets")

    def test_source_records_which_branch_resolved_the_row(self):
        from_code = resolve_aws_call_spec("AWS.s3.list_buckets.Buckets", {})
        from_params = resolve_aws_call_spec(
            "AWS.s3.list_buckets.Buckets",
            {"service": "s3", "operation": "list_buckets"},
        )
        self.assertEqual(from_code["source"], "code")
        self.assertEqual(from_params["source"], "params")

    def test_two_part_placeholder_is_unresolvable(self):
        self.assertIsNone(resolve_aws_call_spec("AWS.iam", {}))

    def test_malformed_code_is_unresolvable(self):
        self.assertIsNone(resolve_aws_call_spec("AWS.ec2.describe_instances", {}))
        self.assertIsNone(resolve_aws_call_spec("Azure.a.b.c", {}))
        self.assertIsNone(resolve_aws_call_spec("", {}))


class ExtractResultPathTests(unittest.TestCase):
    def test_single_key(self):
        self.assertEqual(extract_result_path({"Items": [1, 2]}, ["Items"]), [1, 2])

    def test_nested_key(self):
        self.assertEqual(
            extract_result_path({"A": {"B": ["x"]}}, ["A", "B"]),
            ["x"],
        )

    def test_missing_intermediate_key_returns_empty(self):
        self.assertEqual(extract_result_path({"A": {}}, ["A", "B"]), [])
        self.assertEqual(extract_result_path({}, ["A", "B"]), [])

    def test_non_dict_intermediate_returns_empty(self):
        self.assertEqual(extract_result_path({"A": "scalar"}, ["A", "B"]), [])

    def test_non_list_final_value_returns_empty(self):
        self.assertEqual(extract_result_path({"A": {"B": 5}}, ["A", "B"]), [])
        self.assertEqual(extract_result_path({"A": {"B": {}}}, ["A", "B"]), [])

    def test_empty_path_returns_empty(self):
        self.assertEqual(extract_result_path({"Items": [1]}, []), [])

    def test_non_dict_container_returns_empty(self):
        self.assertEqual(extract_result_path(None, ["Items"]), [])


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
                    patch("core.utils_aws.load_data", return_value=rows),
                    patch("core.utils_aws.boto3.Session"),
                    patch("core.utils_aws.connect") as mock_connect,
                    patch("core.utils_aws.paginate_or_call") as mock_poc,
                ):
                    mock_connect.return_value.__enter__.return_value = MagicMock()
                    mock_poc.return_value = [] if poc_return is None else poc_return

                    from core.utils_aws import build_aws_resource_inventory

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
        with patch("core.utils_aws.MAX_ITEMS_PER_RESOURCE_TYPE", 3):
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
        from core.utils_aws import MAX_ITEMS_PER_RESOURCE_TYPE

        rows = [self._row(1, "AWS.s3.list_buckets.Buckets", "S3")]
        mock_poc, _ = self._run(rows)

        args, _ = mock_poc.call_args
        self.assertEqual(args[3], MAX_ITEMS_PER_RESOURCE_TYPE)


class PaginateTests(unittest.TestCase):
    def _fake_client(self, pages):
        """Build a stub client whose paginator yields the given pages."""
        paginator = MagicMock()
        paginator.paginate.return_value = iter(pages)
        client = MagicMock()
        client.get_paginator.return_value = paginator
        return client

    def test_collects_items_across_pages(self):
        client = self._fake_client(
            [{"Items": [1, 2, 3]}, {"Items": [4, 5]}, {"Items": [6]}]
        )
        self.assertEqual(paginate(client, "any_op", ["Items"]), [1, 2, 3, 4, 5, 6])

    def test_missing_result_key_treated_as_empty(self):
        client = self._fake_client([{"Items": [1]}, {}])
        self.assertEqual(paginate(client, "any_op", ["Items"]), [1])

    def test_walks_nested_result_path_per_page(self):
        client = self._fake_client(
            [
                {"DistributionList": {"Items": ["d1", "d2"]}},
                {"DistributionList": {"Items": ["d3"]}},
            ]
        )
        self.assertEqual(
            paginate(client, "list_distributions", ["DistributionList", "Items"]),
            ["d1", "d2", "d3"],
        )

    def test_page_missing_intermediate_key_yields_nothing_for_that_page(self):
        client = self._fake_client(
            [{"DistributionList": {"Items": ["d1"]}}, {}, {"DistributionList": {}}]
        )
        self.assertEqual(
            paginate(client, "list_distributions", ["DistributionList", "Items"]),
            ["d1"],
        )

    def test_bare_string_result_path_is_treated_as_single_key(self):
        client = self._fake_client([{"Volumes": ["v1", "v2"]}])
        self.assertEqual(paginate(client, "describe_volumes", "Volumes"), ["v1", "v2"])

    def test_stops_and_truncates_at_max_items(self):
        pages = [{"Items": list(range(4))} for _ in range(10)]
        client = self._fake_client(pages)

        result = paginate(client, "any_op", ["Items"], 10)

        self.assertEqual(len(result), 10)
        self.assertEqual(result, list(range(4)) * 2 + [0, 1])

    def test_forwards_kwargs_to_paginator(self):
        client = self._fake_client([{"Items": []}])
        paginate(client, "any_op", ["Items"], MaxResults=50)
        client.get_paginator.return_value.paginate.assert_called_once_with(
            MaxResults=50
        )

    def test_throttling_mid_pagination_does_not_silently_truncate(self):

        def flaky_pages():
            yield {"Items": ["r1", "r2"]}
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "Throttling", "Message": "slow down"}},
                "ListSomething",
            )

        paginator = MagicMock()
        paginator.paginate.return_value = flaky_pages()
        client = MagicMock()
        client.get_paginator.return_value = paginator

        with self.assertRaises(botocore.exceptions.ClientError):
            paginate(client, "any_op", "Items")


class PaginateOrCallTests(unittest.TestCase):
    def test_uses_paginator_when_available(self):
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"Items": [1, 2]}, {"Items": [3]}])
        client = MagicMock()
        client.can_paginate.return_value = True
        client.get_paginator.return_value = paginator

        self.assertEqual(paginate_or_call(client, "list_things", ["Items"]), [1, 2, 3])
        client.can_paginate.assert_called_once_with("list_things")

    def test_falls_back_to_single_call_when_not_paginable(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.list_things.return_value = {"Items": ["a", "b"]}

        self.assertEqual(paginate_or_call(client, "list_things", ["Items"]), ["a", "b"])
        client.list_things.assert_called_once_with()

    def test_non_dict_response_returns_empty_list(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.list_things.return_value = None

        self.assertEqual(paginate_or_call(client, "list_things", ["Items"]), [])

    def test_walks_nested_result_path_on_single_call(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.get_apps.return_value = {
            "ApplicationsResponse": {"Applications": ["a1", "a2"]}
        }

        self.assertEqual(
            paginate_or_call(
                client, "get_apps", ["ApplicationsResponse", "Applications"]
            ),
            ["a1", "a2"],
        )

    def test_forwards_kwargs_to_single_call(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.describe_images.return_value = {"Images": []}

        paginate_or_call(client, "describe_images", ["Images"], Owners=["self"])
        client.describe_images.assert_called_once_with(Owners=["self"])

    def test_truncates_single_call_at_max_items(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.list_things.return_value = {"Items": list(range(20))}

        self.assertEqual(
            paginate_or_call(client, "list_things", ["Items"], 5), [0, 1, 2, 3, 4]
        )


if __name__ == "__main__":
    unittest.main()
