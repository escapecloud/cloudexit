import logging
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import botocore.session
from botocore.awsrequest import AWSResponse
from botocore.exceptions import (
    ConnectTimeoutError,
    EndpointConnectionError,
    UnknownServiceError,
)

from cloudexit.providers.aws import client as aws_client
from cloudexit.providers.aws import costs as aws_costs
from cloudexit.providers.aws.client import (
    AWS_RETRY_CONFIG,
    CONNECTION_ATTEMPTS,
    SERVICE_NOT_OFFERED,
    SERVICE_OK,
    SERVICE_UNKNOWN,
    convert_datetime,
    create_client,
    extract_result_path,
    paginate,
    paginate_or_call,
    parse_resource_type_params,
    resolve_aws_call_spec,
    service_status,
)
from cloudexit.providers.aws.inventory import build_aws_resource_inventory

REGION = "eu-central-1"
URL = "https://ec2.eu-central-1.amazonaws.com"


def row(id_, service, operation="list_things", result="Things"):
    return {
        "code": f"AWS.{service}.{operation}.{result}",
        "id": id_,
        "name": f"{service} {operation}",
        "csp": 2,
        "status": "t",
        "params": "{}",
    }


class Raw:
    def __init__(self, data):
        self.data = data

    def stream(self):
        yield self.data


def throttled():
    body = (
        b"<Response><Errors><Error><Code>RequestLimitExceeded</Code>"
        b"<Message>slow down</Message></Error></Errors><RequestID>x</RequestID>"
        b"</Response>"
    )
    return AWSResponse(URL, 503, {"Content-Type": "text/xml"}, Raw(body))


def answered():
    body = (
        b"<DescribeRegionsResponse xmlns='http://ec2.amazonaws.com/doc/2016-11-15/'>"
        b"<requestId>x</requestId><regionInfo/></DescribeRegionsResponse>"
    )
    return AWSResponse(URL, 200, {"Content-Type": "text/xml"}, Raw(body))


class ConfigTests(unittest.TestCase):
    def test_the_clients_give_up_on_a_connection_after_5_seconds_and_a_read_after_30(
        self,
    ):
        self.assertEqual(AWS_RETRY_CONFIG.connect_timeout, 5)
        self.assertEqual(AWS_RETRY_CONFIG.read_timeout, 30)

    def test_throttling_keeps_the_adaptive_retries(self):
        self.assertEqual(
            AWS_RETRY_CONFIG.retries, {"mode": "adaptive", "max_attempts": 8}
        )


class RetryBehaviourTests(unittest.TestCase):
    def client(self):
        session = boto3.Session(
            aws_access_key_id="AK", aws_secret_access_key="SK", region_name=REGION
        )
        return create_client(session, "ec2", REGION)

    def sends(self, answers):
        return patch("botocore.httpsession.URLLib3Session.send", side_effect=answers)

    def test_a_connection_timeout_costs_at_most_two_attempts(self):
        error = ConnectTimeoutError(endpoint_url=URL)
        with self.sends(error) as send, patch("time.sleep"):
            with self.assertRaises(ConnectTimeoutError):
                self.client().describe_regions()

        self.assertEqual(send.call_count, CONNECTION_ATTEMPTS)

    def test_an_unreachable_endpoint_costs_at_most_two_attempts(self):
        error = EndpointConnectionError(endpoint_url=URL)
        with self.sends(error) as send, patch("time.sleep") as sleep:
            with self.assertRaises(EndpointConnectionError):
                self.client().describe_regions()

        self.assertEqual(send.call_count, CONNECTION_ATTEMPTS)
        self.assertEqual(sleep.call_count, CONNECTION_ATTEMPTS - 1)

    def test_a_connection_that_works_the_second_time_succeeds(self):
        answers = [ConnectTimeoutError(endpoint_url=URL), answered()]
        with self.sends(answers) as send, patch("time.sleep"):
            result = self.client().describe_regions()

        self.assertEqual(result["Regions"], [])
        self.assertEqual(send.call_count, 2)

    def test_throttling_is_still_retried_beyond_two_attempts(self):
        answers = [throttled(), throttled(), throttled(), answered()]
        with self.sends(answers) as send, patch("time.sleep"):
            result = self.client().describe_regions()

        self.assertEqual(result["Regions"], [])
        self.assertEqual(send.call_count, 4)

    def test_the_retry_limit_ignores_every_other_error(self):
        # Called as botocore does; only a connection error is cut short.
        aws_client.limit_connection_retries(5, caught_exception=RuntimeError("x"))
        aws_client.limit_connection_retries(5, caught_exception=None)
        aws_client.limit_connection_retries(
            1, caught_exception=ConnectTimeoutError(endpoint_url=URL)
        )
        with self.assertRaises(EndpointConnectionError):
            aws_client.limit_connection_retries(
                2, caught_exception=EndpointConnectionError(endpoint_url=URL)
            )


class ServiceStatusTests(unittest.TestCase):
    def test_a_service_the_installed_botocore_does_not_know_is_unknown(self):
        for service in ("elastictranscoder", "no-such-service"):
            with self.subTest(service=service):
                self.assertEqual(service_status(service, REGION), SERVICE_UNKNOWN)

    def test_a_regional_service_is_offered_where_botocore_lists_it(self):
        self.assertEqual(service_status("ec2", REGION), SERVICE_OK)
        self.assertEqual(service_status("ec2", "us-east-1"), SERVICE_OK)

    def test_a_global_service_is_never_skipped(self):
        for service in ("iam", "cloudfront", "route53", "organizations"):
            for region in (REGION, "us-east-1", "ap-southeast-2"):
                with self.subTest(service=service, region=region):
                    self.assertEqual(service_status(service, region), SERVICE_OK)

    def test_a_region_botocore_has_never_heard_of_is_left_to_the_call(self):
        self.assertEqual(service_status("ec2", "xx-fake-1"), SERVICE_OK)

    def test_a_service_missing_from_a_region_is_not_offered(self):
        session = botocore.session.get_session()
        found = None
        for service in session.get_available_services():
            regions = session.get_available_regions(service)
            everything = session.get_available_regions(service, allow_non_regional=True)
            if regions and set(everything) == set(regions) and REGION not in regions:
                found = service
                break
        if found is None:
            self.skipTest("every installed service lists this region")

        self.assertEqual(service_status(found, REGION), SERVICE_NOT_OFFERED)


class SweepTests(unittest.TestCase):
    def run_sweep(self, rows, *, status=None, client=None, calls=None):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        aws_logger = logging.getLogger("cloudexit.providers.aws")
        handler = Capture()
        previous = aws_logger.level
        aws_logger.addHandler(handler)
        aws_logger.setLevel(logging.DEBUG)
        session = MagicMock()
        if client is not None:
            session.client.side_effect = client
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = os.path.join(tmp, "report")
                raw = os.path.join(tmp, "raw")
                os.makedirs(os.path.join(report, "data"))
                os.makedirs(raw)
                patches = [
                    patch(
                        "cloudexit.providers.aws.inventory.load_data", return_value=rows
                    ),
                    patch(
                        "cloudexit.providers.aws.inventory.boto3.Session",
                        return_value=session,
                    ),
                    patch("cloudexit.providers.aws.inventory.connect"),
                    patch(
                        "cloudexit.providers.aws.inventory.paginate_or_call",
                        side_effect=calls or (lambda *a, **k: []),
                    ),
                ]
                if status is not None:
                    patches.append(
                        patch(
                            "cloudexit.providers.aws.inventory.service_status",
                            side_effect=lambda service, region: status.get(
                                service, SERVICE_OK
                            ),
                        )
                    )
                for item in patches:
                    item.start()
                try:
                    build_aws_resource_inventory(
                        2,
                        {"accessKey": "AK", "secretKey": "SK", "region": REGION},
                        report,
                        raw,
                    )
                finally:
                    for item in reversed(patches):
                        item.stop()
        finally:
            aws_logger.removeHandler(handler)
            aws_logger.setLevel(previous)
        return session, records

    def messages(self, records, level=None):
        return [r.getMessage() for r in records if level is None or r.levelno == level]

    def test_a_service_the_region_does_not_offer_makes_no_client_call(self):
        rows = [row(1, "healthlake"), row(2, "healthlake", "list_other")]
        session, records = self.run_sweep(
            rows, status={"healthlake": SERVICE_NOT_OFFERED}
        )

        session.client.assert_not_called()
        debug = [
            m
            for m in self.messages(records, logging.DEBUG)
            if m.startswith("Skipping healthlake")
        ]
        self.assertEqual(len(debug), 1)
        self.assertIn(f"not offered in {REGION}", debug[0])

    def test_a_service_unknown_to_boto3_is_skipped_once_without_a_call(self):
        rows = [row(1, "elastictranscoder"), row(2, "elastictranscoder", "list_two")]
        session, records = self.run_sweep(
            rows, status={"elastictranscoder": SERVICE_UNKNOWN}
        )

        session.client.assert_not_called()
        debug = [
            m
            for m in self.messages(records, logging.DEBUG)
            if m.startswith("Skipping elastictranscoder")
        ]
        self.assertEqual(len(debug), 1)

    def test_an_unknown_service_error_from_boto3_is_skipped_once_per_service(self):
        def client(service, **kwargs):
            raise UnknownServiceError(service_name=service, known_service_names=["ec2"])

        rows = [row(1, "iotevents-data"), row(2, "iotevents-data", "list_two")]
        session, records = self.run_sweep(rows, status={}, client=client)

        self.assertEqual(session.client.call_count, 1)
        debug = [
            m
            for m in self.messages(records, logging.DEBUG)
            if m.startswith("Skipping iotevents-data")
        ]
        self.assertEqual(len(debug), 1)
        self.assertFalse(any(r.levelno >= logging.WARNING for r in records))

    def test_an_unreachable_service_is_given_up_on_once_for_every_resource_type(self):
        def calls(client, operation, *args, **kwargs):
            raise EndpointConnectionError(endpoint_url="https://inspector.example")

        rows = [
            row(1, "inspector"),
            row(2, "inspector", "list_two"),
            row(3, "inspector", "list_three"),
            row(4, "ec2", "describe_instances", "Reservations"),
        ]
        attempted = []

        def counting(client, operation, *args, **kwargs):
            attempted.append(operation)
            if operation != "describe_instances":
                raise EndpointConnectionError(endpoint_url="https://inspector.example")
            return []

        session, records = self.run_sweep(rows, status={}, calls=counting)

        # The first inspector call fails; its other types are skipped; ec2 still runs
        self.assertEqual(attempted, ["list_things", "describe_instances"])
        self.assertEqual(session.client.call_count, 2)
        self.assertFalse(any(r.levelno >= logging.WARNING for r in records))

    def test_other_errors_do_not_stop_the_other_resource_types_of_a_service(self):
        attempted = []

        def counting(client, operation, *args, **kwargs):
            attempted.append(operation)
            raise RuntimeError("AccessDenied")

        rows = [row(1, "inspector"), row(2, "inspector", "list_two")]
        self.run_sweep(rows, status={}, calls=counting)

        self.assertEqual(attempted, ["list_things", "list_two"])

    def test_one_summary_line_tells_what_was_skipped(self):
        def counting(client, operation, *args, **kwargs):
            if operation == "list_dead":
                raise EndpointConnectionError(endpoint_url="https://x.example")
            return []

        rows = [
            row(1, "healthlake"),
            row(2, "healthlake", "list_two"),
            row(3, "frauddetector"),
            row(4, "elastictranscoder"),
            row(5, "inspector", "list_dead"),
            row(6, "inspector", "list_again"),
            row(7, "ec2", "describe_instances", "Reservations"),
        ]
        _, records = self.run_sweep(
            rows,
            status={
                "healthlake": SERVICE_NOT_OFFERED,
                "frauddetector": SERVICE_NOT_OFFERED,
                "elastictranscoder": SERVICE_UNKNOWN,
            },
            calls=counting,
        )

        summary = [
            r
            for r in records
            if r.levelno == logging.INFO and "service calls skipped" in r.getMessage()
        ]
        self.assertEqual(len(summary), 1)
        self.assertEqual(
            summary[0].getMessage(),
            f"6 service calls skipped (not offered in {REGION}: frauddetector, "
            "healthlake; unknown to boto3: elastictranscoder; failed: inspector)",
        )

    def test_a_clean_sweep_logs_no_skip_summary(self):
        _, records = self.run_sweep(
            [row(1, "ec2", "describe_instances", "Reservations")], status={}
        )
        self.assertFalse(
            any("service calls skipped" in r.getMessage() for r in records)
        )

    def test_the_real_pre_check_lets_global_and_regional_services_through(self):
        rows = [
            row(1, "iam", "list_users", "Users"),
            row(2, "ec2", "describe_instances", "Reservations"),
        ]
        session, _ = self.run_sweep(rows)

        self.assertEqual(session.client.call_count, 2)


class ClientFactoryTests(unittest.TestCase):
    def test_every_collector_client_gets_the_timeouts_and_the_retry_limit(self):
        session = MagicMock()
        client = create_client(session, "cloudwatch", REGION)

        session.client.assert_called_once_with(
            "cloudwatch", region_name=REGION, config=AWS_RETRY_CONFIG
        )
        client.meta.events.register_first.assert_called_once_with(
            "needs-retry", aws_client.limit_connection_retries
        )

    def test_the_cost_explorer_stays_pinned_to_us_east_1(self):
        source = Path(aws_costs.__file__).read_text(encoding="utf-8")
        self.assertIn('create_client(session, "ce", "us-east-1")', source)


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
