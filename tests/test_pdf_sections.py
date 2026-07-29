import tempfile
import unittest

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import Paragraph, PageBreak, Table, TableStyle

from core.utils_report import (
    anonymize_string,
    _default_table_style,
    _build_summary_section,
    _build_scope_section,
    _build_cost_section,
    _build_risk_section,
    _build_scoring_section,
    _build_resource_section,
    _build_alt_tech_section,
)
from tests.report_fixtures import (
    build_report_fixture,
    stage_report_assets,
)


def _make_styles():
    styles = getSampleStyleSheet()
    content_style = ParagraphStyle(
        "ContentStyle", fontSize=10, leading=12, spaceAfter=10
    )
    styles["Heading1"].leading = 1.5 * styles["Heading1"].fontSize
    styles["Heading1"].textColor = HexColor("#112726")
    styles["Heading2"].leading = 1.5 * styles["Heading2"].fontSize
    styles["Heading2"].textColor = HexColor("#112726")
    return styles, content_style


class AnonymizeStringTests(unittest.TestCase):
    def test_anonymizes_middle_of_long_string(self):
        result = anonymize_string("AKIAIOSFODNN7EXAMPLE")
        self.assertTrue(result.startswith("AKIA"))
        self.assertTrue(result.endswith("MPLE"))
        self.assertIn("*", result)
        self.assertEqual(len(result), len("AKIAIOSFODNN7EXAMPLE"))

    def test_short_string_fully_masked(self):
        result = anonymize_string("abcd")
        self.assertEqual(result, "****")

    def test_empty_string_returns_empty_mask(self):
        result = anonymize_string("")
        self.assertEqual(result, "")

    def test_non_string_returns_na(self):
        self.assertEqual(anonymize_string(None), "N/A")
        self.assertEqual(anonymize_string(12345), "N/A")

    def test_custom_num_visible(self):
        result = anonymize_string("ABCDEFGHIJ", num_visible=2)
        self.assertEqual(result, "AB******IJ")


class DefaultTableStyleTests(unittest.TestCase):
    def test_returns_table_style_instance(self):
        style = _default_table_style()
        self.assertIsInstance(style, TableStyle)

    def test_style_has_expected_commands(self):
        style = _default_table_style()
        commands = style.getCommands()
        command_names = [cmd[0] for cmd in commands]
        self.assertIn("BACKGROUND", command_names)
        self.assertIn("TEXTCOLOR", command_names)
        self.assertIn("GRID", command_names)
        self.assertIn("FONTNAME", command_names)


class BuildSummarySectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def test_returns_non_empty_list(self):
        content = _build_summary_section(
            self.fixture["metadata"], self.styles, self.content_style
        )
        self.assertIsInstance(content, list)
        self.assertGreater(len(content), 0)

    def test_contains_heading_paragraph(self):
        content = _build_summary_section(
            self.fixture["metadata"], self.styles, self.content_style
        )
        paragraphs = [item for item in content if isinstance(item, Paragraph)]
        heading_texts = [p.text for p in paragraphs]
        self.assertIn("Summary", heading_texts)

    def test_contains_table_with_metadata(self):
        content = _build_summary_section(
            self.fixture["metadata"], self.styles, self.content_style
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)

    def test_azure_provider_maps_correctly(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 1}
        content = _build_summary_section(metadata, self.styles, self.content_style)
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)


class BuildScopeSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def test_aws_scope_contains_access_key(self):
        content = _build_scope_section(
            self.fixture["metadata"],
            self.fixture["provider_details"],
            self.styles,
            self.content_style,
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)

    def test_azure_scope_contains_tenant_id(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 1}
        provider_details = {
            "tenantId": "tenant-123",
            "clientId": "client-456",
            "clientSecret": "secret-789-very-long-value",
            "subscriptionId": "sub-000",
            "resourceGroupName": "rg-test",
        }
        content = _build_scope_section(
            metadata, provider_details, self.styles, self.content_style
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)

    def test_unknown_provider_returns_na(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 99}
        content = _build_scope_section(metadata, {}, self.styles, self.content_style)
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)


class BuildTfstateScopeSectionTests(unittest.TestCase):
    _SHA = "a3f1" + "b" * 56 + "9c2e"
    _LINEAGE = "121f3456-7d0f-a7be-ceb0-c91d44c6a689"

    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def _rows(self, metadata, provider_details, tfstate_scope=None):
        content = _build_scope_section(
            metadata,
            provider_details,
            self.styles,
            self.content_style,
            tfstate_scope,
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)
        return {row[0]: row[1] for row in tables[0]._cellvalues[1:]}

    def _aws_scope(self, **overrides):
        scope = {
            "file": "infra.tfstate",
            "sha256": self._SHA,
            "lineage": self._LINEAGE,
            "serial": 7,
            "locations": ["eu-central-1"],
            "subscriptions": [],
            "resource_groups": [],
        }
        scope.update(overrides)
        return scope

    def test_aws_renders_file_state_and_regions(self):
        rows = self._rows(
            self.fixture["metadata"],
            {"tfstatePath": "/tmp/infra.tfstate"},
            self._aws_scope(),
        )

        self.assertEqual(list(rows), ["File", "State", "Region(s)"])
        self.assertEqual(rows["File"], "infra.tfstate (SHA-256 a3f1…9c2e)")
        self.assertEqual(rows["State"], f"{self._LINEAGE} (Serial: 7)")
        self.assertEqual(rows["Region(s)"], "eu-central-1")

    def test_azure_renders_subscription_and_resource_groups(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 1}
        scope = self._aws_scope(
            subscriptions=["0299bf7a-8ca8-479b-8659-c62e62cd7bae"],
            resource_groups=["escape-test-rg-1", "escape-test-rg-2"],
        )

        rows = self._rows(metadata, {"tfstatePath": "/tmp/infra.tfstate"}, scope)

        self.assertEqual(
            list(rows), ["File", "State", "Subscription", "Resource Group(s)"]
        )
        self.assertEqual(rows["Subscription"], "0299bf7a-8ca8-479b-8659-c62e62cd7bae")
        self.assertEqual(
            rows["Resource Group(s)"], "escape-test-rg-1, escape-test-rg-2"
        )
        # Azure deliberately mirrors the live table, which shows no location.
        self.assertNotIn("Location(s)", rows)
        self.assertNotIn("Region(s)", rows)

    def test_many_values_are_capped_with_a_remainder(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 1}
        scope = self._aws_scope(
            subscriptions=["sub-1"],
            resource_groups=[f"rg-{i}" for i in range(6)],
        )

        rows = self._rows(metadata, {"tfstatePath": "/tmp/infra.tfstate"}, scope)

        self.assertEqual(rows["Resource Group(s)"], "rg-0, rg-1, rg-2, +3 more")

    def test_missing_values_report_not_determinable(self):
        rows = self._rows(
            self.fixture["metadata"],
            {"tfstatePath": "/tmp/infra.tfstate"},
            self._aws_scope(locations=[]),
        )

        self.assertEqual(rows["Region(s)"], "Not determinable from state")

    def test_state_row_omitted_when_lineage_and_serial_absent(self):
        rows = self._rows(
            self.fixture["metadata"],
            {"tfstatePath": "/tmp/infra.tfstate"},
            self._aws_scope(lineage=None, serial=None),
        )

        self.assertEqual(list(rows), ["File", "Region(s)"])
        self.assertEqual(rows["File"], "infra.tfstate (SHA-256 a3f1…9c2e)")

    def test_missing_manifest_falls_back_to_file_name_only(self):
        rows = self._rows(
            self.fixture["metadata"], {"tfstatePath": "/tmp/infra.tfstate"}, None
        )

        self.assertEqual(rows["File"], "infra.tfstate")
        self.assertEqual(rows["Region(s)"], "Not determinable from state")
        # Never the credential rows, which would all be N/A here.
        self.assertNotIn("Access Key", rows)

    def test_live_mode_table_is_unchanged(self):
        rows = self._rows(
            self.fixture["metadata"], self.fixture["provider_details"], None
        )

        self.assertEqual(list(rows), ["Access Key", "Secret Key", "Region"])
        self.assertEqual(rows["Region"], "eu-central-1")


class BuildCostSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def test_returns_list_ending_with_page_break(self):
        content = _build_cost_section(
            self.fixture["cost_data"], self.styles, self.content_style
        )
        self.assertIsInstance(content, list)
        self.assertIsInstance(content[-1], PageBreak)

    def test_contains_cost_table(self):
        content = _build_cost_section(
            self.fixture["cost_data"], self.styles, self.content_style
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertGreaterEqual(len(tables), 1)


class BuildRiskSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def test_returns_list_ending_with_page_break(self):
        with tempfile.TemporaryDirectory() as report_dir:
            stage_report_assets(report_dir)
            content = _build_risk_section(
                self.fixture["risk_data"],
                self.fixture["risk_definitions"],
                self.fixture["resource_inventory"],
                report_dir,
                self.styles,
                self.content_style,
            )
        self.assertIsInstance(content[-1], PageBreak)

    def test_contains_risk_table(self):
        with tempfile.TemporaryDirectory() as report_dir:
            stage_report_assets(report_dir)
            content = _build_risk_section(
                self.fixture["risk_data"],
                self.fixture["risk_definitions"],
                self.fixture["resource_inventory"],
                report_dir,
                self.styles,
                self.content_style,
            )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertGreaterEqual(len(tables), 1)

    def test_empty_risk_data_produces_table_with_header_and_total_only(self):
        with tempfile.TemporaryDirectory() as report_dir:
            stage_report_assets(report_dir)
            content = _build_risk_section(
                [],
                self.fixture["risk_definitions"],
                self.fixture["resource_inventory"],
                report_dir,
                self.styles,
                self.content_style,
            )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertGreaterEqual(len(tables), 1)


class BuildResourceSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def test_returns_list_ending_with_page_break(self):
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_resource_section(
                self.fixture["resource_inventory"],
                self.fixture["resource_type_mapping"],
                report_dir,
                self.styles,
                self.content_style,
            )
        self.assertIsInstance(content[-1], PageBreak)

    def test_contains_resource_table(self):
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_resource_section(
                self.fixture["resource_inventory"],
                self.fixture["resource_type_mapping"],
                report_dir,
                self.styles,
                self.content_style,
            )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)

    def test_empty_inventory_produces_header_and_total_only(self):
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_resource_section(
                [],
                {},
                report_dir,
                self.styles,
                self.content_style,
            )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)


class BuildAltTechSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def test_returns_list_ending_with_page_break(self):
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_alt_tech_section(
                self.fixture["resource_inventory"],
                self.fixture["resource_type_mapping"],
                self.fixture["alternatives"],
                self.fixture["alternative_technologies"],
                self.fixture["exit_strategy"],
                report_dir,
                self.styles,
                self.content_style,
            )
        self.assertIsInstance(content[-1], PageBreak)

    def test_contains_alt_tech_table(self):
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_alt_tech_section(
                self.fixture["resource_inventory"],
                self.fixture["resource_type_mapping"],
                self.fixture["alternatives"],
                self.fixture["alternative_technologies"],
                self.fixture["exit_strategy"],
                report_dir,
                self.styles,
                self.content_style,
            )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertGreaterEqual(len(tables), 1)


class BuildScoringSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()

    def test_with_scoring_data_returns_content(self):
        scoring_data = {
            "exit_score": 72,
            "human_score": 4,
            "technology_score": 3,
            "operational_score": 2,
        }
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_scoring_section(
                scoring_data, report_dir, self.styles, self.content_style
            )
        self.assertIsInstance(content, list)
        self.assertGreater(len(content), 0)
        self.assertIsInstance(content[-1], PageBreak)

    def test_with_none_scoring_data_uses_zero_defaults(self):
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_scoring_section(
                None, report_dir, self.styles, self.content_style
            )
        self.assertIsInstance(content, list)
        self.assertGreater(len(content), 0)

    def test_contains_exit_score_and_vendor_lockin_tables(self):
        scoring_data = {
            "exit_score": 50,
            "human_score": 3,
            "technology_score": 4,
            "operational_score": 2,
        }
        with tempfile.TemporaryDirectory() as report_dir:
            content = _build_scoring_section(
                scoring_data, report_dir, self.styles, self.content_style
            )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 2)


if __name__ == "__main__":
    unittest.main()
