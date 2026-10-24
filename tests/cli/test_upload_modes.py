import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import main
from cloudexit.platform.run_output import (
    build_run_json,
    catalogue_outdated,
    upload_status,
)
from cloudexit.platform.upload import UploadResult
from tests.support.api import (
    ASSESSMENT_ID,
    KEY,
    URL,
    meta_reply,
    success_replies,
    whoami_reply,
    created_reply,
)
from tests.report import test_report_flow as flow
from cloudexit import codes
from cloudexit.settings import Setting, Settings

OUTDATED = {
    "code": "collector_catalogue_outdated",
    "message": "The catalogue used is older than the platform's.",
}
UNKNOWN_TYPES = {"code": "unknown_resource_types", "message": "Some types are unknown."}


def with_mode(mode, source="flag --upload"):
    return Settings(
        host=flow.CONFIGURED.host,
        api_key=flow.CONFIGURED.api_key,
        upload_budget=flow.CONFIGURED.upload_budget,
        upload=Setting(mode, source),
    )


class ModeTestCase(flow.FlowTestCase):
    run_assessment = flow.RunFlowTests.run_assessment
    sync = flow.RunFlowTests.sync
    assert_local_report = flow.RunFlowTests.assert_local_report

    def messages(self):
        return [args[0] for args, _ in self.steps]

    def forced(self):
        return [m for m in self.messages() if m.startswith("Forcing Basic")]

    def summary(self, exit_code=0):
        out = io.StringIO()
        main._emit_run_json(out, exit_code)
        return json.loads(out.getvalue())


class UploadModeMatrixTests(ModeTestCase):
    def test_auto_without_a_platform_runs_offline_and_succeeds(self):
        report = self.report()
        with patch("main.upload") as mock_upload:
            self.run_assessment(report, settings=Settings())

        mock_upload.assert_not_called()
        self.assert_local_report(report)
        self.assertEqual(
            self.forced(), ["Forcing Basic Assessment: no platform is configured."]
        )

    def test_auto_with_a_platform_uploads(self):
        report = self.report()
        self.run_assessment(report, settings=flow.CONFIGURED, replies=success_replies())

        self.assert_local_report(report)
        self.assertEqual(self.sync(report)["assessment_type"], 2)
        self.assertEqual(self.forced(), [])
        self.assertEqual(main.RUN["upload"].url, URL)

    def test_auto_with_a_platform_that_refuses_the_key_writes_the_report_first(self):
        report = self.report()
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(
                report,
                settings=flow.CONFIGURED,
                replies=flow.failed_replies(401, "api_key_revoked", False),
            )

        self.assertEqual(ctx.exception.code, codes.UPLOAD_AUTH)
        self.assert_local_report(report)
        self.assertIn("Not uploaded", self.html(report))

    def test_required_without_a_platform_fails_before_collecting_anything(self):
        report = self.report()
        for settings in (
            Settings(upload=Setting("required", "flag --upload")),
            Settings(
                host=flow.CONFIGURED.host, upload=Setting("required", "flag --upload")
            ),
        ):
            with self.subTest(settings=settings.host.value):
                with (
                    patch("main.upload") as mock_upload,
                    self.assertRaises(SystemExit) as ctx,
                ):
                    self.run_assessment(report, settings=settings)

                self.assertEqual(ctx.exception.code, codes.CONFIG)
                mock_upload.assert_not_called()
                self.assertNotIn("Directory successfully created.", self.messages())
                self.assertFalse((Path(report) / "index.html").exists())
                self.assertIn("Upload is required", self.messages()[-1])

    def test_required_with_a_platform_uploads(self):
        report = self.report()
        self.run_assessment(
            report, settings=with_mode("required"), replies=success_replies()
        )
        self.assertEqual(main.RUN["upload"].url, URL)

    def test_off_with_a_platform_makes_no_request_and_forces_basic(self):
        report = self.report()
        with patch("main.upload") as mock_upload:
            self.run_assessment(report, settings=with_mode("off"), replies=[])

        mock_upload.assert_not_called()
        self.assert_local_report(report)
        self.assertEqual(self.sync(report)["assessment_type"], 1)
        self.assertEqual(self.forced(), ["Forcing Basic Assessment: uploads are off."])
        self.assertFalse(self.raw(report, "upload.json").exists())

    def test_off_does_not_even_check_a_key_that_cannot_be_right(self):
        report = self.report()
        broken = Settings(
            host=flow.CONFIGURED.host,
            api_key=Setting("esc_sk_typo", "env CLOUDEXIT_API_KEY"),
            upload=Setting("off", "env CLOUDEXIT_UPLOAD"),
        )
        self.run_assessment(report, settings=broken, replies=[])
        self.assert_local_report(report)

    def test_half_a_configuration_is_an_error_not_an_offline_run(self):
        for settings in (
            Settings(host=flow.CONFIGURED.host),
            Settings(api_key=flow.CONFIGURED.api_key),
        ):
            with self.subTest(settings=settings):
                report = self.report()
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(report, settings=settings, replies=[])

                self.assertEqual(ctx.exception.code, codes.CONFIG)
                self.assertFalse((Path(report) / "index.html").exists())
                self.assertIn("incomplete", self.messages()[-1])

    def test_half_a_configuration_is_fine_when_uploads_are_off(self):
        report = self.report()
        settings = Settings(
            host=flow.CONFIGURED.host, upload=Setting("off", "flag --upload")
        )
        self.run_assessment(report, settings=settings)
        self.assert_local_report(report)

    def test_an_unusable_key_is_a_failed_upload_not_an_offline_run(self):
        report = self.report()
        broken = Settings(
            host=flow.CONFIGURED.host,
            api_key=Setting("esc_sk_typo", "env CLOUDEXIT_API_KEY"),
        )
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(report, settings=broken, replies=[])

        self.assertEqual(ctx.exception.code, codes.UPLOAD_AUTH)
        self.assert_local_report(report)
        self.assertEqual(self.forced(), [])
        self.assertEqual(self.sync(report)["assessment_type"], 2)


class DryRunTests(ModeTestCase):
    def test_a_dry_run_is_upload_off_plus_the_sync_document(self):
        report = self.report()
        with patch("main.upload") as mock_upload:
            self.run_assessment(
                report, settings=flow.CONFIGURED, replies=[], dry_run=True
            )

        mock_upload.assert_not_called()
        self.assertTrue(self.raw(report, "sync.json").is_file())
        self.assertEqual(self.sync(report)["assessment_type"], 2)
        said = " ".join(str(call.args[0]) for call in self.printed.call_args_list)
        self.assertIn("--dry-run is --upload=off plus the sync document", said)
        self.assertEqual(self.forced(), [])

    def test_a_dry_run_cannot_be_combined_with_a_flag_that_uploads(self):
        for mode in ("auto", "required"):
            with self.subTest(mode=mode):
                report = self.report()
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(
                        report,
                        settings=with_mode(mode),
                        replies=[],
                        dry_run=True,
                    )
                self.assertEqual(ctx.exception.code, codes.CONFIG)

    def test_a_dry_run_accepts_an_upload_mode_from_the_environment(self):
        report = self.report()
        self.run_assessment(
            report,
            settings=with_mode("required", "env CLOUDEXIT_UPLOAD"),
            replies=[],
            dry_run=True,
        )
        self.assertTrue(self.raw(report, "sync.json").is_file())

    def test_a_dry_run_with_upload_off_is_fine(self):
        report = self.report()
        self.run_assessment(report, settings=with_mode("off"), replies=[], dry_run=True)
        self.assertTrue(self.raw(report, "sync.json").is_file())


class StrictTests(ModeTestCase):
    def replies_with(self, *warnings):
        return [meta_reply(), whoami_reply(), created_reply(warnings=list(warnings))]

    def test_a_platform_warning_fails_the_run_after_the_report_is_written(self):
        for warning in (OUTDATED, UNKNOWN_TYPES):
            with self.subTest(code=warning["code"]):
                report = self.report()
                with self.assertRaises(SystemExit) as ctx:
                    self.run_assessment(
                        report,
                        settings=flow.CONFIGURED,
                        replies=self.replies_with(warning),
                        strict=True,
                    )

                self.assertEqual(ctx.exception.code, codes.UPLOAD_REJECTED)
                self.assert_local_report(report)
                self.assertTrue(self.raw(report, "upload.json").is_file())

    def test_the_same_warning_does_not_fail_a_run_without_strict(self):
        report = self.report()
        self.run_assessment(
            report, settings=flow.CONFIGURED, replies=self.replies_with(OUTDATED)
        )
        self.assert_local_report(report)

    def test_a_clean_upload_passes_strict(self):
        report = self.report()
        self.run_assessment(
            report, settings=flow.CONFIGURED, replies=success_replies(), strict=True
        )
        self.assert_local_report(report)

    def test_a_deprecated_client_is_a_warning_that_strict_fails_on(self):
        report = self.report()
        deprecated = meta_reply(clients={"cloudexit": {"deprecated_below": "99.0.0"}})
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(
                report,
                settings=flow.CONFIGURED,
                replies=[deprecated, whoami_reply(), created_reply()],
                strict=True,
            )
        self.assertEqual(ctx.exception.code, codes.UPLOAD_REJECTED)
        self.assert_local_report(report)
        self.assertEqual(
            [i["code"] for i in main.RUN["upload"].warning_items], ["client_deprecated"]
        )

    def test_an_upload_failure_keeps_its_own_code_under_strict(self):
        report = self.report()
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(
                report,
                settings=flow.CONFIGURED,
                replies=flow.failed_replies(401, "api_key_revoked", False),
                strict=True,
            )
        self.assertEqual(ctx.exception.code, codes.UPLOAD_AUTH)

    def test_strict_without_a_platform_has_nothing_to_fail_on(self):
        report = self.report()
        self.run_assessment(report, settings=Settings(), strict=True)
        self.assert_local_report(report)


class RunJsonTests(ModeTestCase):
    def test_a_successful_upload_is_summarised(self):
        report = self.report()
        self.run_assessment(report, settings=flow.CONFIGURED, replies=success_replies())

        summary = self.summary()

        self.assertEqual(
            set(summary),
            {"exit_code", "report_dir", "sync_json", "upload", "results", "catalogue"},
        )
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["report_dir"], report)
        self.assertEqual(summary["sync_json"], str(self.raw(report, "sync.json")))
        upload = summary["upload"]
        self.assertEqual(
            set(upload),
            {
                "status",
                "reason",
                "assessment_id",
                "sync_id",
                "platform_sync_id",
                "url",
                "warnings",
                "error",
            },
        )
        self.assertEqual(upload["status"], "uploaded")
        self.assertEqual(upload["assessment_id"], ASSESSMENT_ID)
        self.assertEqual(upload["sync_id"], self.sync(report)["sync"]["id"])
        self.assertEqual(upload["url"], URL)
        self.assertEqual(upload["warnings"], [])
        self.assertIsNone(upload["error"])
        self.assertEqual(summary["results"]["scoring_version"], "1")
        self.assertEqual(set(summary["catalogue"]), {"version", "outdated"})

    def test_the_platform_results_are_in_the_summary_and_not_in_the_report(self):
        report = self.report()
        self.run_assessment(report, settings=flow.CONFIGURED, replies=success_replies())

        results = self.summary()["results"]

        self.assertIn("scores", results)
        self.assertNotIn("scores", self.json_report(report)["data"])
        self.assertEqual(self.rows(report, "scoring_data"), [])

    def test_warnings_carry_their_code_and_message(self):
        report = self.report()
        self.run_assessment(
            report,
            settings=flow.CONFIGURED,
            replies=[
                meta_reply(),
                whoami_reply(),
                created_reply(warnings=[OUTDATED]),
            ],
        )

        summary = self.summary()

        self.assertEqual(summary["upload"]["warnings"], [OUTDATED])
        self.assertIs(summary["catalogue"]["outdated"], True)

    def test_an_offline_run_says_why_nothing_was_uploaded(self):
        for settings, kwargs, reason in (
            (Settings(), {}, "offline"),
            (with_mode("off"), {}, "off"),
            (flow.CONFIGURED, {"dry_run": True}, "dry_run"),
        ):
            with self.subTest(reason=reason):
                report = self.report()
                self.run_assessment(report, settings=settings, replies=[], **kwargs)

                summary = self.summary()

                self.assertEqual(summary["upload"]["status"], "skipped")
                self.assertEqual(summary["upload"]["reason"], reason)
                self.assertIsNone(summary["upload"]["url"])
                self.assertIsNone(summary["results"])
                self.assertIsNone(summary["catalogue"]["outdated"])

    def test_a_failed_upload_carries_the_error(self):
        report = self.report()
        with self.assertRaises(SystemExit):
            self.run_assessment(
                report,
                settings=flow.CONFIGURED,
                replies=flow.failed_replies(401, "api_key_revoked", False),
            )

        summary = self.summary(codes.UPLOAD_AUTH)

        self.assertEqual(summary["exit_code"], codes.UPLOAD_AUTH)
        self.assertEqual(summary["upload"]["status"], "failed")
        error = summary["upload"]["error"]
        self.assertEqual(error["code"], "api_key_revoked")
        self.assertEqual(error["request_id"], "req-fake-0001")
        self.assertTrue(error["message"])
        self.assertIsNone(summary["results"])

    def test_an_upload_kept_for_later_is_spooled(self):
        report = self.report()
        with self.assertRaises(SystemExit) as ctx:
            self.run_assessment(
                report,
                settings=flow.CONFIGURED,
                replies=flow.failed_replies(503, "service_unavailable", True),
            )

        summary = self.summary(ctx.exception.code)

        self.assertEqual(summary["upload"]["status"], "spooled")
        self.assertEqual(summary["upload"]["error"]["code"], "service_unavailable")

    def test_the_summary_never_holds_the_key(self):
        report = self.report()
        self.run_assessment(report, settings=flow.CONFIGURED, replies=success_replies())
        self.assertNotIn(KEY, json.dumps(self.summary()))

        with self.assertRaises(SystemExit):
            self.run_assessment(
                self.report(),
                settings=flow.CONFIGURED,
                replies=flow.failed_replies(401, "api_key_revoked", False),
            )
        self.assertNotIn(KEY, json.dumps(self.summary(codes.UPLOAD_AUTH)))

    def test_a_new_run_does_not_inherit_the_previous_summary(self):
        self.run_assessment(
            self.report(), settings=flow.CONFIGURED, replies=success_replies()
        )
        self.run_assessment(self.report(), settings=Settings())

        self.assertEqual(self.summary()["upload"]["status"], "skipped")


class RunSummaryUnitTests(unittest.TestCase):
    def result(self, **changes):
        fields = dict(attempted=True, success=True, sync_id="s-1")
        fields.update(changes)
        return UploadResult(**fields)

    def test_the_status_names(self):
        self.assertEqual(upload_status(None), "skipped")
        self.assertEqual(upload_status(UploadResult(False, False)), "skipped")
        self.assertEqual(upload_status(self.result()), "uploaded")
        self.assertEqual(upload_status(self.result(success=False)), "failed")
        self.assertEqual(
            upload_status(self.result(success=False, spooled=True)), "spooled"
        )

    def test_outdated_follows_the_warning_or_the_platform_version(self):
        warned = self.result(warning_items=(OUTDATED,))
        self.assertIs(catalogue_outdated("2026-11-02", warned), True)

        def host(version):
            return self.result(catalogue={"host": {"version": version}})

        self.assertIs(catalogue_outdated("2026-11-01", host("2026-11-02")), True)
        self.assertIs(catalogue_outdated("2026-11-02", host("2026-11-02")), False)
        self.assertIs(catalogue_outdated("2026-11-03", host("2026-11-02")), False)
        self.assertIs(catalogue_outdated("2026-11-02", host("2026-11-02.1")), True)
        self.assertIsNone(catalogue_outdated(None, host("2026-11-02")))
        self.assertIsNone(catalogue_outdated("2026-11-02", self.result()))
        self.assertIsNone(catalogue_outdated("2026-11-02", None))

    def test_a_missing_upload_gives_a_complete_summary(self):
        summary = build_run_json(
            exit_code=2,
            report_dir=None,
            sync_json=None,
            upload=None,
            skipped_reason=None,
            catalogue_version=None,
        )
        self.assertEqual(summary["exit_code"], 2)
        self.assertEqual(summary["upload"]["status"], "skipped")
        self.assertEqual(summary["upload"]["warnings"], [])


class StdoutSeparationTests(unittest.TestCase):
    def run_json(self, dispatch):
        out, err = io.StringIO(), io.StringIO()
        main.RUN.clear()
        with (
            patch.object(sys, "stdout", out),
            redirect_stderr(err),
            patch("main._dispatch", side_effect=dispatch),
            patch(
                "main.console.print", side_effect=lambda *a, **k: err.write(str(a[0]))
            ),
        ):
            try:
                main._dispatch_json(object())
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_stdout_holds_one_json_object_and_the_steps_go_to_stderr(self):
        def dispatch(args):
            print("Preliminary Stage")
            sys.stdout.write("a step\n")
            sys.exit(codes.UPLOAD_UNAVAILABLE)

        code, out, err = self.run_json(dispatch)

        self.assertEqual(code, codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(json.loads(out)["exit_code"], codes.UPLOAD_UNAVAILABLE)
        self.assertEqual(out.count("\n"), len(out.splitlines()))
        self.assertIn("Preliminary Stage", err)
        self.assertIn("a step", err)
        self.assertNotIn("Preliminary Stage", out)

    def test_a_clean_run_reports_exit_code_zero(self):
        code, out, _ = self.run_json(lambda args: None)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["exit_code"], 0)

    def test_an_unexpected_error_is_a_summary_with_code_1_and_a_message_on_stderr(self):
        def dispatch(args):
            raise RuntimeError("boom")

        code, out, err = self.run_json(dispatch)

        self.assertEqual(code, codes.UNEXPECTED)
        self.assertEqual(json.loads(out)["exit_code"], codes.UNEXPECTED)
        self.assertIn("boom", err)
        self.assertNotIn("boom", out)


if __name__ == "__main__":
    unittest.main()
