import copy
import itertools
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from cloudexit.engine import generate_report, perform_risk_assessment
from cloudexit.report import fonts
from cloudexit.report.context import Provenance
from cloudexit.report.executive_summary import generate_executive_summary
from cloudexit.report.notices import notices_html
from cloudexit.report.palette import (
    DEFAULT_APPEARANCE,
    Appearance,
    contrast_failures,
    contrast_pairs,
    contrast_ratio,
)
from cloudexit.report.sections import (
    AWS_ICONS_URL,
    AWS_TRADEMARK_URL,
    AZURE_ICONS_URL,
    LICENCE_URL,
    REPOSITORY_URL,
)
from cloudexit.report.wording import BLOCKS, TITLE_CASE_KEYS
from tests.support.flow import build_flow_run
from tests.support.html import html_text
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
AWS_NOTICE_LINKS = {
    AWS_ICONS_URL,
    AWS_TRADEMARK_URL,
    REPOSITORY_URL,
    LICENCE_URL,
}
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
OUTLINE = BASE_OUTLINE + [(0, "Legal Notices")]
CLOSING_PAGE_TEXTS = (
    "What the Full Assessment Adds",
    "The Full Assessment",
    "How to Get Them",
    "HOST",
)
SCORE_PATTERNS = (
    r"/ 100",
    r"/ 5\b",
    "Ø",
    r"\bgrade [A-F]\b",
    "Exit Readiness",
    "Lock-in Score",
    "Scored Alternatives",
)
# An edition name; the "Enterprise support" flag of an alternative is not one
PROMOTION_PATTERNS = (
    r"\bLight\b",
    r"\bEnterprise\b(?!\s+support)",
    "Exit Readiness Grade",
    "Vendor Lock-in Score",
)
EXITCLOUD_URL = "https://app.exitcloud.io/assessments/a-1"
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


class TfstateCoverageTests(BasicReportTestCase):
    def coverage(self, other=0, unmapped=0):
        return {
            "instances_total": 9 + other + unmapped,
            "instances_counted": 9,
            "instances_excluded_other_provider": other,
            "instances_excluded_unmapped": unmapped,
        }

    def pages(self, key, coverage, **overrides):
        _path, context, reader = self.render(
            self.tfstate_fixture(),
            key=key,
            tfstate_scope=TFSTATE_SCOPE,
            tfstate_coverage=coverage,
            **overrides,
        )
        return context, page_texts(reader)

    def test_both_kinds_are_named_with_the_total_in_the_inventory_caption(self):
        context, pages = self.pages("covered", self.coverage(other=3, unmapped=2))

        self.assertEqual(context.scope.not_assessed, 5)
        self.assertIn(
            "Not Assessed 3 resources of other providers, "
            "2 resources of types not in the data catalogue",
            pages[1],
        )
        inventory = next(text for text in pages if "Share = resources" in text)
        self.assertIn(
            "5 resources in the state file were not assessed (see page 2)", inventory
        )

    def test_only_the_parts_that_are_not_zero_are_shown(self):
        for key, coverage, expected, absent in (
            (
                "other",
                self.coverage(other=4),
                "4 resources of other providers",
                "types not in",
            ),
            (
                "unmapped",
                self.coverage(unmapped=1),
                "1 resource of a type not in the data catalogue",
                "other provider",
            ),
            (
                "one other",
                self.coverage(other=1),
                "1 resource of another provider",
                "types not in",
            ),
        ):
            with self.subTest(key):
                _context, pages = self.pages(key, coverage)
                row = pages[1].split("Not Assessed", 1)[1].split("Source", 1)[0]

                self.assertIn(expected, row)
                self.assertNotIn(absent, row)

    def test_a_singular_total_says_was(self):
        _context, pages = self.pages("one total", self.coverage(unmapped=1))

        inventory = next(text for text in pages if "Share = resources" in text)
        self.assertIn(
            "1 resource in the state file was not assessed (see page 2)", inventory
        )

    def test_without_excluded_instances_there_is_no_row_and_no_caption(self):
        for key, coverage in (
            ("zeros", self.coverage()),
            ("older manifest", None),
            ("empty", {}),
            (
                "not numbers",
                {
                    "instances_excluded_other_provider": "3",
                    "instances_excluded_unmapped": -2,
                },
            ),
        ):
            with self.subTest(key):
                context, pages = self.pages(key, coverage)
                text = " ".join(pages)

                self.assertEqual(context.scope.not_assessed, 0)
                self.assertNotIn("Not Assessed", text)
                self.assertNotIn("were not assessed", text)
                self.assertNotIn("was not assessed", text)

    def test_a_live_run_never_shows_the_row(self):
        _path, context, reader = self.render(
            key="live coverage", tfstate_coverage=self.coverage(other=3, unmapped=2)
        )

        self.assertEqual(context.scope.not_assessed, 0)
        self.assertNotIn("Not Assessed", " ".join(page_texts(reader)))


class StructureTests(BasicReportTestCase):
    def test_the_heading_outline_is_the_expected_list(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                self.assertEqual(outline_entries(reader.outline), OUTLINE)

    def test_the_outline_is_the_same_for_an_upload_without_a_link(self):
        _path, _context, reader = self.render(
            key="no link", provenance=provenance(uploaded=True, assessment_id="a-77")
        )

        self.assertEqual(outline_entries(reader.outline), OUTLINE)

    def test_the_outline_does_not_depend_on_the_data(self):
        _path, _context, reader = self.render(
            build_empty_report_fixture(), provenance=provenance()
        )
        self.assertEqual(outline_entries(reader.outline), OUTLINE)

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
        self.assertIn("Legal Notices", pages[numbers[-1]])
        self.assertEqual(numbers[-1], len(pages) - 1)

    def test_running_header_and_footer(self):
        path, context, reader = self.render(key="default")
        pages = page_texts(reader)

        for index, text in enumerate(pages[1:], start=2):
            self.assertIn("Smoke Test Assessment", text)
            self.assertIn(context.document_id, text)
            self.assertIn("COMMUNITY EDITION", text)
            self.assertNotIn("CONFIDENTIAL", text)
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
    def test_a_missing_vendor_shows_a_dash_and_alternatives_are_alphabetical(self):
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
        text = " ".join(page_texts(reader))
        self.assertIn("Alpha · –", text)
        self.assertIn("OpenStack · OpenInfra Foundation", text)
        self.assertLess(text.index("Alpha · –"), text.index("OpenStack ·"))
        self.assertNotIn("Unknown Organization", text)


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
        self.assertEqual(
            link_targets(reader) - AWS_NOTICE_LINKS,
            set(),
        )

    def test_an_uploaded_report_carries_the_link_on_page_two_only(self):
        _path, _context, reader = self.render(key="uploaded", provenance=uploaded())
        pages = page_texts(reader)

        self.assertIn("Uploaded to the platform", pages[1])
        self.assertIn(URL, pages[1])
        self.assertNotIn("Self-collected data", " ".join(pages))
        self.assertEqual(sum(URL in text for text in pages), 1)
        self.assertEqual(link_targets(reader) - AWS_NOTICE_LINKS, {URL})

    def test_an_upload_without_a_link_names_the_assessment_on_page_two(self):
        _path, _context, reader = self.render(
            key="no link", provenance=provenance(uploaded=True, assessment_id="a-77")
        )
        pages = page_texts(reader)

        self.assertIn("Uploaded to the platform", pages[1])
        self.assertIn("Assessment ID: a-77", pages[1])
        self.assertEqual(sum("a-77" in text for text in pages), 1)
        self.assertEqual(link_targets(reader) - AWS_NOTICE_LINKS, set())

    def test_a_failed_upload_says_what_happened(self):
        for name, changes, expected in (
            (
                "kept",
                {"upload_reason": "upload_unavailable", "upload_spooled": True},
                "Upload failed: upload_unavailable. The run was kept for a later upload.",
            ),
            (
                "lost",
                {"upload_reason": "upload_rejected"},
                "Upload failed: upload_rejected.",
            ),
            ("no reason", {}, "Upload failed."),
            (
                "no reason, kept",
                {"upload_spooled": True},
                "Upload failed. The run was kept",
            ),
        ):
            with self.subTest(name):
                _path, _context, reader = self.render(
                    key=f"failed {name}",
                    provenance=provenance(upload_failed=True, **changes),
                )
                pages = page_texts(reader)

                self.assertIn(expected, pages[1])
                self.assertNotIn("Self-collected data", " ".join(pages))
                self.assertEqual("kept for a later upload" in pages[1], "kept" in name)
                self.assertEqual(link_targets(reader) - AWS_NOTICE_LINKS, set())

    def test_a_reason_that_ends_in_a_full_stop_is_not_doubled(self):
        _path, _context, reader = self.render(
            key="failed stop",
            provenance=provenance(upload_failed=True, upload_reason="No answer."),
        )

        self.assertIn("Upload failed: No answer. ", page_texts(reader)[1] + " ")
        self.assertNotIn("No answer..", page_texts(reader)[1])

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
    def test_no_score_or_grade_appears_on_any_page(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                body = " ".join(page_texts(reader))

                for pattern in SCORE_PATTERNS:
                    self.assertIsNone(re.search(pattern, body, re.IGNORECASE), pattern)

    def test_no_closing_page_and_no_command_is_left(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                body = " ".join(page_texts(reader))

                for text in CLOSING_PAGE_TEXTS:
                    self.assertNotIn(text, body)
                self.assertNotIn("--api-key-file", body)

    def test_no_edition_or_grade_is_promoted_on_any_page(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                for number, text in enumerate(page_texts(reader), start=1):
                    for pattern in PROMOTION_PATTERNS:
                        self.assertIsNone(re.search(pattern, text), (number, pattern))

    def test_the_enterprise_support_flag_does_not_trip_the_edition_check(self):
        _path, _context, reader = self.render(key="default")
        body = " ".join(page_texts(reader))

        self.assertIn("Enterprise support", body)
        self.assertIsNone(re.search(PROMOTION_PATTERNS[1], body))
        self.assertIsNotNone(re.search(PROMOTION_PATTERNS[1], "The Enterprise edition"))
        self.assertIsNotNone(re.search(PROMOTION_PATTERNS[0], "A Light assessment"))

    def test_exitcloud_is_named_only_on_page_two_of_an_uploaded_report(self):
        for name, fixture, overrides in self.scenarios():
            if "uploaded" in name:
                overrides = {
                    **overrides,
                    "provenance": provenance(
                        uploaded=True, platform_url=EXITCLOUD_URL, assessment_id="a-1"
                    ),
                }
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, **overrides)
                pages = [text.lower() for text in page_texts(reader)]
                named = [
                    n for n, text in enumerate(pages, start=1) if "exitcloud" in text
                ]

                self.assertEqual(named, [2] if "uploaded" in name else [])

    def test_escapecloud_is_named_only_on_the_legal_notices_page(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _path, _context, reader = self.render(fixture, key=name, **overrides)
                pages = [text.lower() for text in page_texts(reader)]
                named = [
                    n for n, text in enumerate(pages, start=1) if "escapecloud" in text
                ]

                self.assertEqual(named, [len(pages)])
                self.assertIn("legal notices", pages[-1])


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
        # The edition is this product's own, so it is not a foreign brand
        self.assertEqual(find_terms("EscapeCloud Community Edition"), [])
        self.assertEqual(
            find_terms("Generated with exitcloud.io"), ["Generated with exitcloud.io"]
        )
        self.assertEqual(find_terms("an ordinary sentence about cloud exit"), [])
        self.assertEqual(find_terms("ICTs and DORAs"), [])


class LegalNoticesTests(BasicReportTestCase):
    ICON_MARKERS = {1: "Microsoft group", 2: "Amazon.com"}

    def notices_page(self, key, fixture=None, **overrides):
        _path, context, reader = self.render(fixture, key=key, **overrides)
        return context, reader, page_texts(reader)

    def test_the_last_page_is_the_legal_notices_in_every_scenario(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                context, reader, pages = self.notices_page(name, fixture, **overrides)
                last = pages[-1]

                self.assertIn("Legal Notices", last)
                self.assertNotIn("Legal Notices", pages[-2])
                self.assertIn(BLOCKS["notices_disclaimer"], last)
                self.assertIn("© 2024–2026 EscapeCloud", last)
                self.assertIn(REPOSITORY_URL, last)
                self.assertIn(LICENCE_URL, last)
                self.assertIn(BLOCKS["notices_trademark_own"], last)
                self.assertIn(BLOCKS["notices_trademark_others"], last)
                self.assertIn(BLOCKS["notices_purpose"], last)
                self.assertIn("GNU Affero General Public License v3.0", last)
                self.assertTrue({REPOSITORY_URL, LICENCE_URL} <= link_targets(reader))

    def test_only_the_icon_notice_of_the_reports_provider_is_printed(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                context, _reader, pages = self.notices_page(name, fixture, **overrides)
                provider = context.cloud_service_provider
                other = 1 if provider == 2 else 2

                self.assertIn(self.ICON_MARKERS[provider], pages[-1])
                everything = " ".join(pages)
                self.assertNotIn(self.ICON_MARKERS[other], everything)

    def test_the_icon_links_follow_the_provider(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                context, reader, _pages = self.notices_page(name, fixture, **overrides)
                links = link_targets(reader)
                if context.cloud_service_provider == 2:
                    self.assertTrue({AWS_ICONS_URL, AWS_TRADEMARK_URL} <= links)
                    self.assertNotIn(AZURE_ICONS_URL, links)
                else:
                    self.assertIn(AZURE_ICONS_URL, links)
                    self.assertNotIn(AWS_ICONS_URL, links)

    def test_the_icon_names_are_followed_by_their_addresses(self):
        expected = {
            2: [
                f"AWS Architecture Icons ({AWS_ICONS_URL})",
                f"AWS Trademark Guidelines ({AWS_TRADEMARK_URL})",
            ],
            1: [f"Azure Architecture Icons ({AZURE_ICONS_URL})"],
        }
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                context, _reader, pages = self.notices_page(name, fixture, **overrides)
                last = pages[-1].replace(" ", "")
                for text in expected[context.cloud_service_provider]:
                    self.assertIn(text.replace(" ", ""), last)

    def test_the_cover_names_the_edition_and_no_classification(self):
        _context, _reader, pages = self.notices_page("offline", provenance=provenance())
        cover = pages[0]

        self.assertIn("EDITION Community", cover)
        self.assertNotIn("CLASSIFICATION", cover)
        self.assertNotIn("Confidential", cover)
        self.assertNotIn("CONFIDENTIAL", " ".join(pages))

    def test_every_running_footer_names_the_edition(self):
        _context, _reader, pages = self.notices_page("offline", provenance=provenance())

        for text in pages[1:]:
            self.assertIn("COMMUNITY EDITION", text)
            self.assertNotIn("CONFIDENTIAL", text)

    def test_the_copyright_year_is_the_year_of_generation(self):
        later = datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)
        _context, _reader, pages = self.notices_page(
            "later", provenance=provenance(generated_at=later)
        )

        self.assertIn("© 2024–2027 EscapeCloud", pages[-1])
        self.assertNotIn("2024–2026", pages[-1])

    def test_the_notices_fit_one_page(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _context, _reader, pages = self.notices_page(name, fixture, **overrides)
                # Page n-1 is the closing page, n the notices; neither runs over
                self.assertTrue(
                    pages[-2].startswith("Executive Summary"), pages[-2][:60]
                )
                self.assertIn("Legal Notices", pages[-1][:200])

    def test_no_placeholder_is_left_in_the_notices(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _context, _reader, pages = self.notices_page(name, fixture, **overrides)
                self.assertNotRegex(pages[-1], r"{[a-z_]+}")

    def test_the_term_guard_passes_on_the_notices(self):
        for name, fixture, overrides in self.scenarios():
            with self.subTest(name):
                _context, _reader, pages = self.notices_page(name, fixture, **overrides)
                self.assertEqual(find_terms(pages[-1]), [])
                self.assertEqual(american_hits(pages[-1]), [])
                self.assertEqual(retired_term_hits(pages[-1]), [])


class NoticesDataTests(unittest.TestCase):
    def test_the_data_is_plain_and_ordered(self):
        notices = notices_html(2, datetime(2026, 5, 7), BLOCKS)

        self.assertEqual(notices["heading"], "Legal Notices")
        self.assertEqual(
            [block["heading"] for block in notices["blocks"]],
            ["Disclaimer", "Trademarks", "Software"],
        )
        self.assertEqual(len(notices["blocks"][0]["paragraphs"]), 4)
        self.assertEqual(len(notices["blocks"][1]["paragraphs"]), 3)
        self.assertEqual(notices["copyright"], "© 2024–2026 EscapeCloud")

    def test_links_are_text_and_url_pairs(self):
        software = notices_html(1, datetime(2026, 1, 1), BLOCKS)["blocks"][2]
        links = [part for part in software["paragraphs"][0] if isinstance(part, tuple)]

        self.assertEqual(
            links, [(REPOSITORY_URL, REPOSITORY_URL), (LICENCE_URL, LICENCE_URL)]
        )

    def test_each_provider_gets_its_own_icon_notice_and_an_unknown_one_none(self):
        def trademarks(provider):
            blocks = notices_html(provider, datetime(2026, 1, 1), BLOCKS)["blocks"]
            return blocks[1]["paragraphs"]

        self.assertIn("AWS service icons", "".join(map(str, trademarks(2)[2])))
        self.assertIn("Azure service icons", "".join(map(str, trademarks(1)[2])))
        self.assertEqual(len(trademarks(99)), 2)

    def test_the_year_comes_from_the_generation_time(self):
        for year in (2026, 2027, 2031):
            notices = notices_html(2, datetime(year, 6, 1), BLOCKS)
            self.assertEqual(notices["copyright"], f"© 2024–{year} EscapeCloud")


class WordingGuardTests(unittest.TestCase):
    def test_the_licence_name_is_a_proper_name_and_other_license_is_not(self):
        self.assertEqual(
            american_hits(BLOCKS["notices_software"]), [], BLOCKS["notices_software"]
        )
        self.assertTrue(american_hits("the license of the data"))
        self.assertTrue(american_hits("Under this license, you may"))

    def test_the_new_labels_are_in_the_title_case_list(self):
        for key in (
            "notices_heading",
            "notices_disclaimer_heading",
            "notices_trademarks_heading",
            "notices_software_heading",
            "cover_edition",
            "cover_edition_value",
            "footer_edition",
        ):
            self.assertIn(key, TITLE_CASE_KEYS)

    def test_the_egress_labels_are_in_the_title_case_list(self):
        for key in (
            "cover_title_egress",
            "ev_egress_report",
            "ev_companion_document",
            "eg_heading_coverage",
            "eg_heading_landscape",
            "eg_heading_costs",
            "eg_heading_pricing",
            "eg_heading_next_steps",
            "eg_th_size_gib",
            "eg_st_largest",
        ):
            self.assertIn(key, TITLE_CASE_KEYS)

    def test_the_retired_classification_is_gone(self):
        self.assertNotIn("cover_classification", BLOCKS)
        self.assertNotIn("cover_classification_value", BLOCKS)


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
            # The pages that point to the platforms name what they add, and the
            # Scores row of the HTML report names the results they compute
            if key.startswith(("fa_", "eg_next_", "html_scores_", "html_tour_")):
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
