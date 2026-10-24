import logging
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from azure.core.exceptions import HttpResponseError

import main
from core.engine import create_cost_inventory
from core.utils_azure import (
    COST_RATE_LIMITED,
    CostRateLimited,
    build_azure_cost_inventory,
    query_costs,
    retry_wait,
)
from utils import codes

COST_HEADER = "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after"


def http_error(status, **headers):
    response = Mock(status_code=status, headers=headers, reason="Too Many Requests")
    return HttpResponseError(response=response)


def cost_result(rows):
    result = MagicMock()
    result.rows = rows
    result.as_dict.return_value = {"properties": {"rows": rows}}
    return result


class RetryWaitTests(unittest.TestCase):
    def test_the_wait_follows_retry_after(self):
        self.assertEqual(retry_wait(http_error(429, **{"Retry-After": "37"}), 0), 37)

    def test_the_wait_follows_the_cost_management_headers(self):
        error = http_error(429, **{COST_HEADER: "12"})
        self.assertEqual(retry_wait(error, 0), 12)

    def test_the_longest_wait_the_response_asks_for_is_used(self):
        error = http_error(
            429,
            **{
                "Retry-After": "5",
                COST_HEADER: "40",
                "x-ms-ratelimit-microsoft.costmanagement-tenant-retry-after": "25",
            },
        )
        self.assertEqual(retry_wait(error, 0), 40)

    def test_without_a_usable_header_the_wait_grows_20_40_60(self):
        for headers in ({}, {"Retry-After": "soon"}, {"x-other-retry-after": "9"}):
            with self.subTest(headers=headers):
                error = http_error(429, **headers)
                self.assertEqual(
                    [retry_wait(error, attempt) for attempt in range(5)],
                    [20, 40, 60, 60, 60],
                )

    def test_an_error_without_a_response_uses_the_defaults(self):
        self.assertEqual(retry_wait(RuntimeError("x"), 1), 40)


class QueryCostsTests(unittest.TestCase):
    def setUp(self):
        sleep = patch("core.utils_azure.time.sleep")
        notice = patch("core.utils_azure.print_step")
        self.sleep = sleep.start()
        self.notice = notice.start()
        self.addCleanup(sleep.stop)
        self.addCleanup(notice.stop)
        self.client = MagicMock()

    def run_query(self, *answers):
        self.client.query.usage.side_effect = list(answers)
        return query_costs(self.client, "/scope", "query")

    def test_a_first_answer_needs_no_wait(self):
        result = self.run_query("rows")

        self.assertEqual(result, "rows")
        self.sleep.assert_not_called()
        self.notice.assert_not_called()

    def test_429_then_200_succeeds_after_one_wait_that_follows_the_header(self):
        result = self.run_query(http_error(429, **{"Retry-After": "37"}), "rows")

        self.assertEqual(result, "rows")
        self.sleep.assert_called_once_with(37)
        self.assertEqual(self.client.query.usage.call_count, 2)

    def test_the_wait_is_announced_in_a_step_line(self):
        self.run_query(http_error(429, **{"Retry-After": "40"}), "rows")

        (call,) = self.notice.call_args_list
        self.assertEqual(
            call.args[0], "Azure cost API is rate limited, retrying in 40 s…"
        )
        self.assertEqual(call.kwargs["status"], "warning")

    def test_without_headers_the_waits_are_20_40_and_60_seconds(self):
        result = self.run_query(
            http_error(429), http_error(429), http_error(429), "rows"
        )

        self.assertEqual(result, "rows")
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [20, 40, 60])

    def test_four_429s_give_up_with_the_clear_message(self):
        with self.assertRaises(CostRateLimited) as ctx:
            self.run_query(*[http_error(429) for _ in range(4)])

        self.assertEqual(str(ctx.exception), COST_RATE_LIMITED)
        self.assertEqual(self.client.query.usage.call_count, 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [20, 40, 60])

    def test_a_long_wait_that_would_pass_three_minutes_in_total_gives_up(self):
        with self.assertRaises(CostRateLimited):
            self.run_query(*[http_error(429, **{"Retry-After": "100"})] * 4)

        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [100])
        self.assertEqual(self.client.query.usage.call_count, 2)

    def test_503_is_retried_too(self):
        self.assertEqual(self.run_query(http_error(503), "rows"), "rows")
        self.sleep.assert_called_once_with(20)

    def test_other_errors_are_not_retried(self):
        for status in (400, 401, 403, 404, 500):
            with self.subTest(status=status):
                self.client.query.usage.reset_mock()
                with self.assertRaises(HttpResponseError) as ctx:
                    self.run_query(http_error(status))
                self.assertEqual(ctx.exception.status_code, status)
                self.assertEqual(self.client.query.usage.call_count, 1)
        self.sleep.assert_not_called()

    def test_errors_that_are_not_http_errors_are_not_retried(self):
        with self.assertRaises(RuntimeError):
            self.run_query(RuntimeError("boom"))
        self.sleep.assert_not_called()


class CostInventoryTests(unittest.TestCase):
    DETAILS = {
        "credential": MagicMock(),
        "subscriptionId": "00000000-0000-0000-0000-000000000000",
        "resourceGroupName": "rg",
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.report = os.path.join(tmp.name, "report")
        self.raw = os.path.join(tmp.name, "raw")
        os.makedirs(os.path.join(self.report, "data"))
        os.makedirs(self.raw)
        conn = sqlite3.connect(os.path.join(self.report, "data", "assessment.db"))
        conn.execute(
            "CREATE TABLE cost_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "month TEXT NOT NULL, cost REAL NOT NULL, currency TEXT NOT NULL, "
            "UNIQUE(month))"
        )
        conn.commit()
        conn.close()
        for target in ("core.utils_azure.time.sleep", "core.utils_azure.print_step"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def costs(self):
        conn = sqlite3.connect(os.path.join(self.report, "data", "assessment.db"))
        try:
            return conn.execute("SELECT count(*) FROM cost_inventory").fetchone()[0]
        finally:
            conn.close()

    def test_a_run_that_is_rate_limited_once_still_stores_the_costs(self):
        rows = [[12.5, "2026-09-01T00:00:00", "USD"]]
        client = MagicMock()
        client.query.usage.side_effect = [http_error(429), cost_result(rows)]

        with patch(
            "azure.mgmt.costmanagement.CostManagementClient", return_value=client
        ):
            build_azure_cost_inventory(1, self.DETAILS, self.report, self.raw)

        self.assertEqual(self.costs(), 6)

    def test_a_run_that_stays_rate_limited_ends_with_the_clear_line_and_no_error_log(
        self,
    ):
        client = MagicMock()
        client.query.usage.side_effect = [http_error(429) for _ in range(4)]

        with (
            patch(
                "azure.mgmt.costmanagement.CostManagementClient", return_value=client
            ),
            self.assertNoLogs(level=logging.ERROR),
        ):
            result = create_cost_inventory(1, self.DETAILS, self.report, self.raw)

        self.assertEqual(result, {"success": False, "logs": COST_RATE_LIMITED})
        self.assertEqual(self.costs(), 0)

    def test_the_traceback_is_kept_for_run_log(self):
        client = MagicMock()
        client.query.usage.side_effect = [http_error(429) for _ in range(4)]

        with (
            patch(
                "azure.mgmt.costmanagement.CostManagementClient", return_value=client
            ),
            self.assertLogs("core.engine", level=logging.DEBUG) as logs,
        ):
            create_cost_inventory(1, self.DETAILS, self.report, self.raw)

        self.assertTrue(any(record.exc_info for record in logs.records))
        self.assertFalse(
            any(record.levelno >= logging.ERROR for record in logs.records)
        )

    def test_other_cost_errors_still_log_at_error_level(self):
        client = MagicMock()
        client.query.usage.side_effect = http_error(403)

        with (
            patch(
                "azure.mgmt.costmanagement.CostManagementClient", return_value=client
            ),
            self.assertLogs("core.engine", level=logging.ERROR),
        ):
            result = create_cost_inventory(1, self.DETAILS, self.report, self.raw)

        self.assertFalse(result["success"])
        self.assertEqual(client.query.usage.call_count, 1)


class RunExitTests(unittest.TestCase):
    CONFIG = {
        "name": "Azure",
        "cloudServiceProvider": 1,
        "exitStrategy": 1,
        "assessmentType": 1,
        "providerDetails": {
            "credential": MagicMock(),
            "tenantId": "t",
            "subscriptionId": "00000000-0000-0000-0000-000000000000",
            "resourceGroupName": "rg",
        },
    }

    def test_a_stage_that_stays_rate_limited_exits_6_with_the_one_line(self):
        steps = []
        client = MagicMock()
        client.query.usage.side_effect = [http_error(429) for _ in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            report, raw = os.path.join(tmp, "r"), os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report, "data"))
            os.makedirs(raw)
            with (
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
                    "azure.mgmt.costmanagement.CostManagementClient",
                    return_value=client,
                ),
                patch("core.utils_azure.time.sleep"),
                patch("core.utils_azure.print_step"),
                patch(
                    "main.print_step",
                    side_effect=lambda *a, **k: steps.append((a, k)),
                ),
                patch("main.console.print"),
                self.assertNoLogs(level=logging.ERROR),
                self.assertRaises(SystemExit) as ctx,
            ):
                main.run_assessment(dict(self.CONFIG), "azure")

        self.assertEqual(ctx.exception.code, codes.COST_INVENTORY)
        failed = [k for a, k in steps if k.get("status") == "error"]
        self.assertEqual([k["logs"] for k in failed], [COST_RATE_LIMITED])
        self.assertNotIn("Traceback", COST_RATE_LIMITED)


if __name__ == "__main__":
    unittest.main()
