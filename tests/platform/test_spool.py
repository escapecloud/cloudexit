import contextlib
import io
import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudexit.cli import app
from cloudexit.platform.client import ApiError
from cloudexit.platform.spool import (
    AUTO_FLUSH_BUDGET,
    FlushResult,
    Spool,
    flush,
    format_age,
    host_id,
    parse_age,
    spool_root,
    state_dir,
)
from cloudexit.platform.upload import UploadResult, read_sync_files, upload
from tests.support.isolation import isolated_environ
from tests.support.api import (
    HOST,
    KEY,
    created_reply,
    error_body,
    meta_body,
    meta_reply,
    minimal_document,
    reply,
    whoami_reply,
)
from tests.support.http import fake_http
from cloudexit import codes
from cloudexit.settings import Setting, Settings
from tests.support.paths import ROOT

NOW = 1_800_000_000.0
OLD, MIDDLE, NEW = (f"01933b9e-7c2a-7d41-9a51-3c8f0a6e000{n}" for n in (1, 2, 3))
DAY = 86400
POSIX = os.name != "nt"


class Ceiling:
    def uniform(self, low, high):
        return high


def unavailable():
    return reply(503, error_body("service_unavailable", "Later.", True))


def refused(status=422, code="validation_failed"):
    return reply(status, error_body(code, "No.", False))


class SpoolCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.now = NOW
        self.mono = [0.0]
        self.slept = []
        self.spool = Spool(self.tmp / "spool", clock=lambda: self.now)

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.mono[0] += seconds

    def settings(self, **fields):
        values = {
            "host": Setting(HOST, "test"),
            "api_key": Setting(KEY, "test"),
            "spool_dir": Setting(str(self.tmp / "spool"), "test"),
            "upload_budget": Setting(0.0, "test"),
        }
        values.update(fields)
        return Settings(**values)

    def options(self, **more):
        options = {
            "clock": lambda: self.now,
            "monotonic": lambda: self.mono[0],
            "sleep": self.sleep,
            "cache_path": self.tmp / "cache",
            "environ": {},
            "rng": Ceiling(),
        }
        options.update(more)
        return options

    def document(self, sync_id=OLD, age_days=0):
        return minimal_document(sync_id, int(self.now - age_days * DAY - 60))

    def add(self, sync_id=OLD, *, created=None, age_days=0, error=None, **meta):
        if created is not None:
            self.now = created
        entry = self.spool.add(
            HOST,
            self.document(sync_id, age_days),
            error=error,
            attempts=meta.pop("attempts", 1),
            name=meta.pop("name", "Spooled"),
            exit_strategy=meta.pop("exit_strategy", 1),
            assessment_id=meta.pop("assessment_id", None),
        )
        self.now = NOW
        return entry


class HostIdTests(unittest.TestCase):
    def test_equivalent_spellings_of_a_host_are_one_id(self):
        ids = {
            host_id(host)
            for host in (
                "https://platform.example.com",
                "platform.example.com",
                "HTTPS://Platform.Example.com/",
                "https://platform.example.com:443",
            )
        }
        self.assertEqual(len(ids), 1)

    def test_different_hosts_and_ports_are_different_ids(self):
        ids = {
            host_id(host)
            for host in (
                "https://a.example.com",
                "https://b.example.com",
                "http://localhost:8000",
                "http://localhost:8001",
            )
        }
        self.assertEqual(len(ids), 4)

    def test_the_id_is_short_hex_and_does_not_contain_the_host(self):
        value = host_id("https://platform.example.com")
        self.assertRegex(value, r"^[0-9a-f]{16}$")
        self.assertNotIn("example", value)


class RootTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "absolute /xdg paths are POSIX paths")
    def test_the_state_directory_follows_the_platform_conventions(self):
        self.assertEqual(
            state_dir({"XDG_STATE_HOME": "/xdg"}, platform="linux"),
            Path("/xdg/cloudexit"),
        )
        self.assertEqual(
            state_dir({"XDG_STATE_HOME": "relative"}, platform="linux"),
            Path.home() / ".local" / "state" / "cloudexit",
        )
        self.assertEqual(
            state_dir({"LOCALAPPDATA": "C:/L"}, platform="win32"),
            Path("C:/L") / "cloudexit" / "state",
        )

    @unittest.skipIf(os.name == "nt", "absolute /xdg paths are POSIX paths")
    def test_the_spool_lives_under_the_state_directory_unless_set(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/xdg"}):
            self.assertEqual(spool_root(None), Path("/xdg/cloudexit/spool"))
        settings = Settings(spool_dir=Setting("/elsewhere", "test"))
        self.assertEqual(spool_root(settings), Path("/elsewhere"))


class AgeTests(unittest.TestCase):
    def test_ages(self):
        self.assertEqual(parse_age("30d"), 30 * DAY)
        self.assertEqual(parse_age("12h"), 12 * 3600)
        self.assertEqual(parse_age("90m"), 5400)
        self.assertEqual(parse_age("45s"), 45)
        self.assertEqual(parse_age(" 5D "), 5 * DAY)

    def test_something_that_is_not_an_age_is_refused(self):
        for text in ("30", "d", "-3d", "1w", "", "soon"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_age(text)

    def test_ages_are_shown_in_their_largest_unit(self):
        self.assertEqual(
            [format_age(s) for s in (5, 90, 7200, 3 * DAY, -4)],
            ["5s", "1m", "2h", "3d", "0s"],
        )


class SpoolFilesTests(SpoolCase):
    def test_a_sync_is_stored_under_the_host_id_by_its_sync_id(self):
        entry = self.add()

        directory = self.spool.root / host_id(HOST)
        self.assertEqual(entry.document_path, directory / f"{OLD}.json")
        self.assertEqual(entry.meta_path, directory / f"{OLD}.meta.json")
        self.assertTrue(entry.document_path.is_file())
        self.assertTrue(entry.meta_path.is_file())

    @unittest.skipUnless(POSIX, "file modes")
    def test_files_are_for_the_owner_only(self):
        entry = self.add()

        for path in (entry.document_path, entry.meta_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for path in (self.spool.root, entry.document_path.parent):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_the_host_name_is_not_in_any_path(self):
        entry = self.add()
        self.assertNotIn("platform", str(entry.document_path))
        self.assertNotIn("test", entry.document_path.parent.name)

    def test_the_document_is_stored_as_it_is_and_nothing_else(self):
        document = self.document()
        entry = self.add()

        stored = json.loads(entry.document_path.read_text(encoding="utf-8"))
        self.assertEqual(stored, document)

    def test_the_meta_holds_the_attempts_and_what_a_later_upload_needs(self):
        error = ApiError("maintenance", "m", 12, request_id="req-1", retryable=True)
        entry = self.add(
            error=error, attempts=3, name="Prod", exit_strategy=3, assessment_id="a"
        )

        meta = json.loads(entry.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(
            meta,
            {
                "sync_id": OLD,
                "host_id": host_id(HOST),
                "created": int(NOW),
                "first_attempt": int(NOW),
                "last_attempt": int(NOW),
                "attempts": 3,
                "last_error": "maintenance",
                "request_id": "req-1",
                "name": "Prod",
                "exit_strategy": 3,
                "assessment_id": "a",
            },
        )

    def test_adding_again_adds_the_attempts_and_keeps_the_first_times(self):
        self.add(attempts=2, created=NOW - 100)
        self.now = NOW
        later = ApiError("timeout", "t", 12, request_id="req-9", retryable=True)
        entry = self.add(error=later, attempts=4)

        self.assertEqual(entry.meta["attempts"], 6)
        self.assertEqual(entry.meta["created"], int(NOW - 100))
        self.assertEqual(entry.meta["first_attempt"], int(NOW - 100))
        self.assertEqual(entry.meta["last_attempt"], int(NOW))
        self.assertEqual(entry.meta["last_error"], "timeout")

    def test_the_key_never_reaches_the_files(self):
        error = ApiError("invalid_api_key", f"bad {KEY}", 11, request_id="r")
        self.add(error=error)

        for path in self.spool.root.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                self.assertNotIn(KEY.encode(), data)
                self.assertNotIn(KEY[8:].encode(), data)
                self.assertNotIn(b"Bearer", data)
                self.assertNotIn(b"Authorization", data)

    def test_a_sync_id_that_could_leave_the_directory_is_refused(self):
        for sync_id in ("../escape", "a/b", "", None, "x"):
            with self.subTest(sync_id=sync_id):
                document = {"sync": {"id": sync_id}}
                with self.assertRaises(ValueError):
                    self.spool.add(
                        HOST,
                        document,
                        error=None,
                        attempts=1,
                        name=None,
                        exit_strategy=None,
                        assessment_id=None,
                    )
        self.assertFalse(list(self.tmp.rglob("escape*")))

    def test_no_temporary_file_is_left_behind(self):
        self.add()
        names = [p.name for p in self.spool.root.rglob("*") if p.is_file()]
        self.assertEqual(sorted(names), [f"{OLD}.json", f"{OLD}.meta.json"])


class EntriesTests(SpoolCase):
    def test_entries_come_oldest_first(self):
        self.add(NEW, created=NOW - 10)
        self.add(OLD, created=NOW - 300)
        self.add(MIDDLE, created=NOW - 100)

        self.assertEqual([e.sync_id for e in self.spool.entries()], [OLD, MIDDLE, NEW])

    def test_entries_can_be_limited_to_one_host(self):
        self.add(OLD)
        self.spool.add(
            "https://other.example.com",
            self.document(NEW),
            error=None,
            attempts=1,
            name=None,
            exit_strategy=None,
            assessment_id=None,
        )

        self.assertEqual(len(self.spool.entries()), 2)
        self.assertEqual([e.sync_id for e in self.spool.entries(host_id(HOST))], [OLD])

    def test_a_document_whose_meta_was_lost_is_still_listed(self):
        entry = self.add()
        entry.meta_path.unlink()

        (found,) = self.spool.entries()
        self.assertEqual(found.sync_id, OLD)
        self.assertEqual(found.meta["sync_id"], OLD)

    def test_other_files_in_the_spool_are_ignored(self):
        entry = self.add()
        (entry.document_path.parent / "notes.txt").write_text("x")
        (entry.document_path.parent / ".hidden.json").write_text("{}")

        self.assertEqual([e.sync_id for e in self.spool.entries()], [OLD])

    def test_an_empty_or_missing_spool_has_no_entries(self):
        self.assertEqual(self.spool.entries(), [])
        self.assertEqual(Spool(self.tmp / "nowhere").entries(), [])

    def test_remove_deletes_both_files_and_the_empty_host_directory(self):
        entry = self.add()
        self.spool.remove(entry)

        self.assertFalse(entry.document_path.exists())
        self.assertFalse(entry.meta_path.exists())
        self.assertFalse(entry.document_path.parent.exists())

    def test_purge_by_age_keeps_the_newer_entries(self):
        self.add(OLD, created=NOW - 40 * DAY)
        self.add(MIDDLE, created=NOW - 10 * DAY)
        self.add(NEW, created=NOW - 1 * DAY)

        removed = self.spool.purge(older_than=30 * DAY)

        self.assertEqual([e.sync_id for e in removed], [OLD])
        self.assertEqual([e.sync_id for e in self.spool.entries()], [MIDDLE, NEW])

    def test_purge_without_an_age_removes_everything_of_the_host(self):
        self.add(OLD)
        self.add(MIDDLE)
        self.spool.add(
            "https://other.example.com",
            self.document(NEW),
            error=None,
            attempts=1,
            name=None,
            exit_strategy=None,
            assessment_id=None,
        )

        self.spool.purge(host=host_id(HOST))

        self.assertEqual([e.sync_id for e in self.spool.entries()], [NEW])


class UploadSpoolTests(SpoolCase):
    def run_upload(self, replies, *, document=None, client=None, **kwargs):
        options = self.options(budget=5.0, **(client or {}))
        with fake_http(*replies) as fake:
            result = upload(
                document or self.document(),
                kwargs.pop("settings", self.settings()),
                name="Spooled",
                meta={"exit_strategy": 1},
                spool=self.spool,
                **kwargs,
                **options,
            )
        return result, fake

    def test_a_budget_that_runs_out_on_retryable_failures_spools_the_sync(self):
        replies = [meta_reply(), whoami_reply(), *[unavailable()] * 3]
        result, fake = self.run_upload(replies)

        self.assertTrue(result.failed)
        self.assertTrue(result.spooled)
        self.assertEqual(result.exit_code, codes.UPLOAD_UNAVAILABLE)
        (entry,) = self.spool.entries()
        self.assertEqual(entry.sync_id, OLD)
        self.assertEqual(entry.meta["attempts"], 3)
        self.assertEqual(entry.meta["last_error"], "service_unavailable")
        self.assertEqual(entry.meta["name"], "Spooled")
        self.assertEqual(self.spool.read(entry), self.document())

    def test_a_platform_that_cannot_be_reached_spools_too(self):
        import requests

        result, _ = self.run_upload(
            [
                meta_reply(),
                whoami_reply(),
                *[requests.ConnectionError("refused")] * 3,
            ],
            settings=self.settings(),
        )
        self.assertTrue(result.spooled)
        self.assertEqual(
            self.spool.entries()[0].meta["last_error"], "connection_failed"
        )

    def test_a_final_failure_is_not_spooled_by_default(self):
        for status, code in (
            (400, "invalid_json"),
            (401, "invalid_api_key"),
            (402, "insufficient_credits"),
            (409, "target_mismatch"),
            (422, "validation_failed"),
        ):
            with self.subTest(code=code):
                result, _ = self.run_upload(
                    [meta_reply(), whoami_reply(), refused(status, code)],
                    client={"cache_path": self.tmp / f"cache-{code}"},
                )
                self.assertFalse(result.spooled)
                self.assertEqual(self.spool.entries(), [])
                self.assertEqual(result.error_code, code)

    def test_spool_on_error_keeps_a_final_failure_for_a_manual_retry(self):
        result, _ = self.run_upload(
            [meta_reply(), whoami_reply(), refused()], spool_on_error=True
        )

        self.assertTrue(result.spooled)
        self.assertEqual(result.exit_code, codes.UPLOAD_REJECTED)
        (entry,) = self.spool.entries()
        self.assertEqual(entry.meta["last_error"], "validation_failed")

    def test_a_bad_key_is_not_spooled_unless_asked(self):
        from tests.support.api import corrupt

        settings = self.settings(api_key=Setting(corrupt(KEY), "test"))
        result, fake = self.run_upload([], settings=settings)
        self.assertFalse(result.spooled)
        self.assertEqual(fake.requests, [])

        result, _ = self.run_upload([], settings=settings, spool_on_error=True)
        self.assertTrue(result.spooled)

    def test_a_sync_that_gets_through_removes_its_spooled_copy(self):
        self.add(OLD)
        result, _ = self.run_upload([meta_reply(), whoami_reply(), created_reply()])

        self.assertTrue(result.success)
        self.assertEqual(self.spool.entries(), [])

    def test_a_spool_that_cannot_be_written_does_not_crash_the_run(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("a file")
        self.spool = Spool(blocker / "spool")
        with self.assertLogs("core.engine.upload", level="WARNING") as logs:
            result, _ = self.run_upload(
                [meta_reply(), whoami_reply(), *[unavailable()] * 3]
            )

        self.assertTrue(result.failed)
        self.assertFalse(result.spooled)
        self.assertTrue(any("could not be spooled" in line for line in logs.output))

    def test_the_key_is_in_no_spool_file_and_no_log(self):
        with self.assertLogs(level=logging.DEBUG) as logs:
            self.run_upload([meta_reply(), whoami_reply(), *[unavailable()] * 3])

        text = "\n".join(logs.output)
        for secret in (KEY, KEY[8:], "Bearer"):
            self.assertNotIn(secret, text)
        for path in self.spool.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_a_missing_document_is_not_spooled(self):
        result = upload(None, self.settings(), name="x", meta={}, spool=self.spool)
        self.assertFalse(result.spooled)
        self.assertEqual(self.spool.entries(), [])


class FlushTests(SpoolCase):
    def run_flush(self, replies, **kwargs):
        kwargs.setdefault("host", HOST)
        settings = self.settings(upload_budget=Setting(5.0, "test"))
        options = self.options(
            **{"cache_path": kwargs.pop("cache_path", self.tmp / "cache")}
        )
        with fake_http(*replies) as fake:
            result = flush(settings, self.spool, **kwargs, **options)
        return result, fake

    def keys(self, fake):
        return [
            r.kwargs["headers"]["Idempotency-Key"]
            for r in fake.requests
            if r.method == "POST"
        ]

    def test_the_oldest_sync_goes_first_and_delivered_ones_are_removed(self):
        self.add(NEW, created=NOW - 10)
        self.add(OLD, created=NOW - 300)
        self.add(MIDDLE, created=NOW - 100)

        result, fake = self.run_flush(
            [
                meta_reply(),
                whoami_reply(),
                created_reply(),
                whoami_reply(),
                created_reply(),
                whoami_reply(),
                created_reply(),
            ]
        )

        self.assertEqual(self.keys(fake), [OLD, MIDDLE, NEW])
        self.assertEqual(result.uploaded, [OLD, MIDDLE, NEW])
        self.assertEqual(self.spool.entries(), [])

    def test_nothing_spooled_means_no_request(self):
        result, fake = self.run_flush([])
        self.assertTrue(result.empty)
        self.assertEqual(fake.requests, [])

    def test_entries_of_another_host_are_left_alone(self):
        self.spool.add(
            "https://other.example.com",
            self.document(NEW),
            error=None,
            attempts=1,
            name=None,
            exit_strategy=None,
            assessment_id=None,
        )
        result, fake = self.run_flush([])

        self.assertTrue(result.empty)
        self.assertEqual(len(self.spool.entries()), 1)

    def test_a_platform_that_stays_down_keeps_the_rest_waiting(self):
        self.add(OLD, created=NOW - 300)
        self.add(MIDDLE, created=NOW - 100)
        self.add(NEW, created=NOW - 10)

        replies = [
            meta_reply(),
            whoami_reply(),
            created_reply(),
            whoami_reply(),
            *[unavailable()] * 3,
        ]
        result, fake = self.run_flush(replies)

        self.assertEqual(result.uploaded, [OLD])
        self.assertEqual(result.pending, [MIDDLE, NEW])
        self.assertEqual(result.stopped, "service_unavailable")
        self.assertEqual(self.keys(fake), [OLD, MIDDLE, MIDDLE, MIDDLE])
        kept = {e.sync_id: e for e in self.spool.entries()}
        self.assertEqual(sorted(kept), [MIDDLE, NEW])
        self.assertEqual(kept[MIDDLE].meta["attempts"], 1 + 3)
        self.assertEqual(kept[MIDDLE].meta["last_error"], "service_unavailable")
        self.assertEqual(kept[NEW].meta["attempts"], 1)

    def test_a_refused_sync_stays_and_the_next_one_is_still_tried(self):
        self.add(OLD, created=NOW - 300)
        self.add(NEW, created=NOW - 10)

        replies = [
            meta_reply(),
            whoami_reply(),
            refused(422, "validation_failed"),
            whoami_reply(),
            created_reply(),
        ]
        result, fake = self.run_flush(replies)

        self.assertEqual(result.failed, {OLD: "validation_failed"})
        self.assertEqual(result.uploaded, [NEW])
        self.assertEqual(self.keys(fake), [OLD, NEW])
        (kept,) = self.spool.entries()
        self.assertEqual(kept.sync_id, OLD)
        self.assertEqual(kept.meta["last_error"], "validation_failed")

    def test_a_failure_every_entry_would_share_stops_the_flush(self):
        for status, code in ((401, "api_key_revoked"), (402, "insufficient_credits")):
            with self.subTest(code=code):
                self.spool.purge()
                self.add(OLD, created=NOW - 300)
                self.add(NEW, created=NOW - 10)

                result, fake = self.run_flush(
                    [meta_reply(), whoami_reply(), refused(status, code)],
                    cache_path=self.tmp / f"cache-{code}",
                )

                self.assertEqual(result.failed, {OLD: code})
                self.assertEqual(result.pending, [NEW])
                self.assertEqual(result.stopped, code)
                self.assertEqual(self.keys(fake), [OLD])

    def test_a_sync_older_than_the_platform_accepts_is_reported_and_kept(self):
        self.add(OLD, created=NOW - 300, age_days=11)
        self.add(NEW, created=NOW - 10, age_days=5)

        meta = meta_reply(limits={"max_collection_age_days": 10})
        result, fake = self.run_flush([meta, whoami_reply(), created_reply()])

        self.assertEqual(result.rejected, [OLD])
        self.assertEqual(result.uploaded, [NEW])
        self.assertEqual(self.keys(fake), [NEW])
        self.assertEqual([e.sync_id for e in self.spool.entries()], [OLD])

    def test_the_default_age_limit_applies_without_a_platform_answer(self):
        self.add(OLD, age_days=31)
        meta = meta_body()
        del meta["limits"]["max_collection_age_days"]

        result, fake = self.run_flush([reply(200, meta)])

        self.assertEqual(result.rejected, [OLD])
        self.assertEqual(self.keys(fake), [])

    def test_the_whole_flush_has_one_budget(self):
        self.add(OLD, created=NOW - 300)
        self.add(NEW, created=NOW - 10)

        def spent(entry, pushed):
            self.mono[0] += 20.0

        result, fake = self.run_flush(
            [meta_reply(), whoami_reply(), created_reply()],
            budget=AUTO_FLUSH_BUDGET - 45,
            on_delivered=spent,
        )

        self.assertEqual(result.uploaded, [OLD])
        self.assertEqual(result.pending, [NEW])
        self.assertEqual(result.stopped, "budget")
        self.assertEqual(self.keys(fake), [OLD])

    def test_an_entry_is_sent_with_what_was_stored_about_its_target(self):
        self.add(OLD, name="Quarterly", exit_strategy=3)

        _, fake = self.run_flush(
            [meta_reply(), whoami_reply(kind="account"), created_reply()]
        )

        body = json.loads(fake.requests[-1].kwargs["data"])
        self.assertEqual(
            body["target"],
            {"create": {"name": "Quarterly", "exit_strategy": "alternate_cloud"}},
        )

    def test_a_document_that_cannot_be_read_is_reported(self):
        entry = self.add(OLD)
        entry.document_path.write_text("{not json")

        result, _ = self.run_flush([meta_reply()])

        self.assertEqual(result.failed, {OLD: "unreadable"})

    def test_the_stored_body_is_what_the_first_attempt_sent(self):
        document = self.document()
        _, first = self.run_upload(document)
        self.add(OLD)
        _, second = self.run_flush([meta_reply(), whoami_reply(), created_reply()])

        self.assertEqual(
            first.requests[-1].kwargs["data"], second.requests[-1].kwargs["data"]
        )

    def run_upload(self, document):
        with fake_http(meta_reply(), whoami_reply(), created_reply()) as fake:
            upload(
                document,
                self.settings(),
                name="Spooled",
                meta={"exit_strategy": 1},
                spool=self.spool,
                **self.options(cache_path=self.tmp / "cache-first"),
            )
        return None, fake


class AutoFlushTests(SpoolCase):
    CONFIG = {
        "name": "Auto",
        "cloudServiceProvider": 2,
        "exitStrategy": 1,
        "assessmentType": 1,
        "providerDetails": {
            "accessKey": "a",
            "secretKey": "b",
            "region": "eu-central-1",
        },
    }

    def run_run(
        self, *, flush_spool=True, flush_result=None, flush_error=None, dry_run=False
    ):
        order, steps = [], []
        report = self.tmp / "r"
        (report / "data").mkdir(parents=True, exist_ok=True)
        raw = self.tmp / "raw"
        raw.mkdir(exist_ok=True)

        def flushing(*args, **kwargs):
            order.append(("flush", kwargs))
            if flush_error:
                raise flush_error
            return flush_result or FlushResult()

        def directory():
            order.append(("directory", None))
            return str(report), str(raw)

        patches = [
            patch("cloudexit.cli.app.validate_config"),
            patch("cloudexit.cli.app.flush", side_effect=flushing),
            patch("cloudexit.cli.app.create_directory", side_effect=directory),
            patch("cloudexit.cli.app.add_run_log_handler"),
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
            patch("cloudexit.cli.app.prepare_sync_document", return_value=None),
            patch(
                "cloudexit.cli.app.upload",
                side_effect=lambda *a, **k: order.append(("upload", k))
                or UploadResult(True, True, message="uploaded", sync_id=OLD),
            ),
            patch(
                "cloudexit.cli.app.print_step",
                side_effect=lambda *a, **k: steps.append((a, k)),
            ),
            patch("cloudexit.cli.app.console.print"),
            patch("cloudexit.cli.app.console.status"),
        ]
        for item in patches:
            item.start()
        try:
            app.run_assessment(
                dict(self.CONFIG),
                "aws",
                non_interactive=True,
                settings=self.settings(),
                flush_spool=flush_spool,
                dry_run=dry_run,
            )
        finally:
            for item in reversed(patches):
                item.stop()
        return order, steps

    def test_earlier_syncs_are_delivered_before_the_new_run_starts(self):
        self.add(OLD)
        order, _ = self.run_run(flush_result=FlushResult(uploaded=[OLD]))

        names = [name for name, _ in order]
        self.assertEqual(names[:2], ["flush", "directory"])
        self.assertIn("upload", names)

    def test_the_flush_has_a_shorter_budget(self):
        self.add(OLD)
        order, _ = self.run_run()

        self.assertEqual(order[0][1]["budget"], AUTO_FLUSH_BUDGET)
        self.assertEqual(AUTO_FLUSH_BUDGET, 60.0)

    def test_no_flush_skips_it(self):
        self.add(OLD)
        order, _ = self.run_run(flush_spool=False)

        self.assertNotIn("flush", [name for name, _ in order])
        self.assertIn("upload", [name for name, _ in order])

    def test_nothing_spooled_means_no_flush(self):
        order, _ = self.run_run()
        self.assertNotIn("flush", [name for name, _ in order])

    def test_a_dry_run_does_not_flush(self):
        self.add(OLD)
        order, _ = self.run_run(dry_run=True)
        self.assertNotIn("flush", [name for name, _ in order])

    def test_what_was_delivered_and_what_waits_is_said(self):
        self.add(OLD)
        result = FlushResult(
            uploaded=[OLD, MIDDLE], pending=[NEW], stopped="maintenance"
        )
        _, steps = self.run_run(flush_result=result)

        text = " | ".join(str(a[0]) + " " + str(k.get("logs", "")) for a, k in steps)
        self.assertIn("Delivered 2 spooled syncs from earlier runs.", text)
        self.assertIn("1 spooled sync still waiting.", text)
        self.assertIn("maintenance", text)

    def test_a_failing_flush_never_blocks_the_new_run(self):
        self.add(OLD)
        for error in (
            ApiError("maintenance", "The platform is down.", 12, retryable=True),
            RuntimeError("boom"),
            OSError("disk"),
        ):
            with self.subTest(error=type(error).__name__):
                order, steps = self.run_run(flush_error=error)

                self.assertIn("upload", [name for name, _ in order])
                self.assertIn(
                    "Delivering earlier syncs failed.", [a[0] for a, _ in steps]
                )

    def test_a_run_whose_upload_was_spooled_says_how_to_deliver_it(self):
        spooled = UploadResult(
            True, False, 12, "upload_unavailable", "later", OLD, spooled=True
        )
        steps = []
        with (
            patch("cloudexit.cli.app.validate_config"),
            patch(
                "cloudexit.cli.app.create_directory",
                return_value=(str(self.tmp / "r"), str(self.tmp / "raw")),
            ),
            patch("cloudexit.cli.app.add_run_log_handler"),
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
            patch("cloudexit.cli.app.prepare_sync_document", return_value=None),
            patch("cloudexit.cli.app.write_upload_record"),
            patch("cloudexit.cli.app.upload", return_value=spooled),
            patch(
                "cloudexit.cli.app.print_step",
                side_effect=lambda *a, **k: steps.append((a, k)),
            ),
            patch("cloudexit.cli.app.console.print"),
            patch("cloudexit.cli.app.console.status"),
            self.assertRaises(SystemExit) as ctx,
        ):
            (self.tmp / "r" / "data").mkdir(parents=True, exist_ok=True)
            (self.tmp / "raw").mkdir(exist_ok=True)
            app.run_assessment(
                dict(self.CONFIG),
                "aws",
                non_interactive=True,
                settings=self.settings(),
                flush_spool=False,
            )

        self.assertEqual(ctx.exception.code, codes.UPLOAD_UNAVAILABLE)
        logs = [k.get("logs") for _, k in steps]
        self.assertIn("spooled; run `cloudexit spool flush`", logs)


class CommandTestCase(SpoolCase):
    def document(self, sync_id=OLD, age_days=0):
        # The commands read the real clock, so the documents are recent
        return minimal_document(sync_id, int(time.time() - age_days * DAY - 60))

    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLOUDEXIT_")}
        env.update(
            {
                "CLOUDEXIT_HOST": HOST,
                "CLOUDEXIT_API_KEY": KEY,
                "CLOUDEXIT_SPOOL_DIR": str(self.tmp / "spool"),
                "CLOUDEXIT_UPLOAD_BUDGET": "0",
                **isolated_environ(self.tmp),
            }
        )
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, **values):
        import argparse

        return argparse.Namespace(**values)

    def run_command(self, handler, args, replies=()):
        out = io.StringIO()
        code = 0
        with (
            contextlib.redirect_stdout(out),
            patch(
                "cloudexit.cli.app.console.print",
                side_effect=lambda *a, **k: out.write(
                    " ".join(str(x) for x in a) + "\n"
                ),
            ),
            fake_http(*replies) as fake,
        ):
            try:
                handler(args)
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue(), fake


class UploadCommandTests(CommandTestCase):
    def files(self):
        folder = self.tmp / "report" / "raw_data"
        folder.mkdir(parents=True)
        (folder / "sync.json").write_text(json.dumps(self.document(OLD)))
        (folder / "upload.json").write_text(json.dumps({"status": "failed"}))
        (folder / "assessment_result.json").write_text(json.dumps({"meta": {}}))
        (folder / "broken.json").write_text("{not json")
        return folder

    def upload_args(self, *paths, **more):
        values = {
            "paths": list(paths),
            "name": None,
            "exit_strategy": None,
            "spool_on_error": False,
        }
        values.update(more)
        return self.args(**values)

    def test_a_directory_uploads_only_its_sync_documents(self):
        folder = self.files()
        with patch("cloudexit.cli.app.validate_document"):
            code, out, fake = self.run_command(
                app.handle_upload,
                self.upload_args(str(folder)),
                [meta_reply(), whoami_reply(), created_reply()],
            )

        self.assertEqual(code, 0, out)
        self.assertEqual(len([r for r in fake.requests if r.method == "POST"]), 1)
        self.assertIn("sync.json: uploaded", out)
        self.assertNotIn("upload.json", out.replace("sync.json", ""))

    def test_several_files_and_folders_are_uploaded_in_the_order_given(self):
        first = self.tmp / "first.json"
        second = self.tmp / "second.json"
        first.write_text(json.dumps(self.document(MIDDLE)))
        second.write_text(json.dumps(self.document(NEW)))
        with patch("cloudexit.cli.app.validate_document"):
            code, out, fake = self.run_command(
                app.handle_upload,
                self.upload_args(str(second), str(first)),
                [
                    meta_reply(),
                    whoami_reply(),
                    created_reply(),
                    whoami_reply(),
                    created_reply(),
                ],
            )

        keys = [
            r.kwargs["headers"]["Idempotency-Key"]
            for r in fake.requests
            if r.method == "POST"
        ]
        self.assertEqual((code, keys), (0, [NEW, MIDDLE]))

    def test_a_file_that_is_not_a_sync_document_is_refused(self):
        other = self.tmp / "other.json"
        other.write_text(json.dumps({"meta": {}}))
        missing = self.tmp / "missing.json"

        code, out, fake = self.run_command(
            app.handle_upload, self.upload_args(str(other), str(missing))
        )

        self.assertEqual(code, codes.UPLOAD_REJECTED)
        self.assertIn("is not a sync document", out)
        self.assertIn("no such file or directory", out)
        self.assertEqual(fake.requests, [])

    def test_a_folder_without_sync_documents_is_reported(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        code, out, _ = self.run_command(app.handle_upload, self.upload_args(str(empty)))

        self.assertEqual(code, codes.UPLOAD_REJECTED)
        self.assertIn("holds no sync documents", out)

    def test_a_document_that_breaks_the_schema_is_not_sent(self):
        file = self.tmp / "sync.json"
        file.write_text(json.dumps(self.document(OLD)))

        code, out, fake = self.run_command(
            app.handle_upload, self.upload_args(str(file))
        )

        self.assertEqual(code, codes.UPLOAD_REJECTED)
        self.assertEqual(fake.requests, [])

    def test_an_upload_that_cannot_get_through_is_spooled_and_exits_12(self):
        file = self.tmp / "sync.json"
        file.write_text(json.dumps(self.document(OLD)))
        with patch("cloudexit.cli.app.validate_document"):
            code, out, _ = self.run_command(
                app.handle_upload,
                self.upload_args(str(file)),
                [meta_reply(), whoami_reply(), unavailable()],
            )

        self.assertEqual(code, codes.UPLOAD_UNAVAILABLE)
        self.assertIn("spooled; run `cloudexit spool flush`", out)
        self.assertEqual([e.sync_id for e in self.spool.entries()], [OLD])

    def test_spool_on_error_keeps_a_refused_upload(self):
        file = self.tmp / "sync.json"
        file.write_text(json.dumps(self.document(OLD)))
        with patch("cloudexit.cli.app.validate_document"):
            code, out, _ = self.run_command(
                app.handle_upload,
                self.upload_args(str(file), spool_on_error=True),
                [meta_reply(), whoami_reply(), refused()],
            )

        self.assertEqual(code, codes.UPLOAD_REJECTED)
        self.assertEqual(len(self.spool.entries()), 1)

    def test_the_charge_and_the_link_are_shown(self):
        file = self.tmp / "sync.json"
        file.write_text(json.dumps(self.document(OLD)))
        billing = {"class": "new", "charged_credits": 20, "balance": 380}
        with patch("cloudexit.cli.app.validate_document"):
            code, out, _ = self.run_command(
                app.handle_upload,
                self.upload_args(str(file)),
                [meta_reply(), whoami_reply(), created_reply(billing=billing)],
            )

        self.assertIn("Charged 20 credits; balance 380.", out)
        self.assertIn("https://platform.test/assessments/a-1", out)

    def test_an_account_key_needs_to_be_told_where_the_sync_goes(self):
        file = self.tmp / "sync.json"
        file.write_text(json.dumps(self.document(OLD)))
        with patch("cloudexit.cli.app.validate_document"):
            code, out, _ = self.run_command(
                app.handle_upload,
                self.upload_args(str(file)),
                [meta_reply(), whoami_reply(kind="account")],
            )

        self.assertEqual(code, codes.UPLOAD_REJECTED)
        self.assertIn("invalid_target", out)

    def test_without_a_platform_there_is_nothing_to_upload_to(self):
        with patch.dict(os.environ, {"CLOUDEXIT_API_KEY": ""}):
            code, out, fake = self.run_command(
                app.handle_upload, self.upload_args(str(self.tmp / "x.json"))
            )
        self.assertEqual(code, codes.UPLOAD_AUTH)
        self.assertIn("No platform is configured", out)

    def test_a_mistyped_key_is_refused_before_any_request(self):
        from tests.support.api import corrupt

        with patch.dict(os.environ, {"CLOUDEXIT_API_KEY": corrupt(KEY)}):
            code, out, fake = self.run_command(
                app.handle_upload, self.upload_args(str(self.tmp / "x.json"))
            )
        self.assertEqual(code, codes.UPLOAD_AUTH)
        self.assertEqual(fake.requests, [])

    def test_read_sync_files_finds_documents_by_their_shape(self):
        folder = self.files()
        found, problems = read_sync_files([str(folder)])

        self.assertEqual([p.name for p, _ in found], ["sync.json"])
        self.assertEqual(problems, [])


class SpoolCommandTests(CommandTestCase):
    def spool_args(self, command, **more):
        values = {
            "spool_command": command,
            "host": None,
            "older_than": None,
            "yes": False,
        }
        values.update(more)
        return self.args(**values)

    def test_list_shows_id_host_age_attempts_and_last_error(self):
        error = ApiError("maintenance", "m", 12, request_id="r", retryable=True)
        self.add(OLD, created=NOW - 2 * DAY, error=error, attempts=4)
        with patch("time.time", return_value=NOW):
            code, out, _ = self.run_command(app.handle_spool, self.spool_args("list"))

        lines = out.strip().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(
            lines[0].split(), ["SYNC", "ID", "HOST", "AGE", "ATTEMPTS", "LAST", "ERROR"]
        )
        self.assertEqual(
            lines[1].split(), [OLD, host_id(HOST), "2d", "4", "maintenance"]
        )

    def test_list_marks_what_the_platform_will_reject(self):
        self.add(OLD, created=NOW - 31 * DAY)
        with patch("time.time", return_value=NOW):
            _, out, _ = self.run_command(app.handle_spool, self.spool_args("list"))
        self.assertIn("will be rejected (too old)", out)

    def test_list_of_an_empty_spool(self):
        _, out, _ = self.run_command(app.handle_spool, self.spool_args("list"))
        self.assertIn("The spool is empty.", out)

    def test_list_can_be_limited_to_one_host(self):
        self.add(OLD)
        _, out, _ = self.run_command(
            app.handle_spool, self.spool_args("list", host="https://other.example.com")
        )
        self.assertIn("The spool is empty.", out)

    def test_flush_delivers_and_clears_the_spool(self):
        self.add(OLD)
        code, out, fake = self.run_command(
            app.handle_spool,
            self.spool_args("flush"),
            [meta_reply(), whoami_reply(), created_reply()],
        )

        self.assertEqual(code, 0)
        self.assertIn("Delivered 1 spooled sync", out)
        self.assertEqual(self.spool.entries(), [])

    def test_flush_exits_12_while_something_still_waits(self):
        self.add(OLD)
        code, out, _ = self.run_command(
            app.handle_spool,
            self.spool_args("flush"),
            [meta_reply(), whoami_reply(), unavailable()],
        )

        self.assertEqual(code, codes.UPLOAD_UNAVAILABLE)
        self.assertIn("still waiting", out)
        self.assertEqual(len(self.spool.entries()), 1)

    def test_flush_of_an_empty_spool(self):
        code, out, fake = self.run_command(app.handle_spool, self.spool_args("flush"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing to deliver", out)
        self.assertEqual(fake.requests, [])

    def test_flush_can_name_the_host(self):
        self.add(OLD)
        code, out, fake = self.run_command(
            app.handle_spool,
            self.spool_args("flush", host="https://other.example.com"),
        )
        self.assertIn("Nothing to deliver", out)
        self.assertEqual(len(self.spool.entries()), 1)

    def test_flush_needs_a_platform(self):
        with patch.dict(os.environ, {"CLOUDEXIT_HOST": ""}):
            code, out, _ = self.run_command(app.handle_spool, self.spool_args("flush"))
        self.assertEqual(code, codes.UPLOAD_AUTH)

    def test_purge_asks_first_and_deletes_on_yes(self):
        self.add(OLD)
        self.add(NEW)
        with patch("builtins.input", return_value="y") as asked:
            code, out, _ = self.run_command(app.handle_spool, self.spool_args("purge"))

        self.assertIn("Delete 2 spooled syncs?", asked.call_args.args[0])
        self.assertIn("Deleted 2 spooled syncs.", out)
        self.assertEqual(self.spool.entries(), [])

    def test_purge_keeps_everything_on_no(self):
        self.add(OLD)
        for answer in ("n", "", "no"):
            with self.subTest(answer=answer):
                with patch("builtins.input", return_value=answer):
                    _, out, _ = self.run_command(
                        app.handle_spool, self.spool_args("purge")
                    )
                self.assertIn("Nothing deleted.", out)
                self.assertEqual(len(self.spool.entries()), 1)

    def test_purge_with_yes_does_not_ask(self):
        self.add(OLD)
        with patch("builtins.input", side_effect=AssertionError("asked")):
            _, out, _ = self.run_command(
                app.handle_spool, self.spool_args("purge", yes=True)
            )
        self.assertEqual(self.spool.entries(), [])

    def test_purge_by_age_leaves_the_newer_syncs(self):
        self.add(OLD, created=NOW - 40 * DAY)
        self.add(NEW, created=NOW - 1 * DAY)
        with patch("time.time", return_value=NOW):
            _, out, _ = self.run_command(
                app.handle_spool, self.spool_args("purge", older_than="30d", yes=True)
            )

        self.assertIn("Deleted 1 spooled sync.", out)
        self.assertEqual([e.sync_id for e in self.spool.entries()], [NEW])

    def test_purge_of_nothing(self):
        _, out, _ = self.run_command(
            app.handle_spool, self.spool_args("purge", yes=True)
        )
        self.assertIn("Nothing to purge.", out)

    def test_purge_with_a_bad_age_is_a_configuration_error(self):
        code, out, _ = self.run_command(
            app.handle_spool, self.spool_args("purge", older_than="soon")
        )
        self.assertEqual(code, codes.CONFIG)

    def test_purge_without_a_terminal_says_to_use_yes(self):
        self.add(OLD)
        with patch("builtins.input", side_effect=EOFError):
            code, out, _ = self.run_command(app.handle_spool, self.spool_args("purge"))

        self.assertEqual(code, codes.CONFIG)
        self.assertEqual(len(self.spool.entries()), 1)


class SpoolCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("CLOUDEXIT_", "ESC_"))
        }
        self.env.update(
            {
                **isolated_environ(self.tmp),
                "COLUMNS": "200",
            }
        )

    def run_cli(self, *argv, stdin=None):
        result = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), *argv],
            cwd=self.tmp,
            env=self.env,
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode, " ".join((result.stdout + result.stderr).split())

    def test_the_commands_run_without_a_platform_where_they_can(self):
        code, out = self.run_cli("spool", "list")
        self.assertEqual((code, out), (0, "The spool is empty."))
        code, out = self.run_cli("spool", "purge", "--yes")
        self.assertEqual((code, out), (0, "Nothing to purge."))

    def test_upload_and_flush_need_a_platform(self):
        for argv in (("upload", "sync.json"), ("spool", "flush")):
            with self.subTest(argv=argv):
                code, out = self.run_cli(*argv)
                self.assertEqual(code, codes.UPLOAD_AUTH, out)
                self.assertIn("No platform is configured", out)
                self.assertNotIn("Traceback", out)

    def test_the_help_lists_the_new_commands_and_flags(self):
        code, out = self.run_cli("--help")
        self.assertEqual(code, 0)
        for word in ("upload", "spool"):
            self.assertIn(word, out)
        code, out = self.run_cli("aws", "--help")
        for flag in ("--upload-budget", "--spool-on-error", "--no-flush"):
            self.assertIn(flag, out)
        code, out = self.run_cli("spool", "purge", "--help")
        for flag in ("--older-than", "--yes"):
            self.assertIn(flag, out)


if __name__ == "__main__":
    unittest.main()
