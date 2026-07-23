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

        mock_run.assert_called_once_with(ANY, "aws", dry_run=True, egress=False)


def _ni_aws_args(**kwargs):
    defaults = dict(
        config=None,
        profile=None,
        name=None,
        non_interactive=True,
        dry_run=False,
        egress=False,
    )
    defaults.update(kwargs)
    return Namespace(**defaults)


def _ni_azure_args(**kwargs):
    defaults = dict(
        config=None,
        cli=False,
        name=None,
        non_interactive=True,
        dry_run=False,
        egress=False,
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

    def test_includes_optional_session_token_from_env(self):
        env = {**self._BASE_ENV, "AWS_SESSION_TOKEN": "sts-session-token"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.validate_region"),
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_aws(_ni_aws_args())

        config_arg = mock_run.call_args[0][0]
        self.assertEqual(
            config_arg["providerDetails"]["sessionToken"], "sts-session-token"
        )

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

    def test_uses_default_credential_when_client_secret_missing(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "AZURE_CLIENT_SECRET"}
        mock_credential = MagicMock()
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.DefaultAzureCredential", return_value=mock_credential),
            patch("main.ClientSecretCredential") as mock_client_secret_cred,
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_azure(_ni_azure_args())

        mock_client_secret_cred.assert_not_called()
        mock_run.assert_called_once()
        config_arg = mock_run.call_args[0][0]
        self.assertEqual(config_arg["providerDetails"]["credential"], mock_credential)
        self.assertEqual(config_arg["providerDetails"]["tenantId"], "tenant-id-123")
        self.assertEqual(config_arg["providerDetails"]["subscriptionId"], "sub-id-789")
        self.assertEqual(config_arg["providerDetails"]["resourceGroupName"], "my-rg")
        self.assertEqual(config_arg["providerDetails"]["clientId"], "client-id-456")
        self.assertNotIn("clientSecret", config_arg["providerDetails"])

    def test_missing_client_id_for_oidc_raises_config_error(self):
        env = {
            k: v
            for k, v in self._BASE_ENV.items()
            if k not in ("AZURE_CLIENT_SECRET", "AZURE_CLIENT_ID")
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("main.console.print"),
        ):
            with self.assertRaises(main.ConfigError):
                main.handle_azure(_ni_azure_args())

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


class EgressStageTests(unittest.TestCase):
    _ESTIMATE_OK = {
        "success": True,
        "logs": "",
        "json_path": "/tmp/report/raw/egress_estimate.json",
    }
    _REPORT_OK = {
        "success": True,
        "logs": "",
        "html_path": "/tmp/report/egress.html",
    }
    _PDF_OK = {
        "success": True,
        "logs": "",
        "pdf_path": "/tmp/report/egress.pdf",
    }

    @staticmethod
    def _azure_config():
        return {
            "name": "Azure Egress Test",
            "cloudServiceProvider": 1,
            "exitStrategy": 1,
            "assessmentType": 1,
            "providerDetails": {
                "credential": MagicMock(),
                "tenantId": "tenant-id",
                "subscriptionId": "sub-id",
                "resourceGroupName": "my-rg",
            },
        }

    def test_egress_module_not_invoked_without_flag(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        mock_estimate = patch("main.estimate_egress").start()
        mock_render = patch("main.generate_egress_html_report").start()
        mock_pdf = patch("main.generate_egress_pdf_report").start()
        try:
            main.run_assessment(self._azure_config(), "azure")
        finally:
            patch.stopall()

        mock_estimate.assert_not_called()
        mock_render.assert_not_called()
        mock_pdf.assert_not_called()

    def test_egress_invoked_after_report_generation(self):
        manager = MagicMock()
        patches = _base_patches()
        for p in patches:
            p.start()
        mock_report = patch(
            "main.generate_report",
            return_value={"success": True, "reports": {}},
        ).start()
        mock_estimate = patch(
            "main.estimate_egress", return_value=self._ESTIMATE_OK
        ).start()
        mock_render = patch(
            "main.generate_egress_html_report", return_value=self._REPORT_OK
        ).start()
        mock_pdf = patch(
            "main.generate_egress_pdf_report", return_value=self._PDF_OK
        ).start()
        manager.attach_mock(mock_report, "generate_report")
        manager.attach_mock(mock_estimate, "estimate_egress")
        manager.attach_mock(mock_render, "generate_egress_html_report")
        manager.attach_mock(mock_pdf, "generate_egress_pdf_report")
        config = self._azure_config()
        try:
            main.run_assessment(config, "azure", egress=True)
        finally:
            patch.stopall()

        mock_estimate.assert_called_once_with(
            1,
            config["providerDetails"],
            "/tmp/report/raw",
            name=config["name"],
            exit_strategy=config["exitStrategy"],
            assessment_type=config["assessmentType"],
        )
        mock_render.assert_called_once_with(
            "/tmp/report", "/tmp/report/raw/egress_estimate.json"
        )
        mock_pdf.assert_called_once_with(
            "/tmp/report",
            "/tmp/report/raw/egress_estimate.json",
            config["providerDetails"],
        )
        call_names = [name for name, _, _ in manager.mock_calls]
        self.assertLess(
            call_names.index("generate_report"), call_names.index("estimate_egress")
        )
        self.assertLess(
            call_names.index("estimate_egress"),
            call_names.index("generate_egress_html_report"),
        )
        self.assertLess(
            call_names.index("generate_egress_html_report"),
            call_names.index("generate_egress_pdf_report"),
        )

    def test_egress_failure_still_prints_outputs_and_exits_egress(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        patch(
            "main.estimate_egress",
            return_value={"success": False, "logs": "metrics api error"},
        ).start()
        mock_render = patch("main.generate_egress_html_report").start()
        mock_pdf = patch("main.generate_egress_pdf_report").start()
        mock_print = patch("main.console.print").start()
        try:
            with self.assertRaises(SystemExit) as ctx:
                main.run_assessment(self._azure_config(), "azure", egress=True)
        finally:
            patch.stopall()

        self.assertEqual(ctx.exception.code, codes.EGRESS)
        mock_render.assert_not_called()
        mock_pdf.assert_not_called()
        printed = [str(call) for call in mock_print.call_args_list]
        self.assertTrue(any("Outputs:" in line for line in printed))

    def test_egress_report_failure_still_prints_outputs_and_exits_egress(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        patch("main.estimate_egress", return_value=self._ESTIMATE_OK).start()
        patch(
            "main.generate_egress_html_report",
            return_value={"success": False, "logs": "template error"},
        ).start()
        patch("main.generate_egress_pdf_report", return_value=self._PDF_OK).start()
        mock_print = patch("main.console.print").start()
        try:
            with self.assertRaises(SystemExit) as ctx:
                main.run_assessment(self._azure_config(), "azure", egress=True)
        finally:
            patch.stopall()

        self.assertEqual(ctx.exception.code, codes.EGRESS)
        printed = [str(call) for call in mock_print.call_args_list]
        self.assertTrue(any("Outputs:" in line for line in printed))
        # The JSON path is internal input for the reports; it is never listed
        # in Outputs. The failed HTML report is not listed, but the PDF ran
        # independently and succeeded, so it is.
        self.assertFalse(any("Egress Estimate:" in line for line in printed))
        self.assertFalse(any("Egress Report:" in line for line in printed))
        self.assertTrue(any("Egress PDF:" in line for line in printed))

    def test_egress_pdf_failure_still_prints_outputs_and_exits_egress(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        patch("main.estimate_egress", return_value=self._ESTIMATE_OK).start()
        patch("main.generate_egress_html_report", return_value=self._REPORT_OK).start()
        patch(
            "main.generate_egress_pdf_report",
            return_value={"success": False, "logs": "reportlab error"},
        ).start()
        mock_print = patch("main.console.print").start()
        try:
            with self.assertRaises(SystemExit) as ctx:
                main.run_assessment(self._azure_config(), "azure", egress=True)
        finally:
            patch.stopall()

        self.assertEqual(ctx.exception.code, codes.EGRESS)
        printed = [str(call) for call in mock_print.call_args_list]
        self.assertTrue(any("Outputs:" in line for line in printed))
        self.assertTrue(any("Egress Report:" in line for line in printed))
        self.assertFalse(any("Egress PDF:" in line for line in printed))

    def test_egress_invoked_for_aws_with_provider_code(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        mock_estimate = patch(
            "main.estimate_egress", return_value=self._ESTIMATE_OK
        ).start()
        patch("main.generate_egress_html_report", return_value=self._REPORT_OK).start()
        patch("main.generate_egress_pdf_report", return_value=self._PDF_OK).start()
        config = VALID_CONFIG.copy()
        try:
            main.run_assessment(config, "aws", egress=True)
        finally:
            patch.stopall()

        mock_estimate.assert_called_once_with(
            2,
            config["providerDetails"],
            "/tmp/report/raw",
            name=config["name"],
            exit_strategy=config["exitStrategy"],
            assessment_type=config["assessmentType"],
        )

    def test_handle_azure_passes_egress_flag(self):
        mock_credential = MagicMock()
        with (
            patch.dict(os.environ, NonInteractiveAzureTests._BASE_ENV, clear=False),
            patch("main.ClientSecretCredential", return_value=mock_credential),
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_azure(_ni_azure_args(egress=True))

        mock_run.assert_called_once_with(ANY, "azure", dry_run=False, egress=True)

    def test_handle_aws_passes_egress_flag(self):
        with (
            patch.dict(os.environ, NonInteractiveAWSTests._BASE_ENV, clear=False),
            patch("main.validate_region"),
            patch("main.run_assessment") as mock_run,
            patch("main.console.print"),
        ):
            main.handle_aws(_ni_aws_args(egress=True))

        mock_run.assert_called_once_with(ANY, "aws", dry_run=False, egress=True)


class MainExitCodeTests(unittest.TestCase):
    def test_config_error_from_handler_exits_config(self):
        with (
            patch("main.initialize_dataset"),
            patch("main.console.print"),
            patch(
                "main.parse_arguments",
                return_value=Namespace(cloud_provider="aws"),
            ),
            patch("main.handle_aws", side_effect=main.ConfigError),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.main()
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_bad_config_file_exits_config_end_to_end(self):
        args = Namespace(
            cloud_provider="aws",
            config="/nonexistent/aws.json",
            profile=None,
            name=None,
            non_interactive=False,
            dry_run=False,
            egress=False,
        )
        with (
            patch("main.initialize_dataset"),
            patch("main.console.print"),
            patch("main.parse_arguments", return_value=args),
            patch("main.load_config", return_value=None),
            patch("main.run_assessment") as mock_run,
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.main()
        self.assertEqual(ctx.exception.code, codes.CONFIG)
        mock_run.assert_not_called()


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
