import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
from core.api_v2 import ApiClient
from core.upload import (
    UploadResult,
    billing_line,
    report_info,
    upload,
    write_upload_record,
)
from tests.api_fixtures import (
    HOST,
    KEY,
    created_reply,
    meta_reply,
    minimal_document,
    reply,
    sync_body,
    whoami_reply,
)
from tests.http_fixtures import fake_http
from utils.settings import Setting, Settings

SYNC_ID = "01933b9e-7c2a-7d41-9a51-3c8f0a6e0001"
BILLING = {
    "class": "new",
    "charged_credits": 20,
    "balance": 380,
    "transaction_id": "tx-1",
}
CONFIGURED = Settings(
    host=Setting(HOST, "env CLOUDEXIT_HOST"),
    api_key=Setting(KEY, "env CLOUDEXIT_API_KEY"),
)


class BillingLineTests(unittest.TestCase):
    def test_the_line_names_the_charge_and_the_balance(self):
        self.assertEqual(
            billing_line({"charged_credits": 20, "balance": 380}),
            "Charged 20 credits; balance 380.",
        )

    def test_one_credit_is_singular_and_zero_is_plural(self):
        self.assertEqual(
            billing_line({"charged_credits": 1, "balance": 9}),
            "Charged 1 credit; balance 9.",
        )
        self.assertEqual(
            billing_line({"charged_credits": 0, "balance": 9}),
            "Charged 0 credits; balance 9.",
        )

    def test_a_missing_balance_is_left_out(self):
        self.assertEqual(billing_line({"charged_credits": 5}), "Charged 5 credits.")

    def test_without_a_charge_there_is_no_line(self):
        for billing in (None, {}, {"balance": 3}):
            with self.subTest(billing=billing):
                self.assertIsNone(billing_line(billing))


class PushBillingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)

    def push(self, *replies):
        client = ApiClient(HOST, KEY, cache_path=self.cache, environ={})
        with fake_http(meta_reply(), whoami_reply(), *replies):
            return client.push(
                minimal_document(SYNC_ID), name="Billing", exit_strategy=1
            )

    def test_the_charge_and_the_balance_come_back_with_the_result(self):
        result = self.push(created_reply(billing=BILLING))
        self.assertEqual(result.billing, {"charged_credits": 20, "balance": 380})

    def test_a_platform_that_does_not_charge_sends_no_billing_block(self):
        self.assertIsNone(self.push(created_reply()).billing)

    def test_a_replay_charged_nothing_whatever_the_stored_answer_says(self):
        replayed = reply(
            200, sync_body(billing=BILLING), **{"Idempotent-Replayed": "true"}
        )
        self.assertIsNone(self.push(replayed).billing)

    def test_odd_values_are_ignored(self):
        for billing in (
            "20 credits",
            [],
            {"charged_credits": "20", "balance": None},
            {"charged_credits": True},
        ):
            with self.subTest(billing=billing):
                cache = self.cache / str(abs(hash(str(billing))))
                client = ApiClient(HOST, KEY, cache_path=cache, environ={})
                with fake_http(
                    meta_reply(), whoami_reply(), created_reply(billing=billing)
                ):
                    result = client.push(
                        minimal_document(SYNC_ID), name="Billing", exit_strategy=1
                    )
                self.assertIsNone(result.billing)


class UploadBillingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def upload(self, billing):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")}):
            with fake_http(meta_reply(), whoami_reply(), created_reply(**billing)):
                return upload(
                    minimal_document(SYNC_ID),
                    CONFIGURED,
                    name="Billing",
                    meta={"exit_strategy": 1},
                )

    def test_the_result_carries_the_charge(self):
        result = self.upload({"billing": BILLING})
        self.assertEqual(result.billing, {"charged_credits": 20, "balance": 380})

    def test_the_record_file_holds_the_billing_but_the_report_does_not(self):
        result = self.upload({"billing": BILLING})

        path = write_upload_record(str(self.tmp), result)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["billing"], {"charged_credits": 20, "balance": 380})
        self.assertNotIn("billing", report_info(result))

    def test_without_billing_the_record_has_no_such_key(self):
        result = self.upload({})

        self.assertIsNone(result.billing)
        path = write_upload_record(str(self.tmp), result)
        self.assertNotIn("billing", json.loads(path.read_text(encoding="utf-8")))


class ClosingBlockTests(unittest.TestCase):
    AWS = (
        "aws",
        2,
        {"accessKey": "a", "secretKey": "b", "region": "eu-central-1"},
    )
    AZURE = (
        "azure",
        1,
        {
            "credential": MagicMock(),
            "tenantId": "t",
            "subscriptionId": "00000000-0000-0000-0000-000000000000",
            "resourceGroupName": "rg",
        },
    )
    URL = "https://platform.example.com/assessments/a-1"

    def lines(self, provider, uploaded=None, **kwargs):
        name, number, details = provider
        printed = []
        config = {
            "name": "Outputs",
            "cloudServiceProvider": number,
            "exitStrategy": 1,
            "assessmentType": 1,
            "providerDetails": details,
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "r")
            raw = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report, "data"))
            os.makedirs(raw)
            patches = [
                patch("main.validate_config"),
                patch("main.require_azure_sdk"),
                patch("main.create_directory", return_value=(report, raw)),
                patch("main.add_run_log_handler"),
                patch("main.verify_credentials", return_value=(True, "ok")),
                patch("main.test_permissions", return_value=(True, True, True, "ok")),
                patch(
                    "main.create_resource_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.create_cost_inventory",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.perform_risk_assessment",
                    return_value={"success": True, "logs": ""},
                ),
                patch(
                    "main.generate_report",
                    return_value={
                        "success": True,
                        "reports": {"HTML": "h.html", "PDF": "p.pdf", "JSON": "j.json"},
                    },
                ),
                patch("main.prepare_sync_document", return_value=None),
                patch("main.print_step"),
                patch("main.console.status"),
                patch(
                    "main.console.print",
                    side_effect=lambda *a, **k: printed.append(str(a[0]) if a else ""),
                ),
            ]
            if uploaded is not None:
                patches.append(patch("main.upload", return_value=uploaded))
            for item in patches:
                item.start()
            try:
                main.run_assessment(
                    config,
                    name,
                    non_interactive=True,
                    settings=CONFIGURED if uploaded is not None else None,
                    **kwargs,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
        return printed

    def uploaded(self, **fields):
        return UploadResult(
            True,
            True,
            message="uploaded",
            sync_id=SYNC_ID,
            assessment_id="a-1",
            url=self.URL,
            **fields,
        )

    def test_the_closing_block_is_printed_once_for_every_provider_and_state(self):
        for provider in (self.AWS, self.AZURE):
            for uploaded in (None, self.uploaded()):
                with self.subTest(provider=provider[0], uploaded=bool(uploaded)):
                    printed = self.lines(provider, uploaded)

                    self.assertEqual(printed.count("Outputs:"), 1)
                    at = printed.index("Outputs:")
                    self.assertEqual(printed[at - 1], "-" * 43)
                    self.assertEqual(printed[at - 2 :].count("-" * 43), 2)
                    self.assertEqual(printed[-1], "-" * 43)

    def test_the_charge_is_printed_once_under_the_platform_line(self):
        for provider in (self.AWS, self.AZURE):
            with self.subTest(provider=provider[0]):
                printed = self.lines(
                    provider,
                    self.uploaded(billing={"charged_credits": 20, "balance": 380}),
                )

                at = printed.index(f"Platform: {self.URL}")
                self.assertEqual(printed[at + 1], "Charged 20 credits; balance 380.")
                self.assertEqual(printed.count("Charged 20 credits; balance 380."), 1)

    def test_a_platform_that_does_not_charge_prints_no_charge_line(self):
        printed = self.lines(self.AWS, self.uploaded())

        self.assertIn(f"Platform: {self.URL}", printed)
        self.assertFalse(any(line.startswith("Charged") for line in printed))

    def test_an_offline_run_prints_no_platform_or_charge_line(self):
        printed = self.lines(self.AZURE, None)

        self.assertFalse(
            any(line.startswith(("Platform", "Charged")) for line in printed)
        )


if __name__ == "__main__":
    unittest.main()
