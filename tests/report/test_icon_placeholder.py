import copy
import json
import os
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Table

from core.report.executive_summary import generate_executive_summary
from core.report.helpers import initials
from core.report.palette import contrast_failures
from core.report.placeholder import IconPlaceholder, LabelWithPlaceholder
from core.report.sections import ALT_BODY
from core.utils_egress import GIB
from core.utils_report import generate_html_report
from core.utils_report_egress import (
    _icon_flowable,
    generate_egress_html_report,
    generate_egress_pdf_report,
)
from core.utils_report_common import enrich_resource_inventory
from tests.report_fixtures import (
    build_fixture_context,
    build_report_fixture,
    stage_minimal_assets,
    stage_report_assets,
)
from tests.test_utils_report_egress import (
    _archive_tiers,
    _fake_load_data,
    _payload,
    _row,
    _seed_egress_inventory,
)

EC2_ICON = "/icons/aws/Compute/EC2.png"
LAMBDA_ICON = "/icons/aws/Compute/Lambda.png"
PLACEHOLDER = '<span class="resource-icon-placeholder" aria-hidden="true">{}</span>'


def with_initials(initials_text, name):
    # The text extractor may give the label before or after the placeholder.
    return rf"({initials_text}\s+{name}|{name}\s+{initials_text})"


def pdf_text(path):
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def image_count(path):
    return sum(len(page.images) for page in PdfReader(path).pages)


class InitialsTests(unittest.TestCase):
    def test_two_words_give_two_letters_and_one_word_gives_one(self):
        cases = {
            "Virtual Machine": "VM",
            "S3 Bucket": "SB",
            "storage account": "SA",
            "Elastic Block Store Volumes": "EB",
            "Lambda": "L",
            "  padded   words  ": "PW",
            "école supérieure": "ÉS",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(initials(name), expected)

    def test_a_missing_or_symbol_only_name_is_a_question_mark(self):
        for name in (None, "", "   ", "–", "()", "- -"):
            with self.subTest(name=name):
                self.assertEqual(initials(name), "?")

    def test_a_word_that_starts_with_a_symbol_uses_its_first_letter(self):
        self.assertEqual(initials("(Classic) VM"), "CV")


class FlowableTests(unittest.TestCase):
    def render(self, flowables, size=A4):
        buffer = BytesIO()
        SimpleDocTemplate(buffer, pagesize=size).build(flowables)
        buffer.seek(0)
        return PdfReader(buffer)

    def test_the_placeholder_is_a_square_of_the_icon_size(self):
        placeholder = IconPlaceholder("Virtual Machine", 20)
        self.assertEqual(placeholder.wrap(100, 100), (20, 20))

    def test_the_placeholder_draws_the_initials_as_text(self):
        reader = self.render([IconPlaceholder("Virtual Machine", 20)])
        self.assertIn("VM", reader.pages[0].extract_text())

    def test_a_label_with_a_placeholder_wraps_like_text_and_keeps_the_icon_height(self):
        short = LabelWithPlaceholder("Lambda", "<b>Lambda</b>", ALT_BODY)
        width, height = short.wrap(200, 500)
        self.assertEqual(width, 200)
        self.assertGreaterEqual(height, short.size)

        long_name = "A very long resource type name " * 6
        long = LabelWithPlaceholder(long_name, f"<b>{long_name}</b>", ALT_BODY)
        _, long_height = long.wrap(120, 500)
        self.assertGreater(long_height, height)

    def test_a_label_in_a_table_cell_shows_the_initials_and_the_name(self):
        cell = LabelWithPlaceholder(
            "Virtual Machine", "<b>Virtual Machine</b>", ALT_BODY
        )
        text = self.render([Table([[cell, "7"]], colWidths=[200, 50])])
        text = text.pages[0].extract_text()
        self.assertRegex(text, with_initials("VM", "Virtual Machine"))

    def test_the_placeholder_colours_pass_the_report_contrast_check(self):
        self.assertEqual(contrast_failures(), [])

    def test_a_canvas_can_draw_it_directly(self):
        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=A4)
        IconPlaceholder("S3 Bucket", 11).drawOn(canvas, 50, 50)
        canvas.save()
        buffer.seek(0)
        self.assertIn("SB", PdfReader(buffer).pages[0].extract_text())


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.count = 0

    def directory(self):
        self.count += 1
        path = self.tmp / f"report-{self.count}"
        path.mkdir()
        return path


def fixture_with(extra_types):
    fixture = copy.deepcopy(build_report_fixture())
    for type_id, mapping in extra_types.items():
        if mapping is not None:
            fixture["resource_type_mapping"][str(type_id)] = mapping
        fixture["resource_inventory"].append(
            {"resource_type": type_id, "location": "eu-central-1", "count": 3}
        )
    return fixture


class BasicPdfTests(TempDirTestCase):
    def render(self, fixture):
        directory = self.directory()
        stage_minimal_assets(str(directory))
        context = build_fixture_context(fixture, directory)
        return generate_executive_summary(context), context

    def test_a_type_without_an_icon_gets_a_placeholder_and_the_others_keep_their_image(
        self,
    ):
        fixture = fixture_with(
            {
                303: {
                    "id": 303,
                    "code": "AWS.Compute.Machines",
                    "name": "Virtual Machine",
                    "icon": "",
                },
                404: None,
            }
        )

        path, context = self.render(fixture)

        icons = {entry.name: entry.icon for entry in context.resources}
        self.assertTrue(os.path.isfile(icons["EC2 Instance"]))
        self.assertIsNone(icons["Virtual Machine"])
        self.assertIsNone(icons["–"])
        text = pdf_text(path)
        self.assertRegex(text, with_initials("VM", "Virtual Machine"))
        self.assertIn("?", text)

    def test_the_placeholder_replaces_an_image_it_does_not_add_one(self):
        with_icon = fixture_with(
            {
                303: {
                    "id": 303,
                    "code": "AWS.Compute.Machines",
                    "name": "Virtual Machine",
                    "icon": LAMBDA_ICON,
                }
            }
        )
        without_icon = fixture_with(
            {
                303: {
                    "id": 303,
                    "code": "AWS.Compute.Machines",
                    "name": "Virtual Machine",
                    "icon": "",
                }
            }
        )

        with_path, _ = self.render(with_icon)
        without_path, _ = self.render(without_icon)

        self.assertGreater(image_count(with_path), image_count(without_path))
        self.assertNotRegex(pdf_text(with_path), with_initials("VM", "Virtual Machine"))

    def test_an_icon_file_that_does_not_exist_is_a_placeholder_too(self):
        fixture = fixture_with(
            {
                303: {
                    "id": 303,
                    "code": "AWS.Compute.Machines",
                    "name": "Virtual Machine",
                    "icon": "/icons/aws/Compute/NoSuchIcon.png",
                }
            }
        )
        path, context = self.render(fixture)

        self.assertIsNone(
            next(e.icon for e in context.resources if e.name == "Virtual Machine")
        )
        self.assertRegex(pdf_text(path), with_initials("VM", "Virtual Machine"))


class HtmlReportTests(TempDirTestCase):
    def render(self, fixture):
        directory = self.directory()
        html_path = generate_html_report(
            str(directory),
            fixture["metadata"],
            fixture["resource_type_mapping"],
            fixture["resource_inventory"],
            fixture["cost_data"],
            None,
            fixture["risk_data"],
            fixture["risk_definitions"],
            fixture["alternatives"],
            fixture["alternative_technologies"],
            fixture["exit_strategy"],
            fixture["alternative_technology_organizations"],
        )
        return Path(html_path).read_text(encoding="utf-8")

    def test_a_type_without_an_icon_renders_the_placeholder_not_a_broken_image(self):
        fixture = fixture_with(
            {
                303: {
                    "id": 303,
                    "code": "AWS.Compute.Machines",
                    "name": "Virtual Machine",
                    "icon": "",
                },
                404: None,
            }
        )

        html = self.render(fixture)

        self.assertIn(PLACEHOLDER.format("VM"), html)
        # A type missing from the mapping is named "Unknown Resource"
        self.assertIn(PLACEHOLDER.format("UR"), html)
        self.assertNotIn("default.png", html)
        self.assertNotIn('src="assets/"', html)
        self.assertNotIn("None", re.findall(r'src="assets/[^"]*"', html))

    def test_a_type_with_an_icon_keeps_its_image(self):
        html = self.render(fixture_with({}))

        self.assertIn(f'src="assets{EC2_ICON}"', html)
        self.assertNotIn('resource-icon-placeholder" aria-hidden', html)

    def test_both_kinds_appear_side_by_side(self):
        fixture = fixture_with(
            {
                303: {
                    "id": 303,
                    "code": "AWS.Compute.Machines",
                    "name": "Virtual Machine",
                    "icon": "",
                }
            }
        )
        html = self.render(fixture)

        self.assertIn(f'src="assets{EC2_ICON}"', html)
        self.assertIn(PLACEHOLDER.format("VM"), html)

    def test_the_stylesheet_styles_the_placeholder(self):
        from core.resources import asset_path

        css = asset_path("css", "style.css").read_text(encoding="utf-8")
        self.assertIn(".resource-icon-placeholder", css)


class EnrichedInventoryTests(unittest.TestCase):
    MAPPING = {
        "1": {"code": "A", "name": "With Icon", "icon": EC2_ICON},
        "2": {"code": "B", "name": "No Icon", "icon": ""},
    }
    INVENTORY = [
        {"resource_type": 1, "location": "x", "count": 1},
        {"resource_type": 2, "location": "x", "count": 1},
        {"resource_type": 3, "location": "x", "count": 1},
    ]

    def test_the_inventory_carries_no_fallback_icon(self):
        rows = enrich_resource_inventory(self.INVENTORY, self.MAPPING)

        self.assertEqual([row["icon"] for row in rows], [EC2_ICON, "", ""])
        self.assertTrue(all("icon_url" not in row for row in rows))


class EgressReportTests(TempDirTestCase):
    ROWS = [
        _row(
            "stdemo",
            "Storage Account",
            "object",
            2048 * GIB,
            resource_type="Microsoft.Storage/storageAccounts",
        ),
        _row("vol1", "Managed Disk", "block", 100 * GIB, resource_type="some/type"),
    ]

    def prepare(self, icons):
        directory = self.directory()
        stage_report_assets(str(directory))
        json_path = directory / "egress_inventory_raw_data.json"
        json_path.write_text(json.dumps(_payload(self.ROWS)), encoding="utf-8")
        _seed_egress_inventory(str(directory), self.ROWS, _archive_tiers(self.ROWS))
        return directory, str(json_path), icons

    def run_report(self, generate, icons):
        directory, json_path, icons = self.prepare(icons)
        with (
            patch("core.utils_report_egress.load_data", side_effect=_fake_load_data),
            patch("core.utils_report_egress._build_icon_lookup", return_value=icons),
        ):
            result = generate(str(directory), json_path)
        self.assertTrue(result["success"], result.get("logs"))
        return result

    def test_the_icon_flowable_is_a_placeholder_without_an_icon_file(self):
        directory = self.directory()
        stage_report_assets(str(directory))

        self.assertIsInstance(
            _icon_flowable(str(directory), None, "Managed Disk"), IconPlaceholder
        )
        self.assertIsInstance(
            _icon_flowable(str(directory), "/icons/aws/Compute/NoSuchIcon.png", "X"),
            IconPlaceholder,
        )
        image = _icon_flowable(str(directory), EC2_ICON, "Managed Disk")
        self.assertNotIsInstance(image, IconPlaceholder)

    def test_the_pdf_shows_a_placeholder_for_a_type_without_an_icon(self):
        icons = {"microsoft.storage/storageaccounts": EC2_ICON}
        result = self.run_report(
            lambda d, j: generate_egress_pdf_report(d, j, {}), icons
        )

        text = pdf_text(result["pdf_path"])
        self.assertRegex(text, r"MD")
        self.assertIn("Managed Disk", text)

    def test_the_pdf_has_one_image_less_when_a_type_loses_its_icon(self):
        both = {
            "microsoft.storage/storageaccounts": EC2_ICON,
            "some/type": LAMBDA_ICON,
        }
        one = {"microsoft.storage/storageaccounts": EC2_ICON}
        with_both = self.run_report(
            lambda d, j: generate_egress_pdf_report(d, j, {}), both
        )
        count_both = image_count(with_both["pdf_path"])
        with_one = self.run_report(
            lambda d, j: generate_egress_pdf_report(d, j, {}), one
        )

        self.assertEqual(image_count(with_one["pdf_path"]), count_both - 1)

    def test_the_html_shows_a_placeholder_and_keeps_the_images(self):
        icons = {"microsoft.storage/storageaccounts": EC2_ICON}
        result = self.run_report(generate_egress_html_report, icons)

        html = Path(result["html_path"]).read_text(encoding="utf-8")
        self.assertRegex(html, rf'src="assets/+{EC2_ICON.lstrip("/")}"')
        self.assertIn(PLACEHOLDER.format("MD"), html)
        self.assertNotIn("no_image", html)


if __name__ == "__main__":
    unittest.main()
