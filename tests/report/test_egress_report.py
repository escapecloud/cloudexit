import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from cloudexit.engine import generate_report, perform_risk_assessment
from cloudexit.report.context import (
    EGRESS_DOCUMENT_CODE,
    Provenance,
    format_document_id,
)
from cloudexit.report.egress import generate_egress_html_report
from cloudexit.report.egress_report import generate_egress_report
from cloudexit.report.sections import AWS_ICONS_URL, AZURE_ICONS_URL
from cloudexit.report.wording import BLOCKS
from cloudexit.version import __version__
from tests.report import test_egress as egress_helpers
from tests.report.test_basic_report import (
    COLLECTED,
    URL,
    metadata_texts,
    outline_entries,
    page_texts,
    provenance,
    uploaded,
)
from tests.report.test_no_credentials import pricing_for_both_clouds
from tests.support.flow import build_flow_run
from tests.support.html import html_text
from tests.support.report import (
    build_fixture_context,
    build_report_fixture,
    stage_minimal_assets,
)
from tests.support.report_terms import american_hits, find_terms, retired_term_hits
from tests.support.sync import SYNC_ID

GIB = egress_helpers.GIB
ES_ID = format_document_id("ES", SYNC_ID)
EDL_ID = format_document_id(EGRESS_DOCUMENT_CODE, SYNC_ID)
OUTLINE = [
    (0, "Introduction"),
    (1, "Purpose"),
    (1, "Scope"),
    (1, "Limitations"),
    (1, "Scope of the Estimation"),
    (0, "Data Landscape"),
    (0, "Estimated Costs"),
    (0, "Appendix – Egress Prices"),
    (1, "Glossary"),
    (0, "Next Steps"),
    (0, "Legal Notices"),
]
MULTI_TIB = [
    egress_helpers._row("proddata", "Storage Account", "object", 2048 * GIB),
    egress_helpers._row("vm-osdisk", "Managed Disk", "block", 512 * GIB),
    egress_helpers._row("vault1", "Recovery Services Vault", "backup", None),
]
FREE_TIER = [egress_helpers._row("sa1", "Storage Account", "object", 20 * GIB)]
WITH_UNKNOWN = [
    egress_helpers._row("sa1", "Storage Account", "object", 2048 * GIB),
    egress_helpers._row("db1", "SQL Database", "database", None, size_unknown=True),
]


class EgressReportTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.count = 0

    def fixture(self, provider):
        fixture = copy.deepcopy(build_report_fixture())
        fixture["metadata"]["cloud_service_provider"] = provider
        if provider == 1:
            fixture["provider_details"] = {
                "tenantId": "11111111-1111-4111-8111-111111111111",
                "subscriptionId": "22222222-2222-4222-8222-222222222222",
                "resourceGroupName": "Prod-RG",
                "clientSecret": "very-secret-client-value",
            }
        return fixture

    def prepare(self, rows, provider):
        self.count += 1
        folder = self.tmp / f"report-{self.count}"
        folder.mkdir()
        stage_minimal_assets(str(folder))
        payload = egress_helpers._payload(rows)
        payload["meta"]["cloud_service_provider"] = provider
        json_path = folder / "egress_inventory_raw_data.json"
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        egress_helpers._seed_egress_inventory(
            str(folder), rows, egress_helpers._archive_tiers(rows)
        )
        return folder, str(json_path)

    def render(self, rows=MULTI_TIB, provider=1, prov=None):
        folder, json_path = self.prepare(rows, provider)
        context = build_fixture_context(
            self.fixture(provider), folder, provenance=prov or provenance()
        )
        with patch(
            "cloudexit.report.egress.load_data", side_effect=pricing_for_both_clouds
        ):
            path = generate_egress_report(context, json_path)
        return path, context, PdfReader(path)

    def render_html(self, rows=MULTI_TIB, provider=1, details=None):
        folder, json_path = self.prepare(rows, provider)
        with patch(
            "cloudexit.report.egress.load_data", side_effect=pricing_for_both_clouds
        ):
            result = generate_egress_html_report(
                str(folder), json_path, details, SYNC_ID
            )
        self.assertTrue(result["success"], result["logs"])
        html = Path(result["html_path"]).read_text(encoding="utf-8")
        return html, html_text(html)

    def scenarios(self):
        for provider, name in ((1, "azure"), (2, "aws")):
            for state, prov in (("offline", provenance()), ("uploaded", uploaded())):
                yield f"{name} {state}", provider, prov


class IdentityTests(unittest.TestCase):
    def test_a_context_names_a_document_per_code(self):
        context = build_fixture_context(
            build_report_fixture(), tempfile.gettempdir(), provenance=provenance()
        )

        self.assertEqual(context.document_id, ES_ID)
        self.assertEqual(context.document_id_for("EDL"), EDL_ID)
        self.assertRegex(EDL_ID, r"^EDL-[0-9a-f]{8}-EN$")
        # Both end with the same run, so the pair can be matched by eye
        self.assertEqual(ES_ID[3:], EDL_ID[4:])
        self.assertIsNone(context.egress_document_id)

    def test_a_run_without_an_id_is_local(self):
        self.assertEqual(format_document_id("EDL", None), "EDL-LOCAL-EN")


class CoverAndPageTwoTests(EgressReportTestCase):
    def test_the_cover_names_the_document_and_the_edition(self):
        for name, provider, prov in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(provider=provider, prov=prov)
                cover = page_texts(reader)[0]

                self.assertIn("Egress & Data Landscape", cover)
                self.assertIn(EDL_ID, cover)
                self.assertIn("EDITION Community", cover)
                self.assertNotIn("Classification", cover)
                self.assertNotIn("CLASSIFICATION", cover)
                everything = " ".join(page_texts(reader)).lower()
                self.assertNotIn("confidential", everything)
                self.assertNotIn("Page 1 of", cover)

    def test_page_two_is_the_document_information_with_the_companion(self):
        for name, provider, prov in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(provider=provider, prov=prov)
                pages = page_texts(reader)

                self.assertIn("Document Information", pages[1])
                self.assertIn("Assessment Scope", pages[1])
                self.assertIn("Provenance", pages[1])
                self.assertIn("Document Type Egress & Data Landscape", pages[1])
                self.assertIn(f"Companion Document {ES_ID}", pages[1])
                self.assertIn(EDL_ID, pages[1])
                self.assertIn(f"Page 2 of {len(pages)}", pages[1])

    def test_an_uploaded_run_shows_the_platform_link_on_page_two(self):
        # The PDF is written after the upload, so it knows about it
        _path, _context, reader = self.render(prov=uploaded())
        self.assertIn(URL, page_texts(reader)[1])
        self.assertIn("Uploaded to the platform", page_texts(reader)[1])

        _path, _context, offline = self.render(prov=provenance())
        self.assertNotIn(URL, page_texts(offline)[1])
        self.assertIn("Self-collected data", page_texts(offline)[1])

    def test_the_pdf_metadata_follows_the_cover(self):
        _path, _context, reader = self.render()
        info = reader.metadata

        self.assertEqual(info.title, "Egress & Data Landscape – Smoke Test Assessment")
        self.assertEqual(info.author, "cloudexit")
        self.assertEqual(info.creator, f"cloudexit {__version__}")
        self.assertIn(EDL_ID, info.subject)
        for keyword in (EDL_ID, SYNC_ID):
            self.assertIn(keyword, info["/Keywords"])
        for text in metadata_texts(reader):
            self.assertNotIn("EscapeCloud_-_", text)


class StructureTests(EgressReportTestCase):
    def test_the_outline_is_the_planned_list(self):
        for name, provider, prov in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(provider=provider, prov=prov)
                self.assertEqual(outline_entries(reader.outline), OUTLINE)

    def test_the_outline_does_not_depend_on_the_data(self):
        for rows in (MULTI_TIB, FREE_TIER, WITH_UNKNOWN, []):
            with self.subTest(rows=len(rows)):
                _path, _context, reader = self.render(rows)
                self.assertEqual(outline_entries(reader.outline), OUTLINE)

    def test_the_last_page_is_the_legal_notices_and_the_document_is_short(self):
        for name, provider, prov in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(provider=provider, prov=prov)
                pages = page_texts(reader)

                self.assertIn("Legal Notices", pages[-1][:200])
                self.assertNotIn("Legal Notices", " ".join(pages[:-1]))
                self.assertTrue(5 <= len(pages) <= 7, len(pages))

    def test_chapters_start_pages(self):
        _path, _context, reader = self.render()
        pages = page_texts(reader)

        self.assertIn("Introduction", pages[2])
        self.assertIn("Data Landscape", pages[3])
        starts = {
            entry.title: reader.get_destination_page_number(entry)
            for entry in reader.outline
            if not isinstance(entry, list)
        }
        self.assertEqual(starts["Introduction"], 2)
        self.assertEqual(starts["Data Landscape"], 3)
        self.assertGreater(starts["Appendix – Egress Prices"], starts["Data Landscape"])

    def test_every_running_header_and_footer_names_the_document(self):
        _path, _context, reader = self.render()
        pages = page_texts(reader)

        for index, text in enumerate(pages[1:], start=2):
            self.assertIn("Smoke Test Assessment", text)
            self.assertIn(EDL_ID, text)
            self.assertIn("COMMUNITY EDITION", text)
            self.assertIn(f"Page {index} of {len(pages)}", text)

    def test_the_fonts_are_plex_and_nothing_else(self):
        path, _context, _reader = self.render()
        names = set(re.findall(rb"/BaseFont\s*/(\S+)", Path(path).read_bytes()))

        self.assertTrue(names)
        self.assertTrue(all(b"IBMPlex" in name for name in names), names)


class ContentTests(EgressReportTestCase):
    def text(self, rows, **kwargs):
        _path, _context, reader = self.render(rows, **kwargs)
        return " ".join(page_texts(reader))

    def test_the_scope_of_the_estimation_lists_the_providers_types(self):
        azure = self.text(MULTI_TIB, provider=1)
        aws = self.text(MULTI_TIB, provider=2)

        self.assertIn("Recovery Services Vaults", azure)
        self.assertNotIn("S3 Buckets", azure)
        self.assertIn("S3 Buckets", aws)
        self.assertIn("Aurora is not sized", aws)
        self.assertNotIn("Recovery Services Vaults", aws)
        self.assertIn("Prod-RG", azure)

    def test_a_multi_tib_footprint_shows_the_cost_table_and_the_total(self):
        text = self.text(MULTI_TIB)

        # (2560 - 100) GiB at 0.087 per GiB
        self.assertIn("$214.02", text)
        self.assertIn("2,560.0 GiB", text)
        self.assertEqual(text.count("Share of Volume"), 2)
        self.assertIn("Storage Account", text)
        self.assertIn("BILLABLE VOLUME", text)
        self.assertNotIn("TiB", text)

    def test_a_free_tier_footprint_says_so_and_leaves_out_the_cost_table(self):
        text = self.text(FREE_TIER)

        self.assertIn("20.0 GiB of the 100 GiB monthly free tier", text)
        self.assertIn("$0.00", text)
        # Only the landscape table has the share column
        self.assertEqual(text.count("Share of Volume"), 1)
        self.assertNotIn("BILLABLE VOLUME", text)
        self.assertIn("FREE ALLOWANCE / MONTH", text)

    def test_money_has_one_format_everywhere(self):
        for rows in (MULTI_TIB, FREE_TIER, WITH_UNKNOWN):
            with self.subTest(rows=len(rows)):
                text = self.text(rows)
                amounts = re.findall(r"\$[\d,]+(?:\.\d+)?", text)

                self.assertTrue(amounts)
                for amount in amounts:
                    self.assertRegex(amount, r"^\$[\d,]+\.\d\d$")

    def test_unsized_types_are_listed_and_the_totals_carry_a_plus(self):
        text = self.text(MULTI_TIB)
        self.assertIn("Not sized: Recovery Services Vault (1 resource).", text)
        self.assertIn("1 resource could not be sized", text)

        text = self.text(WITH_UNKNOWN)
        self.assertIn("Not sized: SQL Database (1 resource).", text)
        # (2048 - 100) GiB * 0.087, and the + marks the lower bound
        self.assertIn("2,048.0 GiB+", text)
        self.assertIn("$169.48+", text)

    def test_an_empty_inventory_renders_the_notice_and_no_tables(self):
        text = self.text([])

        self.assertIn("No data-bearing resources were identified", text)
        self.assertIn("Estimated Costs", text)
        self.assertEqual(text.count("Share of Volume"), 0)

    def test_the_appendix_names_the_prices_the_sources_and_the_units(self):
        text = self.text(MULTI_TIB)

        for expected in (
            "Appendix – Egress Prices",
            "first 100 GiB / month",
            "0.087 / GiB",
            "Prices per GiB as published by Microsoft Azure",
            "https://azure.microsoft.com/pricing/details/bandwidth/",
            "https://azure.microsoft.com/pricing/details/storage/blobs/",
            "Glossary",
            "Switching charges",
            "Free tier",
        ):
            self.assertIn(expected, text)

    def test_next_steps_name_the_companion_and_the_platforms(self):
        text = self.text(MULTI_TIB, prov=provenance())
        self.assertIn(f"Executive Summary of the same run ({ES_ID})", text)
        self.assertIn("Connected (https://exitcloud.io)", text)
        self.assertIn("Enterprise (https://escapecloud.io/book-demo/)", text)

        text = self.text(MULTI_TIB, prov=uploaded())
        self.assertIn(f"Open the reports: {URL}", text)
        self.assertNotIn("Connected (https://exitcloud.io)", text)

    def test_no_date_is_written_month_first(self):
        for name, provider, prov in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(provider=provider, prov=prov)
                for text in page_texts(reader):
                    self.assertIsNone(re.search(r"\b\d{2}-\d{2}-\d{4}\b", text))

    def test_no_credential_is_shown(self):
        _path, _context, reader = self.render(provider=1)
        text = " ".join(page_texts(reader) + metadata_texts(reader))

        self.assertNotIn("very-secret-client-value", text)
        self.assertNotIn("Client", text)

    def test_the_figures_do_not_move_with_the_presentation(self):
        # The same rows give the same fee as the data functions
        folder, json_path = self.prepare(MULTI_TIB, 1)
        from cloudexit.report.egress import load_egress_estimate

        with patch(
            "cloudexit.report.egress.load_data", side_effect=pricing_for_both_clouds
        ):
            estimate = load_egress_estimate(str(folder), json_path)

        self.assertAlmostEqual(estimate["total_fee"], 2460 * 0.087)
        self.assertEqual(estimate["totals"]["known_size_bytes"], 2560 * GIB)


class NoticesTests(EgressReportTestCase):
    MARKERS = {1: "Microsoft group", 2: "Amazon.com"}

    def test_the_notices_carry_the_egress_paragraph_and_the_providers_icons(self):
        for name, provider, prov in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(provider=provider, prov=prov)
                pages = page_texts(reader)
                other = 1 if provider == 2 else 2

                self.assertIn(BLOCKS["notices_egress"], pages[-1])
                self.assertIn("12 January 2027", pages[-1])
                self.assertIn(self.MARKERS[provider], pages[-1])
                self.assertNotIn(self.MARKERS[other], " ".join(pages))
                self.assertIn(BLOCKS["notices_disclaimer"], pages[-1])

    def test_the_egress_paragraph_comes_before_the_trademarks(self):
        _path, _context, reader = self.render()
        last = page_texts(reader)[-1]

        self.assertLess(last.index(BLOCKS["notices_egress"]), last.index("Trademarks"))

    def test_the_executive_summary_notices_do_not_carry_it(self):
        from cloudexit.report.notices import notices_html

        plain = notices_html(1, COLLECTED, BLOCKS)
        egress = notices_html(1, COLLECTED, BLOCKS, egress=True)

        self.assertNotIn([BLOCKS["notices_egress"]], plain["blocks"][0]["paragraphs"])
        self.assertIn([BLOCKS["notices_egress"]], egress["blocks"][0]["paragraphs"])
        self.assertEqual(
            egress["blocks"][1:], plain["blocks"][1:], "only the disclaimer grows"
        )

    def test_the_icon_links_follow_the_provider(self):
        for provider, own, other in (
            (1, AZURE_ICONS_URL, AWS_ICONS_URL),
            (2, AWS_ICONS_URL, AZURE_ICONS_URL),
        ):
            with self.subTest(provider=provider):
                path, _context, _reader = self.render(provider=provider)
                from tests.report.test_basic_report import link_targets

                links = link_targets(PdfReader(path))
                self.assertIn(own, links)
                self.assertNotIn(other, links)


class GuardTests(EgressReportTestCase):
    def test_the_term_wording_and_retired_term_guards_pass_on_every_page(self):
        for name, provider, prov in self.scenarios():
            for rows in (MULTI_TIB, FREE_TIER, []):
                with self.subTest(name, rows=len(rows)):
                    _path, _context, reader = self.render(rows, provider, prov)
                    for text in metadata_texts(reader):
                        self.assertEqual(find_terms(text), [])
                    for text in page_texts(reader):
                        self.assertEqual(find_terms(text), [])
                        self.assertEqual(retired_term_hits(text), [])
                        self.assertEqual(american_hits(text), [])

    def test_the_html_follows_the_pdf_and_passes_the_guards(self):
        for provider, details in (
            (1, {"resourceGroupName": "Prod-RG", "tenantId": "t-1"}),
            (2, {"region": "eu-central-1", "accountId": "123456789012"}),
        ):
            with self.subTest(provider=provider):
                html, text = self.render_html(MULTI_TIB, provider, details)

                position = 0
                for heading in (
                    "Introduction",
                    "Purpose",
                    "Scope of the Estimation",
                    "Data Landscape",
                    "Estimated Costs",
                    "Appendix – Egress Prices",
                    "Glossary",
                    "Next Steps",
                    "Legal Notices",
                ):
                    position = text.index(heading, position)
                self.assertIn(EDL_ID, text)
                self.assertIn(f"({ES_ID})", text)
                self.assertTrue(
                    text.rstrip().endswith("© 2024–2026 EscapeCloud"), text[-80:]
                )
                self.assertEqual(find_terms(text), [])
                self.assertEqual(find_terms(html.split("<body", 1)[0]), [])
                self.assertEqual(retired_term_hits(text), [])
                self.assertEqual(american_hits(text), [])

    def test_the_html_title_names_the_document_and_the_run(self):
        html, _text = self.render_html()

        self.assertIn(
            "<title>Egress &amp; Data Landscape – Exit Assessment Test</title>", html
        )
        self.assertNotIn("EscapeCloud Community Edition -", html.split("</title>")[0])
        self.assertNotIn("TiB", html)
        self.assertIn(BLOCKS["notices_egress"], html.replace("&#39;", "'"))

    def test_the_html_scope_names_no_account_or_tenant(self):
        _html, text = self.render_html(
            MULTI_TIB, 2, {"region": "eu-central-1", "accountId": "123456789012"}
        )

        self.assertIn("AWS region eu-central-1", text)
        self.assertNotIn("123456789012", text)


class GenerationOrderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.report = build_flow_run(
            Path(tmp.name) / "run",
            resources=[(1, "eu-central-1", 3)],
            costs=[("2026-09-01", 10.0, "USD")],
        )
        perform_risk_assessment(1, self.report)

    def generate(self, **kwargs):
        return generate_report(
            2,
            {"region": "eu-central-1"},
            1,
            1,
            "Flow",
            self.report,
            str(Path(self.report) / "raw_data"),
            sync_id=SYNC_ID,
            **kwargs,
        )

    def es_text(self):
        reader = PdfReader(str(Path(self.report) / "report.pdf"))
        return " ".join(page_texts(reader))

    def test_the_egress_pdf_is_made_from_the_context_of_the_report(self):
        upload = {"status": "uploaded", "url": URL, "assessment_id": "a-1"}
        seen = {}

        def fake(context, json_path):
            seen["context"] = context
            seen["json_path"] = json_path
            return "egress.pdf"

        with patch("cloudexit.engine.generate_egress_report", side_effect=fake):
            result = self.generate(upload=upload, egress_json_path="estimate.json")

        self.assertTrue(result["success"])
        self.assertEqual(result["reports"]["EGRESS_PDF"], "egress.pdf")
        self.assertNotIn("egress_pdf_logs", result)
        context = seen["context"]
        # The upload is known by the time the PDF is made
        self.assertTrue(context.provenance.uploaded)
        self.assertEqual(context.provenance.platform_url, URL)
        self.assertEqual(context.egress_document_id, EDL_ID)
        self.assertEqual(seen["json_path"], "estimate.json")

    def test_the_summary_names_the_egress_document_only_when_there_is_one(self):
        with patch(
            "cloudexit.engine.generate_egress_report", return_value="egress.pdf"
        ):
            self.generate(egress_json_path="estimate.json")
        with_egress = self.es_text()
        html = (Path(self.report) / "index.html").read_text(encoding="utf-8")

        sentence = BLOCKS["es_costs_egress_note"].format(document_id=EDL_ID)
        self.assertIn(sentence, with_egress)
        self.assertIn("A separate Egress &amp; Data Landscape", html)
        self.assertIn(EDL_ID, html)

        self.generate()
        self.assertNotIn("A separate Egress", self.es_text())
        html = (Path(self.report) / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("A separate Egress", html)

    def test_a_failing_egress_pdf_leaves_the_summary_written(self):
        (Path(self.report) / "report.pdf").unlink(missing_ok=True)
        with patch(
            "cloudexit.engine.generate_egress_report",
            side_effect=RuntimeError("reportlab went wrong"),
        ):
            result = self.generate(egress_json_path="estimate.json")

        self.assertTrue(result["success"])
        self.assertTrue((Path(self.report) / "report.pdf").is_file())
        self.assertTrue((Path(self.report) / "index.html").is_file())
        self.assertNotIn("EGRESS_PDF", result["reports"])
        self.assertEqual(result["egress_pdf_logs"], "reportlab went wrong")

    def test_an_unreadable_estimate_is_the_same_failure(self):
        result = self.generate(egress_json_path="/nowhere/estimate.json")

        self.assertTrue(result["success"])
        self.assertIn("egress_pdf_logs", result)
        self.assertTrue((Path(self.report) / "report.pdf").is_file())

    def test_without_an_estimate_nothing_is_added(self):
        result = self.generate()

        self.assertTrue(result["success"])
        self.assertNotIn("EGRESS_PDF", result["reports"])
        self.assertNotIn("egress_pdf_logs", result)


class ProvenanceDefaultsTests(unittest.TestCase):
    def test_the_provenance_helpers_still_name_the_run(self):
        prov = Provenance(
            collector_version="2.0.0",
            collected_at=COLLECTED,
            generated_at=COLLECTED,
            run_id=SYNC_ID,
        )

        self.assertEqual(prov.run_short_id, SYNC_ID.replace("-", "")[-8:])


if __name__ == "__main__":
    unittest.main()
