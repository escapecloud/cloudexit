import json
import os
import tempfile
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import main
from utils import codes
from utils.connection import resolve_mode
from utils.utils import load_config

VALID_CONFIG = {
    "name": "Test Assessment",
    "cloudServiceProvider": 2,
    "exitStrategy": 1,
    "assessmentType": 1,
    "providerDetails": {
        "accessKey": "AKIAIOSFODNN7EXAMPLE",
        "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "region": "eu-central-1",
    },
}


class LoadConfigTests(unittest.TestCase):
    def test_load_config_returns_parsed_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            expected = {
                "cloudServiceProvider": 2,
                "assessmentType": 1,
                "providerDetails": {"region": "eu-central-1"},
            }
            config_path.write_text(json.dumps(expected), encoding="utf-8")

            self.assertEqual(load_config(str(config_path)), expected)

    def test_load_config_returns_none_for_missing_file(self):
        with patch("utils.utils.console.print") as mock_print:
            result = load_config("/tmp/does-not-exist-config.json")

        self.assertIsNone(result)
        mock_print.assert_called_once()

    def test_load_config_returns_none_for_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text("{invalid json", encoding="utf-8")

            with patch("utils.utils.console.print") as mock_print:
                result = load_config(str(config_path))

        self.assertIsNone(result)
        mock_print.assert_called_once()


def _base_patches():
    """Patches that prevent real cloud/filesystem calls for all stage tests."""
    return [
        patch("main.console.print"),
        patch("main.print_step"),
        patch("main.resolve_mode", return_value=("offline", None)),
        patch("main.create_directory", return_value=("/tmp/report", "/tmp/report/raw")),
        patch("main.verify_credentials", return_value=(True, "ok")),
        patch(
            "main.test_permissions",
            return_value=(True, True, True, "ok"),
        ),
        patch(
            "main.create_resource_inventory",
            return_value={"success": True, "logs": ""},
        ),
        patch(
            "main.create_cost_inventory",
            return_value={"success": True, "logs": ""},
        ),
        patch(
            "main.perform_risk_assessment",
            return_value={"success": True, "logs": ""},
        ),
        patch(
            "main.generate_report",
            return_value={
                "success": True,
                "reports": {"HTML": "/tmp/report/index.html"},
            },
        ),
    ]


class RunAssessmentExitCodeTests(unittest.TestCase):
    def _run_with_patches(self, overrides: dict):
        """Apply base patches, override specific ones, and run the assessment."""
        patches = _base_patches()
        for p in patches:
            p.start()
        for target, kwargs in overrides.items():
            patch(target, **kwargs).start()
        try:
            main.run_assessment(VALID_CONFIG.copy(), "aws")
        finally:
            patch.stopall()

    def test_config_validation_failure_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config", side_effect=ValueError("bad config")),
                patch("main.resolve_mode"),
                patch("main.create_directory"),
                patch("main.verify_credentials"),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_directory_creation_failure_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", side_effect=RuntimeError("disk full")),
                patch("main.verify_credentials"),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_credential_failure_exits_3(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
                patch("main.verify_credentials", return_value=(False, "bad creds")),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.CREDENTIALS)

    def test_permission_failure_exits_4(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch(
                    "main.test_permissions",
                    return_value=(False, False, False, "no perms"),
                ),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.PERMISSIONS)

    def test_resource_inventory_failure_exits_5(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch("main.test_permissions", return_value=(True, True, True, "ok")),
                patch(
                    "main.create_resource_inventory",
                    return_value={"success": False, "logs": "api error"},
                ),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.RESOURCE_INVENTORY)

    def test_cost_inventory_failure_exits_6(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch("main.test_permissions", return_value=(True, True, True, "ok")),
                patch(
                    "main.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.create_cost_inventory",
                    return_value={"success": False, "logs": "billing error"},
                ),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.COST_INVENTORY)

    def test_risk_assessment_failure_exits_7(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch("main.test_permissions", return_value=(True, True, True, "ok")),
                patch(
                    "main.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.perform_risk_assessment",
                    return_value={"success": False, "logs": "risk error"},
                ),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.RISK_ASSESSMENT)

    def test_report_generation_failure_exits_8(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("offline", None)),
                patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch("main.test_permissions", return_value=(True, True, True, "ok")),
                patch(
                    "main.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.generate_report",
                    return_value={"success": False, "logs": "render error"},
                ),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.REPORT)

    def test_unexpected_exception_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("main.validate_config", side_effect=RuntimeError("boom")),
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.UNEXPECTED)

    def test_full_success_exits_0(self):
        with (
            patch("main.validate_config"),
            patch("main.resolve_mode", return_value=("offline", None)),
            patch("main.create_directory", return_value=("/tmp/r", "/tmp/r/raw")),
            patch("main.verify_credentials", return_value=(True, "ok")),
            patch("main.test_permissions", return_value=(True, True, True, "ok")),
            patch(
                "main.create_resource_inventory",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "main.create_cost_inventory", return_value={"success": True, "logs": ""}
            ),
            patch(
                "main.perform_risk_assessment",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "main.generate_report", return_value={"success": True, "reports": {}}
            ),
            patch("main.print_step"),
            patch("main.console.print"),
        ):
            result = main.run_assessment(VALID_CONFIG.copy(), "aws")

        self.assertIsNone(result)

    def test_dry_run_writes_payload_generates_report_and_skips_sync(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = os.path.join(tmp_dir, "raw_data")
            os.makedirs(raw_data_path, exist_ok=True)
            payload_path = os.path.join(raw_data_path, "payload.json")
            config = VALID_CONFIG.copy()
            config["assessmentType"] = 2

            with (
                patch("main.validate_config"),
                patch("main.resolve_mode", return_value=("online", "jwt-token")),
                patch(
                    "main.create_directory",
                    return_value=(tmp_dir, raw_data_path),
                ),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch("main.test_permissions", return_value=(True, True, True, "ok")),
                patch(
                    "main.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.write_assessment_payload",
                    return_value=payload_path,
                ) as mock_write,
                patch(
                    "main.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ) as mock_risk,
                patch(
                    "main.generate_report",
                    return_value={
                        "success": True,
                        "reports": {
                            "HTML": f"{tmp_dir}/index.html",
                            "PDF": f"{tmp_dir}/report.pdf",
                        },
                    },
                ) as mock_report,
                patch("main.sync_assessment") as mock_sync,
                patch("main.print_step"),
                patch("main.console.print"),
            ):
                main.run_assessment(config, "aws", dry_run=True)

            mock_write.assert_called_once_with(
                raw_data_path,
                report_path=tmp_dir,
                name=config["name"],
                started_at=ANY,
                exit_strategy=config["exitStrategy"],
                cloud_service_provider=config["cloudServiceProvider"],
                assessment_type=2,
            )
            mock_risk.assert_called_once_with(
                exit_strategy=config["exitStrategy"],
                report_path=tmp_dir,
                mode="offline",
            )
            mock_report.assert_called_once()
            mock_sync.assert_not_called()

    def test_dry_run_flag_passed_from_handle_aws(self):
        with (
            patch.dict(os.environ, NonInteractiveAWSTests._BASE_ENV, clear=False),
            patch("main.validate_region"),
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_aws(_ni_aws_args(dry_run=True))

        mock_run.assert_called_once_with(ANY, "aws", dry_run=True)


def _ni_aws_args(**kwargs):
    """Build a Namespace that looks like 'aws --non-interactive' with optional overrides."""
    defaults = dict(
        config=None,
        profile=None,
        name=None,
        non_interactive=True,
        dry_run=False,
    )
    defaults.update(kwargs)
    return Namespace(**defaults)


def _ni_azure_args(**kwargs):
    """Build a Namespace that looks like 'azure --non-interactive' with optional overrides."""
    defaults = dict(
        config=None,
        cli=False,
        name=None,
        non_interactive=True,
        dry_run=False,
    )
    defaults.update(kwargs)
    return Namespace(**defaults)


class NonInteractiveAWSTests(unittest.TestCase):
    _BASE_ENV = {
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "AWS_DEFAULT_REGION": "eu-central-1",
        "ESC_EXIT_STRATEGY": "1",
        "ESC_ASSESSMENT_TYPE": "1",
    }

    def test_builds_config_from_env_and_calls_run_assessment(self):
        with (
            patch.dict(os.environ, self._BASE_ENV, clear=False),
            patch("main.validate_region"),
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_aws(_ni_aws_args())

        mock_run.assert_called_once()
        config_arg = mock_run.call_args[0][0]
        self.assertEqual(config_arg["exitStrategy"], 1)
        self.assertEqual(config_arg["assessmentType"], 1)
        self.assertEqual(
            config_arg["providerDetails"]["accessKey"], "AKIAIOSFODNN7EXAMPLE"
        )
        self.assertEqual(config_arg["providerDetails"]["region"], "eu-central-1")

    def test_missing_exit_strategy_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "ESC_EXIT_STRATEGY"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.handle_aws(_ni_aws_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_invalid_exit_strategy_exits_config(self):
        env = {**self._BASE_ENV, "ESC_EXIT_STRATEGY": "9"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.handle_aws(_ni_aws_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_missing_aws_region_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "AWS_DEFAULT_REGION"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.handle_aws(_ni_aws_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)


class NonInteractiveAzureTests(unittest.TestCase):
    _BASE_ENV = {
        "AZURE_TENANT_ID": "tenant-id-123",
        "AZURE_CLIENT_ID": "client-id-456",
        "AZURE_CLIENT_SECRET": "super-secret",
        "ESC_SUBSCRIPTION_ID": "sub-id-789",
        "ESC_RESOURCE_GROUP": "my-rg",
        "ESC_EXIT_STRATEGY": "1",
        "ESC_ASSESSMENT_TYPE": "1",
    }

    def test_builds_config_from_env_and_calls_run_assessment(self):
        mock_credential = MagicMock()
        with (
            patch.dict(os.environ, self._BASE_ENV, clear=False),
            patch("main.ClientSecretCredential", return_value=mock_credential),
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_azure(_ni_azure_args())

        mock_run.assert_called_once()
        config_arg = mock_run.call_args[0][0]
        self.assertEqual(config_arg["exitStrategy"], 1)
        self.assertEqual(config_arg["providerDetails"]["tenantId"], "tenant-id-123")
        self.assertEqual(config_arg["providerDetails"]["subscriptionId"], "sub-id-789")
        self.assertEqual(config_arg["providerDetails"]["resourceGroupName"], "my-rg")

    def test_missing_subscription_id_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "ESC_SUBSCRIPTION_ID"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.handle_azure(_ni_azure_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_missing_resource_group_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "ESC_RESOURCE_GROUP"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.handle_azure(_ni_azure_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)


class ResolveModeEnvVarTests(unittest.TestCase):
    def test_host_and_key_env_override_config(self):
        fake_config = types.SimpleNamespace(HOST="config-host.io", KEY="config-key")
        with (
            patch("utils.connection.config", fake_config),
            patch.dict(
                os.environ, {"HOST": "env-host.io", "KEY": "env-key"}, clear=False
            ),
            patch("utils.connection.get_jwt_token", return_value="tok123"),
        ):
            mode, token = resolve_mode()

        self.assertEqual(mode, "online")
        self.assertEqual(token, "tok123")

    def test_falls_back_to_config_when_env_not_set(self):
        fake_config = types.SimpleNamespace(HOST="", KEY="")
        env = {k: v for k, v in os.environ.items() if k not in ("HOST", "KEY")}
        with (
            patch("utils.connection.config", fake_config),
            patch.dict(os.environ, env, clear=True),
        ):
            mode, token = resolve_mode()

        self.assertEqual(mode, "offline")
        self.assertIsNone(token)


if __name__ == "__main__":
    unittest.main()
