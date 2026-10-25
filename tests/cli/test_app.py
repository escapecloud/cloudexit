import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from cloudexit.cli import app
from cloudexit.cli.parser import parse_arguments
from cloudexit.platform.document import SyncOutput
from cloudexit import codes
from cloudexit.settings import Settings

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


def _base_patches():
    return [
        patch("cloudexit.cli.app.console.print"),
        patch("cloudexit.cli.app.print_step"),
        patch(
            "cloudexit.cli.app.create_directory",
            return_value=("/tmp/report", "/tmp/report/raw"),
        ),
        patch("cloudexit.cli.app.verify_credentials", return_value=(True, "ok")),
        patch(
            "cloudexit.cli.app.test_permissions",
            return_value=(True, True, True, "ok"),
        ),
        patch(
            "cloudexit.cli.app.create_resource_inventory",
            return_value={"success": True, "logs": ""},
        ),
        patch(
            "cloudexit.cli.app.create_cost_inventory",
            return_value={"success": True, "logs": ""},
        ),
        patch(
            "cloudexit.cli.app.perform_risk_assessment",
            return_value={"success": True, "logs": ""},
        ),
        patch(
            "cloudexit.cli.app.generate_report",
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
            app.run_assessment(VALID_CONFIG.copy(), "aws")
        finally:
            patch.stopall()

    def test_config_validation_failure_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch(
                    "cloudexit.cli.app.validate_config",
                    side_effect=ValueError("bad config"),
                ),
                patch("cloudexit.cli.app.create_directory"),
                patch("cloudexit.cli.app.verify_credentials"),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_directory_creation_failure_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    side_effect=RuntimeError("disk full"),
                ),
                patch("cloudexit.cli.app.verify_credentials"),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_credential_failure_exits_3(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=("/tmp/r", "/tmp/r/raw"),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials",
                    return_value=(False, "bad creds"),
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.CREDENTIALS)

    def test_permission_failure_exits_4(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=("/tmp/r", "/tmp/r/raw"),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials", return_value=(True, "ok")
                ),
                patch(
                    "cloudexit.cli.app.test_permissions",
                    return_value=(False, False, False, "no perms"),
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.PERMISSIONS)

    def test_resource_inventory_failure_exits_5(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=("/tmp/r", "/tmp/r/raw"),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials", return_value=(True, "ok")
                ),
                patch(
                    "cloudexit.cli.app.test_permissions",
                    return_value=(True, True, True, "ok"),
                ),
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value={"success": False, "logs": "api error"},
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.RESOURCE_INVENTORY)

    def test_cost_inventory_failure_exits_6(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=("/tmp/r", "/tmp/r/raw"),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials", return_value=(True, "ok")
                ),
                patch(
                    "cloudexit.cli.app.test_permissions",
                    return_value=(True, True, True, "ok"),
                ),
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.create_cost_inventory",
                    return_value={"success": False, "logs": "billing error"},
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.COST_INVENTORY)

    def test_risk_assessment_failure_exits_7(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=("/tmp/r", "/tmp/r/raw"),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials", return_value=(True, "ok")
                ),
                patch(
                    "cloudexit.cli.app.test_permissions",
                    return_value=(True, True, True, "ok"),
                ),
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.perform_risk_assessment",
                    return_value={"success": False, "logs": "risk error"},
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.RISK_ASSESSMENT)

    def test_report_generation_failure_exits_8(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=("/tmp/r", "/tmp/r/raw"),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials", return_value=(True, "ok")
                ),
                patch(
                    "cloudexit.cli.app.test_permissions",
                    return_value=(True, True, True, "ok"),
                ),
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.generate_report",
                    return_value={"success": False, "logs": "render error"},
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.REPORT)

    def test_unexpected_exception_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            with (
                patch(
                    "cloudexit.cli.app.validate_config",
                    side_effect=RuntimeError("boom"),
                ),
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(VALID_CONFIG.copy(), "aws")
        self.assertEqual(ctx.exception.code, codes.UNEXPECTED)

    def test_unexpected_error_writes_traceback_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = os.path.join(tmp_dir, "raw")
            os.makedirs(raw_data_path, exist_ok=True)
            with self.assertRaises(SystemExit) as ctx:
                with (
                    patch("cloudexit.cli.app.validate_config"),
                    patch(
                        "cloudexit.cli.app.create_directory",
                        return_value=(tmp_dir, raw_data_path),
                    ),
                    patch(
                        "cloudexit.cli.app.verify_credentials",
                        side_effect=RuntimeError("boom-unexpected"),
                    ),
                    patch("cloudexit.cli.app.print_step"),
                    patch("cloudexit.cli.app.console.print"),
                ):
                    app.run_assessment(VALID_CONFIG.copy(), "aws")

            self.assertEqual(ctx.exception.code, codes.UNEXPECTED)
            logs = [f for f in os.listdir(tmp_dir) if f.startswith("error-")]
            self.assertEqual(len(logs), 1)
            body = Path(tmp_dir, logs[0]).read_text(encoding="utf-8")
            self.assertIn("Traceback", body)
            self.assertIn("boom-unexpected", body)

    def test_full_success_exits_0(self):
        with (
            patch("cloudexit.cli.app.validate_config"),
            patch(
                "cloudexit.cli.app.create_directory",
                return_value=("/tmp/r", "/tmp/r/raw"),
            ),
            patch("cloudexit.cli.app.verify_credentials", return_value=(True, "ok")),
            patch(
                "cloudexit.cli.app.test_permissions",
                return_value=(True, True, True, "ok"),
            ),
            patch(
                "cloudexit.cli.app.create_resource_inventory",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "cloudexit.cli.app.create_cost_inventory",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "cloudexit.cli.app.perform_risk_assessment",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "cloudexit.cli.app.generate_report",
                return_value={"success": True, "reports": {}},
            ),
            patch("cloudexit.cli.app.print_step"),
            patch("cloudexit.cli.app.console.print"),
        ):
            result = app.run_assessment(VALID_CONFIG.copy(), "aws")

        self.assertIsNone(result)

    def test_dry_run_writes_the_sync_document_generates_report_and_skips_sync(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = os.path.join(tmp_dir, "raw_data")
            os.makedirs(raw_data_path, exist_ok=True)
            config = VALID_CONFIG.copy()
            config["assessmentType"] = 2
            output = SyncOutput(
                Path(raw_data_path) / "sync.json", 10, "0" * 64, None, "summary", {}
            )

            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=(tmp_dir, raw_data_path),
                ),
                patch(
                    "cloudexit.cli.app.verify_credentials", return_value=(True, "ok")
                ),
                patch(
                    "cloudexit.cli.app.test_permissions",
                    return_value=(True, True, True, "ok"),
                ),
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.prepare_sync_document", return_value=output
                ) as mock_write,
                patch(
                    "cloudexit.cli.app.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ) as mock_risk,
                patch(
                    "cloudexit.cli.app.generate_report",
                    return_value={
                        "success": True,
                        "reports": {
                            "HTML": f"{tmp_dir}/index.html",
                            "PDF": f"{tmp_dir}/report.pdf",
                        },
                    },
                ) as mock_report,
                patch("cloudexit.cli.app.upload") as mock_upload,
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(config, "aws", dry_run=True)

            report_path, context = mock_write.call_args.args
            self.assertEqual(report_path, tmp_dir)
            self.assertEqual(context.assessment_type, 2)
            self.assertEqual(context.cloud_service_provider, 2)
            self.assertFalse(context.egress_collected)
            self.assertEqual(context.egress_names, "plain")
            self.assertIsNone(mock_write.call_args.kwargs["emit_dir"])
            mock_risk.assert_called_once_with(
                exit_strategy=config["exitStrategy"], report_path=tmp_dir
            )
            mock_report.assert_called_once()
            mock_upload.assert_not_called()

    def test_dry_run_flag_passed_from_handle_aws(self):
        with (
            patch.dict(os.environ, NonInteractiveAWSTests._BASE_ENV, clear=False),
            patch("cloudexit.cli.app.validate_region"),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(_ni_aws_args(dry_run=True))

        mock_run.assert_called_once_with(
            ANY,
            "aws",
            dry_run=True,
            non_interactive=True,
            egress=False,
            settings=None,
            emit_payload=None,
            egress_names="plain",
            spool_on_error=False,
            flush_spool=True,
            strict=False,
        )


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
            patch("cloudexit.cli.app.validate_region"),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(_ni_aws_args())

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
            patch("cloudexit.cli.app.validate_region"),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(_ni_aws_args())

        config_arg = mock_run.call_args[0][0]
        self.assertEqual(
            config_arg["providerDetails"]["sessionToken"], "sts-session-token"
        )

    def test_missing_exit_strategy_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "ESC_EXIT_STRATEGY"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("cloudexit.cli.app.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.handle_aws(_ni_aws_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_invalid_exit_strategy_exits_config(self):
        for value in ("4", "9"):
            with self.subTest(value=value):
                env = {**self._BASE_ENV, "ESC_EXIT_STRATEGY": value}
                with (
                    patch.dict(os.environ, env, clear=False),
                    patch("cloudexit.cli.app.console.print"),
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        app.handle_aws(_ni_aws_args())
                self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_the_hybrid_exit_strategy_reaches_the_assessment(self):
        env = {**self._BASE_ENV, "ESC_EXIT_STRATEGY": "2"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("cloudexit.cli.app.validate_region"),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(_ni_aws_args())

        self.assertEqual(mock_run.call_args[0][0]["exitStrategy"], 2)

    def test_missing_aws_region_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "AWS_DEFAULT_REGION"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("cloudexit.cli.app.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.handle_aws(_ni_aws_args())
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
            patch(
                "azure.identity.ClientSecretCredential", return_value=mock_credential
            ),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_azure(_ni_azure_args())

        mock_run.assert_called_once()
        config_arg = mock_run.call_args[0][0]
        self.assertEqual(config_arg["exitStrategy"], 1)
        self.assertEqual(config_arg["providerDetails"]["tenantId"], "tenant-id-123")
        self.assertEqual(config_arg["providerDetails"]["subscriptionId"], "sub-id-789")
        self.assertEqual(config_arg["providerDetails"]["resourceGroupName"], "my-rg")

    def test_the_hybrid_exit_strategy_reaches_the_assessment(self):
        env = {**self._BASE_ENV, "ESC_EXIT_STRATEGY": "2"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("azure.identity.ClientSecretCredential", return_value=MagicMock()),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_azure(_ni_azure_args())

        self.assertEqual(mock_run.call_args[0][0]["exitStrategy"], 2)

    def test_uses_default_credential_when_client_secret_missing(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "AZURE_CLIENT_SECRET"}
        mock_credential = MagicMock()
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "azure.identity.DefaultAzureCredential", return_value=mock_credential
            ),
            patch("azure.identity.ClientSecretCredential") as mock_client_secret_cred,
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_azure(_ni_azure_args())

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
            patch("cloudexit.cli.app.console.print"),
        ):
            with self.assertRaises(app.ConfigError):
                app.handle_azure(_ni_azure_args())

    def test_missing_subscription_id_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "ESC_SUBSCRIPTION_ID"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("cloudexit.cli.app.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.handle_azure(_ni_azure_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_missing_resource_group_exits_config(self):
        env = {k: v for k, v in self._BASE_ENV.items() if k != "ESC_RESOURCE_GROUP"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("cloudexit.cli.app.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.handle_azure(_ni_azure_args())
        self.assertEqual(ctx.exception.code, codes.CONFIG)


class EgressStageTests(unittest.TestCase):
    _ESTIMATE_OK = {
        "success": True,
        "logs": "",
        "json_path": "/tmp/report/raw/egress_inventory_raw_data.json",
    }
    # The egress PDF is written with the report, from the same context
    _WITH_PDF = {
        "success": True,
        "reports": {
            "HTML": "/tmp/report/index.html",
            "EGRESS_PDF": "/tmp/report/egress.pdf",
        },
    }
    _PDF_FAILED = {
        "success": True,
        "reports": {"HTML": "/tmp/report/index.html"},
        "egress_pdf_logs": "reportlab error",
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

    def _run(self, config, provider, report=None, estimate=None, **kwargs):
        patches = _base_patches()
        for p in patches:
            p.start()
        mocks = {
            "report": patch(
                "cloudexit.cli.app.generate_report",
                return_value=report or self._WITH_PDF,
            ).start(),
            "estimate": patch(
                "cloudexit.cli.app.estimate_egress",
                return_value=estimate or self._ESTIMATE_OK,
            ).start(),
            "print": patch("cloudexit.cli.app.console.print").start(),
            "step": patch("cloudexit.cli.app.print_step").start(),
        }
        try:
            app.run_assessment(config, provider, **kwargs)
        finally:
            patch.stopall()
        return mocks

    def _steps(self, mocks):
        return [call.args[0] for call in mocks["step"].call_args_list]

    def _printed(self, mocks):
        return [str(call) for call in mocks["print"].call_args_list]

    def test_egress_module_not_invoked_without_flag(self):
        mocks = self._run(self._azure_config(), "azure")

        mocks["estimate"].assert_not_called()
        self.assertIsNone(mocks["report"].call_args.kwargs["egress_json_path"])

    def test_the_estimate_comes_before_the_report_and_goes_into_it(self):
        manager = MagicMock()
        patches = _base_patches()
        for p in patches:
            p.start()
        mock_report = patch(
            "cloudexit.cli.app.generate_report", return_value=self._WITH_PDF
        ).start()
        mock_estimate = patch(
            "cloudexit.cli.app.estimate_egress", return_value=self._ESTIMATE_OK
        ).start()
        manager.attach_mock(mock_report, "generate_report")
        manager.attach_mock(mock_estimate, "estimate_egress")
        config = self._azure_config()
        try:
            app.run_assessment(config, "azure", egress=True)
        finally:
            patch.stopall()

        mock_estimate.assert_called_once_with(
            1,
            config["providerDetails"],
            "/tmp/report/raw",
            report_path="/tmp/report",
            name=config["name"],
            exit_strategy=config["exitStrategy"],
            assessment_type=config["assessmentType"],
        )
        self.assertEqual(
            mock_report.call_args.kwargs["egress_json_path"],
            "/tmp/report/raw/egress_inventory_raw_data.json",
        )
        call_names = [name for name, _, _ in manager.mock_calls]
        self.assertLess(
            call_names.index("estimate_egress"), call_names.index("generate_report")
        )

    def test_there_is_no_separate_egress_report_step_any_more(self):
        mocks = self._run(self._azure_config(), "azure", egress=True)

        steps = self._steps(mocks)
        self.assertNotIn("Generating egress report...", steps)
        self.assertIn("Estimating egress data...", steps)
        self.assertIn("Generating egress PDF...", steps)
        printed = self._printed(mocks)
        self.assertFalse(any("Egress Report:" in line for line in printed))
        self.assertFalse(any("egress.html" in line for line in printed))

    def test_the_egress_pdf_path_is_listed(self):
        mocks = self._run(self._azure_config(), "azure", egress=True)

        printed = self._printed(mocks)
        self.assertTrue(
            any("Egress PDF: /tmp/report/egress.pdf" in line for line in printed)
        )
        self.assertTrue(any("HTML Report:" in line for line in printed))

    def test_egress_failure_still_prints_outputs_and_exits_egress(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        patch(
            "cloudexit.cli.app.estimate_egress",
            return_value={"success": False, "logs": "metrics api error"},
        ).start()
        report = patch("cloudexit.cli.app.generate_report").start()
        report.return_value = {"success": True, "reports": {"HTML": "/tmp/index.html"}}
        mock_print = patch("cloudexit.cli.app.console.print").start()
        try:
            with self.assertRaises(SystemExit) as ctx:
                app.run_assessment(self._azure_config(), "azure", egress=True)
        finally:
            patch.stopall()

        self.assertEqual(ctx.exception.code, codes.EGRESS)
        printed = [str(call) for call in mock_print.call_args_list]
        self.assertTrue(any("Outputs:" in line for line in printed))
        self.assertIsNone(report.call_args.kwargs["egress_json_path"])

    def test_egress_pdf_failure_still_prints_outputs_and_exits_egress(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        patch(
            "cloudexit.cli.app.estimate_egress", return_value=self._ESTIMATE_OK
        ).start()
        patch(
            "cloudexit.cli.app.generate_report", return_value=self._PDF_FAILED
        ).start()
        mock_step = patch("cloudexit.cli.app.print_step").start()
        mock_print = patch("cloudexit.cli.app.console.print").start()
        try:
            with self.assertRaises(SystemExit) as ctx:
                app.run_assessment(self._azure_config(), "azure", egress=True)
        finally:
            patch.stopall()

        # The Executive Summary was written; only the egress exit code is set
        self.assertEqual(ctx.exception.code, codes.EGRESS)
        printed = [str(call) for call in mock_print.call_args_list]
        self.assertTrue(any("Outputs:" in line for line in printed))
        self.assertTrue(any("HTML Report:" in line for line in printed))
        self.assertFalse(any("Egress PDF:" in line for line in printed))
        failed = [
            call
            for call in mock_step.call_args_list
            if call.args[:1] == ("Generating egress PDF...",)
        ]
        self.assertEqual(failed[0].kwargs["status"], "error")
        self.assertEqual(failed[0].kwargs["logs"], "reportlab error")

    def test_a_failed_report_is_still_a_report_failure(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                self._azure_config(),
                "azure",
                report={"success": False, "logs": "boom"},
                egress=True,
            )

        self.assertEqual(ctx.exception.code, codes.REPORT)

    def test_egress_invoked_for_aws_with_provider_code(self):
        patches = _base_patches()
        for p in patches:
            p.start()
        mock_estimate = patch(
            "cloudexit.cli.app.estimate_egress", return_value=self._ESTIMATE_OK
        ).start()
        patch("cloudexit.cli.app.generate_report", return_value=self._WITH_PDF).start()
        config = VALID_CONFIG.copy()
        try:
            app.run_assessment(config, "aws", egress=True)
        finally:
            patch.stopall()

        mock_estimate.assert_called_once_with(
            2,
            config["providerDetails"],
            "/tmp/report/raw",
            report_path="/tmp/report",
            name=config["name"],
            exit_strategy=config["exitStrategy"],
            assessment_type=config["assessmentType"],
        )

    def test_handle_azure_passes_egress_flag(self):
        mock_credential = MagicMock()
        with (
            patch.dict(os.environ, NonInteractiveAzureTests._BASE_ENV, clear=False),
            patch(
                "azure.identity.ClientSecretCredential", return_value=mock_credential
            ),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_azure(_ni_azure_args(egress=True))

        mock_run.assert_called_once_with(
            ANY,
            "azure",
            dry_run=False,
            non_interactive=True,
            egress=True,
            settings=None,
            emit_payload=None,
            egress_names="plain",
            spool_on_error=False,
            flush_spool=True,
            strict=False,
        )

    def test_handle_aws_passes_egress_flag(self):
        with (
            patch.dict(os.environ, NonInteractiveAWSTests._BASE_ENV, clear=False),
            patch("cloudexit.cli.app.validate_region"),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(_ni_aws_args(egress=True))

        mock_run.assert_called_once_with(
            ANY,
            "aws",
            dry_run=False,
            non_interactive=True,
            egress=True,
            settings=None,
            emit_payload=None,
            egress_names="plain",
            spool_on_error=False,
            flush_spool=True,
            strict=False,
        )


class MainExitCodeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("cloudexit.cli.app.load_settings", return_value=Settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_config_error_from_handler_exits_config(self):
        with (
            patch("cloudexit.cli.app.prepare_catalogue"),
            patch("cloudexit.cli.app.console.print"),
            patch(
                "cloudexit.cli.app.parse_arguments",
                return_value=Namespace(cloud_provider="aws"),
            ),
            patch("cloudexit.cli.app.handle_aws", side_effect=app.ConfigError),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.main()
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_ctrl_c_in_handler_exits_interrupted(self):
        with (
            patch("cloudexit.cli.app.prepare_catalogue"),
            patch("cloudexit.cli.app.console.print"),
            patch(
                "cloudexit.cli.app.parse_arguments",
                return_value=Namespace(cloud_provider="aws"),
            ),
            patch("cloudexit.cli.app.handle_aws", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.main()
        self.assertEqual(ctx.exception.code, codes.INTERRUPTED)
        self.assertEqual(codes.INTERRUPTED, 130)

    def test_ctrl_c_during_dataset_download_exits_interrupted(self):
        with (
            patch("cloudexit.cli.app.prepare_catalogue", side_effect=KeyboardInterrupt),
            patch("cloudexit.cli.app.console.print"),
            patch(
                "cloudexit.cli.app.parse_arguments",
                return_value=Namespace(cloud_provider="aws"),
            ),
            patch("cloudexit.cli.app.handle_aws") as mock_handle,
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.main()
        self.assertEqual(ctx.exception.code, codes.INTERRUPTED)
        mock_handle.assert_not_called()

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
            patch("cloudexit.cli.app.prepare_catalogue"),
            patch("cloudexit.cli.app.console.print"),
            patch("cloudexit.cli.app.parse_arguments", return_value=args),
            patch("cloudexit.cli.app.load_config", return_value=None),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.main()
        self.assertEqual(ctx.exception.code, codes.CONFIG)
        mock_run.assert_not_called()


TFSTATE_CONFIG = {
    "name": "Tfstate Assessment",
    "cloudServiceProvider": 2,
    "exitStrategy": 1,
    "assessmentType": 1,
    "providerDetails": {"tfstatePath": "config/aws-01.tfstate"},
}


class TfstateCliTests(unittest.TestCase):
    _ENV = {"ESC_EXIT_STRATEGY": "3", "ESC_ASSESSMENT_TYPE": "1"}

    def test_egress_with_tfstate_exits_config_for_aws(self):
        with patch("cloudexit.cli.app.console.print"):
            with self.assertRaises(SystemExit) as ctx:
                app.handle_aws(_ni_aws_args(tfstate="infra.tfstate", egress=True))
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_egress_with_tfstate_exits_config_for_azure(self):
        with patch("cloudexit.cli.app.console.print"):
            with self.assertRaises(SystemExit) as ctx:
                app.handle_azure(_ni_azure_args(tfstate="infra.tfstate", egress=True))
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_aws_non_interactive_needs_no_aws_env_vars(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AWS_") and not k.startswith("ESC_")
        }
        env.update(self._ENV)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_aws(_ni_aws_args(tfstate="infra.tfstate"))

        mock_run.assert_called_once()
        config_arg = mock_run.call_args[0][0]
        self.assertEqual(config_arg["exitStrategy"], 3)
        self.assertEqual(config_arg["assessmentType"], 1)
        self.assertEqual(
            config_arg["providerDetails"], {"tfstatePath": "infra.tfstate"}
        )

    def test_azure_non_interactive_needs_no_azure_env_vars(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AZURE_") and not k.startswith("ESC_")
        }
        env.update(self._ENV)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("cloudexit.cli.app.run_assessment") as mock_run,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.handle_azure(_ni_azure_args(tfstate="infra.tfstate"))

        mock_run.assert_called_once()
        config_arg = mock_run.call_args[0][0]
        self.assertEqual(config_arg["cloudServiceProvider"], 1)
        self.assertEqual(config_arg["exitStrategy"], 3)
        self.assertEqual(
            config_arg["providerDetails"], {"tfstatePath": "infra.tfstate"}
        )

    def test_config_file_carries_tfstate_path_through_handle_aws(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "infra.tfstate"
            state_path.write_text(
                json.dumps({"version": 4, "resources": []}), encoding="utf-8"
            )
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "cloudServiceProvider": 2,
                        "exitStrategy": 1,
                        "assessmentType": 1,
                        "providerDetails": {"tfstatePath": str(state_path)},
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("cloudexit.cli.app.run_assessment") as mock_run,
                patch("cloudexit.cli.app.console.print"),
            ):
                app.handle_aws(_ni_aws_args(config=str(config_path)))

            config_arg = mock_run.call_args[0][0]
            self.assertEqual(
                config_arg["providerDetails"], {"tfstatePath": str(state_path)}
            )
            # The same config must survive validation without credential fields.
            self.assertTrue(app.validate_config(config_arg))

    def test_tfstate_parses_from_both_subcommands(self):
        with patch("sys.argv", ["main.py", "aws", "--tfstate", "infra.tfstate"]):
            args = parse_arguments()
        self.assertEqual(args.tfstate, "infra.tfstate")

        with patch(
            "sys.argv", ["main.py", "azure", "--tfstate", "infra.tfstate", "--dry-run"]
        ):
            args = parse_arguments()
        self.assertEqual(args.tfstate, "infra.tfstate")
        self.assertTrue(args.dry_run)

    def test_tfstate_is_mutually_exclusive_with_config(self):
        with patch(
            "sys.argv", ["main.py", "aws", "--config", "c.json", "--tfstate", "s"]
        ):
            with self.assertRaises(SystemExit):
                parse_arguments()

    def test_run_assessment_skips_credential_permission_and_cost_stages(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = os.path.join(tmp_dir, "raw_data")
            os.makedirs(raw_data_path, exist_ok=True)

            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=(tmp_dir, raw_data_path),
                ),
                patch("cloudexit.cli.app.verify_credentials") as mock_creds,
                patch("cloudexit.cli.app.test_permissions") as mock_perms,
                patch("cloudexit.cli.app.create_cost_inventory") as mock_cost,
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ) as mock_inventory,
                patch(
                    "cloudexit.cli.app.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ) as mock_risk,
                patch(
                    "cloudexit.cli.app.generate_report",
                    return_value={"success": True, "reports": {}},
                ) as mock_report,
                patch("cloudexit.cli.app.print_step"),
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(TFSTATE_CONFIG.copy(), "aws")

        mock_creds.assert_not_called()
        mock_perms.assert_not_called()
        mock_cost.assert_not_called()
        mock_inventory.assert_called_once_with(
            2, {"tfstatePath": "config/aws-01.tfstate"}, tmp_dir, raw_data_path
        )
        mock_risk.assert_called_once()
        mock_report.assert_called_once()

    def _run_tfstate_stage3(self, inventory_result):
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_data_path = os.path.join(tmp_dir, "raw_data")
            os.makedirs(raw_data_path, exist_ok=True)

            with (
                patch("cloudexit.cli.app.validate_config"),
                patch(
                    "cloudexit.cli.app.create_directory",
                    return_value=(tmp_dir, raw_data_path),
                ),
                patch(
                    "cloudexit.cli.app.create_resource_inventory",
                    return_value=inventory_result,
                ),
                patch(
                    "cloudexit.cli.app.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "cloudexit.cli.app.generate_report",
                    return_value={"success": True, "reports": {}},
                ),
                patch("cloudexit.cli.app.print_step") as mock_step,
                patch("cloudexit.cli.app.console.print"),
            ):
                app.run_assessment(TFSTATE_CONFIG.copy(), "aws")

        return [
            call
            for call in mock_step.call_args_list
            if "resource inventory" in call.args[0]
        ]

    def test_excluded_foreign_resources_downgrade_stage3_to_warning(self):
        calls = self._run_tfstate_stage3(
            {
                "success": True,
                "logs": "",
                "coverage": {
                    "instances_total": 31,
                    "instances_counted": 3,
                    "instances_excluded_other_provider": 27,
                    "instances_excluded_unmapped": 1,
                },
            }
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["status"], "warning")
        logs = calls[0].kwargs["logs"]
        self.assertIn("Assessed 3 of 31 resources", logs)
        self.assertIn("27 excluded", logs)

    def test_clean_tfstate_run_keeps_stage3_ok(self):
        calls = self._run_tfstate_stage3(
            {
                "success": True,
                "logs": "",
                "coverage": {
                    "instances_total": 8,
                    "instances_counted": 5,
                    "instances_excluded_other_provider": 0,
                    "instances_excluded_unmapped": 3,
                },
            }
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["status"], "ok")

    def test_live_mode_result_without_coverage_keeps_stage3_ok(self):
        calls = self._run_tfstate_stage3({"success": True, "logs": ""})

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["status"], "ok")

    def test_run_assessment_rejects_egress_from_tfstate_config_file(self):
        with (
            patch("cloudexit.cli.app.validate_config"),
            patch("cloudexit.cli.app.create_directory") as mock_dir,
            patch("cloudexit.cli.app.print_step"),
            patch("cloudexit.cli.app.console.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.run_assessment(TFSTATE_CONFIG.copy(), "aws", egress=True)

        self.assertEqual(ctx.exception.code, codes.CONFIG)
        mock_dir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
