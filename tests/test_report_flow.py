import copy
import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from tests.flow_fixtures import LOCAL_RISKS, SERVER_SCORES, build_flow_run
from tests.sync_fixtures import SYNC_ID, egress_row
from utils import codes
from utils.settings import Setting, Settings

AWS_RESOURCES = [(1, "eu-central-1", 8), (4, "eu-central-1", 2)]
COSTS = [("2026-09-01", 12.5, "USD")]
URL = "https://platform.test/assessments/a-1"
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
    api_key=Setting("k" * 24, "env CLOUDEXIT_API_KEY"),
)
SERVER_PAYLOAD = {
    "data": {
        "assessment_id": "a-1",
        "url": URL,
        "risk_inventory": [{"id": 8, "impacted_resources": [1]}],
        "scoring_data": {
            "exit_score": SERVER_SCORES[0],
            "human_score": SERVER_SCORES[1],
            "technology_score": SERVER_SCORES[2],
            "operational_score": SERVER_SCORES[3],
        },
    }
}


def post_ok(payload=None):
    return {"success": True, "payload": payload or SERVER_PAYLOAD, "status": 201}


def post_failed(status=None, logs="failed"):
    return {"success": False, "payload": None, "status": status, "logs": logs}


class FlowTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.counter = 0

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
            "assessment_id": "a-1",
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
    def call(self, post, document=None, settings=CONFIGURED):
        with patch("core.upload.post_assessment", return_value=post) as mock_post:
            result = upload(
                document if document is not None else {"sync": {"id": SYNC_ID}},
                settings,
                token="jwt",
                name="Flow",
                report_path="/tmp/report",
                started_at=1,
                meta={"assessment_type": 1},
            )
        return result, mock_post

    def test_success_carries_the_link_and_nothing_else(self):
        result, mock_post = self.call(post_ok())

        self.assertTrue(result.success)
        self.assertFalse(result.failed)
        self.assertEqual(
            (result.sync_id, result.assessment_id, result.url), (SYNC_ID, "a-1", URL)
        )
        self.assertEqual(mock_post.call_args.kwargs["host"], "https://platform.test")
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 10.0)
        self.assertFalse(hasattr(result, "risks"))
        self.assertFalse(hasattr(result, "scores"))

    def test_an_unsafe_link_is_dropped(self):
        payload = {"data": {"assessment_id": "a-1", "url": "javascript:alert(1)"}}
        result, _ = self.call(post_ok(payload))
        self.assertIsNone(result.url)

    def test_failures_map_to_the_upload_codes(self):
        cases = {
            503: (REASON_UNAVAILABLE, codes.UPLOAD_UNAVAILABLE),
            500: (REASON_UNAVAILABLE, codes.UPLOAD_UNAVAILABLE),
            429: (REASON_UNAVAILABLE, codes.UPLOAD_UNAVAILABLE),
            None: (REASON_UNAVAILABLE, codes.UPLOAD_UNAVAILABLE),
            400: (REASON_REJECTED, codes.UPLOAD_REJECTED),
            401: (REASON_REJECTED, codes.UPLOAD_REJECTED),
            422: (REASON_REJECTED, codes.UPLOAD_REJECTED),
        }
        for status, (reason, exit_code) in cases.items():
            with self.subTest(status=status):
                result, _ = self.call(post_failed(status, "server said no"))
                self.assertTrue(result.failed)
                self.assertEqual((result.reason, result.exit_code), (reason, exit_code))
                self.assertEqual(result.message, "server said no")
                self.assertEqual(result.sync_id, SYNC_ID)

    def test_an_unexpected_error_is_a_failed_upload_not_a_crash(self):
        with patch("core.upload.post_assessment", side_effect=ValueError("bad json")):
            result = upload(
                None,
                CONFIGURED,
                token="jwt",
                name="Flow",
                report_path="/tmp/report",
                started_at=1,
                meta={},
            )
        self.assertTrue(result.failed)
        self.assertEqual(result.reason, REASON_UNAVAILABLE)
        self.assertIn("ValueError", result.message)
        self.assertNotIn("bad json", result.message)

    def test_report_info_and_the_record_file(self):
        success = UploadResult(
            True, True, sync_id=SYNC_ID, assessment_id="a-1", url=URL
        )
        failed = failure(REASON_REJECTED, "nope", SYNC_ID)

        self.assertEqual(
            report_info(success),
            {
                "status": "uploaded",
                "sync_id": SYNC_ID,
                "assessment_id": "a-1",
                "url": URL,
            },
        )
        self.assertEqual(
            report_info(failed),
            {"status": "failed", "reason": REASON_REJECTED, "sync_id": SYNC_ID},
        )
        self.assertIsNone(report_info(None))
        self.assertIsNone(report_info(UploadResult(False, False)))

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
        mode=("offline", None),
        settings=None,
        post=None,
        egress_ok=None,
        assessment_type=2,
        **kwargs,
    ):
        self.steps = []
        raw_data = os.path.join(report, "raw_data")
        patches = [
            patch("main.validate_config"),
            patch("main.resolve_mode", return_value=mode),
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
            patch("main.console.print"),
        ]
        if post is not None:
            patches.append(patch("core.upload.post_assessment", return_value=post))
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
            item.start()
            self.addCleanup(item.stop)

        config = copy.deepcopy(CONFIG)
        config["assessmentType"] = assessment_type
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
        self.run_assessment(
            report, mode=("online", "jwt"), settings=CONFIGURED, post=post_ok()
        )
        self.assertEqual(
            [(row[1], row[2]) for row in self.rows(report, "risk_inventory")],
            LOCAL_RISKS,
        )

    def test_a_successful_upload_stores_the_link_and_no_platform_results(self):
        report = self.report()

        self.run_assessment(
            report, mode=("online", "jwt"), settings=CONFIGURED, post=post_ok()
        )

        self.assert_local_report(report)
        record = json.loads(self.raw(report, "upload.json").read_text())
        self.assertEqual(
            {key: record[key] for key in ("status", "sync_id", "assessment_id", "url")},
            {
                "status": "uploaded",
                "sync_id": self.sync(report)["sync"]["id"],
                "assessment_id": "a-1",
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

        self.run_assessment(
            report, mode=("online", "jwt"), settings=CONFIGURED, post=post_ok()
        )

        self.assertEqual(self.sync(report)["assessment_type"], 2)
        # The local report is always the Basic report
        self.assertEqual(self.json_report(report)["meta"]["assessment_type"], 1)
        messages = [args[0] for args, _ in self.steps]
        self.assertNotIn("Forcing Basic Assessment due to offline mode.", messages)

    def test_a_failed_upload_still_writes_the_report_and_exits_with_an_upload_code(
        self,
    ):
        for status, exit_code, reason in (
            (503, codes.UPLOAD_UNAVAILABLE, REASON_UNAVAILABLE),
            (None, codes.UPLOAD_UNAVAILABLE, REASON_UNAVAILABLE),
            (422, codes.UPLOAD_REJECTED, REASON_REJECTED),
        ):
            with self.subTest(status=status):
                report = self.report()
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(
                        report,
                        mode=("online", "jwt"),
                        settings=CONFIGURED,
                        post=post_failed(status, "server responded"),
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

    def test_an_unreadable_platform_answer_does_not_cost_the_report(self):
        report = self.report()
        with (
            patch("core.upload.post_assessment", side_effect=ValueError("not json")),
            self.assertRaises(SystemExit) as ctx,
        ):
            self.run_assessment(report, mode=("online", "jwt"), settings=CONFIGURED)

        self.assertEqual(ctx.exception.code, codes.UPLOAD_UNAVAILABLE)
        self.assert_local_report(report)

    def test_a_failed_sign_in_is_a_failed_upload_not_an_offline_run(self):
        report = self.report()
        with (
            patch("main.upload") as mock_upload,
            self.assertRaises(SystemExit) as ctx,
        ):
            self.run_assessment(report, mode=("offline", None), settings=CONFIGURED)

        mock_upload.assert_not_called()
        self.assertEqual(ctx.exception.code, codes.UPLOAD_UNAVAILABLE)
        self.assert_local_report(report)
        self.assertIn("Not uploaded: upload_unavailable", self.html(report))
        # A configured platform keeps the requested depth: nothing forces Basic
        self.assertEqual(self.sync(report)["assessment_type"], 2)
        messages = [args[0] for args, _ in self.steps]
        self.assertNotIn("Forcing Basic Assessment due to offline mode.", messages)

    def test_basic_is_forced_only_without_platform_configuration(self):
        half = Settings(host=Setting("https://platform.test", "env X"))
        for settings in (None, Settings(), half):
            with self.subTest(settings=settings):
                report = self.report()
                self.run_assessment(report, mode=("offline", None), settings=settings)
                self.assertEqual(self.sync(report)["assessment_type"], 1)

    def test_a_dry_run_never_uploads_and_never_forces_basic(self):
        report = self.report()
        with patch("main.upload") as mock_upload:
            self.run_assessment(
                report,
                mode=("online", "jwt"),
                settings=CONFIGURED,
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
                mode=("online", "jwt"),
                settings=CONFIGURED,
                post=post_failed(503),
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
                mode=("online", "jwt"),
                settings=CONFIGURED,
                post=post_failed(503),
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
                mode=("online", "jwt"),
                settings=CONFIGURED,
                post=post_ok(),
                egress=True,
            )

        self.assertEqual(order, ["egress", "risk", "upload", "report"])
        self.assertTrue(self.sync(report)["inventory"]["egress"]["collected"])


if __name__ == "__main__":
    unittest.main()
