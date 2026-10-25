import hashlib
import json
import posixpath
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from pypdf import PdfReader

from cloudexit.engine import generate_report, perform_risk_assessment
from cloudexit.report.sections import AWS_ICONS_URL, AZURE_ICONS_URL, REPOSITORY_URL
from cloudexit.report.wording import BLOCKS
from cloudexit.resources import asset_path
from tests.report.test_basic_report import page_texts
from tests.samples.rendered import rendered
from tests.support.flow import SERVER_SCORES, build_flow_run
from tests.support.html import html_text
from tests.support.report_terms import find_terms
from tests.support.sync import SYNC_ID

DRIVER_JS_SHA256 = "3e3790dea8118c80a7a1d81af9454f8f6fe9a5dd180e843ce8ba638c5b446753"
DRIVER_CSS_SHA256 = "4b398481a7ce8375af4d9f58f39410c73a0b70726fe513686d3d51c10ad76cb5"
BASE_SECTIONS = ["overview", "scores", "risks", "inventory", "alternatives", "about"]
WITH_EGRESS = [
    "overview",
    "scores",
    "risks",
    "inventory",
    "alternatives",
    "egress",
    "about",
]
TOUR_TITLES = [
    "Sections",
    "This Report",
    "Overview",
    "Exit Readiness Grade & Vendor Lock-in Score",
    "Risks",
    "Resource Inventory",
    "Alternative Technologies",
    "Egress & Data Landscape",
    "About This Report",
]
PROVIDER_LOGOS = {"aws": "Amazon Web Services", "azure": "Microsoft Azure"}
PLATFORM_URL = "https://platform.example.test/assessments/a-1"

STATE = {}


def setUpModule():
    STATE.update(rendered())


def tearDownModule():
    STATE.clear()


class References(HTMLParser):
    # What the page loads: stylesheets, scripts and images
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("href"):
            self.found.append((tag, attrs["href"]))
        elif tag in ("script", "img") and attrs.get("src"):
            self.found.append((tag, attrs["src"]))


def references(html):
    parser = References()
    parser.feed(html)
    return parser.found


def is_local(value, base=""):
    # A relative path that stays inside the report folder; base is the folder of
    # the file that holds the reference, relative to the report folder
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or value.startswith(("/", "\\")):
        return False
    return not posixpath.normpath(posixpath.join(base, value)).startswith("..")


def css_references(css):
    found = re.findall(r"@import\s+(?:url\()?\s*[\"']?([^\"')\s;]+)", css)
    return found + re.findall(r"url\(\s*[\"']?([^\"')]+)", css)


def offline_problems(html, stylesheets=()):
    # Anything the page would load from outside its own folder; a stylesheet is
    # given as (folder, text), the folder relative to the report folder
    problems = [
        f"<{tag}> {value}" for tag, value in references(html) if not is_local(value)
    ]
    for base, css in (("", html),) + tuple(stylesheets):
        problems += [
            f"css {value}" for value in css_references(css) if not is_local(value, base)
        ]
    return problems


def composed_of(text, pieces):
    # Whether the text is built only from the pieces, joined by single spaces
    if not text:
        return True
    return any(
        text.startswith(piece)
        and (len(text) == len(piece) or text[len(piece)] == " ")
        and composed_of(text[len(piece) :].lstrip(" "), pieces)
        for piece in pieces
        if piece
    )


class SampleTestCase(unittest.TestCase):
    def cases(self):
        yield from STATE["scenarios"].items()

    def folder(self, case):
        return STATE["results"][case].directory

    def page(self, case):
        return (self.folder(case) / "index.html").read_text(encoding="utf-8")

    def text(self, case):
        return html_text(self.page(case))

    def section(self, case, name):
        match = re.search(
            rf'<section[^>]*id="{name}".*?</section>', self.page(case), re.DOTALL
        )
        self.assertIsNotNone(match, f"{case}: no section {name}")
        return match.group(0)

    def tour(self, case):
        match = re.search(
            r'<script type="application/json" id="tour-steps">(.*?)</script>',
            self.page(case),
            re.DOTALL,
        )
        return json.loads(match.group(1))

    def sections(self, case):
        return re.findall(r'<section[^>]*id="([a-z]+)"', self.page(case))


class StructureTests(SampleTestCase):
    def test_the_sections_come_in_the_planned_order_with_egress_only_when_it_ran(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                expected = WITH_EGRESS if scenario.egress else BASE_SECTIONS
                self.assertEqual(self.sections(case), expected)
                nav = re.search(r"<nav.*?</nav>", self.page(case), re.DOTALL).group(0)
                self.assertEqual(re.findall(r'<a href="#([a-z]+)">', nav), expected)

    def test_the_download_menu_has_one_pdf_or_two_and_no_json(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                menu = re.search(
                    r'<div class="menu-list".*?</div>', self.page(case), re.DOTALL
                ).group(0)
                links = [
                    link
                    for link in re.findall(r'href="([^"]+)"', menu)
                    if not link.startswith("#")
                ]

                self.assertEqual(
                    links,
                    ["report.pdf", "egress.pdf"] if scenario.egress else ["report.pdf"],
                )
                self.assertNotIn(".json", menu)
                self.assertNotIn("JSON", menu)
                self.assertIn("Executive Summary (PDF)", menu)
                self.assertEqual(
                    "Egress &amp; Data Landscape (PDF)" in menu, bool(scenario.egress)
                )

    def test_no_promotion_and_no_subline(self):
        for case, _scenario in self.cases():
            with self.subTest(case):
                text = self.text(case)
                for phrase in (
                    "Brief Summary",
                    "What the Full Assessment Adds",
                    "How to Get Them",
                    "request_report@",
                ):
                    self.assertNotIn(phrase, text)
                self.assertNotIn('class="subline"', self.page(case))

    def test_the_title_bar_shows_the_providers_logo_and_not_its_name(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                provider = PROVIDER_LOGOS[scenario.provider]
                page = self.page(case)

                self.assertIn(
                    f'src="assets/img/csp/{scenario.provider}.svg" alt="{provider}" '
                    f'title="{provider}"',
                    page,
                )
                name = re.search(r'<div class="name">(.*?)</div>', page, re.DOTALL)
                visible = html_text(name.group(1))
                self.assertNotIn(provider, visible)
                self.assertTrue(visible.startswith(scenario.name))
                self.assertTrue(
                    (
                        self.folder(case)
                        / "assets"
                        / "img"
                        / "csp"
                        / f"{scenario.provider}.svg"
                    ).is_file()
                )

    def test_the_uploaded_chip_and_link_appear_only_on_uploaded_runs(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                page = self.page(case)
                chip = re.search(r'<span class="chip[^>]*>(.*?)</span>', page).group(1)
                url = (scenario.upload or {}).get("url")

                self.assertEqual(
                    chip, "Uploaded" if scenario.upload else "Self-collected"
                )
                self.assertEqual("Open on the platform" in page, bool(url))
                self.assertEqual(f'href="{url}"' in page, bool(url))

    def test_the_logo_links_to_the_website_in_a_new_tab(self):
        page = self.page("aws_typical")

        self.assertIn(
            '<a href="https://escapecloud.io" target="_blank" rel="noopener noreferrer" '
            'aria-label="EscapeCloud website (opens in a new tab)">',
            page,
        )
        # The tag is next to the link, not part of it
        self.assertIn('</a><span class="brand-tag">Community</span>', page)

    def test_external_links_open_in_a_new_tab_without_opener(self):
        for case, _scenario in self.cases():
            with self.subTest(case):
                for tag in re.findall(
                    r'<a [^>]*href="https?://[^"]+"[^>]*>', self.page(case)
                ):
                    self.assertIn('target="_blank"', tag)
                    self.assertIn('rel="noopener noreferrer"', tag)


class OverviewTests(SampleTestCase):
    def test_the_scope_card_names_the_scope_of_each_kind(self):
        azure = html_text(self.section("azure_typical", "overview"))
        self.assertIn("Resource Group rg-sample-archive", azure)
        self.assertIn("2 regions · northeurope, westeurope", azure)
        self.assertIn("provider APIs", azure)
        aws = html_text(self.section("aws_typical", "overview"))
        self.assertIn("Region eu-central-1", aws)
        state = html_text(self.section("tfstate_aws", "overview"))
        self.assertIn("State File sample_aws.tfstate", state)
        self.assertIn("serial 12 ·", state)
        self.assertIn("Terraform state", state)

    def test_the_costs_card_shows_the_months_and_the_provisional_one_hatched(self):
        section = self.section("aws_typical", "overview")

        self.assertEqual(section.count('class="col"'), 6)
        self.assertEqual(section.count('class="b prov"'), 1)
        self.assertIn("Nov*", section)
        self.assertIn("* Nov is provisional", html_text(section))
        self.assertIn("$", html_text(section))

    def test_the_risk_card_counts_by_severity(self):
        section = self.section("aws_typical", "overview")

        self.assertEqual(section.count('class="sevrow"'), 4)
        self.assertIn('aria-label="Risks by severity:', section)

    def test_a_state_file_shows_no_costs(self):
        section = html_text(self.section("tfstate_aws", "overview"))

        self.assertIn("Terraform state files hold no billing data", section)
        self.assertIn("Run cloudexit against the provider APIs", section)
        self.assertNotIn("provisional", section)

    def test_mixed_currencies_are_not_totalled(self):
        section = html_text(self.section("stress", "overview"))

        self.assertIn("more than one currency", section)


class ScoresTests(SampleTestCase):
    def test_the_scores_row_has_two_cards_without_values(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                section = self.section(case, "scores")
                text = html_text(section)

                self.assertIn("Exit Readiness Grade", text)
                self.assertIn("Vendor Lock-in Score", text)
                self.assertEqual(
                    re.findall(r'<div class="l">(.*?)</div>', section), ["–"]
                )
                values = re.findall(r"<b>([^<]*)</b>", section)
                self.assertEqual([v for v in values if "/" in v], ["– / 5"] * 3)
                # Only the placeholders sit in the tile and next to the bars
                for body in re.findall(
                    r"<p style=\"margin-top:12px\">.*?</p>", section
                ):
                    section = section.replace(body, "")
                shown = html_text(section).replace("of 100", "").replace("– / 5", "")
                self.assertNotRegex(shown, r"\d")

    def test_every_pill_carries_the_same_hint_and_no_claim_of_patents(self):
        for case, scenario in self.cases():
            if (scenario.upload or {}).get("url"):
                continue
            with self.subTest(case):
                page = self.page(case)
                hints = re.findall(r'<span class="hint-pop".*?</span>', page, re.DOTALL)

                self.assertEqual(len(hints), 2)
                bodies = {re.sub(r' id="[^"]+"', "", hint) for hint in hints}
                self.assertEqual(len(bodies), 1)
                self.assertIn(
                    "EscapeCloud&#39;s scoring engine offers several scoring methods",
                    hints[0],
                )
                self.assertIn("exitcloud.io", hints[0])
                self.assertIn("escapecloud.io", hints[0])

    def test_no_patent_pioneer_or_your_in_any_page(self):
        for case, _scenario in self.cases():
            with self.subTest(case):
                text = self.text(case).lower()
                for word in ("patent", "pioneer", "your "):
                    self.assertNotIn(word, text)

    def test_the_menu_entry_has_a_lock_unless_the_run_has_a_link(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                entry = re.search(
                    r'<a href="#scores">(.*?)</a>', self.page(case)
                ).group(1)
                linked = bool((scenario.upload or {}).get("url"))

                self.assertEqual("i-lock" in entry, not linked)

    def test_a_platform_score_in_the_database_is_never_shown(self):
        for url in (None, PLATFORM_URL):
            with self.subTest(url=url):
                with tempfile.TemporaryDirectory() as tmp:
                    report = build_flow_run(
                        Path(tmp) / "run",
                        platform_scores=True,
                        resources=[(1, "eu-central-1", 3)],
                        costs=[("2026-09-01", 10.0, "USD")],
                    )
                    perform_risk_assessment(1, report)
                    upload = (
                        {"status": "uploaded", "url": url, "sync_id": SYNC_ID}
                        if url
                        else None
                    )
                    generate_report(
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
                    html = (Path(report) / "index.html").read_text(encoding="utf-8")

                scores = re.search(
                    r'<section[^>]*id="scores".*?</section>', html, re.DOTALL
                ).group(0)
                for value in SERVER_SCORES:
                    self.assertNotIn(str(value), scores)
                self.assertEqual(
                    re.findall(r'<div class="l">(.*?)</div>', scores), ["–"]
                )
                # The pills are the platform link only when there is a link
                self.assertEqual(scores.count("Open on the platform"), 2 if url else 0)
                self.assertEqual(scores.count("hint-pop"), 0 if url else 2)


class ListTests(SampleTestCase):
    def test_the_risks_are_grouped_by_severity_with_their_codes(self):
        section = self.section("aws_typical", "risks")
        text = html_text(section)

        self.assertIn("Risks", text)
        self.assertRegex(text, r"\d+ unique risks · ")
        self.assertIn("ordered by severity", text)
        self.assertRegex(text, r"(Critical|High|Medium|Low) · \d+")
        self.assertIn("Risk codes are stable across runs.", text)
        self.assertIn('<a href="#about">About This Report</a>', section)

    def test_the_inventory_names_the_regions_with_their_shares(self):
        section = html_text(self.section("aws_typical", "inventory"))

        self.assertRegex(section, r"\d+ resources · \d+ types · 2 regions \(")
        self.assertRegex(section, r"\(eu-central-1 \d+\s%, eu-west-1 \d+\s%\)")
        self.assertIn("Share = resources of the type", section)

    def test_the_alternatives_are_alphabetical_with_marks_and_data_attributes(self):
        page = self.page("aws_typical")
        rows = re.findall(r"<tr data-cc=[^>]*>.*?</tr>", page, re.DOTALL)

        self.assertTrue(rows)
        for row in rows:
            self.assertRegex(row, r'data-sp="[01]" data-os="[01]"')
            self.assertIn('class="visually-hidden"', row)
            self.assertNotIn("red", row)
        self.assertIn("Enterprise Support", page)
        self.assertNotIn("Support plan", html_text(page))
        names = re.findall(r'<span class="alt-name">([^<]+)</span>', page)
        self.assertTrue(names)

    def test_a_missing_vendor_shows_the_dash_and_every_row_is_in_the_page(self):
        page = self.page("aws_typical")
        caption = re.search(
            r"(\d+) alternatives for", self.section("aws_typical", "alternatives")
        )

        self.assertEqual(len(re.findall(r'<tr data-cc="', page)), int(caption.group(1)))
        self.assertIn('<span class="alt-meta">–</span>', page)
        self.assertNotIn("vendor not recorded", page)
        self.assertNotIn("Unknown Organization", page)

    def test_the_filters_are_labelled_and_combinable(self):
        page = self.page("aws_typical")

        self.assertEqual(page.count('aria-pressed="false" data-f='), 3)
        for label in ("Open Source", "Enterprise Support", "European Vendors"):
            self.assertIn(f">{label}</button>", page)
        self.assertIn("No alternative matches the search and filters.", page)
        self.assertIn('placeholder="Search alternatives"', page)

    def test_rows_beyond_the_first_few_are_in_the_page_but_collapsed(self):
        page = self.page("aws_typical")
        collapsed = len(re.findall(r'data-extra="1" hidden>', page))
        more = [int(n) for n in re.findall(r"Show (\d+) more alternatives", page)]

        # EC2 has twelve alternatives in the test catalogue: four are collapsed
        self.assertEqual(more, [4])
        self.assertEqual(collapsed, 4)

    def test_an_empty_scope_shows_the_empty_states_and_no_filters(self):
        page = self.page("empty")
        text = html_text(page)

        for phrase in (
            "No exit risks were identified.",
            "With no resources in the scope, no risk from the data catalogue applies.",
            "No resources were found in the defined scope.",
            "Check that the region eu-central-1 is the intended scope",
            "Resource types not in the data catalogue are not counted.",
            "No resource types to list alternatives for.",
        ):
            self.assertIn(phrase, text)
        self.assertEqual(page.count('class="empty-state"'), 3)
        self.assertNotIn('class="toolbar"', page)
        nav = html_text(re.search(r"<nav.*?</nav>", page, re.DOTALL).group(0))
        self.assertIn("Risks 0 Resource Inventory 0 Alternative Technologies 0", nav)
        self.assertIn("$0.00", text)
        self.assertIn("no costs in the last six months", text)
        # The scores text drops the count of alternatives
        self.assertIn("with the alternatives rated and ranked", text)

    def test_no_risks_with_resources_says_why(self):
        text = html_text(self.page("no_risks"))

        self.assertIn("No exit risks were identified.", text)
        self.assertIn("Every resource type has enough alternatives", text)
        self.assertNotIn("With no resources in the scope", text)


class EgressTests(SampleTestCase):
    def test_the_egress_section_has_the_kpis_the_table_and_the_two_blocks(self):
        section = self.section("azure_typical", "egress")
        text = html_text(section)

        for phrase in (
            "Estimated Transfer Fee",
            "Data Volume",
            "Free-Tier Headroom",
            "Not Sized",
            "Size (GiB)",
            "Share of Volume",
            "Estimated Fee (USD)",
            "What is sized, and how",
            "Prices used (Microsoft Azure, zone 1, public internet routing)",
            "also as PDF EDL-",
            "not sized",
        ):
            self.assertIn(phrase, text)
        self.assertEqual(len(re.findall(r'class="kpi( primary)?"', section)), 4)
        self.assertIn('class="vol"', section)
        self.assertEqual(section.count('<details class="prices">'), 2)
        self.assertRegex(text, r"\d[\d,]*\.\d+\s+GiB\+")

    def test_the_figures_match_the_egress_pdf(self):
        for case in ("aws_typical", "azure_typical", "egress_multi_tib"):
            with self.subTest(case):
                page = html_text(self.section(case, "egress"))
                pdf = " ".join(
                    page_texts(PdfReader(str(self.folder(case) / "egress.pdf")))
                )
                fee = re.search(
                    r"Estimated Transfer Fee (\$[\d,]+\.\d\d\+?)", page
                ).group(1)

                self.assertIn(fee, pdf)

    def test_a_free_tier_footprint_shows_the_headroom(self):
        section = self.section("egress_free_tier", "egress")
        text = html_text(section)

        self.assertIn("$0.00", text)
        self.assertIn("within the monthly free tier", text)
        self.assertIn("of 100 GiB per month left", text)
        self.assertIn('class="headroom"', section)

    def test_a_large_footprint_has_a_fee_and_a_legend(self):
        section = self.section("egress_multi_tib", "egress")
        text = html_text(section)

        self.assertIn("at list prices", text)
        self.assertRegex(text, r"\$[1-9][\d,]*\.\d\d")
        self.assertIn('class="vol-legend"', section)
        self.assertNotIn("TiB", text)

    def test_the_egress_section_carries_no_promotion(self):
        text = html_text(self.section("aws_typical", "egress")).lower()

        for word in ("enterprise", "upgrade", "exitcloud", "book a demo"):
            self.assertNotIn(word, text)

    def test_without_egress_nothing_of_it_is_left(self):
        for case in ("aws_no_egress", "tfstate_aws", "empty"):
            with self.subTest(case):
                page = self.page(case)
                text = html_text(page)

                self.assertNotIn('id="egress"', page)
                self.assertNotIn("egress.pdf", page)
                self.assertNotIn("Egress & Data Landscape", text)
                self.assertNotIn("egress prices", text)
                self.assertNotIn("Free tier", text)
                self.assertNotIn("GiB / GB", text)


class AboutTests(SampleTestCase):
    def test_the_document_scope_and_provenance_tables(self):
        text = html_text(self.section("azure_typical", "about"))

        for phrase in (
            "Document ID ES-",
            "Document Type Executive Summary",
            "Language English",
            "Tenant ID 00000000-0000-4000-8000-0000000000a1",
            "Subscription ID",
            "Resource Group rg-sample-archive",
            "Run ID 01939b8e-7c2a-7d41-9a51-3c8f0a6e0003",
            "Data Catalogue 2026-11-02",
            "Self-collected data: no platform evaluated this report.",
            "is stored unchanged",
        ):
            self.assertIn(phrase, text)

    def test_the_glossary_has_three_terms_without_egress_and_six_with_it(self):
        def terms(case):
            text = html_text(self.section(case, "about"))
            return int(re.search(r"Glossary (\d+) terms", text).group(1))

        # Data catalogue, Lock-in hot-spot and Run; AWS adds its own entry and
        # a run with egress adds Egress, Free tier and GiB / GB
        self.assertEqual(terms("tfstate_azure"), 3)
        self.assertEqual(terms("azure_typical"), 6)
        self.assertEqual(terms("aws_no_egress"), 4)
        self.assertEqual(terms("aws_typical"), 7)

    def test_a_state_file_lists_its_own_scope_rows(self):
        text = html_text(self.section("tfstate_azure", "about"))

        for phrase in (
            "State File sample_azure.tfstate (SHA-256",
            "State 00000000-0000-4000-8000-0000000000c2 (serial 12)",
            "Regions northeurope, westeurope",
            "Source Terraform State",
            "reads the resources managed in the Terraform state",
        ):
            self.assertIn(phrase, text)
        for label in ("Tenant ID", "Subscription", "Resource Group"):
            self.assertNotIn(label, text)

    def test_a_state_file_says_so_in_the_inventory_and_the_notices(self):
        page = self.text("tfstate_aws")

        self.assertIn("resources that Terraform does not manage are not assessed", page)
        self.assertIn(
            "a state file holds no billing data, so this report shows no costs", page
        )

    def test_the_disclosures_are_collapsed_and_named(self):
        section = self.section("aws_typical", "about")

        self.assertEqual(section.count('<details class="disc">'), 4)
        self.assertNotIn('<details class="disc" open', section)
        for summary in (
            "Purpose and Method",
            "Limitations",
            "Risk Severity Levels",
            "Glossary",
        ):
            self.assertIn(summary, html_text(section))


class ConnectedTests(SampleTestCase):
    def test_a_run_with_a_link_points_to_the_platform_everywhere_it_should(self):
        page = self.page("azure_uploaded")
        text = html_text(page)
        url = STATE["scenarios"]["azure_uploaded"].upload["url"]

        self.assertEqual(page.count(f'href="{url}"'), 4)
        self.assertEqual(text.count("For this run it is on the platform."), 2)
        self.assertIn("Uploaded to the platform:", text)
        self.assertNotIn("Self-collected data", text)
        self.assertNotIn("Light & Enterprise", text)

    def test_an_upload_without_a_link_keeps_the_hints_and_names_the_assessment(self):
        page = self.page("uploaded_no_link")
        text = html_text(page)

        self.assertNotIn("Open on the platform", text)
        self.assertEqual(page.count("hint-pop"), 2 + 0)
        self.assertIn(
            "Uploaded to the platform · Assessment ID: sample-assessment-0001", text
        )
        self.assertNotIn("For this run it is on the platform.", text)

    def test_a_failed_upload_is_named_in_the_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_flow_run(
                Path(tmp) / "run",
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
                upload={"status": "failed", "reason": "upload_unavailable"},
                sync_id=SYNC_ID,
            )
            html = (Path(report) / "index.html").read_text(encoding="utf-8")

        self.assertIn("Not uploaded: upload_unavailable", html_text(html))
        self.assertIn("Self-collected", html)


class TourTests(SampleTestCase):
    def test_the_steps_are_the_planned_list(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                expected = [
                    title
                    for title in TOUR_TITLES
                    if title != "Egress & Data Landscape" or scenario.egress
                ]
                steps = self.tour(case)

                self.assertEqual([step["title"] for step in steps], expected)
                for step in steps:
                    self.assertTrue(step["description"])
                    self.assertIn(step["side"], ("right", "bottom", "top"))

    def test_every_step_text_comes_from_the_wording(self):
        pieces = {value for value in BLOCKS.values() if isinstance(value, str)}
        for case, _scenario in self.cases():
            with self.subTest(case):
                for step in self.tour(case):
                    self.assertIn(step["title"], pieces)
                    self.assertTrue(composed_of(step["description"], pieces), step)

    def test_the_variants(self):
        def description(case, title):
            return next(
                s["description"] for s in self.tour(case) if s["title"] == title
            )

        self.assertIn(
            "Download has the same results as PDF",
            description("aws_typical", "This Report"),
        )
        self.assertEqual(
            description("aws_no_egress", "This Report").split(". ")[-1],
            "Download has the Executive Summary as PDF.",
        )
        self.assertIn(
            "A Terraform state holds no billing data",
            description("tfstate_aws", "Overview"),
        )
        self.assertIn("marked with a lock", description("aws_typical", "Sections"))
        # A run with a link: no lock sentence, and the scores are on the platform
        self.assertNotIn("lock", description("azure_uploaded", "Sections"))
        self.assertIn(
            "This local report never shows platform scores.",
            description(
                "azure_uploaded", "Exit Readiness Grade & Vendor Lock-in Score"
            ),
        )
        self.assertIn(
            "Open on the platform", description("azure_uploaded", "This Report")
        )
        self.assertIn("as PDF.", description("azure_uploaded", "This Report"))
        self.assertTrue(
            description(
                "aws_typical", "Exit Readiness Grade & Vendor Lock-in Score"
            ).startswith(
                "Available in EscapeCloud Light & Enterprise. EscapeCloud's scoring engine"
            )
        )

    def test_the_tour_starts_only_on_a_click_and_help_has_a_fallback(self):
        page = self.page("aws_typical")

        self.assertEqual(page.count("tour.drive()"), 1)
        self.assertIn("button.addEventListener('click'", page)
        self.assertIn("window.innerWidth < 992", page)
        self.assertIn("location.hash = 'about'", page)
        self.assertIn('id="help"', page)
        self.assertIn("onDestroyed: function () { button.focus(); }", page)


class OfflineTests(SampleTestCase):
    def test_no_page_loads_anything_from_outside_its_folder(self):
        for case, _scenario in self.cases():
            with self.subTest(case):
                folder = self.folder(case)
                css = [
                    (
                        "assets/css",
                        (folder / "assets" / "css" / name).read_text(encoding="utf-8"),
                    )
                    for name in ("report.css", "driver.css")
                ]

                self.assertEqual(offline_problems(self.page(case), css), [])

    def test_everything_the_page_references_is_in_the_report_folder(self):
        for case, _scenario in self.cases():
            with self.subTest(case):
                folder = self.folder(case)
                for tag, value in references(self.page(case)):
                    self.assertTrue((folder / value).is_file(), f"{tag} {value}")
                for name in ("report.css", "driver.css"):
                    for value in css_references(
                        (folder / "assets" / "css" / name).read_text(encoding="utf-8")
                    ):
                        self.assertTrue(
                            (folder / "assets" / "css" / value).resolve().is_file(),
                            f"{name} {value}",
                        )

    def test_the_guard_finds_a_planted_cdn_link(self):
        bad = (
            '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">'
            '<script src="//cdn.example/x.js"></script><img src="/etc/logo.png">'
            '<link href="../outside.css" rel="stylesheet"><a href="https://github.com/x">fine</a>'
        )

        problems = offline_problems(
            bad,
            [
                ("assets/css", '@import url("https://fonts.googleapis.com/css");'),
                (
                    "assets/css",
                    "a { background: url(../../../x.png) } "
                    "b { background: url(data:image/png;base64,AA) } "
                    "c { background: url(../fonts/ok.ttf) }",
                ),
            ],
        )

        self.assertEqual(len(set(problems)), 7, problems)
        self.assertEqual(
            offline_problems(
                '<link href="assets/css/report.css"><a href="https://github.com/x">x</a>'
            ),
            [],
        )

    def test_the_vendored_scripts_are_unmodified_and_have_their_licence(self):
        for name, digest in (
            ("js/driver.js.iife.js", DRIVER_JS_SHA256),
            ("css/driver.css", DRIVER_CSS_SHA256),
        ):
            with self.subTest(name):
                data = asset_path(*name.split("/")).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        licence = asset_path("js", "driver.js.LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("The MIT License", licence)
        self.assertIn("Kamran Ahmed", licence)

    def test_the_report_folder_gets_the_fonts_scripts_and_logos(self):
        folder = self.folder("aws_typical")

        for name in (
            "fonts/IBMPlexSans-Regular.ttf",
            "fonts/IBMPlexMono-SemiBold.ttf",
            "fonts/OFL.txt",
            "js/driver.js.iife.js",
            "js/driver.js.LICENSE.txt",
            "css/driver.css",
            "css/report.css",
            "img/csp/aws.svg",
            "img/csp/azure.svg",
            "img/csp/gcp.svg",
            "img/csp/alibaba.svg",
            "img/logo/report_logo.png",
        ):
            self.assertTrue((folder / "assets" / name).is_file(), name)

    def test_the_provider_logos_are_self_contained(self):
        for name in ("aws", "azure", "gcp", "alibaba"):
            with self.subTest(name):
                svg = asset_path("img", "csp", f"{name}.svg").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("<script", svg)
                self.assertNotIn("href=", svg)
                self.assertEqual(
                    re.findall(r"https?://[^\"' ]+", svg),
                    ["http://www.w3.org/2000/svg"],
                )


class NoticesTests(SampleTestCase):
    def test_the_page_ends_with_the_disclaimer_box_and_then_the_notices(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                page = self.page(case)
                text = html_text(page)

                self.assertLess(
                    page.index("</section>", page.index('id="about"')),
                    page.index('<aside class="disclaimer"'),
                )
                self.assertLess(
                    page.index('<aside class="disclaimer"'),
                    page.index('class="notices"'),
                )
                tail = html_text(page[page.index('<aside class="disclaimer"') :])
                self.assertTrue(tail.startswith("Disclaimer "))
                self.assertIn(BLOCKS["notices_disclaimer"], tail)
                self.assertIn(BLOCKS["notices_generated"], tail)
                self.assertIn(BLOCKS["html_notice_advice"], tail)
                own, other = (
                    ("Microsoft group", "Amazon.com")
                    if scenario.provider == "azure"
                    else ("Amazon.com", "Microsoft group")
                )
                self.assertIn(own, tail)
                self.assertNotIn(other, text)
                self.assertRegex(
                    tail,
                    r"© 2024–2026 EscapeCloud [0-9A-Z]{2,3}-[0-9a-f]{8}-EN · cloudexit \S+$",
                )

    def test_the_notice_blocks_and_their_links(self):
        page = self.page("aws_typical")
        footer = page[page.index('class="notices"') :]

        for heading in ("Trademarks", "Figures", "Software"):
            self.assertIn(f"<h2>{heading}</h2>", footer)
        self.assertIn(
            f'<a href="{REPOSITORY_URL}" target="_blank" rel="noopener noreferrer">github.com/escapecloud/cloudexit</a>',
            footer,
        )
        self.assertIn("Fonts: IBM Plex, SIL Open Font License 1.1.", footer)
        self.assertIn(
            "Guided tour: driver.js, MIT License, included in this report's assets.",
            html_text(footer),
        )
        self.assertIn(AWS_ICONS_URL, page)
        self.assertNotIn(AZURE_ICONS_URL, page)

    def test_the_figures_notice_follows_the_state(self):
        def figures(case):
            page = self.page(case)
            block = page[page.index("<h2>Figures</h2>") :]
            return html_text(block[: block.index("</div>")])

        self.assertIn("egress prices are public list prices", figures("aws_typical"))
        self.assertNotIn("egress", figures("aws_no_egress"))
        self.assertIn("a state file holds no billing data", figures("tfstate_aws"))

    def test_the_trademark_sentences_are_in_the_page_and_both_pdfs(self):
        for case, scenario in self.cases():
            with self.subTest(case):
                text = self.text(case)
                self.assertIn(BLOCKS["notices_trademark_own"], text)
                self.assertIn(BLOCKS["notices_trademark_others"], text)
                for pdf in ("report.pdf", "egress.pdf"):
                    if (self.folder(case) / pdf).is_file():
                        last = page_texts(PdfReader(str(self.folder(case) / pdf)))[-1]
                        self.assertIn(BLOCKS["notices_trademark_own"], last)
                        self.assertIn(BLOCKS["notices_trademark_others"], last)

    def test_no_trademark_claim_for_cloudexit_or_the_logo(self):
        texts = [value for value in BLOCKS.values() if isinstance(value, str)]
        texts += [self.text(case) for case, _scenario in self.cases()]
        for text in texts:
            self.assertIsNone(
                re.search(r"(?i)cloudexit[^.]{0,60}trademark", text), text[:80]
            )
            self.assertIsNone(
                re.search(
                    r"(?i)(escapecloud|cloudexit)\b[^.]{0,60}\blogo\b[^.]{0,30}trademark",
                    text,
                ),
                text[:80],
            )
        for case, _scenario in self.cases():
            self.assertEqual(find_terms(self.text(case)), [])

    def test_the_logo_files_carry_their_own_notice(self):
        note = asset_path("img", "logo", "README.md").read_text(encoding="utf-8")

        self.assertIn(
            "The EscapeCloud logo files in this directory are not covered by this "
            "project's LICENSE. All rights reserved.",
            note,
        )


if __name__ == "__main__":
    unittest.main()
