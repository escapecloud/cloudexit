import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.api_fixtures import (
    KEY,
    error_body,
    meta_body,
    sync_body,
    whoami_body,
)
from tests.isolation import isolated_environ
from tests.make_smoke_dataset import main as make_smoke_dataset
from utils import codes

ROOT = Path(__file__).resolve().parent.parent
SMOKE_STATE = ROOT / "tests" / "fixtures" / "smoke" / "aws.tfstate"
WARNING = {
    "code": "collector_catalogue_outdated",
    "message": "The catalogue used is older than the platform's.",
}


class StubPlatform:
    # A platform on localhost that answers from canned bodies and counts calls
    def __init__(self):
        self.routes = {
            ("GET", "/api/v2/meta"): (200, meta_body()),
            ("GET", "/api/v2/whoami"): (200, whoami_body()),
            ("POST", "/api/v2/syncs"): (201, sync_body()),
        }
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                stub.requests.append((self.command, self.path))
                status, body = stub.routes.get(
                    (self.command, self.path.split("?")[0]),
                    (404, error_body("not_found")),
                )
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Request-ID", "req-stub-0001")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_request

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


class PlatformCliTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.dataset = Path(tmp.name) / "smoke.db"
        make_smoke_dataset(str(cls.dataset))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("ESC_", "CLOUDEXIT_"))
            and name.lower() not in ("http_proxy", "https_proxy", "all_proxy")
        }
        self.env.update(
            {
                **isolated_environ(self.tmp),
                "NO_PROXY": "*",
                "COLUMNS": "200",
                "ESC_EXIT_STRATEGY": "1",
                "ESC_ASSESSMENT_TYPE": "2",
            }
        )

    def connect(self, platform):
        self.env["CLOUDEXIT_HOST"] = platform.url
        self.env["CLOUDEXIT_API_KEY"] = KEY

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "main.py"), *args],
            cwd=self.tmp,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def run_aws(self, *extra):
        return self.cli(
            "aws",
            "--tfstate",
            str(SMOKE_STATE),
            "--catalogue",
            str(self.dataset),
            "--non-interactive",
            *extra,
        )

    def run_json(self, *extra):
        result = self.run_aws("--output", "json", *extra)
        # Stdout is exactly one JSON object, whatever happened
        summary = json.loads(result.stdout)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        return result, summary

    def reports(self):
        return list(self.tmp.glob("reports/*/index.html"))


class RunModeCliTests(PlatformCliTestCase):
    def test_auto_uploads_and_stdout_is_only_the_json_summary(self):
        with StubPlatform() as platform:
            self.connect(platform)
            result, summary = self.run_json()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["upload"]["status"], "uploaded")
        self.assertEqual(summary["upload"]["url"], sync_body()["assessment"]["url"])
        self.assertEqual(summary["results"]["scoring_version"], "1")
        self.assertEqual(summary["catalogue"]["outdated"], False)
        self.assertTrue(Path(summary["report_dir"], "index.html").is_file())
        self.assertTrue(Path(summary["sync_json"]).is_file())
        self.assertEqual(
            [r[0] for r in platform.requests], ["GET", "GET", "POST"], platform.requests
        )
        self.assertIn("Stage #7", result.stderr)
        self.assertNotIn("Stage #7", result.stdout)
        self.assertNotIn("Preliminary Stage", result.stdout)

    def test_a_refused_key_still_writes_the_report_and_exits_11(self):
        with StubPlatform() as platform:
            platform.routes[("GET", "/api/v2/whoami")] = (
                401,
                error_body("api_key_revoked"),
            )
            self.connect(platform)
            result, summary = self.run_json()

        self.assertEqual(result.returncode, codes.UPLOAD_AUTH, result.stderr)
        self.assertEqual(summary["exit_code"], codes.UPLOAD_AUTH)
        self.assertEqual(summary["upload"]["status"], "failed")
        self.assertEqual(summary["upload"]["error"]["code"], "api_key_revoked")
        self.assertEqual(summary["upload"]["error"]["request_id"], "req-fake-0001")
        self.assertEqual(len(self.reports()), 1)

    def test_off_sends_nothing_even_with_a_key(self):
        with StubPlatform() as platform:
            self.connect(platform)
            result, summary = self.run_json("--upload", "off")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(platform.requests, [])
        self.assertEqual(summary["upload"]["status"], "skipped")
        self.assertEqual(summary["upload"]["reason"], "off")
        self.assertIn("Forcing Basic Assessment: uploads are off.", result.stderr)
        self.assertEqual(len(self.reports()), 1)

    def test_the_mode_can_come_from_the_environment(self):
        with StubPlatform() as platform:
            self.connect(platform)
            self.env["CLOUDEXIT_UPLOAD"] = "off"
            result, summary = self.run_json()

        self.assertEqual(platform.requests, [])
        self.assertEqual(summary["upload"]["reason"], "off")

    def test_required_without_a_platform_fails_before_collecting(self):
        result, summary = self.run_json("--upload", "required")

        self.assertEqual(result.returncode, codes.CONFIG, result.stderr)
        self.assertEqual(summary["exit_code"], codes.CONFIG)
        self.assertIsNone(summary["report_dir"])
        self.assertEqual(self.reports(), [])
        self.assertFalse((self.tmp / "reports").exists())
        self.assertIn("Upload is required", " ".join(result.stderr.split()))

    def test_auto_without_a_platform_runs_offline(self):
        result, summary = self.run_json()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(summary["upload"]["status"], "skipped")
        self.assertEqual(summary["upload"]["reason"], "offline")
        self.assertIn(
            "Forcing Basic Assessment: no platform is configured.",
            " ".join(result.stderr.split()),
        )

    def test_half_a_configuration_is_not_an_offline_run(self):
        self.env["CLOUDEXIT_HOST"] = "http://127.0.0.1:9"
        result, summary = self.run_json()

        self.assertEqual(result.returncode, codes.CONFIG, result.stderr)
        self.assertEqual(self.reports(), [])

    def test_strict_fails_after_the_report_when_the_platform_warns(self):
        with StubPlatform() as platform:
            platform.routes[("POST", "/api/v2/syncs")] = (
                201,
                sync_body(warnings=[WARNING]),
            )
            self.connect(platform)
            result, summary = self.run_json("--strict")

        self.assertEqual(result.returncode, codes.UPLOAD_REJECTED, result.stderr)
        self.assertEqual(summary["exit_code"], codes.UPLOAD_REJECTED)
        self.assertEqual(summary["upload"]["status"], "uploaded")
        self.assertEqual(summary["upload"]["warnings"], [WARNING])
        self.assertIs(summary["catalogue"]["outdated"], True)
        self.assertEqual(len(self.reports()), 1)

    def test_the_same_warning_passes_without_strict(self):
        with StubPlatform() as platform:
            platform.routes[("POST", "/api/v2/syncs")] = (
                201,
                sync_body(warnings=[WARNING]),
            )
            self.connect(platform)
            result, summary = self.run_json()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(summary["upload"]["warnings"], [WARNING])

    def test_a_dry_run_is_upload_off_with_the_sync_document(self):
        with StubPlatform() as platform:
            self.connect(platform)
            result, summary = self.run_json("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(platform.requests, [])
        self.assertEqual(summary["upload"]["reason"], "dry_run")
        self.assertTrue(Path(summary["sync_json"]).is_file())
        self.assertIn(
            "--dry-run is --upload=off plus the sync document",
            " ".join(result.stderr.split()),
        )

    def test_text_output_is_unchanged_on_stdout(self):
        result = self.run_aws()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Preliminary Stage", result.stdout)
        self.assertNotIn('"exit_code"', result.stdout)


class StatusCliTests(PlatformCliTestCase):
    def test_a_healthy_platform(self):
        with StubPlatform() as platform:
            self.connect(platform)
            result = self.cli("status", "--output", "json")

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["key"]["kind"], "assessment")
        self.assertEqual(data["tls"]["status"], "not_applicable")
        self.assertNotIn(KEY, result.stdout + result.stderr)

    def test_the_text_report(self):
        with StubPlatform() as platform:
            self.connect(platform)
            result = self.cli("status")

        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            "Settings",
            "Platform (GET /meta)",
            "Key (GET /whoami)",
            "Spool",
            "Catalogue",
            "Everything checked is fine.",
        ):
            self.assertIn(expected, result.stdout)
        self.assertNotIn(KEY, result.stdout + result.stderr)

    def test_a_host_that_does_not_answer_is_exit_12(self):
        self.env["CLOUDEXIT_HOST"] = closed_port_url()
        self.env["CLOUDEXIT_API_KEY"] = KEY
        self.env["CLOUDEXIT_UPLOAD_BUDGET"] = "0"

        result = self.cli("status")

        self.assertEqual(result.returncode, codes.UPLOAD_UNAVAILABLE, result.stdout)

    def test_a_refused_key_is_exit_11(self):
        with StubPlatform() as platform:
            platform.routes[("GET", "/api/v2/whoami")] = (
                401,
                error_body("api_key_revoked"),
            )
            self.connect(platform)
            result = self.cli("status")

        self.assertEqual(result.returncode, codes.UPLOAD_AUTH, result.stdout)
        self.assertIn("api_key_revoked", result.stdout)

    def test_a_client_the_platform_refuses_is_exit_15(self):
        with StubPlatform() as platform:
            platform.routes[("GET", "/api/v2/meta")] = (
                200,
                meta_body(clients={"cloudexit": {"min_supported": "99.0.0"}}),
            )
            self.connect(platform)
            result = self.cli("status")

        self.assertEqual(result.returncode, codes.UPLOAD_UNSUPPORTED, result.stdout)

    def test_nothing_configured_is_exit_0(self):
        result = self.cli("status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not configured", result.stdout)

    def test_catalogue_status_names_the_platforms_catalogue(self):
        with StubPlatform() as platform:
            self.connect(platform)
            self.env["CLOUDEXIT_CATALOGUE_URL"] = closed_port_url()
            result = self.cli("catalogue", "status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("platform:  2026-11-02", result.stdout)


if __name__ == "__main__":
    unittest.main()
