import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.utils_sync import post_assessment
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


class WriteAssessmentPayloadTests(unittest.TestCase):
    def test_writes_payload_json_to_raw_data(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = os.path.join(tmp_dir, "report")
            raw_data_path = os.path.join(report_path, "raw_data")
            data_path = os.path.join(report_path, "data")
            os.makedirs(raw_data_path, exist_ok=True)
            os.makedirs(data_path, exist_ok=True)

            with patch("core.utils_sync.load_data") as mock_load:
                mock_load.side_effect = lambda table, db_path=None: (
                    [
                        {
                            "resource_type": 10,
                            "location": "eu-central-1",
                            "count": 3,
                        }
                    ]
                    if table == "resource_inventory"
                    else [
                        {
                            "month": "2025-01",
                            "cost": 42.5,
                            "currency": "USD",
                        }
                    ]
                )

                from core.utils_sync import write_assessment_payload

                payload_path = write_assessment_payload(
                    raw_data_path,
                    report_path=report_path,
                    name="Dry Run Demo",
                    started_at=1000,
                    exit_strategy=1,
                    cloud_service_provider=2,
                    assessment_type=2,
                )

            self.assertEqual(payload_path, os.path.join(raw_data_path, "payload.json"))
            with open(payload_path, encoding="utf-8") as payload_file:
                payload = json.load(payload_file)

            self.assertEqual(payload["type"], "local.assessment.succeeded")
            self.assertEqual(payload["data"]["name"], "Dry Run Demo")
            self.assertEqual(payload["data"]["assessmentType"], 2)
            self.assertEqual(len(payload["data"]["resource_inventory"]), 1)
            self.assertEqual(len(payload["data"]["cost_inventory"]), 1)


if __name__ == "__main__":
    unittest.main()
