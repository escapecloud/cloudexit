import io
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from cloudexit.report.sections import AWS_ICONS_URL, AZURE_ICONS_URL
from cloudexit.report.wording import BLOCKS
from tests.report.test_basic_report import metadata_texts, page_texts
from tests.report.test_no_credentials import find_leaks, folder_text
from tests.samples import __main__ as samples_main
from tests.samples.catalogue import build_sample_catalogue
from tests.samples.rendered import rendered
from tests.samples.render import (
    expected_files,
    pdf_dump,
    render_case,
    render_cases,
)
from tests.support.credentials import AWS_DETAILS, AZURE_DETAILS
from tests.support.html import html_blocks, html_text
from tests.support.report_terms import american_hits, find_terms, retired_term_hits
from tests.support.sync import SECRETS

IDENTIFYING = ("accountId", "tenantId", "subscriptionId", "resourceGroupName")
ICON_MARKERS = {"azure": "Microsoft group", "aws": "Amazon.com"}
EGRESS_FILES = (
    "egress.pdf",
    "texts/egress.txt",
)

STATE = {}


def squashed(text):
    return text.replace(" ", "")


def setUpModule():
    STATE.update(rendered())


def tearDownModule():
    STATE.clear()


class RenderedTestCase(unittest.TestCase):
    def cases(self):
        yield from STATE["scenarios"].items()

    def folder(self, case):
        return STATE["results"][case].directory

    def text(self, case, name):
        return (self.folder(case) / name).read_text(encoding="utf-8")

    def pdf_pages(self, case, name):
        return page_texts(PdfReader(str(self.folder(case) / name)))


class RenderTests(RenderedTestCase):
    def test_every_case_renders(self):
        self.assertEqual(STATE["failures"], {})
        self.assertEqual(set(STATE["results"]), set(STATE["scenarios"]))

    def test_the_expected_files_exist_and_no_others_of_the_list(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                for name in expected_files(scenario):
                    self.assertTrue((self.folder(case) / name).is_file(), name)
                if scenario.egress is None:
                    for name in EGRESS_FILES:
                        self.assertFalse((self.folder(case) / name).exists(), name)
                self.assertEqual(STATE["results"][case].files, expected_files(scenario))

    def test_a_state_file_never_produces_egress(self):
        for case, scenario in self.cases():
            if scenario.tfstate:
                with self.subTest(case):
                    self.assertEqual(scenario.egress, None)
                    self.assertFalse((self.folder(case) / "egress.pdf").exists())

    def test_the_dumps_match_the_files(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                folder = self.folder(case)
                for pdf, dump in (
                    ("report.pdf", "report.txt"),
                    ("egress.pdf", "egress.txt"),
                ):
                    if (folder / pdf).is_file():
                        text, pages = pdf_dump(folder / pdf)
                        self.assertEqual(self.text(case, f"texts/{dump}"), text)
                        self.assertEqual(text.count("--- page "), pages)
                self.assertEqual(
                    self.text(case, "texts/index-html.txt"),
                    html_blocks(self.text(case, "index.html")),
                )
                # One HTML report: there is no second page for the egress cases
                self.assertFalse((folder / "egress.html").exists())

    def test_the_html_dump_has_the_egress_section_for_the_egress_cases(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                dump = self.text(case, "texts/index-html.txt")
                has_section = "## Egress & Data Landscape" in dump
                self.assertEqual(has_section, scenario.egress is not None)

    def test_rendering_a_case_twice_gives_the_same_texts(self):
        scenario = STATE["scenarios"]["aws_typical"]
        with tempfile.TemporaryDirectory() as tmp:
            again = render_case(scenario, tmp, catalogue_path=STATE["catalogue"])
            for name in expected_files(scenario):
                if name.startswith("texts/"):
                    self.assertEqual(
                        (again.directory / name).read_text(encoding="utf-8"),
                        self.text("aws_typical", name),
                        name,
                    )


class GuardTests(RenderedTestCase):
    def test_no_credential_and_no_value_of_another_case(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                folder = self.folder(case)
                state = ""
                if scenario.tfstate:
                    state = Path(scenario.tfstate).read_text(encoding="utf-8")
                own = {v for k, v in scenario.scope.items() if k in IDENTIFYING}
                others = {
                    v
                    for other, candidate in self.cases()
                    if other != case
                    for k, v in candidate.scope.items()
                    if k in IDENTIFYING
                }
                forbidden = {v for v in others - own if v not in state}
                texts = folder_text(folder)
                for name, text in texts.items():
                    for value in forbidden:
                        self.assertNotIn(value, text, f"{name}: {value}")
                    for secret in SECRETS:
                        self.assertNotIn(secret, text, name)
                    self.assertIsNone(re.search(r"\bAKIA[0-9A-Z]{16}\b", text), name)
                for details in (AWS_DETAILS, AZURE_DETAILS):
                    self.assertEqual(find_leaks(folder, details), [])

    def test_the_term_wording_and_retired_term_guards(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                folder = self.folder(case)
                texts = []
                for pdf in ("report.pdf", "egress.pdf"):
                    if (folder / pdf).is_file():
                        reader = PdfReader(str(folder / pdf))
                        for text in metadata_texts(reader):
                            self.assertEqual(find_terms(text), [])
                        texts += page_texts(reader)
                source = self.text(case, "index.html")
                texts.append(html_text(source))
                self.assertEqual(find_terms(source.split("<body", 1)[0]), [])
                self.assertTrue(texts)
                for text in texts:
                    self.assertEqual(find_terms(text), [])
                    self.assertEqual(retired_term_hits(text), [])
                    self.assertEqual(american_hits(text), [])

    def test_the_fonts_are_plex_and_nothing_else(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                for pdf in ("report.pdf", "egress.pdf"):
                    path = self.folder(case) / pdf
                    if path.is_file():
                        names = set(
                            re.findall(rb"/BaseFont\s*/(\S+)", path.read_bytes())
                        )
                        self.assertTrue(names)
                        self.assertTrue(all(b"IBMPlex" in n for n in names), names)

    def test_the_last_page_is_the_notices_with_the_providers_icon_notice_only(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                other = "azure" if scenario.provider == "aws" else "aws"
                for pdf in ("report.pdf", "egress.pdf"):
                    if not (self.folder(case) / pdf).is_file():
                        continue
                    pages = self.pdf_pages(case, pdf)
                    self.assertIn("Legal Notices", pages[-1][:200])
                    self.assertIn(ICON_MARKERS[scenario.provider], pages[-1])
                    self.assertNotIn(ICON_MARKERS[other], " ".join(pages))
                    links = {
                        str(annotation.get_object()["/A"]["/URI"])
                        for page in PdfReader(str(self.folder(case) / pdf)).pages
                        for annotation in page.get("/Annots", [])
                        if "/URI" in (annotation.get_object().get("/A") or {})
                    }
                    own = (
                        AWS_ICONS_URL if scenario.provider == "aws" else AZURE_ICONS_URL
                    )
                    foreign = (
                        AZURE_ICONS_URL if scenario.provider == "aws" else AWS_ICONS_URL
                    )
                    self.assertIn(own, links)
                    self.assertNotIn(foreign, links)

    def test_the_egress_notices_carry_the_egress_paragraph(self):
        for case, scenario in self.cases():
            if scenario.egress is not None:
                with self.subTest(case):
                    self.assertIn(
                        BLOCKS["notices_egress"],
                        self.pdf_pages(case, "egress.pdf")[-1],
                    )
                    self.assertNotIn(
                        BLOCKS["notices_egress"],
                        self.pdf_pages(case, "report.pdf")[-1],
                    )


class UploadTests(RenderedTestCase):
    def test_uploaded_cases_show_their_link_or_assessment_id_on_page_two_only(self):
        linked = STATE["scenarios"]["azure_uploaded"].upload["url"]
        for pdf in ("report.pdf", "egress.pdf"):
            pages = self.pdf_pages("azure_uploaded", pdf)
            self.assertIn(linked, squashed(pages[1]))
            self.assertIn("Uploaded to the platform", pages[1])
            if pdf == "report.pdf":
                self.assertEqual(sum(linked in squashed(text) for text in pages), 1)
        self.assertNotIn(
            "The Full Assessment",
            " ".join(self.pdf_pages("azure_uploaded", "report.pdf")),
        )

        pages = self.pdf_pages("uploaded_no_link", "report.pdf")
        self.assertIn("Uploaded to the platform", pages[1])
        self.assertIn("Assessment ID: sample-assessment-0001", pages[1])
        self.assertEqual(sum("sample-assessment-0001" in text for text in pages), 1)
        self.assertNotIn("https://platform", " ".join(pages))

    def test_offline_cases_show_the_self_collected_line_and_no_closing_page(self):
        for case, scenario in self.cases():
            if scenario.upload is None:
                with self.subTest(case):
                    pages = self.pdf_pages(case, "report.pdf")
                    self.assertIn("Self-collected data", pages[1])
                    self.assertNotIn("What the Full Assessment Adds", " ".join(pages))
                    self.assertIn("Legal Notices", pages[-1][:200])
                    if scenario.egress is not None:
                        egress = self.pdf_pages(case, "egress.pdf")
                        self.assertIn("Self-collected data", egress[1])

    def test_a_failed_upload_says_what_happened_in_both_pdfs_and_the_html(self):
        wording = (
            "Upload failed: The platform did not answer in time. "
            "The run was kept for a later upload."
        )
        for pdf in ("report.pdf", "egress.pdf"):
            if (self.folder("upload_failed") / pdf).is_file():
                self.assertIn(wording, self.pdf_pages("upload_failed", pdf)[1])
        pages = self.pdf_pages("upload_failed", "report.pdf")
        self.assertNotIn("Self-collected data", " ".join(pages))
        self.assertIn(wording, html_text(self.text("upload_failed", "index.html")))
        chip = re.search(
            r'<span class="chip[^>]*>(.*?)</span>',
            self.text("upload_failed", "index.html"),
        )
        self.assertEqual(chip.group(1), "Self-collected")


class CaseTests(RenderedTestCase):
    def test_no_risks_has_the_empty_risk_wording(self):
        text = " ".join(self.pdf_pages("no_risks", "report.pdf"))

        self.assertIn("There are no risks identified in this assessment.", text)

    def test_the_other_cases_have_risks(self):
        for case in ("aws_typical", "azure_typical", "stress"):
            with self.subTest(case):
                text = " ".join(self.pdf_pages(case, "report.pdf"))
                self.assertNotIn("There are no risks identified", text)

    def test_empty_renders_with_no_resources(self):
        text = " ".join(self.pdf_pages("empty", "report.pdf"))

        self.assertIn("There are no resources to be found", text)

    def test_the_free_tier_case_has_the_headroom_sentence(self):
        text = " ".join(self.pdf_pages("egress_free_tier", "egress.pdf"))

        self.assertIn("of the 100 GiB monthly free tier", text)
        self.assertIn("$0.00", text)
        self.assertEqual(text.count("Share of Volume"), 1)

    def test_the_multi_tib_case_has_nonzero_fees(self):
        text = " ".join(self.pdf_pages("egress_multi_tib", "egress.pdf"))
        amounts = [
            float(value.replace(",", ""))
            for value in re.findall(r"\$([\d,]+\.\d\d)", text)
        ]

        self.assertTrue(amounts)
        self.assertGreater(max(amounts), 1000)
        self.assertEqual(text.count("Share of Volume"), 2)
        self.assertNotIn("TiB", text)

    def test_the_egress_sentence_is_in_the_summary_only_with_egress(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                text = " ".join(self.pdf_pages(case, "report.pdf"))
                html = html_text(self.text(case, "index.html"))
                if scenario.egress is None:
                    self.assertNotIn("A separate Egress", text)
                    self.assertNotIn("A separate Egress", html)
                else:
                    edl = f"EDL-{scenario.run_id.replace('-', '')[-8:]}-EN"
                    self.assertIn(f"A separate Egress & Data Landscape ({edl})", text)
                    self.assertIn(edl, html)

    def test_unsized_types_carry_a_plus(self):
        text = " ".join(self.pdf_pages("azure_typical", "egress.pdf"))

        self.assertIn("GiB+", text)
        self.assertIn("Not sized:", text)

    def test_a_state_file_has_no_costs_and_an_unknown_region(self):
        for case in ("tfstate_aws", "tfstate_azure"):
            with self.subTest(case):
                text = " ".join(self.pdf_pages(case, "report.pdf"))
                self.assertIn("Terraform state files hold no billing data", text)
        aws = " ".join(self.pdf_pages("tfstate_aws", "report.pdf"))
        self.assertIn("eu-central-1, eu-west-1, unknown", aws)
        azure = " ".join(self.pdf_pages("tfstate_azure", "report.pdf"))
        self.assertIn("Subscriptions", azure)
        self.assertIn("rg-sample-archive", azure)

    def test_unsized_egress_is_not_sized_on_every_type(self):
        text = " ".join(self.pdf_pages("egress_unsized", "egress.pdf"))
        dump = self.text("egress_unsized", "texts/index-html.txt")
        dash = BLOCKS["ev_dash"]

        self.assertIn("None of the 5 data-bearing resources could be sized.", text)
        self.assertIn("RESOURCES SIZED 0 of 5", text)
        self.assertIn("Not sized: Storage Account (2 resources)", text)
        self.assertIn("SQL Database (1 resource)", text)
        # Nothing was measured, so no figure may claim a lower bound of zero
        for strip in ("DATA VOLUME", "ESTIMATED FEE (USD)", "BILLABLE VOLUME"):
            self.assertIn(f"{strip} {dash}", text)
        for kpi in ("Estimated Transfer Fee", "Data Volume", "Billable Volume"):
            self.assertIn(f"{kpi} {dash}", dump)
        self.assertIn(f"Total {dash} {dash}", dump)
        for banned in ("0.0 GiB+", "$0.00+", "0.00+"):
            self.assertNotIn(banned, text)
            self.assertNotIn(banned, dump)
        self.assertNotIn("monthly free tier", text)
        self.assertNotIn("Free-Tier Headroom", dump)
        self.assertNotIn("no free tier in the prices used", dump)
        self.assertIn("not sized", dump.lower())

    def test_no_egress_pdf_promotes_a_platform_or_an_edition(self):
        for case, scenario in self.cases():
            if scenario.egress is None:
                continue
            with self.subTest(case):
                text = " ".join(self.pdf_pages(case, "egress.pdf"))
                for banned in (
                    "Next Steps",
                    "exitcloud.io",
                    "book-demo",
                    "book a demo",
                    "Connected",
                ):
                    self.assertNotIn(banned, text)

    def test_an_empty_scope_with_costs_has_empty_states_next_to_real_figures(self):
        text = " ".join(self.pdf_pages("empty_with_costs", "report.pdf"))
        dump = self.text("empty_with_costs", "texts/index-html.txt")

        self.assertIn("There are no risks identified in this assessment.", text)
        self.assertIn("There are no resources to be found", text)
        self.assertNotIn("No cost data available", text)
        self.assertRegex(text, r"€\s?\d")
        self.assertIn("No resources were found in the defined scope.", dump)
        self.assertRegex(dump, r"Costs, Last 6 Months\s+€\s?\d")

    def test_a_dataset_without_catalogue_meta_says_the_version_is_unknown(self):
        unknown = BLOCKS["ev_catalogue_unknown"]
        pages = self.pdf_pages("legacy_catalogue", "report.pdf")
        dump = self.text("legacy_catalogue", "texts/index-html.txt")

        self.assertIn(unknown, pages[1])
        self.assertGreaterEqual(dump.count(unknown), 2)
        self.assertNotIn("2026-11-02", pages[1])
        self.assertIn("2026-11-02", self.pdf_pages("aws_typical", "report.pdf")[1])

    def test_a_state_file_with_foreign_and_unmapped_instances_has_the_not_assessed_row(
        self,
    ):
        pages = self.pdf_pages("tfstate_skipped", "report.pdf")
        dump = self.text("tfstate_skipped", "texts/index-html.txt")
        row = (
            "Not Assessed 3 resources of other providers, "
            "3 resources of types not in the data catalogue"
        )
        caption = "6 resources in the state file were not assessed"

        self.assertIn(row, pages[1])
        self.assertIn(row, dump)
        self.assertIn(f"{caption} (see page 2)", " ".join(pages))
        self.assertIn(f"{caption} (see About This Report)", dump)

    def test_the_stress_case_is_long_and_has_mixed_currencies(self):
        pages = self.pdf_pages("stress", "report.pdf")
        text = " ".join(pages)

        self.assertGreater(len(pages), 11)
        self.assertIn("more than one currency", text)
        self.assertIn("Deliberately Long Project Name", text)
        self.assertIn("Legal Notices", pages[-1][:200])


class FailureTests(unittest.TestCase):
    def test_a_missing_or_inactive_type_fails_only_its_cases(self):
        scenarios = STATE["scenarios"]
        with tempfile.TemporaryDirectory() as tmp:
            catalogue = Path(tmp) / "catalogue.db"
            build_sample_catalogue(catalogue, scenarios.values())
            conn = sqlite3.connect(catalogue)
            conn.execute(
                "DELETE FROM resourcetype WHERE code = 'AWS.sns.list_topics.Topics'"
            )
            conn.execute(
                "UPDATE resourcetype SET status = 'f' "
                "WHERE code = 'Microsoft.ContainerRegistry/registries'"
            )
            conn.commit()
            conn.close()

            results, failures = render_cases(
                [scenarios[c] for c in ("aws_typical", "azure_typical", "empty")],
                Path(tmp) / "out",
                catalogue_path=catalogue,
            )

        self.assertEqual([result.case for result in results], ["empty"])
        self.assertEqual(
            failures["aws_typical"],
            "aws_typical: resource type AWS.sns.list_topics.Topics is not in the catalogue",
        )
        self.assertEqual(
            failures["azure_typical"],
            "azure_typical: resource type Microsoft.ContainerRegistry/registries "
            "is not active in the catalogue",
        )


class CommandTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = samples_main.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_one_case_writes_the_reports_the_dumps_and_the_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _err = self.run_main(
                "--case",
                "aws_typical",
                "--out",
                tmp,
                "--catalogue",
                str(STATE["catalogue"]),
            )

            folder = Path(tmp) / "aws_typical"
            for name in expected_files(STATE["scenarios"]["aws_typical"]):
                self.assertTrue((folder / name).is_file(), name)
            overview = (Path(tmp) / "index.html").read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn("rendered aws_typical", out)
        for link in (
            'href="aws_typical/report.pdf"',
            'href="aws_typical/egress.pdf"',
            'href="aws_typical/index.html"',
        ):
            self.assertIn(link, overview)
        self.assertIn("Executive Summary: 10", overview)
        self.assertIn("Sample – Customer Self-Service Portal", overview)
        self.assertNotIn("azure_typical", overview)

    def test_a_failing_case_exits_non_zero_and_the_others_still_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalogue = Path(tmp) / "catalogue.db"
            build_sample_catalogue(catalogue, STATE["scenarios"].values())
            conn = sqlite3.connect(catalogue)
            conn.execute(
                "DELETE FROM resourcetype WHERE code = 'AWS.ec2.describe_volumes.Volumes'"
            )
            conn.commit()
            conn.close()

            code, _out, err = self.run_main(
                "--case",
                "aws_typical",
                "empty",
                "--out",
                str(Path(tmp) / "out"),
                "--catalogue",
                str(catalogue),
            )
            overview = (Path(tmp) / "out" / "index.html").read_text(encoding="utf-8")
            rendered = (Path(tmp) / "out" / "empty" / "report.pdf").is_file()

        self.assertEqual(code, 1)
        self.assertIn("FAILED aws_typical", err)
        self.assertTrue(rendered)
        self.assertIn("is not in the catalogue", overview)

    def test_without_a_catalogue_it_stops_and_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                samples_main,
                "active_catalogue_path",
                return_value=Path(tmp) / "none.db",
            ):
                code, _out, err = self.run_main("--out", str(Path(tmp) / "out"))

        self.assertEqual(code, 2)
        self.assertIn("Run cloudexit once", err)

    def test_an_unknown_case_is_named(self):
        code, _out, err = self.run_main(
            "--case", "nothing", "--catalogue", str(STATE["catalogue"])
        )

        self.assertEqual(code, 2)
        self.assertIn("No such case: nothing", err)

    def test_sheets_without_pymupdf_say_so_and_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(samples_main, "load_pymupdf", return_value=None):
                code, out, _err = self.run_main(
                    "--case",
                    "empty",
                    "--sheets",
                    "--out",
                    tmp,
                    "--catalogue",
                    str(STATE["catalogue"]),
                )
            sheets = list(Path(tmp).rglob("sheet-*.png"))

        self.assertEqual(code, 0)
        self.assertIn("PyMuPDF is not installed", out)
        self.assertEqual(sheets, [])


if __name__ == "__main__":
    unittest.main()
