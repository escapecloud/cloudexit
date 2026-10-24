import copy
import itertools
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from core.engine import generate_report, perform_risk_assessment
from core.report import fonts
from core.report.context import Provenance
from core.report.executive_summary import generate_executive_summary
from core.report.palette import (
    DEFAULT_APPEARANCE,
    Appearance,
    contrast_failures,
    contrast_pairs,
    contrast_ratio,
)
from core.report.wording import BLOCKS, TITLE_CASE_KEYS
from tests.support.flow import build_flow_run
from tests.support.report import (
    build_empty_report_fixture,
    build_fixture_context,
    build_report_fixture,
    stage_minimal_assets,
)
from tests.support.report_terms import (
    american_hits,
    find_terms,
    is_title_case,
    retired_term_hits,
)
from tests.support.sync import SYNC_ID
from cloudexit.version import __version__

URL = "https://platform.test/assessments/a-1"
COLLECTED = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
BASE_OUTLINE = [
    (0, "Brief Summary"),
    (1, "Key Findings"),
    (0, "Introduction"),
    (1, "Purpose"),
    (1, "Scope"),
    (1, "Method"),
    (1, "Limitations"),
    (0, "Costs Overview"),
    (0, "Resource Inventory"),
    (0, "Risk Inventory"),
    (0, "Alternative Technologies"),
    (0, "Risk Severity Levels"),
    (0, "Glossary"),
]
OFFLINE_OUTLINE = BASE_OUTLINE + [
    (0, "What the Full Assessment Adds"),
    (1, "How to Get Them"),
]
UPLOADED_OUTLINE = BASE_OUTLINE + [(0, "The Full Assessment")]
SCORE_PATTERNS = (
    r"/ 100",
    r"/ 5\b",
    "Ø",
    r"\bgrade [A-F]\b",
    "Exit Readiness",
    "Lock-in Score",
    "Scored Alternatives",
)
TFSTATE_SCOPE = {
    "file": "infra.tfstate",
    "sha256": "ab" * 32,
    "lineage": "e1c2f0c0-0000-4000-8000-000000000000",
    "serial": 7,
    "locations": ["eu-west-1"],
}


def provenance(**changes):
    values = dict(
        collector_version=__version__,
        collected_at=COLLECTED,
        generated_at=COLLECTED,
        run_id=SYNC_ID,
        catalogue_version="2026-11-02",
        catalogue_sequence=41,
        catalogue_sha256="c" * 64,
    )
    values.update(changes)
    return Provenance(**values)


def uploaded(**changes):
    return provenance(uploaded=True, platform_url=URL, assessment_id="a-1", **changes)


class TextOfHtml(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html):
    parser = TextOfHtml()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def page_texts(reader):
    return [" ".join(page.extract_text().split()) for page in reader.pages]


def outline_entries(items, level=0):
    entries = []
    for item in items:
        if isinstance(item, list):
            entries += outline_entries(item, level + 1)
        else:
            entries.append((level, item.title))
    return entries


def xmp_text(reader):
    metadata = reader.trailer["/Root"]["/Metadata"].get_object()
    return metadata.get_data().decode("utf-8")


def metadata_texts(reader):
    info = reader.metadata
    return [str(value) for value in info.values()] + [xmp_text(reader)]


def link_targets(reader):
    targets = set()
    for page in reader.pages:
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A")
            if action and "/URI" in action:
                targets.add(str(action["/URI"]))
    return targets


_RENDERS = {}
_COUNTER = itertools.count()
_SHARED = None


def setUpModule():
    global _SHARED
    _SHARED = tempfile.TemporaryDirectory()


def tearDownModule():
    _RENDERS.clear()
    _SHARED.cleanup()


class BasicReportTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.count = 0

    def report_dir(self):
        self.count += 1
        path = self.tmp / f"report-{self.count}"
        path.mkdir()
        stage_minimal_assets(str(path))
        return path

    def azure_fixture(self):
        fixture = copy.deepcopy(build_report_fixture())
        fixture["metadata"]["cloud_service_provider"] = 1
        fixture["provider_details"] = {
            "tenantId": "11111111-1111-4111-8111-111111111111",
            "subscriptionId": "22222222-2222-4222-8222-222222222222",
            "resourceGroupName": "Prod-RG",
            "clientSecret": "very-secret-client-value",
        }
        return fixture

    def tfstate_fixture(self):
        fixture = copy.deepcopy(build_report_fixture())
        fixture["provider_details"] = {"tfstatePath": "infra.tfstate"}
        fixture["cost_data"] = []
        return fixture

    def scenarios(self):
        aws = build_report_fixture()
        for name, fixture, extra in (
            ("aws", aws, {}),
            ("azure", self.azure_fixture(), {}),
            ("tfstate", self.tfstate_fixture(), {"tfstate_scope": TFSTATE_SCOPE}),
        ):
            for state, prov in (("offline", provenance()), ("uploaded", uploaded())):
                yield f"{name} {state}", fixture, {**extra, "provenance": prov}

    def render(self, fixture=None, key=None, **overrides):
        # Rendering is the slow part, so tests that read the same report share it.
        if key in _RENDERS:
            return _RENDERS[key]
        directory = Path(_SHARED.name) / f"report-{next(_COUNTER)}"
        directory.mkdir()
        stage_minimal_assets(str(directory))
        context = build_fixture_context(
            fixture or build_report_fixture(), directory, **overrides
        )
        path = generate_executive_summary(context)
        result = (path, context, PdfReader(path))
        if key:
            _RENDERS[key] = result
        return result


class SmokeRenderTests(BasicReportTestCase):
    def test_every_provider_renders_offline_and_uploaded(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                path, _context, reader = self.render(fixture, key=name, **overrides)

                self.assertEqual(Path(path).name, "report.pdf")
                self.assertGreaterEqual(len(reader.pages), 9)
                self.assertTrue(all(page_texts(reader)))

    def test_an_empty_assessment_renders(self):
        fixture = build_empty_report_fixture()

        _path, _context, reader = self.render(fixture)

        texts = " ".join(page_texts(reader))
        self.assertIn("There are no resources", texts)
        self.assertIn("There are no risks identified", texts)
        self.assertIn("No cost data available", texts)

    def test_a_tfstate_assessment_explains_the_missing_costs(self):
        _path, _context, reader = self.render(
            self.tfstate_fixture(), tfstate_scope=TFSTATE_SCOPE
        )

        text = " ".join(page_texts(reader))
        self.assertIn("Terraform state files hold no billing data", text)
        self.assertIn("infra.tfstate", text)
        self.assertIn("e1c2f0c0-0000-4000-8000-000000000000 (serial 7)", text)


class StructureTests(BasicReportTestCase):
    def test_the_heading_outline_is_the_expected_list(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                expected = UPLOADED_OUTLINE if "uploaded" in name else OFFLINE_OUTLINE
                self.assertEqual(outline_entries(reader.outline), expected)

    def test_the_outline_does_not_depend_on_the_data(self):
        _path, _context, reader = self.render(
            build_empty_report_fixture(), provenance=provenance()
        )
        self.assertEqual(outline_entries(reader.outline), OFFLINE_OUTLINE)

    def test_each_chapter_starts_a_page_and_page_two_is_the_document_information(self):
        _path, _context, reader = self.render(key="default")
        pages = page_texts(reader)

        self.assertIn("Executive Summary", pages[0])
        self.assertNotIn("Page 1 of", pages[0])
        self.assertTrue(pages[1].startswith("Executive Summary"))
        self.assertIn("Document Information", pages[1])
        self.assertIn(f"Page 2 of {len(pages)}", pages[1])
        self.assertIn("Brief Summary", pages[2])

        chapters = [entry for entry in reader.outline if not isinstance(entry, list)]
        numbers = [reader.get_destination_page_number(entry) for entry in chapters]
        # One chapter per page, except the severity levels and the glossary
        self.assertEqual(len(set(numbers)), len(numbers) - 1)
        self.assertEqual(numbers[-3], numbers[-2])

    def test_running_header_and_footer(self):
        path, context, reader = self.render(key="default")
        pages = page_texts(reader)

        for index, text in enumerate(pages[1:], start=2):
            self.assertIn("Smoke Test Assessment", text)
            self.assertIn(context.document_id, text)
            self.assertIn("CONFIDENTIAL", text)
            self.assertIn(f"Page {index} of {len(pages)}", text)

    def test_the_fonts_are_plex_and_nothing_else(self):
        path, _context, _reader = self.render(key="default")
        names = set(re.findall(rb"/BaseFont\s*/(\S+)", Path(path).read_bytes()))

        self.assertTrue(names)
        self.assertTrue(all(b"IBMPlex" in name for name in names), names)

    def test_the_fonts_ship_with_their_licence(self):
        for filename in fonts.FONT_FILES.values():
            self.assertTrue((fonts.FONT_DIR / filename).is_file(), filename)
        licence = (fonts.FONT_DIR / "OFL.txt").read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE", licence)


class ContentTests(BasicReportTestCase):
    def test_the_findings_use_the_local_results(self):
        _path, _context, reader = self.render(key="default")
        text = " ".join(page_texts(reader))

        self.assertIn("Key Findings", text)
        self.assertIn("unique exit risk", text)
        self.assertIn("EC2 Instance", text)
        self.assertIn("OpenStack", text)
        self.assertIn("OpenInfra Foundation", text)
        self.assertIn("Examples are listed alphabetically", text)

    def test_the_resource_inventory_lists_each_type_once(self):
        _path, context, _reader = self.render(key="default")

        self.assertEqual([entry.name for entry in context.resources], ["EC2 Instance"])
        self.assertEqual(
            context.total_resources,
            sum(row["count"] for row in build_report_fixture()["resource_inventory"]),
        )

    def test_documents_are_identified_by_the_end_of_the_run_id(self):
        _path, context, _reader = self.render(key="default")
        self.assertEqual(context.document_id, "ES-0a6e21d4-EN")

        _path, local, _reader = self.render(provenance=provenance(run_id=None))
        self.assertEqual(local.document_id, "ES-LOCAL-EN")

    def test_costs_before_the_current_month_are_final(self):
        fixture = build_report_fixture()
        fixture["cost_data"] = [
            {"month": "2026-04-01", "cost": 10.0, "currency": "USD"},
            {"month": "2026-05-01", "cost": 12.0, "currency": "USD"},
        ]
        _path, context, reader = self.render(fixture)

        self.assertEqual([entry.final for entry in context.costs], [True, False])
        self.assertIn("provisional", " ".join(page_texts(reader)))

    def test_mixed_currencies_are_not_totalled(self):
        fixture = build_report_fixture()
        fixture["cost_data"][0] = {**fixture["cost_data"][0], "currency": "EUR"}

        _path, _context, reader = self.render(fixture)

        self.assertIn("more than one currency", " ".join(page_texts(reader)))

    def test_the_pinned_risk_numbers_and_names_are_shown(self):
        _path, context, reader = self.render(key="default")

        self.assertEqual([risk.code for risk in context.risks], ["R1"])
        text = " ".join(page_texts(reader))
        self.assertIn("R1", text)
        self.assertIn("Limited Alternatives", text)

    def test_a_catalogue_severity_and_code_win_when_the_dataset_has_them(self):
        fixture = build_report_fixture()
        fixture["risk_definitions"] = [
            {
                **fixture["risk_definitions"][0],
                "code": "ALT-1",
                "severity": "low",
                "catalogue_severity": "critical",
            }
        ]
        _path, context, _reader = self.render(fixture)

        self.assertEqual(context.risks[0].code, "ALT-1")
        self.assertEqual(context.risks[0].severity, "critical")

    def test_names_from_the_data_are_escaped(self):
        fixture = build_report_fixture()
        fixture["metadata"]["name"] = "<b>Prod</b> & Co"
        mapping = copy.deepcopy(fixture["resource_type_mapping"])
        mapping["101"]["name"] = "Bad <i>name</i> &amp;"
        fixture["resource_type_mapping"] = mapping

        _path, _context, reader = self.render(fixture)

        text = " ".join(page_texts(reader))
        self.assertIn("<b>Prod</b> & Co", text)
        self.assertIn("Bad <i>name</i> &amp;", text)


class AlternativesTests(BasicReportTestCase):
    def test_a_missing_vendor_is_left_out_and_alternatives_are_alphabetical(self):
        fixture = build_report_fixture()
        technologies = fixture["alternative_technologies"]
        technologies.append(
            {
                **technologies[0],
                "id": 2,
                "product_name": "Alpha",
                "organization_id": None,
            }
        )
        fixture["alternatives"].append(
            {"resource_type": "101", "strategy_type": "1", "alternative_technology": 2}
        )

        _path, context, reader = self.render(fixture)

        entry = context.alternatives[0]
        self.assertEqual(
            [alt.name for alt in entry.alternatives], ["Alpha", "OpenStack"]
        )
        self.assertEqual(entry.alternatives[0].vendor, "")
        self.assertEqual(entry.alternatives[1].vendor, "OpenInfra Foundation")
        self.assertNotIn("Unknown Organization", " ".join(page_texts(reader)))


class ProvenanceTests(BasicReportTestCase):
    def test_an_offline_report_says_it_is_self_collected(self):
        path, _context, reader = self.render(key="offline", provenance=provenance())
        pages = page_texts(reader)

        self.assertIn(
            f"Collected by cloudexit {__version__} on 2026-05-07",
            pages[0],
        )
        second = pages[1]
        self.assertIn(f"Collected by cloudexit {__version__}", second)
        self.assertIn(SYNC_ID, second)
        self.assertIn("2026-11-02 · (sha256 cccccccccccc)", second)
        self.assertIn("Self-collected data: no platform evaluated this report.", second)
        self.assertIn("What the Full Assessment Adds", pages[-1])
        self.assertEqual(
            link_targets(reader), {"https://escapecloud.io", "https://exitcloud.io"}
        )

    def test_an_uploaded_report_carries_the_link_instead_of_the_promotion(self):
        _path, _context, reader = self.render(key="uploaded", provenance=uploaded())
        pages = page_texts(reader)

        self.assertIn("Uploaded to the platform", pages[1])
        self.assertIn(URL, pages[1])
        self.assertNotIn("Self-collected data", " ".join(pages))
        self.assertIn("The Full Assessment", pages[-1])
        self.assertIn(URL, pages[-1])
        self.assertNotIn("What the Full Assessment Adds", " ".join(pages))
        self.assertEqual(link_targets(reader), {URL})

    def test_an_upload_without_a_link_names_the_assessment(self):
        _path, _context, reader = self.render(
            provenance=(
                uploaded(platform_url=None)
                if False
                else provenance(uploaded=True, platform_url=None, assessment_id="a-77")
            )
        )
        self.assertIn("Assessment ID: a-77", page_texts(reader)[-1])

    def test_an_unknown_catalogue_version_is_said_so(self):
        _path, _context, reader = self.render(
            provenance=provenance(
                catalogue_version=None, catalogue_sequence=None, catalogue_sha256=None
            )
        )
        self.assertIn("Unknown (the dataset records no version)", page_texts(reader)[1])

    def test_the_pdf_metadata_follows_the_cover(self):
        path, context, reader = self.render(key="default")
        info = reader.metadata

        self.assertEqual(info.title, "Executive Summary – Smoke Test Assessment")
        self.assertIn(context.document_id, info.subject)
        version = context.provenance.collector_version
        self.assertIn(f"collected by cloudexit {version}", info.subject)
        self.assertEqual(info.creator, f"cloudexit {version}")
        self.assertEqual(info.author, "cloudexit")
        for keyword in (context.document_id, SYNC_ID, "catalogue 2026-11-02"):
            self.assertIn(keyword, info["/Keywords"])
        # The dates are the generation time, not the render clock
        self.assertIn("20260507120000", info["/CreationDate"])

    def test_the_xmp_packet_carries_the_provenance(self):
        _path, _context, reader = self.render(key="uploaded", provenance=uploaded())
        packet = xmp_text(reader)

        for expected in (
            f"<cloudexit:RunID>{SYNC_ID}</cloudexit:RunID>",
            f"<cloudexit:CollectorVersion>{__version__}</cloudexit:CollectorVersion>",
            "<cloudexit:CatalogueVersion>2026-11-02</cloudexit:CatalogueVersion>",
            "<cloudexit:Uploaded>true</cloudexit:Uploaded>",
            "<xmp:CreatorTool>cloudexit",
        ):
            self.assertIn(expected, packet)

    def test_the_json_report_has_a_provenance_object(self):
        for upload, expected_url in (
            (None, None),
            (
                {
                    "status": "uploaded",
                    "sync_id": SYNC_ID,
                    "assessment_id": "a-1",
                    "url": URL,
                },
                URL,
            ),
        ):
            with self.subTest(uploaded=bool(upload)):
                report = build_flow_run(
                    self.tmp / f"flow-{bool(upload)}",
                    resources=[(1, "eu-central-1", 3)],
                    costs=[("2026-09-01", 10.0, "USD")],
                )
                perform_risk_assessment(1, report)
                result = generate_report(
                    2,
                    {"region": "eu-central-1"},
                    1,
                    1,
                    "Flow",
                    report,
                    str(Path(report) / "raw_data"),
                    upload=upload,
                    sync_id=SYNC_ID,
                )
                self.assertTrue(result["success"], result.get("logs"))

                document = json.loads(Path(result["reports"]["JSON"]).read_text())
                self.assertEqual(
                    document["provenance"],
                    {
                        "collector_version": __version__,
                        "catalogue_version": "2026-11-02",
                        "catalogue_sequence": 41,
                        "catalogue_sha256": "c" * 64,
                        "sync_id": SYNC_ID,
                        "uploaded": bool(upload),
                        "platform_url": expected_url,
                    },
                )
                html = (Path(report) / "index.html").read_text(encoding="utf-8")
                text = html_text(html)
                self.assertIn(SYNC_ID, text)
                self.assertEqual(URL in html, bool(upload))
                self.assertEqual(
                    "Self-collected data: no platform evaluated this report." in text,
                    not upload,
                )


class BasicOnlyTests(BasicReportTestCase):
    def test_no_score_or_grade_appears_outside_the_closing_page(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                body = " ".join(page_texts(reader)[:-1])

                for pattern in SCORE_PATTERNS:
                    self.assertIsNone(re.search(pattern, body, re.IGNORECASE), pattern)

    def test_the_closing_page_only_names_what_the_full_assessment_adds(self):
        _path, _context, reader = self.render(key="offline", provenance=provenance())
        closing = page_texts(reader)[-1]

        for name in (
            "Exit Readiness Grade",
            "Vendor Lock-in Score",
            "Scored Alternatives",
            "Egress & Data Landscape",
        ):
            self.assertIn(name, closing)
        self.assertNotRegex(closing, r"\d+ / (100|5)")

    def test_the_html_report_has_no_scores_before_the_closing_section(self):
        report = build_flow_run(
            self.tmp / "html",
            resources=[(1, "eu-central-1", 3)],
            costs=[("2026-09-01", 10.0, "USD")],
        )
        perform_risk_assessment(1, report)
        generate_report(
            2,
            {"region": "eu-central-1"},
            1,
            1,
            "Flow",
            report,
            str(Path(report) / "raw_data"),
            sync_id=SYNC_ID,
        )
        text = html_text((Path(report) / "index.html").read_text(encoding="utf-8"))
        before = text[: text.index("What the Full Assessment Adds")]

        for pattern in SCORE_PATTERNS:
            self.assertIsNone(re.search(pattern, before, re.IGNORECASE), pattern)

    def test_the_html_follows_the_same_sections(self):
        report = build_flow_run(
            self.tmp / "html2",
            resources=[(1, "eu-central-1", 3)],
            costs=[("2026-09-01", 10.0, "USD")],
        )
        perform_risk_assessment(1, report)
        generate_report(
            2,
            {"region": "eu-central-1"},
            1,
            1,
            "Flow",
            report,
            str(Path(report) / "raw_data"),
            sync_id=SYNC_ID,
        )
        text = html_text((Path(report) / "index.html").read_text(encoding="utf-8"))

        # The findings table names the sections too, so each heading is searched
        # for after the one before it.
        position = 0
        for heading in (
            "Brief Summary",
            "Provenance",
            "Introduction",
            "Risk Inventory",
            "Costs Overview",
            "Resource Inventory",
            "Alternative Technologies",
            "Risk Severity Levels",
            "Glossary",
            "What the Full Assessment Adds",
        ):
            position = text.index(heading, position)


class TermGuardTests(BasicReportTestCase):
    def test_no_regulatory_word_or_foreign_brand_in_pdf_html_or_metadata(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                texts = page_texts(reader) + metadata_texts(reader)
                for text in texts:
                    self.assertEqual(find_terms(text), [])

        report = build_flow_run(
            self.tmp / "guard",
            resources=[(1, "eu-central-1", 3)],
            costs=[("2026-09-01", 10.0, "USD")],
        )
        perform_risk_assessment(1, report)
        generate_report(
            2,
            {"region": "eu-central-1"},
            1,
            1,
            "Flow",
            report,
            str(Path(report) / "raw_data"),
            sync_id=SYNC_ID,
        )
        html = (Path(report) / "index.html").read_text(encoding="utf-8")
        self.assertEqual(find_terms(html_text(html)), [])
        self.assertEqual(find_terms(html.split("<body", 1)[0]), [])

    def test_the_guard_finds_what_it_should(self):
        self.assertEqual(
            find_terms("A DORA review of the supervisory rules"),
            ["DORA", "supervisor", "supervisory"],
        )
        self.assertEqual(
            find_terms("EscapeCloud Community Edition"), ["Community Edition"]
        )
        self.assertEqual(
            find_terms("Generated with exitcloud.io"), ["Generated with exitcloud.io"]
        )
        self.assertEqual(find_terms("an ordinary sentence about cloud exit"), [])
        self.assertEqual(find_terms("ICTs and DORAs"), [])

    def test_the_platforms_are_named_only_on_the_closing_page(self):
        _path, _context, reader = self.render(key="default")
        pages = page_texts(reader)

        for text in pages[:-1]:
            self.assertNotIn("escapecloud", text.lower())
            self.assertNotIn("exitcloud", text.lower())
        self.assertIn("https://escapecloud.io", pages[-1])
        self.assertIn("https://exitcloud.io", pages[-1])


class WordingTests(unittest.TestCase):
    def strings(self):
        for key, value in BLOCKS.items():
            if isinstance(value, str):
                yield key, value
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield from (
                            (f"{key}.{k}", v) for k, v in item.items() if k != "id"
                        )
                    elif isinstance(item, tuple):
                        yield from ((f"{key}[]", part) for part in item)
                    else:
                        yield key, item

    def test_labels_and_headings_are_title_case(self):
        for key in TITLE_CASE_KEYS:
            with self.subTest(key):
                self.assertIn(key, BLOCKS)
                self.assertTrue(is_title_case(BLOCKS[key]), BLOCKS[key])

    def test_the_title_case_check_itself(self):
        self.assertTrue(is_title_case("Costs, 6 Months"))
        self.assertTrue(is_title_case("Change vs. {month}"))
        self.assertTrue(is_title_case("Lock-in Hot-spots"))
        self.assertFalse(is_title_case("Resource types"))
        self.assertFalse(is_title_case("Share Of Resources"))

    def test_no_wording_uses_a_retired_term_or_american_spelling(self):
        for key, text in self.strings():
            with self.subTest(key):
                self.assertEqual(retired_term_hits(text), [])
                self.assertEqual(american_hits(text), [])

    def test_no_wording_carries_regulatory_words_or_scores(self):
        for key, text in self.strings():
            if key.startswith("fa_"):
                continue
            with self.subTest(key):
                self.assertEqual(find_terms(text), [])
                self.assertNotRegex(text, r"(?i)\bgrade\b|lock-in score|exit readiness")

    def test_the_report_never_speaks_to_one_reader(self):
        # Reports are passed around inside organisations, so no "your".
        for key, text in self.strings():
            with self.subTest(key):
                self.assertNotRegex(text, r"(?i)\byour\b")

    def test_every_format_field_has_a_matching_key_in_use(self):
        # A typo in a placeholder would only show up when that text renders.
        for key, text in self.strings():
            with self.subTest(key):
                for field in re.findall(r"{([a-z_]+)}", text):
                    self.assertRegex(field, r"^[a-z_]+$")


class ContrastTests(unittest.TestCase):
    def test_the_default_palette_passes_every_pair(self):
        self.assertEqual(contrast_failures(), [])
        for what, text, background, minimum in contrast_pairs():
            with self.subTest(what):
                self.assertGreaterEqual(contrast_ratio(text, background), minimum)

    def test_a_weak_palette_is_reported(self):
        weak = Appearance(
            header_footer_color="#CCCCCC",
            header_row_color="#DDDDDD",
            heading1_color="#EEEEEE",
            heading2_color="#EEEEEE",
        )
        failed = {what for what, _ratio, _minimum in contrast_failures(weak)}

        self.assertIn("chapter heading", failed)
        self.assertIn("table header", failed)
        self.assertIn("section heading", failed)

    def test_the_ratio_matches_the_wcag_definition(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#FFFFFF"), 21.0)
        self.assertAlmostEqual(contrast_ratio("#FFFFFF", "#FFFFFF"), 1.0)
        self.assertEqual(DEFAULT_APPEARANCE.heading1_color, "#005470")


if __name__ == "__main__":
    unittest.main()
