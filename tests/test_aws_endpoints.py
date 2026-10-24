import logging
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import boto3
import botocore.session
from botocore.awsrequest import AWSResponse
from botocore.exceptions import (
    ConnectTimeoutError,
    EndpointConnectionError,
    UnknownServiceError,
)

from core import utils_aws
from core.utils_aws import (
    AWS_RETRY_CONFIG,
    CONNECTION_ATTEMPTS,
    SERVICE_NOT_OFFERED,
    SERVICE_OK,
    SERVICE_UNKNOWN,
    build_aws_resource_inventory,
    create_client,
    service_status,
)

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
        utils_aws.limit_connection_retries(5, caught_exception=RuntimeError("x"))
        utils_aws.limit_connection_retries(5, caught_exception=None)
        utils_aws.limit_connection_retries(
            1, caught_exception=ConnectTimeoutError(endpoint_url=URL)
        )
        with self.assertRaises(EndpointConnectionError):
            utils_aws.limit_connection_retries(
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

        aws_logger = logging.getLogger("core.engine.aws")
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
                    patch("core.utils_aws.load_data", return_value=rows),
                    patch("core.utils_aws.boto3.Session", return_value=session),
                    patch("core.utils_aws.connect"),
                    patch(
                        "core.utils_aws.paginate_or_call",
                        side_effect=calls or (lambda *a, **k: []),
                    ),
                ]
                if status is not None:
                    patches.append(
                        patch(
                            "core.utils_aws.service_status",
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
            "needs-retry", utils_aws.limit_connection_retries
        )

    def test_the_cost_explorer_stays_pinned_to_us_east_1(self):
        source = open(utils_aws.__file__, encoding="utf-8").read()
        self.assertIn('create_client(session, "ce", "us-east-1")', source)


if __name__ == "__main__":
    unittest.main()
