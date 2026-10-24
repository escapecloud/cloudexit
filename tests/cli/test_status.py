import contextlib
import io
import json
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from cloudexit.cli import app
from cloudexit.platform.client import ApiClient
from cloudexit.platform.spool import Spool
from cloudexit.platform.status import (
    TlsResult,
    catalogue_standing,
    certificate_names,
    check_tls,
    host_catalogue_lines,
    run_status,
    status_budget,
    STATUS_BUDGET,
)
from tests.support.api import (
    EU_KEY,
    KEY,
    error_body,
    meta_reply,
    minimal_document,
    reply,
    whoami_reply,
)
from tests.support.http import fake_http
from tests.support.isolation import isolated_environ
from tests.support.sync import SYNC_ID
from cloudexit import codes
from cloudexit.catalogue.store import (
    compare_catalogue_versions,
    parse_catalogue_version,
)
from cloudexit.settings import Setting, Settings

NOW = 1_800_000_000.0
REAL_CLIENT = ApiClient.from_settings
HOST = "https://platform.example.com"
GOOD = TlsResult(
    "ok",
    "/etc/ssl/ca.pem",
    subject="CN=platform.example.com",
    issuer="O=Example CA, CN=Example Issuing CA",
)


def configured(**changes):
    fields = dict(
        host=Setting(HOST, "env CLOUDEXIT_HOST"),
        api_key=Setting(KEY, "env CLOUDEXIT_API_KEY"),
    )
    fields.update(changes)
    return Settings(**fields)


def der(tag, content):
    if len(content) < 128:
        length = bytes([len(content)])
    else:
        raw = len(content).to_bytes(2, "big")
        length = bytes([0x82]) + raw
    return bytes([tag]) + length + content


def der_name(**parts):
    oids = {"CN": b"\x55\x04\x03", "O": b"\x55\x04\x0a"}
    sets = b""
    for label, value in parts.items():
        pair = der(0x06, oids[label]) + der(0x0C, value.encode())
        sets += der(0x31, der(0x30, pair))
    return der(0x30, sets)


def der_certificate(issuer, subject):
    tbs = der(
        0x30,
        der(0xA0, der(0x02, b"\x02"))
        + der(0x02, b"\x01")
        + der(0x30, der(0x06, b"\x2a\x03"))
        + issuer
        + der(0x30, der(0x17, b"260101000000Z") + der(0x17, b"270101000000Z"))
        + subject,
    )
    return der(0x30, tbs + der(0x30, der(0x06, b"\x2a\x03")) + der(0x03, b"\x00"))


class StatusTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.clients = 0
        self.bundle = self.tmp / "ca.pem"
        self.bundle.write_text("bundle")
        self.other = self.tmp / "other.pem"
        self.other.write_text("other")
        patcher = patch.dict(
            "os.environ",
            isolated_environ(self.tmp),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_client(self, settings, **options):
        self.clients += 1
        options["budget"] = 0.0
        options["environ"] = {}
        options["cache_path"] = self.tmp / f"cache-{self.clients}"
        options["sleep"] = lambda seconds: None
        return REAL_CLIENT(settings, **options)

    def status(self, settings, replies=None, tls=GOOD, **options):
        options.setdefault("environ", {})
        with fake_http(*(replies or [])) as http:
            report = run_status(
                settings,
                now=lambda: NOW,
                tls=lambda url, bundle: tls,
                make_client=self.make_client,
                spool=Spool(self.tmp / "spool"),
                **options,
            )
        self.http = http
        return report

    def text(self, report):
        return "\n".join(report.lines())


class ExitCodeTests(StatusTestCase):
    def test_nothing_configured_is_fine_and_offline(self):
        report = self.status(Settings())

        self.assertEqual(report.exit_code, codes.SUCCESS)
        self.assertEqual(self.http.requests, [])
        self.assertIn("not configured", self.text(report))

    def test_all_good(self):
        report = self.status(configured(), [meta_reply(), whoami_reply()])

        self.assertEqual(report.exit_code, codes.SUCCESS)
        text = self.text(report)
        self.assertIn("Everything checked is fine.", text)
        self.assertIn("certificate: verified", text)
        self.assertIn("CA bundle: ", text)
        self.assertIn("host:      escapecloud", text)
        self.assertIn("catalogue: 2026-11-02", text)
        self.assertIn("max_body_bytes=1000000", text)
        self.assertIn("organisation: Org (organization)", text)
        self.assertIn("assessment:   0193a000-0000-7000-8000-000000000001", text)
        self.assertIn("scopes:       syncs:write", text)
        self.assertIn("is accepted", text)
        self.assertEqual([r.method for r in self.http.requests], ["GET", "GET"])

    def test_a_rejected_key_is_an_auth_problem(self):
        report = self.status(
            configured(),
            [meta_reply(), reply(401, error_body("api_key_revoked"))],
        )

        self.assertEqual(report.exit_code, codes.UPLOAD_AUTH)
        self.assertIn("api_key_revoked", self.text(report))

    def test_a_key_that_cannot_be_right_is_an_auth_problem_without_a_request(self):
        settings = configured(api_key=Setting("esc_sk_typo", "env CLOUDEXIT_API_KEY"))

        report = self.status(settings)

        self.assertEqual(report.exit_code, codes.UPLOAD_AUTH)
        self.assertEqual(self.http.requests, [])

    def test_half_a_configuration_is_an_auth_problem(self):
        for settings in (
            Settings(host=Setting(HOST, "env CLOUDEXIT_HOST")),
            Settings(api_key=Setting(KEY, "env CLOUDEXIT_API_KEY")),
        ):
            with self.subTest(settings=settings.host.value):
                report = self.status(settings)
                self.assertEqual(report.exit_code, codes.UPLOAD_AUTH)
                self.assertEqual(self.http.requests, [])

    def test_a_key_for_another_region_is_an_auth_problem(self):
        settings = configured(api_key=Setting(EU_KEY, "env CLOUDEXIT_API_KEY"))
        replies = [meta_reply(host={"product": "escapecloud", "region": "us"})]

        report = self.status(settings, replies)

        self.assertEqual(report.exit_code, codes.UPLOAD_AUTH)
        self.assertIn("key", self.text(report))

    def test_an_unreachable_host_is_exit_12(self):
        report = self.status(configured(), [requests.ConnectionError("refused")])

        self.assertEqual(report.exit_code, codes.UPLOAD_UNAVAILABLE)

    def test_a_client_the_platform_does_not_accept_is_exit_15(self):
        replies = [
            meta_reply(
                clients={
                    "cloudexit": {"min_supported": "99.0.0", "recommended": "99.1"}
                }
            )
        ]

        report = self.status(configured(), replies + [whoami_reply()])

        self.assertEqual(report.exit_code, codes.UPLOAD_UNSUPPORTED)
        self.assertIn("is not accepted by the platform", self.text(report))

    def test_a_schema_version_the_platform_does_not_accept_is_exit_15(self):
        replies = [meta_reply(contract={"schema_versions": ["9.0"], "current": "9.0"})]

        report = self.status(configured(), replies + [whoami_reply()])

        self.assertEqual(report.exit_code, codes.UPLOAD_UNSUPPORTED)

    def test_unreachable_wins_over_auth_and_auth_over_unsupported(self):
        report = self.status(configured(), [meta_reply(), whoami_reply()])
        report.problem(codes.UPLOAD_UNSUPPORTED, "a")
        self.assertEqual(report.exit_code, codes.UPLOAD_UNSUPPORTED)
        report.problem(codes.UPLOAD_AUTH, "b")
        self.assertEqual(report.exit_code, codes.UPLOAD_AUTH)
        report.problem(codes.UPLOAD_UNAVAILABLE, "c")
        self.assertEqual(report.exit_code, codes.UPLOAD_UNAVAILABLE)


class TlsFailureTests(StatusTestCase):
    def test_a_failed_chain_names_the_bundle_and_the_failing_certificate(self):
        failed = TlsResult(
            "failed",
            str(self.bundle),
            "self-signed certificate in certificate chain",
            subject="CN=platform.example.com",
            issuer="O=Corp, CN=Corp Root CA",
        )
        settings = configured(ca_bundle=Setting(str(self.bundle), "flag --ca-bundle"))

        report = self.status(settings, [], tls=failed)

        self.assertEqual(report.exit_code, codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(self.http.requests, [])
        text = self.text(report)
        self.assertIn(f"CA bundle: flag --ca-bundle: {self.bundle}", text)
        self.assertIn(
            "NOT verified: self-signed certificate in certificate chain", text
        )
        self.assertIn("CN=platform.example.com", text)
        self.assertIn(
            "issued by O=Corp, CN=Corp Root CA (not trusted by this bundle)", text
        )
        self.assertIn("--ca-bundle", text)

    def test_an_unreachable_port_is_exit_12(self):
        down = TlsResult("unreachable", "x", "cannot connect to h:443 (OSError)")
        self.assertEqual(
            self.status(configured(), [], tls=down).exit_code, codes.UPLOAD_UNAVAILABLE
        )

    def test_a_proxy_leaves_the_check_to_the_request(self):
        proxied = TlsResult("proxy", "x", "a proxy is configured")

        report = self.status(configured(), [meta_reply(), whoami_reply()], tls=proxied)

        self.assertEqual(report.exit_code, codes.SUCCESS)
        self.assertIn("not checked directly", self.text(report))

    def test_the_configured_bundle_beats_the_environment(self):
        settings = configured(
            ca_bundle=Setting(str(self.bundle), "config file [default]")
        )
        report = self.status(
            settings,
            [meta_reply(), whoami_reply()],
            environ={"REQUESTS_CA_BUNDLE": str(self.other)},
        )
        self.assertIn(
            f"CA bundle: config file [default]: {self.bundle}", self.text(report)
        )

        report = self.status(
            configured(),
            [meta_reply(), whoami_reply()],
            environ={"REQUESTS_CA_BUNDLE": str(self.other)},
        )
        self.assertIn(f"the environment: {self.other}", self.text(report))

    def test_nothing_in_the_output_holds_the_key(self):
        report = self.status(configured(), [meta_reply(), whoami_reply()])
        self.assertNotIn(KEY, self.text(report))
        self.assertNotIn(KEY, json.dumps(report.as_json()))


class CheckTlsTests(unittest.TestCase):
    def setUp(self):
        # A proxy in the developer's environment would change what is checked
        names = [n for n in os.environ if n.lower().endswith("_proxy")]
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in names:
            del os.environ[name]

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Context:
        def __init__(self, error=None, cert=None):
            self.error = error
            self.cert = cert or {}

        def wrap_socket(self, sock, server_hostname=None):
            if self.error:
                raise self.error
            outer = self

            class Wrapped:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def getpeercert(self, binary_form=False):
                    return outer.cert

            return Wrapped()

    def check(self, url="https://platform.example.com", **context):
        return check_tls(
            url,
            "/ca.pem",
            connect=lambda address, timeout: self.Socket(),
            context_factory=lambda bundle: self.Context(**context),
        )

    def test_a_verified_certificate(self):
        cert = {
            "subject": ((("commonName", "platform.example.com"),),),
            "issuer": (
                (("organizationName", "Example CA"), ("commonName", "Issuing")),
            ),
            "notAfter": "Jan  1 00:00:00 2027 GMT",
        }
        result = self.check(cert=cert)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.subject, "CN=platform.example.com")
        self.assertEqual(result.issuer, "O=Example CA, CN=Issuing")
        self.assertEqual(result.expires, "Jan  1 00:00:00 2027 GMT")

    def test_a_chain_that_does_not_verify_says_why_and_what_was_presented(self):
        error = ssl.SSLCertVerificationError(1, "verify failed")
        error.verify_message = "unable to get local issuer certificate"
        names = {"subject": "CN=platform.example.com", "issuer": "CN=Unknown CA"}

        with patch("cloudexit.platform.status._presented", return_value=names):
            result = self.check(error=error)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.message, "unable to get local issuer certificate")
        self.assertEqual(result.issuer, "CN=Unknown CA")
        self.assertEqual(result.subject, "CN=platform.example.com")

    def test_another_tls_error_is_a_failure_with_its_reason(self):
        error = ssl.SSLError(1, "handshake")
        error.reason = "TLSV1_ALERT_PROTOCOL_VERSION"

        result = self.check(error=error)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.message, "TLSV1_ALERT_PROTOCOL_VERSION")

    def test_a_refused_connection_is_unreachable(self):
        result = self.check(error=ConnectionRefusedError())

        self.assertEqual(result.status, "unreachable")
        self.assertIn("platform.example.com:443", result.message)

    def test_a_plain_http_host_has_no_tls_to_check(self):
        self.assertEqual(
            self.check(url="http://localhost:8000").status, "not_applicable"
        )

    def test_a_proxy_is_left_to_the_request(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.example.com:3128"}):
            self.assertEqual(self.check().status, "proxy")

    def test_the_names_of_a_certificate_are_read_from_its_der_form(self):
        issuer = der_name(O="Example CA", CN="Example Issuing CA")
        subject = der_name(CN="platform.example.com")

        names = certificate_names(der_certificate(issuer, subject))

        self.assertEqual(names["issuer"], "O=Example CA, CN=Example Issuing CA")
        self.assertEqual(names["subject"], "CN=platform.example.com")

    def test_garbage_gives_no_names_instead_of_an_error(self):
        for data in (b"", b"\x30", b"\x30\x05abc", b"not a certificate"):
            with self.subTest(data=data):
                self.assertEqual(certificate_names(data), {})


class ReportContentTests(StatusTestCase):
    def test_a_key_that_expires_soon_warns_and_a_later_one_does_not(self):
        soon = whoami_reply(key={"expires_at": int(NOW + 5 * 86400)})
        report = self.status(configured(), [meta_reply(), soon])

        self.assertEqual(report.exit_code, codes.SUCCESS)
        self.assertIn("expires in 5 days", self.text(report))

        later = whoami_reply(key={"expires_at": int(NOW + 14 * 86400 + 60)})
        report = self.status(configured(), [meta_reply(), later])
        self.assertEqual(report.warnings, [])

    def test_a_deprecated_client_warns(self):
        meta = meta_reply(clients={"cloudexit": {"deprecated_below": "99.0.0"}})

        report = self.status(configured(), [meta, whoami_reply()])

        self.assertEqual(report.exit_code, codes.SUCCESS)
        self.assertTrue(any("deprecated" in w for w in report.warnings))

    def test_the_spool_is_summarised(self):
        settings = configured()
        spool = Spool(self.tmp / "spool")
        spool.add(
            HOST,
            minimal_document(SYNC_ID, int(NOW) - 100),
            error=None,
            attempts=1,
            name=None,
            exit_strategy=None,
            assessment_id=None,
        )

        with fake_http(meta_reply(), whoami_reply()):
            report = run_status(
                settings,
                now=lambda: NOW + 3600,
                tls=lambda url, bundle: GOOD,
                make_client=self.make_client,
                spool=spool,
            )

        self.assertIn("1 sync waiting for this host", self.text(report))
        self.assertEqual(report.data["spool"]["pending"], 1)

    def test_an_empty_spool_says_so(self):
        report = self.status(configured(), [meta_reply(), whoami_reply()])
        self.assertEqual(report.data["spool"]["pending"], 0)
        self.assertIn("empty", self.text(report))

    def test_the_catalogue_is_compared_with_the_platforms(self):
        with patch(
            "cloudexit.platform.status._local_catalogue_version",
            return_value="2026-10-01",
        ):
            report = self.status(configured(), [meta_reply(), whoami_reply()])

        self.assertIn("installed: 2026-10-01", self.text(report))
        self.assertIn("the installed one is older", self.text(report))
        self.assertEqual(report.data["catalogue"]["standing"], "older")

    def test_the_json_form_carries_the_exit_code_and_the_problems(self):
        report = self.status(configured(), [requests.ConnectionError("refused")])

        data = report.as_json()

        self.assertEqual(data["exit_code"], codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(data["problems"][0]["exit_code"], codes.UPLOAD_UNAVAILABLE)
        self.assertTrue(data["configured"])


class StatusBudgetTests(unittest.TestCase):
    def test_a_budget_the_user_set_applies_and_otherwise_the_default_does(self):
        self.assertEqual(status_budget(Settings()), STATUS_BUDGET)
        chosen = Settings(upload_budget=Setting(2.5, "flag --upload-budget"))
        self.assertEqual(status_budget(chosen), 2.5)


class CatalogueComparisonTests(unittest.TestCase):
    def test_versions_are_a_date_and_an_optional_suffix(self):
        self.assertEqual(parse_catalogue_version("2026-11-02"), (2026, 11, 2, 0))
        self.assertEqual(parse_catalogue_version("2026-11-02.3"), (2026, 11, 2, 3))
        for bad in (None, "", "latest", "2026-11", "2026-11-02-1", "v2026-11-02"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_catalogue_version(bad))

    def test_older_same_newer(self):
        cases = (
            ("2026-11-01", "2026-11-02", "older"),
            ("2026-11-02", "2026-11-02", "same"),
            ("2026-11-03", "2026-11-02", "newer"),
            ("2026-11-02", "2026-11-02.1", "older"),
            ("2026-11-02.2", "2026-11-02.1", "newer"),
            ("2026-11-02.10", "2026-11-02.9", "newer"),
            ("2026-11-02", "2026-11-02.0", "same"),
            ("2025-12-31", "2026-01-01", "older"),
        )
        for local, other, expected in cases:
            with self.subTest(local=local, other=other):
                self.assertEqual(compare_catalogue_versions(local, other), expected)

    def test_an_unreadable_version_gives_no_answer(self):
        self.assertIsNone(compare_catalogue_versions("x", "2026-11-02"))
        self.assertIsNone(compare_catalogue_versions("2026-11-02", None))

    def test_the_wording(self):
        self.assertIn("older", catalogue_standing("2026-11-01", "2026-11-02"))
        self.assertIn("same", catalogue_standing("2026-11-02", "2026-11-02"))
        self.assertIn("newer", catalogue_standing("2026-11-03", "2026-11-02"))
        self.assertIn("none is installed", catalogue_standing(None, "2026-11-02"))
        self.assertIn("cannot be compared", catalogue_standing("x", "2026-11-02"))
        self.assertIn("reports none", catalogue_standing("2026-11-02", None))


class HostCatalogueLinesTests(StatusTestCase):
    def lines(self, settings, local, replies):
        with fake_http(*replies) as http:
            lines = host_catalogue_lines(settings, local, make_client=self.make_client)
        self.http = http
        return lines

    def test_without_a_key_nothing_is_asked(self):
        for settings in (Settings(), Settings(host=Setting(HOST, "env"))):
            self.assertEqual(self.lines(settings, "2026-11-01", []), [])

    def test_the_hosts_version_is_compared(self):
        for local, word in (
            ("2026-11-01", "older"),
            ("2026-11-02", "same"),
            ("2026-11-03", "newer"),
        ):
            with self.subTest(local=local):
                lines = self.lines(configured(), local, [meta_reply()])
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("platform:  2026-11-02"))
                self.assertIn(word, lines[0])

    def test_an_unreachable_platform_is_reported_not_hidden(self):
        lines = self.lines(
            configured(), "2026-11-01", [requests.ConnectionError("refused")]
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("unavailable", lines[0])


class CommandTests(StatusTestCase):
    def run_command(self, argv, replies=None, settings=None):
        out = io.StringIO()

        def checked(found):
            return run_status(
                found,
                now=lambda: NOW,
                tls=lambda url, bundle: GOOD,
                make_client=self.make_client,
                spool=Spool(self.tmp / "spool"),
            )

        with (
            patch("sys.argv", ["cloudexit", *argv]),
            patch(
                "cloudexit.cli.app.load_settings", return_value=settings or configured()
            ),
            patch("cloudexit.cli.app.run_status", side_effect=checked),
            contextlib.redirect_stdout(out),
            fake_http(*(replies or [])),
        ):
            try:
                app.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_status_prints_the_report_and_exits_zero(self):
        code, out = self.run_command(["status"], [meta_reply(), whoami_reply()])

        self.assertEqual(code, 0)
        self.assertIn("Everything checked is fine.", out)
        self.assertNotIn(KEY, out)

    def test_status_exits_with_the_problem_code(self):
        code, out = self.run_command(["status"], [requests.ConnectionError("x")])

        self.assertEqual(code, codes.UPLOAD_UNAVAILABLE)
        self.assertIn("problem:", out)

    def test_status_as_json(self):
        code, out = self.run_command(
            ["status", "--output", "json"], [meta_reply(), whoami_reply()]
        )

        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["platform"]["catalogue_version"], "2026-11-02")
        self.assertEqual(data["key"]["kind"], "assessment")

    def test_status_needs_no_platform_to_run(self):
        code, out = self.run_command(["status"], settings=Settings())

        self.assertEqual(code, 0)
        self.assertIn("not configured", out)

    def test_catalogue_status_compares_with_the_platform_when_a_key_is_set(self):
        out = io.StringIO()
        with (
            patch("sys.argv", ["cloudexit", "catalogue", "status"]),
            patch("cloudexit.cli.app.load_settings", return_value=configured()),
            patch("cloudexit.cli.app.status_lines", return_value=["installed: none"]),
            patch("cloudexit.cli.app.installed_catalogue", return_value=None),
            patch(
                "cloudexit.platform.status.ApiClient.from_settings",
                side_effect=self.make_client,
            ),
            contextlib.redirect_stdout(out),
            fake_http(meta_reply()),
        ):
            app.main()

        self.assertIn("installed: none", out.getvalue())
        self.assertIn("platform:  2026-11-02 (", out.getvalue())

    def test_catalogue_status_without_a_key_asks_the_platform_nothing(self):
        out = io.StringIO()
        with (
            patch("sys.argv", ["cloudexit", "catalogue", "status"]),
            patch("cloudexit.cli.app.load_settings", return_value=Settings()),
            patch("cloudexit.cli.app.status_lines", return_value=["installed: none"]),
            contextlib.redirect_stdout(out),
            fake_http(),
        ):
            app.main()

        self.assertNotIn("platform:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
