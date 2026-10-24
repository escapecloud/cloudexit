import copy
import inspect
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import requests

import main
from core.engine import generate_report, perform_risk_assessment
from core.sync_document import SyncDocumentError
from core.upload import (
    REASON_REJECTED,
    REASON_UNAVAILABLE,
    UploadResult,
    failure,
    report_info,
    upload,
    write_upload_record,
)
from tests.api_fixtures import (
    ASSESSMENT_ID,
    KEY,
    URL,
    corrupt,
    error_body,
    meta_reply,
    minimal_document,
    reply,
    success_replies,
    sync_body,
    whoami_reply,
)
from tests.flow_fixtures import LOCAL_RISKS, SERVER_SCORES, build_flow_run
from tests.http_fixtures import fake_http, make_response
from tests.sync_fixtures import SYNC_ID, egress_row
from utils import codes
from utils.settings import Setting, Settings

AWS_RESOURCES = [(1, "eu-central-1", 8), (4, "eu-central-1", 2)]
COSTS = [("2026-09-01", 12.5, "USD")]
CONFIG = {
    "name": "Flow Test",
    "cloudServiceProvider": 2,
    "exitStrategy": 1,
    "assessmentType": 2,
    "providerDetails": {
        "accessKey": "AKIAFAKEACCESSKEY1234",
        "secretKey": "fake-secret-key-AAAABBBBCCCC",
        "region": "eu-central-1",
        "accountId": "123456789012",
    },
}
CONFIGURED = Settings(
    host=Setting("https://platform.test", "env CLOUDEXIT_HOST"),
    api_key=Setting(KEY, "env CLOUDEXIT_API_KEY"),
    upload_budget=Setting(0.0, "env CLOUDEXIT_UPLOAD_BUDGET"),
)


def failed_replies(status, code="service_unavailable", retryable=True):
    if status is None:
        return [meta_reply(), whoami_reply(), requests.ConnectionError("refused")]
    body = error_body(code, "server said no", retryable)
    return [meta_reply(), whoami_reply(), reply(status, body)]


class FlowTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.tmp / "data"),
                "XDG_CACHE_HOME": str(self.tmp / "cache"),
                "XDG_STATE_HOME": str(self.tmp / "state"),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.counter = 0

    def forget_meta(self):
        shutil.rmtree(self.tmp / "cache", ignore_errors=True)

    def report(self, **kwargs):
        self.counter += 1
        kwargs.setdefault("resources", AWS_RESOURCES)
        kwargs.setdefault("costs", COSTS)
        return build_flow_run(self.tmp / f"report-{self.counter}", **kwargs)

    def rows(self, report, table):
        conn = sqlite3.connect(Path(report) / "data" / "assessment.db")
        try:
            return conn.execute(f"SELECT * FROM {table}").fetchall()
        finally:
            conn.close()

    def raw(self, report, name):
        return Path(report) / "raw_data" / name

    def json_report(self, report):
        return json.loads(self.raw(report, "assessment_result.json").read_text())

    def html(self, report):
        return (Path(report) / "index.html").read_text(encoding="utf-8")


class LocalResultsTests(FlowTestCase):
    def test_the_risk_assessment_has_no_online_mode_any_more(self):
        parameters = list(inspect.signature(perform_risk_assessment).parameters)
        self.assertEqual(parameters, ["exit_strategy", "report_path"])

    def test_the_risk_assessment_computes_the_local_risks(self):
        report = self.report()

        result = perform_risk_assessment(1, report)

        self.assertTrue(result["success"])
        self.assertEqual(
            [(row[1], row[2]) for row in self.rows(report, "risk_inventory")],
            LOCAL_RISKS,
        )

    def test_platform_scores_left_in_the_database_are_never_rendered(self):
        report = self.report(platform_scores=True)
        perform_risk_assessment(1, report)

        for assessment_type in (1, 2):
            with self.subTest(assessment_type=assessment_type):
                result = generate_report(
                    2,
                    CONFIG["providerDetails"],
                    1,
                    assessment_type,
                    "Flow",
                    report,
                    str(Path(report) / "raw_data"),
                )
                self.assertTrue(result["success"], result.get("logs"))

                self.assertNotIn("scoring_data", self.json_report(report)["data"])
                html = self.html(report)
                self.assertNotIn(f"exitScoreValue = {SERVER_SCORES[0]}", html)
                (
                    self.assertNotIn("Scoring #1 - Exit Readiness", html)
                    if (assessment_type == 1)
                    else None
                )

    def test_an_offline_report_has_no_upload_trace(self):
        report = self.report()
        perform_risk_assessment(1, report)

        result = generate_report(
            2,
            CONFIG["providerDetails"],
            1,
            1,
            "Flow",
            report,
            str(Path(report) / "raw_data"),
        )

        self.assertTrue(result["success"])
        document = self.json_report(report)
        self.assertNotIn("upload", document["meta"])
        self.assertEqual(
            sorted(document["data"]),
            [
                "alternative_technologies",
                "cost_inventory",
                "resource_inventory",
                "risk_inventory",
            ],
        )
        self.assertEqual(len(document["data"]["resource_inventory"]), 2)
        self.assertEqual(
            sorted(int(entry["id"]) for entry in document["data"]["risk_inventory"]),
            [2, 4],
        )
        self.assertNotIn("Not uploaded", self.html(report))
        self.assertTrue(Path(result["reports"]["PDF"]).is_file())

    def generate(self, report, upload_info):
        return generate_report(
            2,
            CONFIG["providerDetails"],
            1,
            1,
            "Flow",
            report,
            str(Path(report) / "raw_data"),
            upload=upload_info,
        )

    def test_a_failed_upload_puts_a_banner_in_every_report(self):
        report = self.report()
        perform_risk_assessment(1, report)
        info = {"status": "failed", "reason": REASON_UNAVAILABLE, "sync_id": SYNC_ID}

        result = self.generate(report, info)

        self.assertTrue(result["success"], result.get("logs"))
        self.assertIn("Not uploaded: upload_unavailable", self.html(report))
        self.assertEqual(self.json_report(report)["meta"]["upload"], info)
        self.assertTrue(Path(result["reports"]["PDF"]).is_file())

    def test_a_successful_upload_records_only_the_link(self):
        report = self.report()
        perform_risk_assessment(1, report)
        info = {
            "status": "uploaded",
            "sync_id": SYNC_ID,
            "assessment_id": ASSESSMENT_ID,
            "url": URL,
        }

        result = self.generate(report, info)

        self.assertTrue(result["success"], result.get("logs"))
        html = self.html(report)
        self.assertIn(f'href="{URL}"', html)
        self.assertIn("Open the platform report", html)
        self.assertEqual(self.json_report(report)["meta"]["upload"], info)

    def test_banner_text_is_escaped(self):
        report = self.report()
        perform_risk_assessment(1, report)
        info = {"status": "failed", "reason": "<script>alert(1)</script>"}

        self.generate(report, info)

        self.assertNotIn("<script>alert(1)</script>", self.html(report))


class UploadInterfaceTests(FlowTestCase):
    def call(self, replies, document=None, settings=CONFIGURED):
        self.forget_meta()
        with fake_http(*replies) as fake:
            result = upload(
                document if document is not None else minimal_document(SYNC_ID),
                settings,
                name="Flow",
                meta={"assessment_type": 1, "exit_strategy": 1},
            )
        return result, fake

    def test_success_carries_the_link_and_nothing_else(self):
        result, fake = self.call(success_replies())

        self.assertTrue(result.success)
        self.assertFalse(result.failed)
        self.assertEqual(
            (result.sync_id, result.assessment_id, result.url),
            (SYNC_ID, ASSESSMENT_ID, URL),
        )
        self.assertEqual(
            [(r.method, r.url) for r in fake.requests],
            [
                ("GET", "https://platform.test/api/v2/meta"),
                ("GET", "https://platform.test/api/v2/whoami"),
                ("POST", "https://platform.test/api/v2/syncs"),
            ],
        )
        self.assertFalse(hasattr(result, "risks"))
        self.assertFalse(hasattr(result, "scores"))

    def test_the_result_carries_the_platform_ids_and_warnings(self):
        warning = {"code": "tier_downgraded", "message": "Granted\nBasic."}
        result, _ = self.call(success_replies(warnings=[warning]))

        self.assertEqual(result.warnings, ("Granted Basic. (tier_downgraded)",))
        self.assertEqual(result.platform_sync_id, sync_body()["id"])
        self.assertEqual(result.request_id, "req-fake-0001")
        self.assertEqual(result.message, "uploaded")

    def test_an_unsafe_link_is_dropped(self):
        result, _ = self.call(
            success_replies(assessment={"url": "javascript:alert(1)"})
        )
        self.assertIsNone(result.url)

    def test_failures_map_to_the_upload_codes(self):
        cases = (
            (503, "service_unavailable", True, REASON_UNAVAILABLE, 12),
            (500, "internal_error", True, REASON_UNAVAILABLE, 12),
            (429, "rate_limited", True, REASON_UNAVAILABLE, 12),
            (None, None, True, REASON_UNAVAILABLE, 12),
            (400, "invalid_json", False, REASON_REJECTED, 10),
            (422, "validation_failed", False, REASON_REJECTED, 10),
            (401, "invalid_api_key", False, "upload_auth", 11),
            (402, "insufficient_credits", False, "upload_quota", 13),
            (404, "assessment_not_found", False, "upload_target", 14),
            (400, "unsupported_schema_version", False, "upload_unsupported", 15),
        )
        for status, code, retryable, reason, exit_code in cases:
            with self.subTest(status=status, code=code):
                result, _ = self.call(failed_replies(status, code, retryable))
                self.assertTrue(result.failed)
                self.assertEqual((result.reason, result.exit_code), (reason, exit_code))
                self.assertEqual(result.sync_id, SYNC_ID)
                if code:
                    self.assertEqual(result.message, "server said no")
                    self.assertEqual(result.error_code, code)
                    self.assertEqual(result.request_id, "req-fake-0001")

    def test_no_document_is_a_rejected_upload_without_a_request(self):
        with fake_http():
            result = upload(None, CONFIGURED, name="Flow", meta={})
        self.assertEqual((result.reason, result.exit_code), (REASON_REJECTED, 10))

    def test_an_unexpected_error_is_a_failed_upload_not_a_crash(self):
        with patch("core.upload.ApiClient.from_settings", side_effect=ValueError("x")):
            result = upload(minimal_document(SYNC_ID), CONFIGURED, name="Flow", meta={})
        self.assertTrue(result.failed)
        self.assertEqual(result.reason, REASON_UNAVAILABLE)
        self.assertIn("ValueError", result.message)

    def test_report_info_and_the_record_file(self):
        success = UploadResult(
            True, True, sync_id=SYNC_ID, assessment_id=ASSESSMENT_ID, url=URL
        )
        failed = failure(REASON_REJECTED, "nope", SYNC_ID)

        self.assertEqual(
            report_info(success),
            {
                "status": "uploaded",
                "sync_id": SYNC_ID,
                "assessment_id": ASSESSMENT_ID,
                "url": URL,
            },
        )
        self.assertEqual(
            report_info(failed),
            {"status": "failed", "reason": REASON_REJECTED, "sync_id": SYNC_ID},
        )
        self.assertIsNone(report_info(None))
        self.assertIsNone(report_info(UploadResult(False, False)))

        detailed = UploadResult(
            True,
            True,
            sync_id=SYNC_ID,
            assessment_id=ASSESSMENT_ID,
            url=URL,
            platform_sync_id="p-1",
            request_id="req-1",
            warnings=("Granted Basic.",),
            pending=True,
        )
        self.assertEqual(report_info(detailed), report_info(success))
        record = json.loads(
            write_upload_record(str(self.tmp), detailed).read_text(encoding="utf-8")
        )
        self.assertEqual(record["warnings"], ["Granted Basic."])
        self.assertEqual(
            (record["platform_sync_id"], record["request_id"], record["pending"]),
            ("p-1", "req-1", True),
        )

        path = write_upload_record(str(self.tmp), success)
        record = json.loads(path.read_text())
        self.assertEqual(record["url"], URL)
        self.assertIn("recorded_at", record)
        self.assertIsNone(
            write_upload_record(str(self.tmp), UploadResult(False, False))
        )
        self.assertNotIn("nope", json.dumps(report_info(failed)))


class RunFlowTests(FlowTestCase):
    def run_assessment(
        self,
        report,
        *,
        settings=None,
        replies=None,
        egress_ok=None,
        assessment_type=2,
        **kwargs,
    ):
        self.steps = []
        raw_data = os.path.join(report, "raw_data")
        console_patch = patch("main.console.print")
        patches = [
            patch("main.validate_config"),
            patch("main.create_directory", return_value=(report, raw_data)),
            patch("main.verify_credentials", return_value=(True, "ok")),
            patch("main.test_permissions", return_value=(True, True, True, "ok")),
            patch(
                "main.create_resource_inventory",
                return_value={"success": True, "logs": ""},
            ),
            patch(
                "main.create_cost_inventory", return_value={"success": True, "logs": ""}
            ),
            patch("main.add_run_log_handler"),
            patch(
                "main.print_step",
                side_effect=lambda *a, **k: self.steps.append((a, k)),
            ),
            console_patch,
        ]
        if egress_ok is not None:
            patches += [
                patch(
                    "main.estimate_egress",
                    return_value={"success": egress_ok, "logs": "x", "json_path": "p"},
                ),
                patch(
                    "main.generate_egress_html_report",
                    return_value={"success": True, "html_path": "h"},
                ),
                patch(
                    "main.generate_egress_pdf_report",
                    return_value={"success": True, "pdf_path": "p"},
                ),
            ]
        for item in patches:
            started = item.start()
            self.addCleanup(item.stop)
            if item is console_patch:
                self.printed = started

        config = copy.deepcopy(CONFIG)
        config["assessmentType"] = assessment_type
        self.forget_meta()
        # The spool has tests of its own; here an earlier run must not answer for this one
        kwargs.setdefault("flush_spool", False)
        with fake_http(*replies) if replies is not None else nullcontext():
            return main.run_assessment(config, "aws", settings=settings, **kwargs)

    def sync(self, report):
        return json.loads(self.raw(report, "sync.json").read_text())

    def statuses(self):
        return [kwargs.get("status") for _, kwargs in self.steps]

    def assert_local_report(self, report):
        for path in (
            Path(report) / "index.html",
            Path(report) / "report.pdf",
            self.raw(report, "assessment_result.json"),
            self.raw(report, "sync.json"),
        ):
            self.assertTrue(path.is_file(), f"{path.name} is missing")

    def test_an_offline_run_forces_basic_and_never_uploads(self):
        report = self.report()
        with patch("main.upload") as mock_upload:
            self.run_assessment(report)

        mock_upload.assert_not_called()
        self.assert_local_report(report)
        self.assertEqual(self.sync(report)["assessment_type"], 1)
        self.assertFalse(self.raw(report, "upload.json").exists())
        self.assertNotIn("upload", self.json_report(report)["meta"])
        self.assertEqual(self.json_report(report)["meta"]["assessment_type"], 1)
        self.assertEqual(
            [(row[1], row[2]) for row in self.rows(report, "risk_inventory")],
            LOCAL_RISKS,
        )
        messages = [args[0] for args, _ in self.steps]
        self.assertIn("Forcing Basic Assessment due to offline mode.", messages)

    def test_the_local_results_are_computed_when_connected_too(self):
        report = self.report()
        self.run_assessment(report, settings=CONFIGURED, replies=success_replies())
        self.assertEqual(
            [(row[1], row[2]) for row in self.rows(report, "risk_inventory")],
            LOCAL_RISKS,
        )

    def test_a_successful_upload_stores_the_link_and_no_platform_results(self):
        report = self.report()

        self.run_assessment(report, settings=CONFIGURED, replies=success_replies())

        self.assert_local_report(report)
        record = json.loads(self.raw(report, "upload.json").read_text())
        self.assertEqual(
            {key: record[key] for key in ("status", "sync_id", "assessment_id", "url")},
            {
                "status": "uploaded",
                "sync_id": self.sync(report)["sync"]["id"],
                "assessment_id": ASSESSMENT_ID,
                "url": URL,
            },
        )
        # No server risks or scores reach the database or the local report
        self.assertEqual(self.rows(report, "scoring_data"), [])
        self.assertEqual(
            [(row[1], row[2]) for row in self.rows(report, "risk_inventory")],
            LOCAL_RISKS,
        )
        document = self.json_report(report)
        self.assertNotIn("scoring_data", document["data"])
        self.assertEqual(document["meta"]["upload"]["url"], URL)
        self.assertIn(URL, self.html(report))
        self.assertNotIn(str(SERVER_SCORES[0]), json.dumps(document["data"]))

    def test_connected_runs_keep_the_requested_depth_in_the_document(self):
        report = self.report()

        self.run_assessment(report, settings=CONFIGURED, replies=success_replies())

        self.assertEqual(self.sync(report)["assessment_type"], 2)
        # The local report is always the Basic report
        self.assertEqual(self.json_report(report)["meta"]["assessment_type"], 1)
        messages = [args[0] for args, _ in self.steps]
        self.assertNotIn("Forcing Basic Assessment due to offline mode.", messages)

    def test_a_failed_upload_still_writes_the_report_and_exits_with_an_upload_code(
        self,
    ):
        for status, code, exit_code, reason in (
            (503, "service_unavailable", codes.UPLOAD_UNAVAILABLE, REASON_UNAVAILABLE),
            (None, None, codes.UPLOAD_UNAVAILABLE, REASON_UNAVAILABLE),
            (422, "validation_failed", codes.UPLOAD_REJECTED, REASON_REJECTED),
            (401, "api_key_revoked", codes.UPLOAD_AUTH, "upload_auth"),
        ):
            with self.subTest(status=status):
                report = self.report()
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(
                        report,
                        settings=CONFIGURED,
                        replies=failed_replies(status, code, status in (503, None)),
                    )

                self.assertEqual(ctx.exception.code, exit_code)
                self.assert_local_report(report)
                self.assertIn(f"Not uploaded: {reason}", self.html(report))
                self.assertEqual(
                    self.json_report(report)["meta"]["upload"]["reason"], reason
                )
                record = json.loads(self.raw(report, "upload.json").read_text())
                self.assertEqual(
                    (record["status"], record["reason"]), ("failed", reason)
                )
                self.assertEqual(self.rows(report, "scoring_data"), [])

    def test_the_failure_is_printed_with_its_code_and_request_id(self):
        report = self.report()
        with self.assertRaises(SystemExit):
            self.run_assessment(
                report,
                settings=CONFIGURED,
                replies=failed_replies(422, "validation_failed", False),
            )

        (logs,) = [
            kwargs["logs"]
            for args, kwargs in self.steps
            if args[0] == "Uploading to the platform..." and kwargs.get("logs")
        ]
        self.assertEqual(logs, "validation_failed: server said no")
        self.assertIn("req-fake-0001", str(self.printed.call_args_list))

    def test_warnings_from_the_platform_are_printed_one_per_line(self):
        report = self.report()
        warnings = [
            {"code": "unknown_resource_types", "message": "Two types are unknown."},
            {"code": "tier_downgraded", "message": "Granted Basic."},
        ]
        self.run_assessment(
            report,
            settings=CONFIGURED,
            replies=success_replies(warnings=warnings),
        )

        printed = [
            kwargs["logs"]
            for args, kwargs in self.steps
            if args[0] == "Platform warning"
        ]
        self.assertEqual(
            printed,
            [
                "Two types are unknown. (unknown_resource_types)",
                "Granted Basic. (tier_downgraded)",
            ],
        )

    def test_a_run_still_processing_is_recorded_and_exits_zero(self):
        report = self.report()
        pending = UploadResult(
            True,
            True,
            message="accepted, still processing",
            sync_id=SYNC_ID,
            assessment_id=ASSESSMENT_ID,
            url=URL,
            pending=True,
        )
        with patch("main.upload", return_value=pending):
            self.run_assessment(report, settings=CONFIGURED)

        self.assert_local_report(report)
        record = json.loads(self.raw(report, "upload.json").read_text())
        self.assertEqual((record["status"], record["pending"]), ("uploaded", True))
        self.assertIn(URL, self.html(report))
        logs = [kwargs.get("logs") for _, kwargs in self.steps]
        self.assertIn("Accepted by the platform, still processing.", logs)

    def test_an_unreadable_platform_answer_does_not_cost_the_report(self):
        report = self.report()
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(
                report,
                settings=CONFIGURED,
                replies=[
                    meta_reply(),
                    whoami_reply(),
                    make_response(201, text="not json"),
                ],
            )

        self.assertEqual(ctx.exception.code, codes.UPLOAD_UNAVAILABLE)
        self.assert_local_report(report)

    def test_a_key_that_cannot_be_right_is_a_failed_upload_not_an_offline_run(self):
        report = self.report()
        settings = Settings(
            host=CONFIGURED.host, api_key=Setting(corrupt(KEY), "env CLOUDEXIT_API_KEY")
        )
        with (
            patch("main.upload") as mock_upload,
            self.assertRaises(SystemExit) as ctx,
        ):
            self.run_assessment(report, settings=settings, replies=[])

        mock_upload.assert_not_called()
        self.assertEqual(ctx.exception.code, codes.UPLOAD_AUTH)
        self.assert_local_report(report)
        self.assertIn("Not uploaded: upload_auth", self.html(report))
        # A configured platform keeps the requested depth: nothing forces Basic
        self.assertEqual(self.sync(report)["assessment_type"], 2)
        messages = [args[0] for args, _ in self.steps]
        self.assertNotIn("Forcing Basic Assessment due to offline mode.", messages)

    def test_basic_is_forced_only_without_platform_configuration(self):
        half = Settings(host=Setting("https://platform.test", "env X"))
        for settings in (None, Settings(), half):
            with self.subTest(settings=settings):
                report = self.report()
                self.run_assessment(report, settings=settings)
                self.assertEqual(self.sync(report)["assessment_type"], 1)

    def test_a_dry_run_never_uploads_and_never_forces_basic(self):
        report = self.report()
        with patch("main.upload") as mock_upload:
            self.run_assessment(
                report,
                settings=CONFIGURED,
                replies=[],
                dry_run=True,
            )

        mock_upload.assert_not_called()
        self.assert_local_report(report)
        self.assertEqual(self.sync(report)["assessment_type"], 2)
        self.assertFalse(self.raw(report, "upload.json").exists())

    def test_collection_problems_win_over_upload_codes(self):
        report = self.report(egress=[egress_row(1, "web-1", 10)])
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(
                report,
                settings=CONFIGURED,
                replies=failed_replies(503, "service_unavailable"),
                egress=True,
                egress_ok=False,
            )

        self.assertEqual(ctx.exception.code, codes.EGRESS)
        self.assert_local_report(report)
        self.assertIn("Not uploaded", self.html(report))

    def test_a_sync_document_problem_wins_over_an_upload_code(self):
        report = self.report()
        with (
            patch("main.prepare_sync_document", side_effect=SyncDocumentError("bad")),
            self.assertRaises(SystemExit) as ctx,
        ):
            self.run_assessment(
                report,
                settings=CONFIGURED,
                replies=[],
                emit_payload=str(self.tmp / "out"),
            )

        self.assertEqual(ctx.exception.code, codes.REPORT)
        self.assertTrue((Path(report) / "index.html").is_file())

    def test_the_upload_runs_after_the_collection_and_before_the_report(self):
        report = self.report(egress=[egress_row(1, "web-1", 10)])
        order = []
        original_upload = main.upload

        def traced_upload(*args, **kwargs):
            order.append("upload")
            return original_upload(*args, **kwargs)

        def traced_report(*args, **kwargs):
            order.append("report")
            return generate_report(*args, **kwargs)

        def traced_risk(*args, **kwargs):
            order.append("risk")
            return perform_risk_assessment(*args, **kwargs)

        with (
            patch("main.upload", side_effect=traced_upload),
            patch("main.generate_report", side_effect=traced_report),
            patch("main.perform_risk_assessment", side_effect=traced_risk),
            patch(
                "main.estimate_egress",
                side_effect=lambda *a, **k: order.append("egress")
                or {"success": True, "json_path": "p"},
            ),
            patch("main.generate_egress_html_report", return_value={"success": True}),
            patch("main.generate_egress_pdf_report", return_value={"success": True}),
        ):
            self.run_assessment(
                report,
                settings=CONFIGURED,
                replies=success_replies(),
                egress=True,
            )

        self.assertEqual(order, ["egress", "risk", "upload", "report"])
        self.assertTrue(self.sync(report)["inventory"]["egress"]["collected"])


if __name__ == "__main__":
    unittest.main()
