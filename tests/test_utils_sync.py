import unittest
from unittest.mock import patch

from core.utils_sync import _build_payload, post_assessment
from tests.http_fixtures import fake_http, make_response
from utils.settings import SettingsError

META = {"exit_strategy": 1, "cloud_service_provider": 2, "assessment_type": 1}


def post(**overrides):
    kwargs = dict(
        name="Demo",
        started_at=123,
        report_path="/tmp/report",
        meta=META,
        token="jwt-token",
        host="platform.test",
    )
    kwargs.update(overrides)
    return post_assessment(**kwargs)


class PostAssessmentTests(unittest.TestCase):
    def test_posts_to_the_given_host_over_https(self):
        with (
            patch("core.utils_sync._build_payload", return_value={"sample": "payload"}),
            fake_http(make_response(json_body={"data": {"ok": True}})) as fake,
        ):
            result = post()

        self.assertTrue(result["success"])
        (request,) = fake.requests
        self.assertEqual(request.url, "https://platform.test/api/v1/assessments/")
        self.assertEqual(request.kwargs["json"], {"sample": "payload"})

    def test_timeout_and_ca_bundle_are_passed_to_requests(self):
        with (
            patch("core.utils_sync._build_payload", return_value={}),
            fake_http(make_response(json_body={})) as fake,
        ):
            post(timeout=42.5, verify="/etc/ca.pem")

        self.assertEqual(fake.requests[0].kwargs["timeout"], 42.5)
        self.assertEqual(fake.requests[0].kwargs["verify"], "/etc/ca.pem")

    def test_returns_clear_error_when_no_host_is_configured(self):
        result = post(host=None)

        self.assertFalse(result["success"])
        self.assertEqual(result["payload"], None)
        self.assertIn("no upload host configured", result["logs"])

    def test_plain_http_to_a_remote_host_is_refused(self):
        with patch("core.utils_sync._build_payload", return_value={}):
            with self.assertRaises(SettingsError):
                post(host="http://platform.test")


class BuildPayloadLoggingTests(unittest.TestCase):
    def test_the_outgoing_payload_is_summarised_not_dumped(self):
        rows = {
            "resource_inventory": [
                {"resource_type": 10, "location": "eu-central-1", "count": 3}
            ],
            "cost_inventory": [{"month": "2025-01", "cost": 42.5, "currency": "USD"}],
        }
        with (
            patch(
                "core.utils_sync.load_data",
                side_effect=lambda table, db_path=None: rows[table],
            ),
            self.assertLogs("core.engine.sync", level="DEBUG") as logs,
        ):
            payload = _build_payload(
                report_path="/tmp/report",
                name="Secret Assessment Name",
                started_at=1000,
                exit_strategy=1,
                cloud_service_provider=2,
                assessment_type=2,
            )

        self.assertEqual(payload["data"]["name"], "Secret Assessment Name")
        text = "\n".join(logs.output)
        self.assertIn("1 resource row(s), 1 cost month(s)", text)
        self.assertNotIn("Secret Assessment Name", text)
        self.assertNotIn("eu-central-1", text)


if __name__ == "__main__":
    unittest.main()
