import unittest

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import Paragraph, Table, TableStyle

from cloudexit.report.egress import (
    build_scope_section,
    build_summary_section,
    default_table_style,
)
from cloudexit.report.context import scope_facts
from cloudexit.report.wording import BLOCKS
from tests.support.credentials import AWS_DETAILS, AZURE_DETAILS, leaked_fragments
from tests.support.report import (
    build_report_fixture,
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


class DefaultTableStyleTests(unittest.TestCase):
    def test_returns_table_style_instance(self):
        style = default_table_style()
        self.assertIsInstance(style, TableStyle)

    def test_style_has_expected_commands(self):
        style = default_table_style()
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
        content = build_summary_section(
            self.fixture["metadata"], self.styles, self.content_style
        )
        self.assertIsInstance(content, list)
        self.assertGreater(len(content), 0)

    def test_contains_heading_paragraph(self):
        content = build_summary_section(
            self.fixture["metadata"], self.styles, self.content_style
        )
        paragraphs = [item for item in content if isinstance(item, Paragraph)]
        heading_texts = [p.text for p in paragraphs]
        self.assertIn("Summary", heading_texts)

    def test_contains_table_with_metadata(self):
        content = build_summary_section(
            self.fixture["metadata"], self.styles, self.content_style
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)

    def test_azure_provider_maps_correctly(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 1}
        content = build_summary_section(metadata, self.styles, self.content_style)
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)


class BuildScopeSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()
        self.aws = {**self.fixture["metadata"], "cloud_service_provider": 2}
        self.azure = {**self.fixture["metadata"], "cloud_service_provider": 1}

    def rows(self, metadata, provider_details):
        content = build_scope_section(
            metadata, provider_details, self.styles, self.content_style
        )
        tables = [item for item in content if isinstance(item, Table)]
        self.assertEqual(len(tables), 1)
        return [list(row) for row in tables[0]._cellvalues[1:]]

    def test_aws_scope_is_the_account_and_the_region(self):
        rows = self.rows(self.aws, AWS_DETAILS)

        self.assertEqual(
            rows, [["Account ID", "123456789012"], ["Region", "eu-central-1"]]
        )

    def test_aws_scope_leaves_out_an_account_that_is_not_known(self):
        details = {k: v for k, v in AWS_DETAILS.items() if k != "accountId"}

        self.assertEqual(self.rows(self.aws, details), [["Region", "eu-central-1"]])

    def test_a_missing_value_is_a_dash_not_na(self):
        self.assertEqual(self.rows(self.aws, {}), [["Region", "–"]])
        self.assertEqual(
            self.rows(self.azure, {}),
            [["Tenant ID", "–"], ["Subscription ID", "–"], ["Resource Group", "–"]],
        )

    def test_azure_scope_is_the_tenant_the_subscription_and_the_resource_group(self):
        rows = self.rows(self.azure, AZURE_DETAILS)

        self.assertEqual(
            rows,
            [
                ["Tenant ID", AZURE_DETAILS["tenantId"]],
                ["Subscription ID", AZURE_DETAILS["subscriptionId"]],
                ["Resource Group", "rg-leakcheck"],
            ],
        )

    def test_no_row_names_or_shows_a_credential(self):
        for metadata, details in ((self.aws, AWS_DETAILS), (self.azure, AZURE_DETAILS)):
            with self.subTest(provider=metadata["cloud_service_provider"]):
                rows = self.rows(metadata, details)
                for label, value in rows:
                    for word in ("Key", "Secret", "Client", "Token"):
                        self.assertNotIn(word, label)
                text = " ".join(map(str, sum(rows, [])))
                for name, fragment in leaked_fragments(details).items():
                    self.assertNotIn(fragment, text, name)

    def test_the_rows_agree_with_the_scope_on_page_two(self):
        # Both documents take the facts from scope_facts and name them the same way
        self.assertEqual(
            [row[0] for row in self.rows(self.azure, AZURE_DETAILS)],
            [
                BLOCKS["ev_tenant_id"],
                BLOCKS["ev_subscription_id"],
                BLOCKS["ev_resource_group"],
            ],
        )
        self.assertEqual(
            [row[0] for row in self.rows(self.aws, AWS_DETAILS)],
            [BLOCKS["ev_account_id"], BLOCKS["ev_region"]],
        )
        facts = dict(scope_facts(AWS_DETAILS, 2, None).rows)
        self.assertEqual(self.rows(self.aws, AWS_DETAILS)[0][1], facts["account"])

    def test_unknown_provider_returns_na(self):
        metadata = {**self.fixture["metadata"], "cloud_service_provider": 99}

        self.assertEqual(self.rows(metadata, {}), [["N/A", "N/A"]])


class BuildTfstateScopeSectionTests(unittest.TestCase):
    _SHA = "a3f1" + "b" * 56 + "9c2e"
    _LINEAGE = "121f3456-7d0f-a7be-ceb0-c91d44c6a689"

    def setUp(self):
        self.styles, self.content_style = _make_styles()
        self.fixture = build_report_fixture()

    def _rows(self, metadata, provider_details, tfstate_scope=None):
        content = build_scope_section(
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

    def test_live_mode_table_has_no_credential_rows(self):
        rows = self._rows(
            self.fixture["metadata"], self.fixture["provider_details"], None
        )

        self.assertEqual(list(rows), ["Region"])
        self.assertEqual(rows["Region"], "eu-central-1")


if __name__ == "__main__":
    unittest.main()
