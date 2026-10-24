import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import test_catalogue as cat
from tests.catalogue_fixtures import catalogue_gz, manifest, manifest_entry, sha256
from tests.dataset_shapes import VERSION, build_shape
from tests.http_fixtures import fake_http, make_response
from tests.sync_fixtures import schema_errors
from utils import codes
from utils.data import (
    UNPACK_FLOOR,
    CatalogueError,
    read_state,
    resolve_file_url,
    unpack_limit,
    update_catalogue,
)

ROOT = Path(__file__).resolve().parent.parent
SMOKE_STATE = ROOT / "tests" / "fixtures" / "smoke" / "aws.tfstate"


class RunResult:
    def __init__(self, directory, process):
        self.dir = Path(directory)
        self.code = process.returncode
        self.output = " ".join((process.stdout + process.stderr).split())

    def one(self, pattern):
        found = list(self.dir.glob(pattern))
        assert len(found) == 1, (pattern, found)
        return found[0]

    def html(self):
        return self.one("reports/*/index.html").read_text(encoding="utf-8")

    def text(self):
        return " ".join(re.sub(r"<[^>]+>", " ", self.html()).split())

    def report_json(self):
        return json.loads(
            self.one("reports/*/raw_data/assessment_result.json").read_text()
        )

    def payload(self):
        return json.loads(self.one("payload/*.json").read_text())


class ShapeTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("ESC_", "CLOUDEXIT_"))
        }
        self.env.update(
            {
                "XDG_DATA_HOME": str(self.tmp / "data"),
                "XDG_CONFIG_HOME": str(self.tmp / "config"),
                "XDG_CACHE_HOME": str(self.tmp / "cache"),
                "XDG_STATE_HOME": str(self.tmp / "state"),
                "ESC_EXIT_STRATEGY": "1",
                "ESC_ASSESSMENT_TYPE": "1",
                "COLUMNS": "200",
            }
        )

    def run_shape(self, shape):
        # The offline pipeline end to end: tfstate in, report and sync document out
        directory = self.tmp / shape
        directory.mkdir()
        dataset = build_shape(self.tmp, shape)
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "main.py"),
                "aws",
                "--tfstate",
                str(SMOKE_STATE),
                "--catalogue",
                str(dataset),
                "--non-interactive",
                "--emit-payload",
                "payload",
            ],
            cwd=directory,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return RunResult(directory, process)

    def assert_a_complete_offline_run(self, result):
        self.assertEqual(result.code, 0, result.output)
        report = result.one("reports/*")
        self.assertTrue((report / "index.html").is_file())
        self.assertEqual((report / "report.pdf").read_bytes()[:5], b"%PDF-")
        self.assertGreater((report / "report.pdf").stat().st_size, 10000)
        self.assertTrue((report / "raw_data" / "sync.json").is_file())
        self.assertEqual(schema_errors(result.payload()), [])


class AcceptedShapeTests(ShapeTestCase):
    def test_a_legacy_dataset_runs_with_an_unknown_version_and_numbered_risks(self):
        result = self.run_shape("legacy")

        self.assert_a_complete_offline_run(result)
        self.assertIn("High: R2 No alternatives", result.text())
        self.assertIn("unknown", result.html().lower())
        self.assertIsNone(result.report_json()["provenance"]["catalogue_version"])
        # Without catalogue_meta the sync document makes no claim about a version
        self.assertNotIn("catalogue", result.payload()["sync"]["collector"])

    def test_the_current_dataset_gives_real_codes_and_the_version(self):
        result = self.run_shape("current")

        self.assert_a_complete_offline_run(result)
        text = result.text()
        # catalogue_severity wins over severity, and risk.code over R<id>
        self.assertIn("Critical: LOCK-002 No alternatives", text)
        self.assertNotIn("R2 No alternatives", text)
        self.assertIn(VERSION, text)
        provenance = result.report_json()["provenance"]
        self.assertEqual(provenance["catalogue_version"], VERSION)
        claim = result.payload()["sync"]["collector"]["catalogue"]
        self.assertEqual(claim["version"], VERSION)
        self.assertEqual(claim["sequence"], 41)
        self.assertEqual(len(claim["sha256"]), 64)
        self.assertEqual(claim["resource_type_ids"], [1, 2, 3, 4])

    def test_a_future_additive_dataset_works_exactly_like_the_current_one(self):
        current = self.run_shape("current")
        future = self.run_shape("future")

        self.assert_a_complete_offline_run(future)
        self.assertEqual(
            future.payload()["sync"]["collector"]["catalogue"],
            current.payload()["sync"]["collector"]["catalogue"],
        )
        self.assertEqual(future.payload()["inventory"], current.payload()["inventory"])
        self.assertEqual(
            future.report_json()["data"]["risk_inventory"],
            current.report_json()["data"]["risk_inventory"],
        )
        self.assertEqual(
            future.report_json()["data"]["resource_inventory"],
            current.report_json()["data"]["resource_inventory"],
        )
        self.assertIn("Critical: LOCK-002 No alternatives", future.text())


class RejectedShapeTests(ShapeTestCase):
    def assert_refused(self, shape, message):
        result = self.run_shape(shape)

        self.assertEqual(result.code, codes.CONFIG, result.output)
        self.assertIn(message, result.output)
        self.assertNotIn("Traceback", result.output)
        # Nothing is written as if the run had worked
        self.assertFalse((result.dir / "reports").exists())
        self.assertFalse((result.dir / "payload").exists())

    def test_a_newer_dataset_schema_asks_for_an_update(self):
        self.assert_refused("refused", "Update cloudexit")

    def test_a_catalogue_meta_with_two_rows_is_refused(self):
        self.assert_refused("two_meta_rows", "exactly one row, found 2")

    def test_a_truncated_download_is_refused(self):
        self.assert_refused("truncated", "not a valid gzip archive")

    def test_a_file_that_is_not_sqlite_is_refused(self):
        self.assert_refused("not_sqlite", "not a valid catalogue database")


class BrokenDownloadTests(cat.CatalogueTestCase):
    def installed_bytes(self):
        return (self.dir / "data.db").read_bytes()

    def test_a_checksum_mismatch_keeps_the_installed_dataset(self):
        self.install(cat.V1)
        before = self.installed_bytes()
        gz, entry = self.release(cat.V2)
        tampered = gz[:-1] + bytes([gz[-1] ^ 1])

        with fake_http(self.manifest_reply(entry), make_response(content=tampered)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(cat.BASE, directory=self.dir)

        self.assertIn("checksum mismatch", str(ctx.exception))
        self.assertEqual(self.installed_bytes(), before)
        self.assertEqual(self.version(), cat.V1)

    def test_every_broken_shape_is_refused_and_the_installed_dataset_stays(self):
        self.install(cat.V1)
        before = self.installed_bytes()
        shapes = ("two_meta_rows", "truncated", "not_sqlite", "refused")
        for shape in shapes:
            with self.subTest(shape):
                path = build_shape(self.tmp, shape)
                data = path.read_bytes()
                if path.suffix != ".gz":
                    data = gzip.compress(data)
                entry = manifest_entry(cat.V2, data)
                reply = self.manifest_reply(entry)
                with fake_http(reply, make_response(content=data)):
                    with self.assertRaises(CatalogueError):
                        update_catalogue(cat.BASE, directory=self.dir)
                self.assertEqual(self.installed_bytes(), before)
                self.assertEqual(read_state(self.dir)["version"], cat.V1)
                self.assertEqual(list(self.dir.glob("*.tmp")), [])


class HostileManifestTests(cat.CatalogueTestCase):
    def update_with_file(self, file):
        gz, entry = self.release(cat.V2)
        entry["file"] = file
        with fake_http(self.manifest_reply(entry)) as fake:
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(cat.BASE, directory=self.dir)
        return fake, ctx.exception

    def test_a_file_on_another_host_is_refused_before_anything_is_fetched(self):
        for file in (
            "https://elsewhere.example.test/releases/x.db.gz",
            "http://elsewhere.example.test/x.db.gz",
            "//elsewhere.example.test/x.db.gz",
            "https://catalogue.example.test.elsewhere.example.test/mirror/x.db.gz",
            "https://catalogue.example.test:8443/mirror/x.db.gz",
            "https://user@elsewhere.example.test/mirror/x.db.gz",
        ):
            with self.subTest(file=file):
                fake, error = self.update_with_file(file)
                self.assertIn("outside the catalogue location", str(error))
                self.assertEqual([r.url for r in fake.requests], [cat.MANIFEST_URL])

    def test_a_file_that_climbs_out_of_the_folder_is_refused(self):
        for file in (
            "../other/x.db.gz",
            "releases/../../other/x.db.gz",
            "releases/%2e%2e/%2e%2e/other/x.db.gz",
            "releases/..%2f..%2fother/x.db.gz",
            "releases\\..\\..\\other\\x.db.gz",
            "/other/x.db.gz",
            "/mirror/../other/x.db.gz",
        ):
            with self.subTest(file=file):
                fake, _ = self.update_with_file(file)
                self.assertEqual([r.url for r in fake.requests], [cat.MANIFEST_URL])

    def test_a_file_below_the_manifest_is_still_accepted(self):
        for file in ("x.db.gz", "releases/x.db.gz", "./releases/x.db.gz"):
            with self.subTest(file=file):
                url = resolve_file_url(cat.MANIFEST_URL, file)
                self.assertTrue(url.startswith(cat.BASE + "/"))

    def test_the_manifest_never_decides_the_host_even_with_a_version_choice(self):
        _, entry = self.release(cat.V2)
        entry["file"] = "https://elsewhere.example.test/x.db.gz"
        with fake_http(self.manifest_reply(entry)) as fake:
            with self.assertRaises(CatalogueError):
                update_catalogue(cat.BASE, directory=self.dir, version=cat.V2)
        self.assertEqual(len(fake.requests), 1)


def zeros_gz(size):
    inflater = zlib.compressobj(9, zlib.DEFLATED, zlib.MAX_WBITS | 16)
    return inflater.compress(bytes(size)) + inflater.flush()


class DecompressionBombTests(cat.CatalogueTestCase):
    def test_the_limit_follows_the_download_between_a_floor_and_a_ceiling(self):
        self.assertEqual(unpack_limit(None), UNPACK_FLOOR)
        self.assertEqual(unpack_limit(1000), UNPACK_FLOOR)
        self.assertEqual(unpack_limit(10 * 1024 * 1024), 100 * 1024 * 1024)
        self.assertEqual(unpack_limit(10**12), 512 * 1024 * 1024)

    def test_a_small_archive_that_unpacks_far_beyond_the_limit_is_refused(self):
        self.install(cat.V1)
        before = (self.dir / "data.db").read_bytes()
        bomb = zeros_gz(UNPACK_FLOOR + 4 * 1024 * 1024)
        self.assertLess(len(bomb), 100 * 1024)
        entry = manifest_entry(cat.V2, bomb)

        with fake_http(self.manifest_reply(entry), make_response(content=bomb)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(cat.BASE, directory=self.dir)

        self.assertIn("unpacks to more than", str(ctx.exception))
        self.assertEqual((self.dir / "data.db").read_bytes(), before)
        self.assertEqual(self.version(), cat.V1)
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_the_bomb_is_stopped_while_unpacking_not_after(self):
        bomb = zeros_gz(UNPACK_FLOOR + 4 * 1024 * 1024)
        entry = manifest_entry(cat.V2, bomb)
        written = []
        real_write = Path.write_bytes

        def watch(path, data):
            written.append(len(data))
            return real_write(path, data)

        with (
            fake_http(self.manifest_reply(entry), make_response(content=bomb)),
            patch.object(Path, "write_bytes", watch),
            self.assertRaises(CatalogueError),
        ):
            update_catalogue(cat.BASE, directory=self.dir)
        self.assertTrue(all(size < UNPACK_FLOOR for size in written))

    def test_many_small_members_cannot_get_past_the_limit(self):
        member = zeros_gz(UNPACK_FLOOR // 4)
        bomb = member * 8
        entry = manifest_entry(cat.V2, bomb)

        with fake_http(self.manifest_reply(entry), make_response(content=bomb)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(cat.BASE, directory=self.dir)
        self.assertIn("unpacks to more than", str(ctx.exception))

    def test_an_announced_size_cannot_widen_the_limit_past_the_ceiling(self):
        bomb = zeros_gz(UNPACK_FLOOR + 4 * 1024 * 1024)
        entry = manifest_entry(cat.V2, bomb)
        entry["size_bytes"] = 10**12

        with (
            patch("utils.data.UNPACK_CEILING", UNPACK_FLOOR),
            fake_http(self.manifest_reply(entry), make_response(content=bomb)),
            self.assertRaises(CatalogueError),
        ):
            update_catalogue(cat.BASE, directory=self.dir)

    def test_the_latest_file_fallback_and_an_imported_file_have_the_same_limit(self):
        bomb = zeros_gz(UNPACK_FLOOR + 4 * 1024 * 1024)
        digest = sha256(bomb)
        with fake_http(
            make_response(404),
            make_response(text=f"{digest}  cloudexit-latest.db.gz\n"),
            make_response(content=bomb),
        ):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(cat.BASE, directory=self.dir)
        self.assertIn("unpacks to more than", str(ctx.exception))

        path = self.tmp / "bomb.db.gz"
        path.write_bytes(bomb)
        with self.assertRaises(CatalogueError):
            cat.import_catalogue(path, directory=self.dir)
        with self.assertRaises(CatalogueError):
            cat.use_catalogue_file(path)

    def test_a_real_dataset_is_far_below_the_limit(self):
        gz = catalogue_gz(version=cat.V2)
        self.assertLess(len(gzip.decompress(gz)), UNPACK_FLOOR)
        entry = manifest_entry(cat.V2, gz)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            result = update_catalogue(cat.BASE, directory=self.dir)
        self.assertEqual(result.status, "updated")


class RecordingServer:
    # Two servers on localhost: the first redirects the dataset to the second
    # and both record what they were sent.
    def __init__(self, gz, entry_for):
        self.received = {"first": [], "second": []}
        outer = self

        class Second(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.received["second"].append(
                    (self.path, {k.lower(): v for k, v in self.headers.items()})
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(gz)))
                self.end_headers()
                self.wfile.write(gz)

            def log_message(self, *args):
                pass

        self.second = ThreadingHTTPServer(("127.0.0.1", 0), Second)
        second_url = f"http://127.0.0.1:{self.second.server_port}/stolen.db.gz"

        class First(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.received["first"].append(
                    (self.path, {k.lower(): v for k, v in self.headers.items()})
                )
                if self.path.endswith("cloudexit-manifest.json"):
                    body = json.dumps(
                        manifest(entry_for(f"http://127.0.0.1:{outer.first_port}"))
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(302)
                    self.send_header("Location", second_url)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def log_message(self, *args):
                pass

        self.first = ThreadingHTTPServer(("127.0.0.1", 0), First)
        self.first_port = self.first.server_port
        self.url = f"http://127.0.0.1:{self.first_port}"
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.first, self.second)
        ]

    def __enter__(self):
        for thread in self.threads:
            thread.start()
        return self

    def __exit__(self, *exc):
        for server in (self.first, self.second):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()


class RedirectTests(cat.CatalogueTestCase):
    def test_a_redirect_to_another_host_carries_no_credentials(self):
        home = self.tmp / "home"
        home.mkdir()
        # A login the user has for that host must not be sent along either
        (home / ".netrc").write_text(
            "machine 127.0.0.1 login someone password hunter2\n", encoding="utf-8"
        )
        (home / "_netrc").write_text(
            "machine 127.0.0.1 login someone password hunter2\n", encoding="utf-8"
        )
        gz = catalogue_gz(version=cat.V2)

        def entry(base):
            return manifest_entry(cat.V2, gz, file="releases/cloudexit.db.gz")

        env = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "NETRC": str(home / ".netrc"),
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        with RecordingServer(gz, entry) as servers, patch.dict(os.environ, env):
            update_catalogue(servers.url, directory=self.dir)

        seen = servers.received["first"] + servers.received["second"]
        self.assertEqual(len(servers.received["second"]), 1)
        for path, headers in seen:
            self.assertNotIn("authorization", headers, path)
            self.assertNotIn("cookie", headers, path)
            self.assertNotIn("proxy-authorization", headers, path)
            self.assertNotIn("hunter2", json.dumps(headers))
        self.assertEqual(self.version(), cat.V2)

    def test_the_request_itself_has_no_headers_cookies_or_body(self):
        gz, entry = self.release(cat.V2)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)) as fake:
            update_catalogue(cat.BASE, directory=self.dir)

        for request in fake.requests:
            for name in ("headers", "cookies", "data", "json"):
                self.assertIsNone(request.kwargs.get(name), name)
            self.assertIs(request.kwargs["verify"], True)


if __name__ == "__main__":
    unittest.main()
