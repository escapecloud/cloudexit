import ast
import importlib
import logging
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from cloudexit.cli import app
from cloudexit.log_redaction import redact
from tests.support.paths import ROOT

PACKAGE = ROOT / "cloudexit"
# Loggers of other libraries that the package sets a level on
THIRD_PARTY = (
    "botocore",
    "botocore.auth",
    "botocore.credentials",
    "botocore.endpoint",
    "boto3",
    "azure",
    "urllib3",
    "msal",
    "PIL",
)

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SIGNATURE = "deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef0123"
TOKEN = "FwoGZXIvYXdzEExAMPLESESSIONTOKEN"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


class PreparedRequest:
    # botocore reaches the logger with the request object as a %s argument, so
    # the secret is not in the format string
    def __repr__(self):
        return (
            "<AWSPreparedRequest method=POST, "
            "url=https://ec2.eu-west-1.amazonaws.com/, headers={"
            f"'X-Amz-Security-Token': b'{TOKEN}', "
            "'Authorization': b'AWS4-HMAC-SHA256 "
            f"Credential={ACCESS_KEY}/20261025/eu-west-1/ec2/aws4_request, "
            f"SignedHeaders=host;x-amz-date, Signature={SIGNATURE}'}}>"
        )


class LoggingStateMixin:
    def setUp(self):
        root = logging.getLogger()
        saved = (root.level, list(root.handlers))
        levels = {name: logging.getLogger(name).level for name in THIRD_PARTY}

        def restore():
            root.setLevel(saved[0])
            root.handlers[:] = saved[1]
            for name, level in levels.items():
                logging.getLogger(name).setLevel(level)

        self.addCleanup(restore)

    def level(self, name):
        return logging.getLogger(name).getEffectiveLevel()


def module_loggers(path):
    # `logger = logging.getLogger(...)` at module level
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "logger" for t in node.targets)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "logging.getLogger"
        ):
            yield node


class LoggerNameTests(unittest.TestCase):
    def test_every_logger_of_the_package_is_named_after_its_module(self):
        found = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in module_loggers(path):
                if ast.unparse(node.value) != "logging.getLogger(__name__)":
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        self.assertEqual(found, [])

    def test_a_module_logger_carries_the_dotted_path_of_its_module(self):
        for name in (
            "cloudexit.platform.client",
            "cloudexit.providers.aws.inventory",
            "cloudexit.report.egress",
            "cloudexit.catalogue.download",
            "cloudexit.settings",
            "cloudexit.engine",
        ):
            with self.subTest(name):
                module = importlib.import_module(name)
                self.assertEqual(module.logger.name, name)

    def test_the_run_log_names_the_module_that_wrote_each_line(self):
        from cloudexit.platform import client

        with tempfile.TemporaryDirectory() as tmp:
            app.add_run_log_handler(tmp)
            try:
                client.logger.warning("a line for the run log")
            finally:
                app.close_run_log_handler()
            text = (Path(tmp) / "run.log").read_text(encoding="utf-8")
            self.assertIn("cloudexit.platform.client - WARNING - a line", text)
            self.assertNotIn("core.engine", text)


class ThirdPartyLevelTests(LoggingStateMixin, unittest.TestCase):
    def test_msal_is_never_below_warning_because_it_logs_the_client_id(self):
        for verbose in (0, 1, 2, 3):
            with self.subTest(verbose=verbose):
                app.configure_logging(verbose)
                self.assertEqual(self.level("msal"), logging.WARNING)
                self.assertEqual(self.level("msal.telemetry"), logging.WARNING)

    def test_urllib3_shows_connection_lines_only_at_the_deepest_verbosity(self):
        for verbose, expected in (
            (0, logging.WARNING),
            (1, logging.WARNING),
            (2, logging.DEBUG),
        ):
            with self.subTest(verbose=verbose):
                app.configure_logging(verbose)
                self.assertEqual(self.level("urllib3"), expected)

    def test_the_other_libraries_keep_their_levels(self):
        app.configure_logging(0)
        for name in ("botocore", "boto3", "azure", "PIL"):
            self.assertEqual(self.level(name), logging.WARNING, name)
        app.configure_logging(2)
        for name in ("botocore", "boto3", "azure"):
            self.assertEqual(self.level(name), logging.DEBUG, name)
        self.assertEqual(self.level("PIL"), logging.WARNING)

    def test_botocore_signing_and_credentials_are_never_below_warning(self):
        for verbose in (0, 1, 2):
            with self.subTest(verbose=verbose):
                app.configure_logging(verbose)
                self.assertEqual(self.level("botocore.auth"), logging.WARNING)
                self.assertEqual(self.level("botocore.credentials"), logging.WARNING)

    def test_the_endpoint_logger_still_shows_requests_at_the_deepest_verbosity(self):
        app.configure_logging(2)
        self.assertEqual(self.level("botocore.endpoint"), logging.DEBUG)


class CredentialRedactionTests(LoggingStateMixin, unittest.TestCase):
    def emit(self):
        buffer = StringIO()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(app, "console", Console(file=buffer, width=2000)):
            app.configure_logging(2)
            app.add_run_log_handler(tmp.name)
            try:
                endpoint = logging.getLogger("botocore.endpoint")
                endpoint.debug("Sending http request: %s", PreparedRequest())
                endpoint.debug(
                    "Making request with headers Authorization: AWS4-HMAC-SHA256 "
                    f"Credential={ACCESS_KEY}/20261025/eu-west-1/ec2/aws4_request, "
                    f"SignedHeaders=host;x-amz-date, Signature={SIGNATURE}"
                )
                logging.getLogger("botocore.auth").warning(
                    "CanonicalRequest:\nPOST\n/\n\nx-amz-security-token:%s\n", TOKEN
                )
                logging.getLogger("botocore.credentials").warning(
                    "Found credentials: aws_secret_access_key=%s, "
                    "aws_session_token=%s",
                    SECRET,
                    TOKEN,
                )
            finally:
                app.close_run_log_handler()
        run_log = (Path(tmp.name) / "run.log").read_text(encoding="utf-8")
        # The console wraps long lines, so compare without whitespace
        return "".join(buffer.getvalue().split()), run_log

    def test_no_credential_reaches_the_console_or_the_run_log(self):
        console_text, run_log = self.emit()
        for name, text in (("console", console_text), ("run.log", run_log)):
            for secret in (ACCESS_KEY, SIGNATURE, TOKEN, SECRET):
                with self.subTest(target=name, secret=secret[:8]):
                    self.assertNotIn(secret, text)
            with self.subTest(target=name):
                self.assertIn("REDACTED", text)

    def test_the_request_detail_around_the_secret_is_kept(self):
        _, run_log = self.emit()
        self.assertIn("ec2.eu-west-1.amazonaws.com", run_log)
        self.assertIn("botocore.endpoint - DEBUG", run_log)
        self.assertIn("'Authorization': REDACTED", run_log)

    def test_a_credential_scope_outside_a_header_keeps_its_shape(self):
        # A presigned URL carries the key ID in the query string, where the
        # scope that follows it is worth reading
        cleaned = redact(
            "https://s3.eu-west-1.amazonaws.com/bucket/key?"
            f"X-Amz-Credential={ACCESS_KEY}/20261025/eu-west-1/s3/aws4_request"
            f"&X-Amz-Signature={SIGNATURE}"
        )
        self.assertIn("Credential=REDACTED/20261025/eu-west-1/s3/aws4_request", cleaned)
        self.assertIn("X-Amz-Signature=REDACTED", cleaned)
        self.assertNotIn(ACCESS_KEY, cleaned)
        self.assertNotIn(SIGNATURE, cleaned)


if __name__ == "__main__":
    unittest.main()
