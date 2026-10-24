import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest import mock

import main
from tests.support.isolation import isolated_environ
from tests.make_smoke_dataset import main as make_smoke_dataset
from tests.support.paths import ROOT
from cloudexit import codes
from cloudexit.providers.azure import sdk as azure_utils
from cloudexit.providers.azure.sdk import AzureSdkError, azure_import, require_azure_sdk

SMOKE_STATE = ROOT / "tests" / "fixtures" / "smoke" / "aws.tfstate"

DRIVER = """
import json, runpy, sys
root, mode, *argv = sys.argv[1:]
sys.path.insert(0, root)
if mode == "broken":
    sys.modules["azure"] = None
sys.argv = ["cloudexit", *argv]
code = 0
try:
    runpy.run_module("main", run_name="__main__")
except SystemExit as exc:
    code = exc.code or 0
loaded = sorted(
    name
    for name, module in sys.modules.items()
    if (name == "azure" or name.startswith("azure.")) and module is not None
)
print("AZURE_MODULES=" + json.dumps(loaded))
sys.exit(code)
"""


class SubprocessTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.env = {
            **os.environ,
            **isolated_environ(self.tmp),
            "CLOUDEXIT_CATALOGUE_URL": "http://127.0.0.1:9",
            "ESC_EXIT_STRATEGY": "1",
            "ESC_ASSESSMENT_TYPE": "1",
            "COLUMNS": "200",
        }

    def dataset(self):
        path = self.tmp / "smoke.db"
        if not path.exists():
            make_smoke_dataset(str(path))
        return str(path)

    def run_cli(self, mode, *argv):
        result = subprocess.run(
            [sys.executable, "-c", DRIVER, str(ROOT), mode, *argv],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        match = re.search(r"AZURE_MODULES=(\[.*\])", result.stdout)
        loaded = json.loads(match.group(1)) if match else None
        return result.returncode, loaded, " ".join(output.split())


class NonAzureCommandsTests(SubprocessTestCase):
    COMMANDS = (
        ("--version",),
        ("config", "path"),
        ("catalogue", "status"),
    )

    def test_no_azure_module_is_imported_for_commands_that_never_use_azure(self):
        for argv in self.COMMANDS:
            with self.subTest(argv=" ".join(argv)):
                code, loaded, output = self.run_cli("open", *argv)
                self.assertEqual(code, 0, output)
                self.assertEqual(loaded, [])

    def test_a_broken_azure_sdk_does_not_stop_those_commands(self):
        for argv in self.COMMANDS:
            with self.subTest(argv=" ".join(argv)):
                code, loaded, output = self.run_cli("broken", *argv)
                self.assertEqual(code, 0, output)
                self.assertEqual(loaded, [])
                self.assertNotIn("Traceback", output)

    def test_an_offline_aws_run_needs_no_azure_sdk(self):
        argv = (
            "aws",
            "--tfstate",
            str(SMOKE_STATE),
            "--catalogue",
            self.dataset(),
            "--non-interactive",
            "--dry-run",
        )
        for mode in ("open", "broken"):
            with self.subTest(mode=mode):
                code, loaded, output = self.run_cli(mode, *argv)
                self.assertEqual(code, 0, output)
                self.assertEqual(loaded, [])
                self.assertTrue(list(self.work.glob("reports/*/index.html")))


class BrokenSdkOnAzurePathsTests(SubprocessTestCase):
    def assert_guard_message(self, code, output):
        self.assertEqual(code, codes.CONFIG, output)
        self.assertNotIn("Traceback", output)
        self.assertIn("The Azure SDK in this environment is incomplete", output)
        self.assertRegex(
            output, r"pip install --force-reinstall --no-deps azure-[a-z-]+"
        )
        self.assertIn("or install cloudexit into a new virtual environment", output)

    def test_a_live_azure_run_stops_with_the_repair_message_before_any_prompt(self):
        for argv in (
            ("azure", "--non-interactive"),
            ("azure", "--non-interactive", "--cli"),
        ):
            with self.subTest(argv=" ".join(argv)):
                code, _, output = self.run_cli(
                    "broken", *argv, "--catalogue", self.dataset()
                )
                self.assert_guard_message(code, output)

    def test_a_config_file_run_stops_after_the_configuration_is_validated(self):
        config = self.work / "azure.json"
        shutil.copy(ROOT / "config" / "azure_example.json", config)

        code, _, output = self.run_cli(
            "broken",
            "azure",
            "--config",
            str(config),
            "--non-interactive",
            "--catalogue",
            self.dataset(),
        )

        self.assert_guard_message(code, output)
        self.assertIn("Configuration successfully validated", output)
        self.assertFalse(list(self.work.glob("reports/*")))


class AzureImportTests(unittest.TestCase):
    def test_the_symbols_the_azure_paths_use_import_in_a_complete_environment(self):
        require_azure_sdk()
        self.assertEqual(
            azure_import("azure.identity", "ClientSecretCredential").__name__,
            "ClientSecretCredential",
        )

    def test_a_missing_symbol_names_what_could_not_be_imported(self):
        with self.assertRaises(AzureSdkError) as ctx:
            azure_import("azure.mgmt.resource.resources", "NoSuchClient")

        self.assertEqual(ctx.exception.module, "azure.mgmt.resource.resources")
        self.assertIn(
            "cannot import azure.mgmt.resource.resources.NoSuchClient",
            str(ctx.exception),
        )
        self.assertIsNone(ctx.exception.__cause__)

    def test_a_missing_package_is_the_same_error(self):
        with mock.patch.dict(sys.modules, {"azure.mgmt.subscription": None}):
            with self.assertRaises(AzureSdkError):
                azure_import("azure.mgmt.subscription", "SubscriptionClient")

    def test_the_repair_command_names_the_package_and_the_pinned_version(self):
        pins = [
            "azure-identity==1.26.0",
            "azure-mgmt-resource==26.0.0",
            "azure-mgmt-subscription==3.1.1",
            "boto3==1.43.118",
            "black ; extra == 'dev'",
        ]
        cases = {
            "azure.identity": "azure-identity==1.26.0",
            "azure.mgmt.resource.resources": "azure-mgmt-resource==26.0.0",
            "azure.mgmt.subscription": "azure-mgmt-subscription==3.1.1",
            "azure.core.exceptions": "azure-core",
            "azure.mgmt.costmanagement.models": "azure-mgmt-costmanagement",
        }
        with mock.patch.object(azure_utils, "requires", return_value=pins):
            for module, expected in cases.items():
                with self.subTest(module=module):
                    message = str(AzureSdkError(module, "Thing"))
                    self.assertIn(f"--force-reinstall --no-deps {expected},", message)

    def test_without_an_installed_cloudexit_the_package_name_alone_is_given(self):
        with mock.patch.object(
            azure_utils, "requires", side_effect=PackageNotFoundError("cloudexit")
        ):
            message = str(
                AzureSdkError("azure.mgmt.subscription", "SubscriptionClient")
            )
        self.assertIn("--no-deps azure-mgmt-subscription,", message)

    def test_every_symbol_has_a_distribution_the_message_can_name(self):
        for module, _ in azure_utils.SDK_SYMBOLS:
            with self.subTest(module=module):
                self.assertTrue(
                    any(
                        module == prefix or module.startswith(f"{prefix}.")
                        for prefix in azure_utils.SDK_DISTRIBUTIONS
                    )
                )


class GuardPlacementTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "tfstate": None,
            "config": None,
            "cli": False,
            "non_interactive": True,
            "name": None,
            "egress": False,
            "dry_run": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_handle(self, args):
        calls = []
        with (
            mock.patch(
                "main._require_azure_sdk", side_effect=lambda: calls.append("sdk")
            ),
            mock.patch("main.load_config", return_value={"name": "n"}),
            mock.patch("main.require_env_int", return_value=1),
            mock.patch(
                "main._azure_provider_noninteractive",
                side_effect=lambda a: calls.append("provider") or {},
            ),
            mock.patch("main.build_config", return_value={}),
            mock.patch("main.run_assessment"),
        ):
            main.handle_azure(args)
        return calls

    def test_a_live_run_checks_the_sdk_before_it_asks_for_anything(self):
        self.assertEqual(self.run_handle(self.args()), ["sdk", "provider"])

    def test_a_terraform_state_run_does_not_need_the_sdk(self):
        self.assertEqual(self.run_handle(self.args(tfstate="state.tfstate")), [])

    def test_a_config_file_run_is_checked_when_the_run_starts(self):
        self.assertEqual(self.run_handle(self.args(config="azure.json")), [])


class RunAssessmentGuardTests(unittest.TestCase):
    CONFIG = {
        "name": "Guard",
        "cloudServiceProvider": 1,
        "exitStrategy": 1,
        "assessmentType": 1,
        "providerDetails": {
            "tenantId": "t",
            "clientId": "c",
            "clientSecret": "s",
            "subscriptionId": "00000000-0000-0000-0000-000000000000",
            "resourceGroupName": "rg",
        },
    }

    def run_with(self, config, error=None):
        with (
            mock.patch("main.validate_config"),
            mock.patch("main.require_azure_sdk", side_effect=error) as guard,
            mock.patch("main.console.print") as printed,
            mock.patch("main.print_step"),
            mock.patch(
                "main.create_directory", side_effect=RuntimeError("stop")
            ) as made,
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.run_assessment(config, "azure")
        return ctx.exception.code, guard, printed, made

    def test_an_incomplete_sdk_ends_the_run_with_the_configuration_code(self):
        error = AzureSdkError("azure.identity", "ClientSecretCredential")
        code, _, printed, made = self.run_with(dict(self.CONFIG), error)

        self.assertEqual(code, codes.CONFIG)
        made.assert_not_called()
        self.assertIn("incomplete", str(printed.call_args_list))

    def test_a_terraform_state_run_never_asks_for_the_sdk(self):
        config = dict(self.CONFIG, providerDetails={"tfstatePath": "state.tfstate"})
        code, guard, _, made = self.run_with(config)

        guard.assert_not_called()
        made.assert_called_once()
        self.assertEqual(code, codes.CONFIG)

    def test_an_aws_run_never_asks_for_the_sdk(self):
        config = dict(self.CONFIG, cloudServiceProvider=2)
        _, guard, _, _ = self.run_with(config)
        guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
