import os
import types
import unittest
from unittest.mock import MagicMock, patch

from core.utils_sync import post_assessment


class PostAssessmentHostResolutionTests(unittest.TestCase):
    def test_uses_host_from_environment_when_config_host_empty(self):
        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.status_code = 200
        fake_response.json.return_value = {"data": {"ok": True}}

        with (
            patch.dict(os.environ, {"HOST": "env.exitcloud.io"}, clear=False),
            patch(
                "core.utils_sync.config",
                types.SimpleNamespace(HOST="", CLI_VERSION="v1"),
            ),
            patch("core.utils_sync._build_payload", return_value={"sample": "payload"}),
            patch(
                "core.utils_sync.requests.post", return_value=fake_response
            ) as mock_post,
        ):
            result = post_assessment(
                name="Demo",
                started_at=123,
                report_path="/tmp/report",
                meta={
                    "exit_strategy": 1,
                    "cloud_service_provider": 2,
                    "assessment_type": 1,
                },
                token="jwt-token",
            )

        self.assertTrue(result["success"])
        called_url = mock_post.call_args.args[0]
        self.assertEqual(called_url, "https://env.exitcloud.io/api/v1/assessments/")

    def test_returns_clear_error_when_host_missing_everywhere(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "core.utils_sync.config",
                types.SimpleNamespace(HOST="", CLI_VERSION="v1"),
            ),
        ):
            result = post_assessment(
                name="Demo",
                started_at=123,
                report_path="/tmp/report",
                meta={
                    "exit_strategy": 1,
                    "cloud_service_provider": 2,
                    "assessment_type": 1,
                },
                token="jwt-token",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["payload"], None)
        self.assertEqual(result["logs"], "HOST missing in environment and config.py")


if __name__ == "__main__":
    unittest.main()
