import copy
from contextlib import closing
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from cloudexit.cli import app
from cloudexit.egress.model import write_egress_inventory
from tests.report import test_egress as egress_helpers
from tests.report.test_report_flow import FlowTestCase
from tests.support.credentials import AWS_DETAILS, AZURE_DETAILS, leaked_fragments

MSAL_LINE = '{"client_id": "%s", "api_id": "729", "event_name": "msal.token"}'


def pricing_for_both_clouds(table_name, db_path=None):
    if table_name == "egresspricing":
        return [
            {**row, "csp": csp}
            for csp in (1, 2)
            for row in egress_helpers.RENDER_PRICING
        ]
    return []


def folder_text(folder):
    # Every file of the report folder as text; the PDFs by their extracted text
    found = {}
    for path in sorted(Path(folder).rglob("*")):
        relative = path.relative_to(folder)
        if not path.is_file() or relative.parts[0] == "assets":
            continue
        if path.suffix == ".pdf":
            reader = PdfReader(str(path))
            found[relative.as_posix()] = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        else:
            found[relative.as_posix()] = path.read_bytes().decode("utf-8", "ignore")
    return found


def find_leaks(folder, details):
    fragments = leaked_fragments(details)
    return sorted(
        f"{name}: {fragment}"
        for name, text in folder_text(folder).items()
        for fragment in fragments
        if fragments[fragment] in text
    )


class OutputGuardTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        root = logging.getLogger()
        saved = (root.level, list(root.handlers))
        levels = {
            name: logging.getLogger(name).level
            for name in (*app._THIRD_PARTY_NOISY, *app._ALWAYS_QUIET)
        }

        def restore():
            root.setLevel(saved[0])
            root.handlers[:] = saved[1]
            for name, level in levels.items():
                logging.getLogger(name).setLevel(level)

        self.addCleanup(restore)

    def run_with_egress(self, provider, details):
        # (type id, code, label) of a resource type in the fixture catalogue
        type_id, code, label = {
            2: (1, "AWS.EC2.DescribeInstances", "EC2 Instance"),
            1: (3, "Azure.Compute.VirtualMachines", "Virtual Machine"),
        }[provider]
        report = self.report(
            resources=[(type_id, "eu-central-1" if provider == 2 else "westeurope", 8)]
        )
        raw_data = os.path.join(report, "raw_data")
        rows = [
            egress_helpers._row(
                "proddata",
                label,
                "object",
                2048 * egress_helpers.GIB,
                resource_type=code,
            ),
            egress_helpers._row("vault1", label, "object", None, resource_type=code),
        ]

        def estimate(csp, provider_details, raw_path, **kwargs):
            payload = egress_helpers._payload(rows)
            payload["meta"]["cloud_service_provider"] = csp
            json_path = os.path.join(raw_path, "egress_inventory_raw_data.json")
            Path(json_path).write_text(json.dumps(payload), encoding="utf-8")
            db_path = os.path.join(report, "data", "assessment.db")
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS resourcetype_data (id INTEGER PRIMARY "
                    "KEY, resource_type INTEGER NOT NULL, data_category TEXT NOT NULL, "
                    "strategy TEXT NOT NULL, params TEXT, status TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO resourcetype_data VALUES (1, ?, 'object', 's', '{}', 't')",
                    (type_id,),
                )
                conn.commit()
            write_egress_inventory(
                rows, {code: type_id}, egress_helpers._archive_tiers(rows), db_path
            )
            return {"success": True, "logs": "", "json_path": json_path}

        def verify(csp, provider_details):
            # What MSAL writes at DEBUG while the Azure credential is built
            logging.getLogger("msal.telemetry").debug(
                MSAL_LINE % provider_details.get("clientId", "none")
            )
            return True, "ok"

        patches = [
            patch("cloudexit.cli.app.validate_config"),
            patch(
                "cloudexit.cli.app.create_directory", return_value=(report, raw_data)
            ),
            patch("cloudexit.cli.app.verify_credentials", side_effect=verify),
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
            patch("cloudexit.cli.app.estimate_egress", side_effect=estimate),
            patch(
                "cloudexit.report.egress.load_data",
                side_effect=pricing_for_both_clouds,
            ),
            patch("cloudexit.cli.app.print_step"),
            patch("cloudexit.cli.app.console.print"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        config = {
            "name": "Guard Test",
            "cloudServiceProvider": provider,
            "exitStrategy": 1,
            "assessmentType": 1,
            "providerDetails": copy.deepcopy(details),
        }
        # -vv is the most permissive setting: everything not muted gets into run.log
        app.configure_logging(2)
        app.run_assessment(
            config,
            "aws" if provider == 2 else "azure",
            egress=True,
            flush_spool=False,
        )
        return report

    def assert_complete_run(self, report):
        for name in (
            "index.html",
            "report.pdf",
            "egress.html",
            "egress.pdf",
            "run.log",
            "raw_data/assessment_result.json",
            "raw_data/sync.json",
        ):
            self.assertTrue((Path(report) / name).is_file(), name)

    def test_an_aws_run_with_egress_writes_no_credential(self):
        report = self.run_with_egress(2, AWS_DETAILS)

        self.assert_complete_run(report)
        self.assertEqual(find_leaks(report, AWS_DETAILS), [])

    def test_an_azure_run_with_egress_writes_no_credential(self):
        report = self.run_with_egress(1, AZURE_DETAILS)

        self.assert_complete_run(report)
        self.assertEqual(find_leaks(report, AZURE_DETAILS), [])
        log = (Path(report) / "run.log").read_text(encoding="utf-8")
        self.assertNotIn("msal", log)

    def test_the_egress_pdf_shows_the_scope_without_the_credentials(self):
        report = self.run_with_egress(1, AZURE_DETAILS)

        text = folder_text(report)["egress.pdf"]
        self.assertIn("Assessment Scope", text)
        self.assertIn("Subscription ID", text)
        self.assertIn(AZURE_DETAILS["subscriptionId"], text)
        self.assertNotIn("Client", text)

    def test_the_guard_finds_a_planted_value(self):
        for part in ("", "[:4]", "[-4:]"):
            with self.subTest(part=part):
                with tempfile.TemporaryDirectory() as tmp:
                    value = eval(f"AZURE_DETAILS['clientId']{part}")
                    (Path(tmp) / "raw_data").mkdir()
                    (Path(tmp) / "raw_data" / "x.json").write_text(
                        f'{{"a": "{value}"}}', encoding="utf-8"
                    )
                    (Path(tmp) / "assets").mkdir()
                    (Path(tmp) / "assets" / "ignored.txt").write_text(value)

                    leaks = find_leaks(tmp, AZURE_DETAILS)

                self.assertTrue(leaks)
                self.assertTrue(
                    all(leak.startswith("raw_data/x.json") for leak in leaks)
                )

    def test_the_guard_finds_nothing_in_a_clean_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.log").write_text("nothing to see", encoding="utf-8")
            self.assertEqual(find_leaks(tmp, AWS_DETAILS), [])


if __name__ == "__main__":
    unittest.main()
