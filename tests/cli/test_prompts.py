import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudexit.cli import app
from tests.support.isolation import isolated_environ
from tests.make_smoke_dataset import main as make_smoke_dataset
from cloudexit import codes
from cloudexit.providers.azure.cli_session import (
    select_resource_group,
    select_subscription,
)
from cloudexit.cli.prompts import prompt_required_inputs
from cloudexit.terminal import NO_TERMINAL_MESSAGE, ask
from tests.support.paths import ROOT

SMOKE_STATE = ROOT / "tests" / "fixtures" / "smoke" / "aws.tfstate"


class Entry:
    display_name = "Demo"
    subscription_id = "00000000-0000-0000-0000-000000000000"
    name = "rg"


class AskTests(unittest.TestCase):
    def test_an_answer_is_returned(self):
        with patch("builtins.input", return_value="1") as asked:
            self.assertEqual(ask("Question: "), "1")
        asked.assert_called_once_with("Question: ")

    def test_a_secret_is_read_without_echo(self):
        with patch(
            "cloudexit.terminal.getpass.getpass", return_value="s3cret"
        ) as asked:
            self.assertEqual(ask("Secret: ", secret=True), "s3cret")
        asked.assert_called_once_with("Secret: ")

    def test_no_terminal_ends_with_the_message_and_the_configuration_code(self):
        for secret in (False, True):
            with self.subTest(secret=secret):
                with (
                    patch("builtins.input", side_effect=EOFError),
                    patch("cloudexit.terminal.getpass.getpass", side_effect=EOFError),
                    patch("cloudexit.terminal.console.print") as printed,
                    self.assertRaises(SystemExit) as ctx,
                ):
                    ask("Question: ", secret=secret)

                self.assertEqual(ctx.exception.code, codes.CONFIG)
                self.assertIn(NO_TERMINAL_MESSAGE, str(printed.call_args_list))

    def test_the_message_says_what_to_do(self):
        self.assertIn("--non-interactive", NO_TERMINAL_MESSAGE)
        self.assertIn("ESC_EXIT_STRATEGY", NO_TERMINAL_MESSAGE)
        self.assertIn("ESC_ASSESSMENT_TYPE", NO_TERMINAL_MESSAGE)
        self.assertIn("no terminal is attached", NO_TERMINAL_MESSAGE)

    def test_ctrl_c_is_not_swallowed(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                ask("Question: ")


class EveryPromptTests(unittest.TestCase):
    def assert_ends_with_the_message(self, call):
        with (
            patch("builtins.input", side_effect=EOFError),
            patch("cloudexit.terminal.getpass.getpass", side_effect=EOFError),
            patch("cloudexit.terminal.console.print") as printed,
            patch("cloudexit.cli.app.console.print"),
            patch("cloudexit.providers.azure.cli_session.console.print"),
            self.assertRaises(SystemExit) as ctx,
        ):
            call()
        self.assertEqual(ctx.exception.code, codes.CONFIG)
        self.assertIn(NO_TERMINAL_MESSAGE, str(printed.call_args_list))

    def test_the_exit_strategy_and_assessment_type_prompts(self):
        self.assert_ends_with_the_message(prompt_required_inputs)

    def test_the_assessment_type_prompt_after_an_answer(self):
        def run():
            with patch("builtins.input", side_effect=["1", EOFError]):
                prompt_required_inputs()

        with (
            patch("cloudexit.terminal.console.print") as printed,
            self.assertRaises(SystemExit) as ctx,
        ):
            run()
        self.assertEqual(ctx.exception.code, codes.CONFIG)
        self.assertIn(NO_TERMINAL_MESSAGE, str(printed.call_args_list))

    def test_the_subscription_and_resource_group_selection(self):
        self.assert_ends_with_the_message(lambda: select_subscription([Entry()]))
        self.assert_ends_with_the_message(lambda: select_resource_group([Entry()]))

    def test_the_manual_aws_credentials(self):
        self.assert_ends_with_the_message(app._aws_provider_from_prompt)

    def test_the_manual_aws_secret_key(self):
        with (
            patch("builtins.input", return_value="AKIAEXAMPLE"),
            patch("cloudexit.terminal.getpass.getpass", side_effect=EOFError),
            patch("cloudexit.terminal.console.print") as printed,
            self.assertRaises(SystemExit) as ctx,
        ):
            app._aws_provider_from_prompt()
        self.assertEqual(ctx.exception.code, codes.CONFIG)
        self.assertIn(NO_TERMINAL_MESSAGE, str(printed.call_args_list))

    def test_the_manual_azure_credentials(self):
        self.assert_ends_with_the_message(app._azure_provider_from_prompt)

    def test_the_azure_tenant_prompt_of_the_cli_login(self):
        with patch("cloudexit.cli.app._azure_cli_credential"):
            self.assert_ends_with_the_message(app._azure_provider_from_cli)


class WithoutATerminalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.dataset = self.tmp / "smoke.db"
        make_smoke_dataset(str(self.dataset))
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ESC_", "CLOUDEXIT_"))
        }
        self.env.update({**isolated_environ(self.tmp), "COLUMNS": "200"})

    def run_cli(self, stdin, *extra):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "main.py"),
                "aws",
                "--tfstate",
                str(SMOKE_STATE),
                "--catalogue",
                str(self.dataset),
                "--dry-run",
                *extra,
            ],
            cwd=self.tmp,
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode, " ".join((result.stdout + result.stderr).split())

    def test_a_closed_stdin_ends_with_the_message_and_no_traceback(self):
        with open(os.devnull) as closed:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "main.py"),
                    "aws",
                    "--tfstate",
                    str(SMOKE_STATE),
                    "--catalogue",
                    str(self.dataset),
                ],
                cwd=self.tmp,
                env=self.env,
                stdin=closed,
                capture_output=True,
                text=True,
                timeout=120,
            )

        output = " ".join((result.stdout + result.stderr).split())
        self.assertEqual(result.returncode, codes.CONFIG, output)
        self.assertIn(NO_TERMINAL_MESSAGE, output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("EOFError", output)

    def test_answers_piped_in_still_work(self):
        code, output = self.run_cli("1\n1\n")

        self.assertEqual(code, 0, output)
        self.assertNotIn("no terminal is attached", output)
        self.assertTrue(list(self.tmp.glob("reports/*/index.html")))

    def test_non_interactive_needs_no_terminal(self):
        self.env["ESC_EXIT_STRATEGY"] = "1"
        self.env["ESC_ASSESSMENT_TYPE"] = "1"
        code, output = self.run_cli("", "--non-interactive")

        self.assertEqual(code, 0, output)

    def test_the_stdin_helper_works_with_an_in_memory_stream(self):
        with patch("sys.stdin", io.StringIO("1\n3\n")):
            self.assertEqual(ask("x"), "1")
            self.assertEqual(ask("y"), "3")
            with (
                patch("cloudexit.terminal.console.print"),
                self.assertRaises(SystemExit),
            ):
                ask("z")


if __name__ == "__main__":
    unittest.main()
