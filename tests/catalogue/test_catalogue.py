import contextlib
import gzip
import io
import json
import os
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import requests

from cloudexit.cli import app
from cloudexit.catalogue import db as catalogue_db
from tests.support.isolation import isolated_environ
from tests.support.catalogue import (
    build_catalogue,
    catalogue_bytes,
    catalogue_gz,
    manifest,
    manifest_entry,
    sha256,
)
from tests.support.http import fake_http, make_response
from cloudexit import codes
from cloudexit.catalogue.store import (
    CATALOGUE_BASE_URL,
    CatalogueError,
    CatalogueUnavailable,
    EnsureResult,
    active_catalogue_path,
    active_resource_type_ids,
    catalogue_base_url,
    catalogue_dir,
    catalogue_info,
    ensure_catalogue,
    import_catalogue,
    installed_catalogue,
    migrate_legacy_dataset,
    read_state,
    resolve_file_url,
    set_active_catalogue,
    status_lines,
    update_catalogue,
    use_catalogue_file,
)
from cloudexit.settings import Setting, Settings, SettingsError

BASE = "https://catalogue.example.test/mirror"
MANIFEST_URL = f"{BASE}/cloudexit-manifest.json"
V1, V2, V3 = "2026-10-26", "2026-11-02", "2026-11-09"


class CatalogueTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.dir = self.tmp / "catalogue"
        self.addCleanup(set_active_catalogue, None)
        patcher = patch(
            "cloudexit.catalogue.store.LEGACY_DATABASE",
            self.tmp / "no-legacy" / "data.db",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, version=V1, **kwargs):
        gz = catalogue_gz(version=version, **kwargs)
        path = self.tmp / f"{version}.db.gz"
        path.write_bytes(gz)
        import_catalogue(path, directory=self.dir)
        return gz

    def release(self, version, **kwargs):
        gz = catalogue_gz(version=version, **kwargs)
        return gz, manifest_entry(version, gz)

    def manifest_reply(self, latest, previous=(), **kwargs):
        return make_response(json_body=manifest(latest, previous, **kwargs))

    def version(self):
        installed = installed_catalogue(self.dir)
        return installed.version if installed else None


class ManifestUpdateTests(CatalogueTestCase):
    def test_installs_the_latest_listed_in_the_manifest_below_the_base_path(self):
        gz, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)) as fake:
            result = update_catalogue(BASE, directory=self.dir)

        self.assertEqual(result.status, "updated")
        self.assertEqual(result.info.version, V2)
        self.assertEqual(self.version(), V2)
        self.assertEqual(
            [request.url for request in fake.requests],
            [MANIFEST_URL, f"{BASE}/releases/cloudexit-{V2}.db.gz"],
        )
        self.assertEqual(fake.requests[0].kwargs["timeout"], 10)
        state = read_state(self.dir)
        self.assertEqual(state["version"], V2)
        self.assertEqual(state["sha256"], entry["file_sha256"])
        self.assertIn("downloaded_at", state)

    def test_unchanged_version_downloads_nothing(self):
        self.install(V2)
        _, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry)) as fake:
            result = update_catalogue(BASE, directory=self.dir)

        self.assertEqual(result.status, "current")
        self.assertEqual(len(fake.requests), 1)

    def test_checksum_mismatch_is_rejected_and_the_old_dataset_kept(self):
        self.install(V1)
        before = (self.dir / "data.db").read_bytes()
        gz, entry = self.release(V2)
        entry["file_sha256"] = "0" * 64
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(BASE, directory=self.dir)

        self.assertIn("checksum mismatch", str(ctx.exception))
        self.assertEqual((self.dir / "data.db").read_bytes(), before)
        self.assertEqual(self.version(), V1)
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["data.db", "state.json"],
        )

    def test_a_version_that_differs_from_the_manifest_is_rejected(self):
        self.install(V1)
        gz = catalogue_gz(version=V3)
        entry = manifest_entry(V2, gz)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            with self.assertRaises(CatalogueError):
                update_catalogue(BASE, directory=self.dir)
        self.assertEqual(self.version(), V1)

    def test_a_download_without_catalogue_meta_is_rejected(self):
        gz = catalogue_gz(meta=False)
        entry = manifest_entry(V2, gz)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            with self.assertRaises(CatalogueError):
                update_catalogue(BASE, directory=self.dir)
        self.assertIsNone(installed_catalogue(self.dir))

    def test_catalogue_meta_must_have_exactly_one_row(self):
        gz = catalogue_gz(version=V2, meta_rows=2)
        entry = manifest_entry(V2, gz)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(BASE, directory=self.dir)
        self.assertIn("exactly one row", str(ctx.exception))

    def test_the_swap_keeps_the_previous_dataset(self):
        self.install(V1)
        old = (self.dir / "data.db").read_bytes()
        gz, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            update_catalogue(BASE, directory=self.dir)

        self.assertEqual((self.dir / "data.db.previous").read_bytes(), old)
        self.assertEqual(catalogue_info(self.dir / "data.db.previous").version, V1)
        self.assertEqual(self.version(), V2)

    def test_a_listed_older_version_can_be_installed(self):
        self.install(V3)
        gz1, entry1 = self.release(V1)
        _, entry3 = self.release(V3)
        with fake_http(
            self.manifest_reply(entry3, [entry1]), make_response(content=gz1)
        ):
            result = update_catalogue(BASE, directory=self.dir, version=V1)

        self.assertEqual(result.info.version, V1)

    def test_an_unlisted_version_lists_the_available_ones(self):
        _, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(BASE, directory=self.dir, version="2020-01-01")
        self.assertIn(V2, str(ctx.exception))

    def test_a_file_outside_the_catalogue_location_is_refused(self):
        for file in (
            "https://elsewhere.example.test/x.db.gz",
            "../other/x.db.gz",
            "//elsewhere.example.test/x.db.gz",
        ):
            with self.subTest(file=file):
                with self.assertRaises(CatalogueError):
                    resolve_file_url(MANIFEST_URL, file)

    def test_a_newer_dataset_schema_asks_for_a_cloudexit_update(self):
        _, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry, schema=2)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(BASE, directory=self.dir)
        self.assertIn("Update cloudexit", str(ctx.exception))

    def test_an_unreachable_server_is_an_error_not_a_fallback(self):
        with fake_http(requests.ConnectionError("offline")) as fake:
            with self.assertRaises(CatalogueError):
                update_catalogue(BASE, directory=self.dir)
        self.assertEqual(len(fake.requests), 1)


class LatestFallbackTests(CatalogueTestCase):
    def test_without_a_manifest_the_latest_file_is_used(self):
        gz = catalogue_gz(version=V2)
        with fake_http(
            make_response(status=404),
            make_response(text=f"{sha256(gz)}  cloudexit-latest.db.gz\n"),
            make_response(content=gz),
        ) as fake:
            result = update_catalogue(BASE, directory=self.dir)

        self.assertEqual(result.status, "updated")
        self.assertEqual(
            [request.url for request in fake.requests],
            [
                MANIFEST_URL,
                f"{BASE}/cloudexit-latest.db.gz.sha256",
                f"{BASE}/cloudexit-latest.db.gz",
            ],
        )
        self.assertEqual(self.version(), V2)

    def test_an_unchanged_checksum_downloads_nothing(self):
        gz = catalogue_gz(version=V2)
        reply = make_response(text=sha256(gz))
        with fake_http(make_response(status=404), reply, make_response(content=gz)):
            update_catalogue(BASE, directory=self.dir)
        with fake_http(make_response(status=404), make_response(text=sha256(gz))):
            result = update_catalogue(BASE, directory=self.dir)
        self.assertEqual(result.status, "current")

    def test_a_dataset_without_catalogue_meta_installs_as_version_unknown(self):
        gz = catalogue_gz(meta=False)
        with fake_http(
            make_response(status=404),
            make_response(text=sha256(gz)),
            make_response(content=gz),
        ):
            result = update_catalogue(BASE, directory=self.dir)

        self.assertIsNone(result.info)
        installed = installed_catalogue(self.dir)
        self.assertIsNotNone(installed)
        self.assertIsNone(installed.version)
        self.assertEqual(active_resource_type_ids("aws", installed.path), [1, 4])

    def test_nothing_published_is_an_error(self):
        with fake_http(make_response(status=404), make_response(status=404)):
            with self.assertRaises(CatalogueError) as ctx:
                update_catalogue(BASE, directory=self.dir)
        self.assertIn("no catalogue manifest or latest file", str(ctx.exception))

    def test_a_version_cannot_be_chosen_without_a_manifest(self):
        with fake_http(make_response(status=404)):
            with self.assertRaises(CatalogueError):
                update_catalogue(BASE, directory=self.dir, version=V1)

    def test_a_checksum_mismatch_is_rejected(self):
        gz = catalogue_gz(version=V2)
        with fake_http(
            make_response(status=404),
            make_response(text="0" * 64),
            make_response(content=gz),
        ):
            with self.assertRaises(CatalogueError):
                update_catalogue(BASE, directory=self.dir)
        self.assertIsNone(installed_catalogue(self.dir))


class CatalogueInfoTests(CatalogueTestCase):
    def test_reads_catalogue_meta(self):
        path = self.tmp / "data.db"
        build_catalogue(path, version=V2, sequence=41)

        info = catalogue_info(path)

        self.assertEqual(info.version, V2)
        self.assertEqual(info.sequence, 41)
        self.assertEqual(info.sha256, "c" * 64)
        self.assertEqual(info.dataset_schema, 1)

    def test_an_old_dataset_has_no_info(self):
        path = self.tmp / "data.db"
        build_catalogue(path, meta=False)
        self.assertIsNone(catalogue_info(path))

    def test_extra_tables_and_columns_are_tolerated(self):
        path = self.tmp / "data.db"
        build_catalogue(path, extras=True)
        self.assertEqual(catalogue_info(path).version, V2)
        self.assertEqual(active_resource_type_ids("azure", path), [3])

    def test_active_resource_type_ids_filter_by_provider_and_status(self):
        path = self.tmp / "data.db"
        build_catalogue(path)

        self.assertEqual(active_resource_type_ids("aws", path), [1, 4])
        self.assertEqual(active_resource_type_ids("azure", path), [3])
        self.assertEqual(active_resource_type_ids(2, path), [1, 4])
        self.assertEqual(active_resource_type_ids("AWS", path), [1, 4])
        with self.assertRaises(ValueError):
            active_resource_type_ids("gcp", path)

    def test_the_active_dataset_is_the_default(self):
        path = self.tmp / "data.db"
        build_catalogue(path, version=V3)
        set_active_catalogue(path)

        self.assertEqual(active_catalogue_path(), path)
        self.assertEqual(catalogue_info().version, V3)
        self.assertEqual(active_resource_type_ids("azure"), [3])
        self.assertEqual(catalogue_db.load_data("risk")[0]["name"], "Example risk")


class ImportTests(CatalogueTestCase):
    def test_imports_a_good_file(self):
        path = self.tmp / "catalogue.db.gz"
        gz = catalogue_gz(version=V2)
        path.write_bytes(gz)

        info = import_catalogue(path, directory=self.dir, sha256=sha256(gz))

        self.assertEqual(info.version, V2)
        self.assertEqual(self.version(), V2)
        self.assertEqual(read_state(self.dir)["sha256"], sha256(gz))

    def test_imports_a_plain_database_and_an_old_one(self):
        plain = self.tmp / "plain.db"
        plain.write_bytes(catalogue_bytes(meta=False))

        self.assertIsNone(import_catalogue(plain, directory=self.dir))
        self.assertIsNone(self.version())
        self.assertIsNotNone(installed_catalogue(self.dir))

    def test_a_wrong_checksum_is_rejected(self):
        path = self.tmp / "catalogue.db.gz"
        path.write_bytes(catalogue_gz())
        with self.assertRaises(CatalogueError):
            import_catalogue(path, directory=self.dir, sha256="0" * 64)
        self.assertIsNone(installed_catalogue(self.dir))

    def test_corrupt_files_are_rejected_and_nothing_is_installed(self):
        self.install(V1)
        before = (self.dir / "data.db").read_bytes()
        good = catalogue_gz(version=V2)
        samples = {
            "truncated gzip": good[: len(good) // 2],
            "not a database": gzip.compress(b"this is not sqlite"),
            "random bytes": os.urandom(256),
            "missing tables": gzip.compress(catalogue_bytes(core_tables=False)),
            "two meta rows": catalogue_gz(meta_rows=2),
            "newer schema": catalogue_gz(schema=2),
        }
        for name, data in samples.items():
            with self.subTest(name):
                path = self.tmp / "bad.db.gz"
                path.write_bytes(data)
                with self.assertRaises(CatalogueError):
                    import_catalogue(path, directory=self.dir)
                self.assertEqual((self.dir / "data.db").read_bytes(), before)

    def test_a_missing_file_is_an_error(self):
        with self.assertRaises(CatalogueError):
            import_catalogue(self.tmp / "missing.db.gz", directory=self.dir)


class UseCatalogueFileTests(CatalogueTestCase):
    def test_a_file_is_used_without_touching_the_installed_dataset(self):
        self.install(V1)
        before = (self.dir / "data.db").read_bytes()
        state = read_state(self.dir)
        path = self.tmp / "other.db.gz"
        path.write_bytes(catalogue_gz(version=V3))

        used, info = use_catalogue_file(path)

        self.assertEqual(info.version, V3)
        self.assertNotEqual(used, self.dir / "data.db")
        self.assertEqual(catalogue_info(used).version, V3)
        self.assertEqual((self.dir / "data.db").read_bytes(), before)
        self.assertEqual(read_state(self.dir), state)
        self.assertFalse((self.dir / "data.db.previous").exists())

    def test_a_plain_database_is_used_in_place(self):
        path = self.tmp / "plain.db"
        build_catalogue(path, meta=False)
        used, info = use_catalogue_file(path)
        self.assertEqual(used, path)
        self.assertIsNone(info)

    def test_an_invalid_file_is_an_error(self):
        path = self.tmp / "bad.db"
        path.write_bytes(b"nope")
        with self.assertRaises(CatalogueError):
            use_catalogue_file(path)


class MigrationTests(CatalogueTestCase):
    def legacy(self, **kwargs):
        path = self.tmp / "datasets" / "data.db"
        path.parent.mkdir()
        build_catalogue(path, **kwargs)
        return path

    def test_copies_the_legacy_dataset_once(self):
        legacy = self.legacy(version=V2)

        self.assertTrue(migrate_legacy_dataset(self.dir, legacy))

        self.assertEqual(self.version(), V2)
        self.assertEqual(read_state(self.dir)["version"], V2)
        self.assertEqual((self.dir / "data.db").read_bytes(), legacy.read_bytes())
        self.assertFalse(migrate_legacy_dataset(self.dir, legacy))

    def test_does_not_overwrite_an_installed_dataset(self):
        self.install(V3)
        legacy = self.legacy(version=V1)

        self.assertFalse(migrate_legacy_dataset(self.dir, legacy))
        self.assertEqual(self.version(), V3)

    def test_an_old_or_invalid_legacy_file(self):
        self.assertTrue(migrate_legacy_dataset(self.dir, self.legacy(meta=False)))
        self.assertIsNone(self.version())

        other = self.tmp / "other"
        bad = self.tmp / "bad.db"
        bad.write_bytes(b"nope")
        self.assertFalse(migrate_legacy_dataset(other, bad))
        self.assertFalse(migrate_legacy_dataset(other, self.tmp / "missing.db"))

    def test_ensure_reports_the_migration(self):
        legacy = self.legacy(version=V2)
        _, entry = self.release(V2)
        with (
            patch("cloudexit.catalogue.store.LEGACY_DATABASE", legacy),
            fake_http(self.manifest_reply(entry)),
        ):
            result = ensure_catalogue(BASE, directory=self.dir)

        self.assertTrue(result.migrated)
        self.assertEqual(result.status, "current")


class EnsureCatalogueTests(CatalogueTestCase):
    def test_a_failed_update_keeps_the_local_dataset(self):
        self.install(V1)
        with fake_http(requests.ConnectionError("offline")):
            result = ensure_catalogue(BASE, directory=self.dir)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.version, V1)
        self.assertEqual(result.path, self.dir / "data.db")
        self.assertIn("offline", result.reason)
        self.assertRegex(result.date, r"^\d{4}-\d\d-\d\d$")

    def test_a_failed_update_with_an_unknown_version_dataset(self):
        self.install(meta=False)
        with fake_http(requests.ConnectionError("offline")):
            result = ensure_catalogue(BASE, directory=self.dir)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.version)

    def test_a_failed_update_without_any_dataset_is_unavailable(self):
        with fake_http(requests.ConnectionError("offline")):
            with self.assertRaises(CatalogueUnavailable):
                ensure_catalogue(BASE, directory=self.dir)

    def test_a_server_error_does_not_stop_a_run_with_a_dataset(self):
        self.install(V1)
        with fake_http(make_response(status=503)):
            result = ensure_catalogue(BASE, directory=self.dir)
        self.assertEqual(result.status, "failed")

    def test_updates_and_reports_the_new_version(self):
        self.install(V1)
        gz, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry), make_response(content=gz)):
            result = ensure_catalogue(BASE, directory=self.dir)
        self.assertEqual((result.status, result.version), ("updated", V2))


class StatusTests(CatalogueTestCase):
    def test_shows_installed_and_latest(self):
        self.install(V1)
        _, entry = self.release(V2)
        with fake_http(self.manifest_reply(entry)):
            text = "\n".join(status_lines(BASE, directory=self.dir))

        self.assertIn(f"installed: {V1} (sequence 41)", text)
        self.assertIn("(today)", text)
        self.assertIn(f"latest:    {V2} (sequence 41)", text)
        self.assertIn("an update is available", text)

    def test_shows_how_old_the_installed_dataset_is(self):
        self.install(V1)
        state = read_state(self.dir)
        state["downloaded_at"] = "2020-01-01T00:00:00+00:00"
        (self.dir / "state.json").write_text(json.dumps(state))
        with fake_http(make_response(status=404)):
            text = "\n".join(status_lines(BASE, directory=self.dir))
        self.assertIn("2020-01-01 (", text)
        self.assertIn("days ago)", text)

    def test_nothing_installed_and_no_manifest(self):
        with fake_http(make_response(status=404)):
            text = "\n".join(status_lines(BASE, directory=self.dir))
        self.assertIn("installed: none", text)
        self.assertIn("no manifest is published", text)

    def test_unknown_version_and_unreachable_server(self):
        self.install(meta=False)
        with fake_http(requests.ConnectionError("offline")):
            text = "\n".join(status_lines(BASE, directory=self.dir))
        self.assertIn("unknown (the dataset has no catalogue_meta)", text)
        self.assertIn("unavailable", text)


class LocationTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "absolute /xdg paths are POSIX paths")
    def test_xdg_data_home_and_defaults(self):
        self.assertEqual(
            catalogue_dir({"XDG_DATA_HOME": "/xdg"}, platform="linux"),
            Path("/xdg/cloudexit/catalogue"),
        )
        self.assertEqual(
            catalogue_dir({}, platform="linux"),
            Path.home() / ".local" / "share" / "cloudexit" / "catalogue",
        )
        self.assertEqual(
            catalogue_dir({"XDG_DATA_HOME": "relative"}, platform="linux"),
            Path.home() / ".local" / "share" / "cloudexit" / "catalogue",
        )

    def test_localappdata_on_windows(self):
        self.assertEqual(
            catalogue_dir({"LOCALAPPDATA": "/local"}, platform="win32"),
            Path("/local/cloudexit/catalogue"),
        )

    def test_the_base_url_defaults_and_can_be_overridden(self):
        self.assertEqual(catalogue_base_url(Settings()), CATALOGUE_BASE_URL)
        override = Settings(
            catalogue_url=Setting(BASE + "/", "env CLOUDEXIT_CATALOGUE_URL")
        )
        self.assertEqual(catalogue_base_url(override), BASE)

    def test_a_plain_http_base_url_is_refused_except_for_localhost(self):
        remote = Settings(catalogue_url=Setting("http://example.com", "env X"))
        with self.assertRaises(SettingsError):
            catalogue_base_url(remote)
        local = Settings(catalogue_url=Setting("http://localhost:9000/m", "env X"))
        self.assertEqual(catalogue_base_url(local), "http://localhost:9000/m")


class RunIntegrationTests(CatalogueTestCase):
    def args(self, **kwargs):
        return Namespace(settings=Settings(), catalogue=None, **kwargs)

    def ensure(self, **kwargs):
        defaults = dict(
            path=Path("/x/data.db"), status="current", version=V2, date="2026-11-03"
        )
        defaults.update(kwargs)
        return patch(
            "cloudexit.cli.app.ensure_catalogue", return_value=EnsureResult(**defaults)
        )

    def test_a_failed_update_is_a_visible_step_result(self):
        with (
            self.ensure(status="failed", version=V1, reason="cannot reach server"),
            patch("cloudexit.cli.app.print_step") as mock_step,
        ):
            app.prepare_catalogue(self.args())

        mock_step.assert_called_once_with(
            f"Catalogue update failed, using {V1} from 2026-11-03.",
            status="warning",
            logs="cannot reach server",
        )

    def test_a_failed_update_with_an_unknown_version_says_so(self):
        with (
            self.ensure(status="failed", version=None, reason="offline"),
            patch("cloudexit.cli.app.print_step") as mock_step,
        ):
            app.prepare_catalogue(self.args())
        self.assertIn("using unknown from 2026-11-03", mock_step.call_args.args[0])

    def test_up_to_date_and_updated_steps(self):
        for status, text in (
            ("current", f"Catalogue {V2} is up to date."),
            ("updated", f"Catalogue updated to {V2}."),
        ):
            with self.subTest(status):
                with (
                    self.ensure(status=status),
                    patch("cloudexit.cli.app.print_step") as step,
                ):
                    app.prepare_catalogue(self.args())
                step.assert_called_once_with(text, status="ok")

    def test_without_any_dataset_the_run_stops_with_the_config_code(self):
        with (
            patch(
                "cloudexit.cli.app.ensure_catalogue",
                side_effect=CatalogueUnavailable("no dataset"),
            ),
            patch("cloudexit.cli.app.console.print") as mock_print,
        ):
            with self.assertRaises(SystemExit) as ctx:
                app.prepare_catalogue(self.args())

        self.assertEqual(ctx.exception.code, codes.CONFIG)
        self.assertIn("no dataset", mock_print.call_args.args[0])

    def test_catalogue_flag_uses_the_file_without_updating_or_installing(self):
        self.install(V1)
        before = (self.dir / "data.db").read_bytes()
        path = self.tmp / "run.db.gz"
        path.write_bytes(catalogue_gz(version=V3))
        args = Namespace(settings=Settings(), catalogue=str(path))

        with (
            patch("cloudexit.cli.app.ensure_catalogue") as mock_ensure,
            patch("cloudexit.cli.app.print_step") as mock_step,
        ):
            app.prepare_catalogue(args)

        mock_ensure.assert_not_called()
        self.assertIn(V3, mock_step.call_args.args[0])
        self.assertIn("not installed", mock_step.call_args.args[0])
        self.assertEqual(catalogue_info().version, V3)
        self.assertEqual((self.dir / "data.db").read_bytes(), before)

    def test_a_bad_catalogue_flag_stops_the_run(self):
        args = Namespace(settings=Settings(), catalogue=str(self.tmp / "missing.db"))
        with (
            patch("cloudexit.cli.app.console.print"),
            self.assertRaises(SystemExit) as ctx,
        ):
            app.prepare_catalogue(args)
        self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_the_report_copy_uses_the_dataset_the_run_used(self):
        path = self.tmp / "run.db"
        build_catalogue(path, version=V3)
        set_active_catalogue(path)
        report = self.tmp / "report"
        (report / "data").mkdir(parents=True)

        with patch("cloudexit.report.assets.shutil.copytree"):
            from cloudexit.report.assets import copy_assets

            copy_assets(str(report), 2)

        copied = report / "data" / "assessment.db"
        self.assertEqual(catalogue_info(copied).version, V3)


class CatalogueCommandTests(CatalogueTestCase):
    def run_command(self, *argv, replies=()):
        out = io.StringIO()
        environ = isolated_environ(self.tmp)
        with (
            patch("sys.argv", ["cloudexit", "catalogue", *argv]),
            patch.dict(os.environ, environ, clear=True),
            patch(
                "cloudexit.cli.app.load_settings",
                return_value=Settings(
                    catalogue_url=Setting(BASE, "env CLOUDEXIT_CATALOGUE_URL")
                ),
            ),
            patch("cloudexit.cli.app.prepare_catalogue") as mock_prepare,
            patch("cloudexit.cli.app.console.print") as mock_print,
            fake_http(*replies) as fake,
            contextlib.redirect_stdout(out),
        ):
            try:
                app.main()
                code = None
            except SystemExit as exc:
                code = exc.code
        mock_prepare.assert_not_called()
        return out.getvalue(), code, fake, mock_print

    @property
    def command_dir(self):
        return self.tmp / "data" / "cloudexit" / "catalogue"

    def test_update_installs_the_latest(self):
        gz, entry = self.release(V2)
        out, code, fake, _ = self.run_command(
            "update",
            replies=[self.manifest_reply(entry), make_response(content=gz)],
        )

        self.assertIsNone(code)
        self.assertIn(f"Catalogue updated to {V2}.", out)
        self.assertEqual(catalogue_info(self.command_dir / "data.db").version, V2)

    def test_update_with_a_version(self):
        gz1, entry1 = self.release(V1)
        _, entry2 = self.release(V2)
        out, code, _, _ = self.run_command(
            "update",
            "--version",
            V1,
            replies=[
                self.manifest_reply(entry2, [entry1]),
                make_response(content=gz1),
            ],
        )
        self.assertIsNone(code)
        self.assertIn(V1, out)

    def test_update_failure_exits_with_the_config_code(self):
        out, code, _, mock_print = self.run_command(
            "update", replies=[requests.ConnectionError("offline")]
        )
        self.assertEqual(code, codes.CONFIG)
        self.assertIn("offline", mock_print.call_args.args[0])

    def test_status_prints_the_installed_and_latest_catalogue(self):
        _, entry = self.release(V2)
        out, code, _, _ = self.run_command(
            "status", replies=[self.manifest_reply(entry)]
        )
        self.assertIsNone(code)
        self.assertIn("installed: none", out)
        self.assertIn(f"latest:    {V2}", out)

    def test_import_installs_a_file(self):
        path = self.tmp / "offline.db.gz"
        gz = catalogue_gz(version=V2)
        path.write_bytes(gz)
        out, code, fake, _ = self.run_command(
            "import", str(path), "--sha256", sha256(gz)
        )

        self.assertIsNone(code)
        self.assertEqual(fake.requests, [])
        self.assertIn(f"Catalogue {V2} installed.", out)

    def test_import_of_a_corrupt_file_fails(self):
        path = self.tmp / "bad.db.gz"
        path.write_bytes(b"\x1f\x8b broken")
        _, code, _, _ = self.run_command("import", str(path))
        self.assertEqual(code, codes.CONFIG)
        self.assertFalse((self.command_dir / "data.db").exists())

    def test_the_subcommand_is_required(self):
        with (
            patch("sys.argv", ["cloudexit", "catalogue"]),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            app.parse_arguments()

    def test_the_run_flag_is_accepted_on_aws_and_azure(self):
        for provider in ("aws", "azure"):
            with patch("sys.argv", ["cloudexit", provider, "--catalogue", "f.db.gz"]):
                self.assertEqual(app.parse_arguments().catalogue, "f.db.gz")


class DatabaseContractTests(CatalogueTestCase):
    def test_a_corrupt_state_file_is_ignored(self):
        self.install(V1)
        (self.dir / "state.json").write_text("not json")
        self.assertEqual(read_state(self.dir), {})
        self.assertEqual(self.version(), V1)

    def test_installed_catalogue_ignores_an_empty_file(self):
        self.dir.mkdir()
        (self.dir / "data.db").write_bytes(b"")
        self.assertIsNone(installed_catalogue(self.dir))

    def test_the_catalogue_database_is_opened_read_only_for_info(self):
        path = self.tmp / "data.db"
        build_catalogue(path)
        before = path.read_bytes()
        catalogue_info(path)
        active_resource_type_ids("aws", path)
        self.assertEqual(path.read_bytes(), before)
        with contextlib.closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
