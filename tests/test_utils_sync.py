import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
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
                types.SimpleNamespace(HOST=""),
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
                types.SimpleNamespace(HOST=""),
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


class MissingConfigModuleTests(unittest.TestCase):
    def load_without_config(self):
        path = Path(__file__).resolve().parent.parent / "core" / "utils_sync.py"
        spec = importlib.util.spec_from_file_location("utils_sync_no_config", path)
        module = importlib.util.module_from_spec(spec)
        # None in sys.modules makes `import config` fail as if the file were absent
        with patch.dict(sys.modules, {"config": None}):
            spec.loader.exec_module(module)
        return module

    def test_module_imports_without_config(self):
        self.assertIsNone(self.load_without_config().config)

    def test_post_assessment_reports_missing_host_without_config(self):
        module = self.load_without_config()
        with patch.dict(os.environ, {}, clear=True):
            result = module.post_assessment(
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
        self.assertEqual(result["logs"], "HOST missing in environment and config.py")


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
