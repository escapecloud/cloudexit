# tests/test_utils_report_egress.py
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reportlab.graphics.shapes import Drawing
from reportlab.platypus import PageBreak, Paragraph, Table

from tests.report_fixtures import stage_report_assets

from core.utils_egress import GIB
from core.utils_report_egress import (
    _build_allocation,
    _build_coverage_section,
    _build_data_landscape_section,
    _build_estimated_costs_section,
    _build_pricing_basis_section,
    _build_type_groups,
    _resolve_icon,
    build_fee_estimate,
    calculate_tiered_cost,
    format_fee,
    free_tier_limit_display,
    generate_egress_html_report,
    generate_egress_pdf_report,
    load_pricing,
)


def _price_row(
    csp, component, tier_from, tier_to, price, unit="GiB", zone="zone1", valid_to=None
):
    return {
        "id": 0,
        "csp": csp,
        "component": component,
        "zone": zone,
        "tier_from": tier_from,
        "tier_to": tier_to,
        "price_per_unit": price,
        "currency": "USD",
        "unit": unit,
        "valid_from": "2026-07-01",
        "valid_to": valid_to,
    }


AZURE_INTERNET_TIERS = [
    _price_row(1, "internet_egress", 0, 100, 0.0),
    _price_row(1, "internet_egress", 100, 10240, 0.087),
    _price_row(1, "internet_egress", 10240, 51200, 0.083),
    _price_row(1, "internet_egress", 51200, 153600, 0.07),
    _price_row(1, "internet_egress", 153600, None, 0.05),
]

SIMPLE_PRICING = [
    _price_row(1, "internet_egress", 0, 100, 0.0),
    _price_row(1, "internet_egress", 100, None, 0.1),
    _price_row(1, "archive_retrieval", 0, None, 0.02),
]

RENDER_PRICING = AZURE_INTERNET_TIERS + [
    _price_row(1, "archive_retrieval", 0, None, 0.02),
]


def _fake_load_data(table_name, db_path=None):
    if table_name == "egresspricing":
        return list(RENDER_PRICING)
    return []


def _row(name, label, category, size_bytes, resource_type="some/type", **overrides):
    row = {
        "id": f"/{name}",
        "name": name,
        "type": resource_type,
        "label": label,
        "category": category,
        "size_bytes": size_bytes,
        "size_unknown": False,
        "tier_bytes": None,
        "flags": [],
        "notes": [],
    }
    row.update(overrides)
    return row


def _totals(rows, unknown_count=0, archive_tier_bytes=0):
    return {
        "known_size_bytes": sum(
            row["size_bytes"] for row in rows if row["size_bytes"] is not None
        ),
        "archive_tier_bytes": archive_tier_bytes,
        "resources_discovered": len(rows),
        "resources_with_unknown_size": unknown_count,
    }


def _payload(rows, unknown_count=0, archive_tier_bytes=0):
    return {
        "meta": {
            "name": "Exit Assessment Test",
            "cloud_service_provider": 1,
            "exit_strategy": 3,
            "assessment_type": 1,
            "timestamp": "2026-07-12 18:45:08 UTC",
        },
        "data": {
            "resources": rows,
            "totals": _totals(rows, unknown_count, archive_tier_bytes),
        },
    }


class LoadPricingTests(unittest.TestCase):
    @staticmethod
    def _make_report_db(report_path, rows):
        db_dir = os.path.join(report_path, "data")
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(db_dir, "assessment.db"))
        conn.execute(
            "CREATE TABLE egresspricing (id INTEGER PRIMARY KEY, csp INTEGER, "
            "component TEXT, zone TEXT, tier_from REAL, tier_to REAL, "
            "price_per_unit REAL, currency TEXT, unit TEXT, valid_from TEXT, "
            "valid_to TEXT)"
        )
        conn.executemany(
            "INSERT INTO egresspricing (csp, component, zone, tier_from, tier_to, "
            "price_per_unit, currency, unit, valid_from, valid_to) "
            "VALUES (:csp, :component, :zone, :tier_from, :tier_to, "
            ":price_per_unit, :currency, :unit, :valid_from, :valid_to)",
            [{key: row[key] for key in row if key != "id"} for row in rows],
        )
        conn.commit()
        conn.close()

    def test_loads_only_active_rows_from_run_database(self):
        rows = SIMPLE_PRICING + [
            _price_row(1, "internet_egress", 100, None, 0.2, valid_to="2026-06-30"),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._make_report_db(tmp_dir, rows)
            loaded = load_pricing(tmp_dir)

        self.assertEqual(len(loaded), len(SIMPLE_PRICING))
        self.assertTrue(all(not row["valid_to"] for row in loaded))

    def test_falls_back_to_master_dataset_when_table_missing(self):
        with patch(
            "core.utils_report_egress.load_data",
            side_effect=[sqlite3.OperationalError("no such table"), SIMPLE_PRICING],
        ) as mock_load:
            loaded = load_pricing("/tmp/report")

        self.assertEqual(len(loaded), len(SIMPLE_PRICING))
        self.assertEqual(mock_load.call_count, 2)
        # The fallback call uses the master dataset default path.
        self.assertEqual(mock_load.call_args_list[1].kwargs, {})

    @unittest.skipUnless(
        os.path.exists("datasets/data.db"), "master dataset not downloaded"
    )
    def test_shipped_dataset_has_contiguous_zone1_ladders(self):
        from itertools import groupby

        from core.utils_db import load_data as db_load

        rows = [
            row
            for row in db_load("egresspricing", db_path="datasets/data.db")
            if not row["valid_to"]
        ]
        rows.sort(key=lambda r: (r["csp"], r["component"], r["zone"], r["tier_from"]))
        seen = set()
        for key, group in groupby(
            rows, key=lambda r: (r["csp"], r["component"], r["zone"])
        ):
            group = list(group)
            seen.add(key)
            self.assertEqual(group[0]["tier_from"], 0, key)
            for lower, upper in zip(group, group[1:]):
                self.assertEqual(lower["tier_to"], upper["tier_from"], key)
            self.assertIsNone(group[-1]["tier_to"], key)
        for csp in (1, 2):
            self.assertIn((csp, "internet_egress", "zone1"), seen)


class TieredCostTests(unittest.TestCase):
    def test_free_tier_under_100_gib(self):
        self.assertEqual(calculate_tiered_cost(50, AZURE_INTERNET_TIERS), 0.0)
        self.assertEqual(calculate_tiered_cost(100, AZURE_INTERNET_TIERS), 0.0)

    def test_multi_tib_crosses_tiers(self):
        # 20 TiB: 100 GiB free, 10,140 GiB @ 0.087, 10,240 GiB @ 0.083
        expected = 10140 * 0.087 + 10240 * 0.083
        self.assertAlmostEqual(
            calculate_tiered_cost(20 * 1024, AZURE_INTERNET_TIERS), expected
        )

    def test_unbounded_top_tier(self):
        expected = 10140 * 0.087 + 40960 * 0.083 + 102400 * 0.07 + 100 * 0.05
        self.assertAlmostEqual(
            calculate_tiered_cost(153700, AZURE_INTERNET_TIERS), expected
        )


class BuildFeeEstimateTests(unittest.TestCase):
    def test_prorates_internet_fee_and_adds_retrieval_per_resource(self):
        rows = [
            _row(
                "sa1",
                "Storage Account",
                "object",
                600 * GIB,
                tier_bytes={"Hot": 400 * GIB, "Archive": 200 * GIB},
            ),
            _row("disk1", "Managed Disk", "block", 400 * GIB),
            _row("db1", "SQL Database", "database", None, size_unknown=True),
        ]
        totals = _totals(rows, unknown_count=1, archive_tier_bytes=200 * GIB)

        total_fee, fees_by_id = build_fee_estimate(rows, totals, 1, SIMPLE_PRICING)

        # internet: (1000 - 100) * 0.1 = 90; retrieval: 200 * 0.02 = 4
        self.assertAlmostEqual(total_fee, 94.0)
        self.assertAlmostEqual(fees_by_id["/sa1"], 90 * 0.6 + 4.0)
        self.assertAlmostEqual(fees_by_id["/disk1"], 90 * 0.4)
        self.assertIsNone(fees_by_id["/db1"])

    def test_zero_bytes_produce_zero_fee(self):
        rows = [_row("vault1", "Backup Vault", "backup", None)]
        totals = _totals(rows)

        total_fee, fees_by_id = build_fee_estimate(rows, totals, 1, SIMPLE_PRICING)

        self.assertEqual(total_fee, 0.0)
        self.assertIsNone(fees_by_id["/vault1"])

    def test_format_fee(self):
        self.assertEqual(format_fee(None), "n/a")
        self.assertEqual(format_fee(0), "$0.00")
        self.assertEqual(format_fee(1732.1), "$1,732.10")

    def test_free_tier_limit_derived_from_pricing(self):
        self.assertEqual(free_tier_limit_display(SIMPLE_PRICING, 1), "100 GiB")
        self.assertIsNone(free_tier_limit_display(SIMPLE_PRICING, 2))


class PricingUnitAndZoneTests(unittest.TestCase):
    def test_units_follow_the_pricing_rows(self):
        prices = [
            _price_row(1, "internet_egress", 0, 100, 0.0, unit="GB"),
            _price_row(1, "internet_egress", 100, None, 0.1, unit="GB"),
        ]
        rows = [_row("sa1", "Storage Account", "object", 1000 * 10**9)]

        total_fee, _ = build_fee_estimate(rows, _totals(rows), 1, prices)

        # Decimal-GB ladder: (1000 GB - 100 GB free) * 0.1
        self.assertAlmostEqual(total_fee, 90.0)

    def test_other_zones_are_ignored(self):
        prices = SIMPLE_PRICING + [
            _price_row(1, "internet_egress", 100, None, 9.9, zone="zone3"),
        ]
        rows = [_row("sa1", "Storage Account", "object", 200 * GIB)]

        total_fee, _ = build_fee_estimate(rows, _totals(rows), 1, prices)

        self.assertAlmostEqual(total_fee, 10.0)

    def test_missing_provider_pricing_raises(self):
        rows = [_row("sa1", "Storage Account", "object", 200 * GIB)]

        with self.assertRaises(ValueError):
            build_fee_estimate(rows, _totals(rows), 2, SIMPLE_PRICING)


class ResolveIconTests(unittest.TestCase):
    def test_exact_match_from_resourcetype_table(self):
        lookup = {"microsoft.storage/storageaccounts": "/icons/azure/storage/sa.png"}

        icon = _resolve_icon("Microsoft.Storage/storageAccounts", lookup)

        self.assertEqual(icon, "/icons/azure/storage/sa.png")

    def test_trims_path_segments_until_match(self):
        lookup = {"microsoft.sql": "/icons/azure/databases/sql.png"}

        icon = _resolve_icon("Microsoft.Sql/servers/databases", lookup)

        self.assertEqual(icon, "/icons/azure/databases/sql.png")

    def test_known_gap_uses_hardcoded_fallback(self):
        icon = _resolve_icon("AWS.ec2.describe_volumes.Volumes", {})

        self.assertEqual(icon, "/icons/aws/Storage/Elastic-Block-Store.png")

    def test_unknown_type_uses_default_icon(self):
        icon = _resolve_icon("Vendor.Unknown/things", {})

        self.assertEqual(icon, "/icons/misc/no_image.png")


class AllocationSeriesTests(unittest.TestCase):
    def test_groups_known_bytes_by_category_in_fixed_order(self):
        rows = [
            _row("disk1", "Managed Disk", "block", 40 * GIB),
            _row("sa1", "Storage Account", "object", 60 * GIB),
            _row("db1", "SQL Database", "database", 10 * GIB),
            _row("vault1", "Recovery Services Vault", "backup", None),
            _row("db2", "SQL Database", "database", None, size_unknown=True),
        ]

        labels, values, colors = _build_allocation(rows)

        self.assertEqual(
            labels,
            ["Object Storage", "Block (allocated)", "Databases"],
        )
        self.assertEqual(values, [60 * GIB, 40 * GIB, 10 * GIB])
        self.assertEqual(len(colors), 3)

    def test_zero_categories_are_omitted(self):
        rows = [_row("sa1", "Storage Account", "object", 5 * GIB)]

        labels, values, _ = _build_allocation(rows)

        self.assertEqual(labels, ["Object Storage"])
        self.assertEqual(values, [5 * GIB])


class TypeGroupTests(unittest.TestCase):
    def test_groups_by_label_and_sorts_by_fee(self):
        rows = [
            _row("disk1", "Managed Disk", "block", 100 * GIB),
            _row("disk2", "Managed Disk", "block", 50 * GIB),
            _row("sa1", "Storage Account", "object", 400 * GIB),
            _row("vault1", "Recovery Services Vault", "backup", None),
        ]
        fees_by_id = {"/disk1": 10.0, "/disk2": 5.0, "/sa1": 40.0, "/vault1": None}

        groups = _build_type_groups(rows, fees_by_id, {})

        self.assertEqual(
            [group["label"] for group in groups],
            ["Storage Account", "Managed Disk", "Recovery Services Vault"],
        )
        disk_group = groups[1]
        self.assertEqual(disk_group["size_display"], "150.0 GiB")
        self.assertEqual(disk_group["fee_display"], "$15.00")
        self.assertEqual(len(disk_group["resources"]), 2)
        self.assertEqual(disk_group["resources"][0]["fee_display"], "$10.00")
        vault_group = groups[2]
        self.assertEqual(vault_group["size_display"], "n/a")
        self.assertEqual(vault_group["fee_display"], "n/a")
        self.assertIsNone(vault_group["resources"][0]["fee_display"])

    def test_flags_and_notes_become_resource_detail(self):
        rows = [
            _row(
                "disk1",
                "Managed Disk",
                "block",
                100 * GIB,
                flags=["allocated (upper bound)"],
                notes=["shared with vm-1"],
            ),
        ]

        groups = _build_type_groups(rows, {"/disk1": 1.0}, {})

        self.assertEqual(
            groups[0]["resources"][0]["detail"],
            "allocated (upper bound); shared with vm-1",
        )


class GenerateEgressHtmlReportTests(unittest.TestCase):
    def _generate(self, payload):
        with tempfile.TemporaryDirectory() as tmp_dir:
            json_path = os.path.join(tmp_dir, "egress_estimate.json")
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(payload, json_file)

            with patch(
                "core.utils_report_egress.load_data",
                side_effect=_fake_load_data,
            ):
                result = generate_egress_html_report(tmp_dir, json_path)

            html = None
            if result["success"]:
                self.assertEqual(
                    result["html_path"], os.path.join(tmp_dir, "egress.html")
                )
                with open(result["html_path"], encoding="utf-8") as html_file:
                    html = html_file.read()
        return result, html

    def test_renders_report_with_fees_table_and_charts(self):
        rows = [
            _row(
                "proddata",
                "Storage Account",
                "object",
                2048 * GIB,
                resource_type="Microsoft.Storage/storageAccounts",
            ),
            _row(
                "vm-osdisk",
                "Managed Disk",
                "block",
                512 * GIB,
                resource_type="Microsoft.Compute/disks",
                flags=["allocated (upper bound)"],
            ),
        ]
        result, html = self._generate(_payload(rows))

        self.assertTrue(result["success"], result["logs"])
        self.assertIn("Exit Assessment Test", html)
        normalized_html = " ".join(html.split())
        self.assertIn(
            "The Data Landscape &amp; Egress Estimation feature is currently in",
            html,
        )
        self.assertIn("mailto:beta@escapecloud.io", html)
        # (2560 - 100) GiB * 0.087 = $214.02 total internet egress fee
        self.assertIn("$214.02", html)
        self.assertIn("Estimated Egress Fee", html)
        self.assertIn("Estimated one-time charge to transfer the identified", html)
        self.assertIn(
            'any resources whose size could not be measured. A "+" means',
            normalized_html,
        )
        self.assertIn("lower-bound estimate.", html)
        self.assertIn("feesChart", html)
        self.assertIn("allocationChart", html)
        self.assertIn("proddata", html)
        self.assertIn("category-object", html)
        self.assertIn("allocated (upper bound)", html)
        self.assertNotIn("Estimates only", html)
        self.assertNotIn("At least", html)

    def test_unknown_sizes_add_plus_suffix_to_headlines(self):
        rows = [
            _row("sa1", "Storage Account", "object", 2048 * GIB),
            _row("db1", "SQL Database", "database", None, size_unknown=True),
        ]
        result, html = self._generate(_payload(rows, unknown_count=1))

        self.assertTrue(result["success"], result["logs"])
        # 2048 GiB known: (2048 - 100) * 0.087 = $169.48, "+" marks the lower bound
        self.assertIn("2.0 TiB+", html)
        self.assertIn("$169.48+", html)
        self.assertIn("Excludes 1 resource", html)
        self.assertIn("n/a", html)

    def test_free_tier_environment_shows_included_in_free_tier_card(self):
        rows = [_row("sa1", "Storage Account", "object", 5 * GIB)]
        result, html = self._generate(_payload(rows))

        self.assertTrue(result["success"], result["logs"])
        self.assertIn("Included in Free Tier", html)
        # The fee headline stays visible above the card, as a clean "$0".
        self.assertIn("Estimated Egress Fee", html)
        self.assertIn('<div class="count">$0</div>', html)
        self.assertIn("5.0 GiB", html)
        self.assertIn("100 GiB free tier", html)
        self.assertNotIn("No fee estimate available.", html)
        self.assertNotIn("feesChart", html)

    def test_empty_inventory_renders_empty_states(self):
        result, html = self._generate(_payload([]))

        self.assertTrue(result["success"], result["logs"])
        self.assertIn("$0.00", html)
        # Zero known bytes is the empty state, not the free-tier success card.
        self.assertIn("No fee estimate available.", html)
        self.assertNotIn("Included in Free Tier", html)
        self.assertIn("No data volume available.", html)
        self.assertIn(
            "No data-bearing resources were discovered during the",
            html,
        )
        self.assertNotIn("allocationChart", html)

    def test_missing_json_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = generate_egress_html_report(
                tmp_dir, os.path.join(tmp_dir, "missing.json")
            )

        self.assertFalse(result["success"])


def _make_pdf_styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    content_style = ParagraphStyle(
        "ContentStyle", fontSize=10, leading=12, spaceAfter=10
    )
    styles["Heading1"].textColor = HexColor("#112726")
    styles["Heading2"].textColor = HexColor("#112726")
    return styles, content_style


class BuildEstimatedCostsSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_pdf_styles()
        self.type_groups = [
            {
                "label": "Storage Account",
                "fee": 180.0,
                "fee_display": "$180.00",
            },
            {
                "label": "Managed Disk",
                "fee": 34.02,
                "fee_display": "$34.02",
            },
            {
                "label": "Backup Vault",
                "fee": None,
                "fee_display": "n/a",
            },
        ]

    def _totals(self, known, unknown_count=0):
        return {
            "known_size_bytes": known,
            "archive_tier_bytes": 0,
            "resources_discovered": 1,
            "resources_with_unknown_size": unknown_count,
        }

    def _tables(self, content):
        tables = [f for f in content if isinstance(f, Table)]
        self.assertGreaterEqual(len(tables), 2)
        return tables

    def test_normal_fee_shell(self):
        content = _build_estimated_costs_section(
            214.02,
            self._totals(2560 * GIB),
            False,
            "100 GiB",
            self.type_groups,
            self.styles,
            self.content_style,
        )

        tables = self._tables(content)
        self.assertEqual(tables[0]._cellvalues[1][0].text, "$214.02")
        self.assertIsInstance(tables[1]._cellvalues[0][0], Drawing)
        breakdown = tables[-1]._cellvalues
        self.assertEqual(breakdown[1], ["Storage Account", "$180.00"])
        self.assertEqual(breakdown[2], ["Managed Disk", "$34.02"])
        self.assertEqual(breakdown[3], ["Backup Vault", "n/a (not sized)"])
        self.assertEqual(breakdown[-1][0].text, "Estimated Total Egress Fee")
        self.assertEqual(breakdown[-1][1].text, "$214.02")
        paragraphs = [f.text for f in content if isinstance(f, Paragraph)]
        self.assertTrue(any("Estimated Costs" in text for text in paragraphs))
        self.assertTrue(any("Cost Breakdown" in text for text in paragraphs))

    def test_unknowns_add_plus_suffix_to_fee(self):
        content = _build_estimated_costs_section(
            100.0,
            self._totals(2000 * GIB, unknown_count=2),
            False,
            "100 GiB",
            self.type_groups,
            self.styles,
            self.content_style,
        )

        tables = self._tables(content)
        self.assertEqual(tables[0]._cellvalues[1][0].text, "$100.00+")
        self.assertIn("Excludes 2 resources", tables[0]._cellvalues[2][0].text)
        paragraphs = [f.text for f in content if isinstance(f, Paragraph)]
        self.assertTrue(any("Estimated Costs" in text for text in paragraphs))

    def test_free_tier_variant(self):
        content = _build_estimated_costs_section(
            0.0,
            self._totals(5 * GIB),
            True,
            "100 GiB",
            [{"label": "Storage Account", "fee": 0.0, "fee_display": "$0.00"}],
            self.styles,
            self.content_style,
        )

        tables = self._tables(content)
        self.assertEqual(tables[0]._cellvalues[1][0].text, "$0")
        self.assertEqual(len(tables), 2)
        paragraphs = [f.text for f in content if isinstance(f, Paragraph)]
        self.assertTrue(any("100 GiB free tier" in text for text in paragraphs))


class BuildDataLandscapeSectionTests(unittest.TestCase):
    def test_table_rows_and_total(self):
        styles, content_style = _make_pdf_styles()
        type_groups = [
            {
                "label": "S3 Bucket",
                "icon": "/icons/aws/Storage/Simple-Storage-Service.png",
                "fee_display": "$200.00",
                "size_display": "20.0 MiB",
            },
            {
                "label": "Backup Vault",
                "icon": "/icons/aws/Storage/Backup.png",
                "fee_display": "n/a",
                "size_display": "n/a",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            content = _build_data_landscape_section(
                type_groups,
                "20.0 MiB+",
                tmp_dir,
                styles,
                content_style,
            )

        tables = [f for f in content if isinstance(f, Table)]
        self.assertEqual(len(tables), 1)
        cells = tables[0]._cellvalues
        # Costs column deliberately absent — dollars live on the Estimated
        # Costs page so each type is not shown twice in the report.
        self.assertEqual(cells[0], ["#", "Resource Type", "", "Size"])
        self.assertEqual(cells[1][1], "S3 Bucket")
        self.assertEqual(cells[1][3], "20.0 MiB")
        self.assertEqual(cells[2][3], "n/a")
        self.assertEqual(cells[-1][0], "Total")
        self.assertEqual(cells[-1][3], "20.0 MiB+")
        for row in cells:
            for cell in row:
                self.assertNotIn("$", str(cell))


class BuildCoverageSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_pdf_styles()

    def _cells(self, content):
        tables = [f for f in content if isinstance(f, Table)]
        self.assertEqual(len(tables), 1)
        return tables[0]._cellvalues

    def test_aws_coverage_rows_and_alpha_note(self):
        content = _build_coverage_section(2, self.styles, self.content_style)

        cells = self._cells(content)
        self.assertEqual(cells[0], ["Name", "Notes"])
        names = [row[0] for row in cells[1:]]
        self.assertIn("S3 Buckets", names)
        self.assertIn("AWS Backup Vaults", names)
        texts = [f.text for f in content if isinstance(f, Paragraph)]
        self.assertTrue(any(text.startswith("NOTE:") for text in texts))
        self.assertTrue(any("alpha version" in text for text in texts))
        # Estimated Costs starts on a fresh page after the coverage block.
        self.assertIsInstance(content[-1], PageBreak)

    def test_azure_coverage_rows(self):
        content = _build_coverage_section(1, self.styles, self.content_style)

        names = [row[0] for row in self._cells(content)[1:]]
        self.assertIn("Storage Accounts", names)
        self.assertIn("Recovery Services Vaults", names)

    def test_unknown_provider_renders_nothing(self):
        self.assertEqual(
            _build_coverage_section(9, self.styles, self.content_style), []
        )


class BuildPricingBasisSectionTests(unittest.TestCase):
    def setUp(self):
        self.styles, self.content_style = _make_pdf_styles()

    def _texts(self, content):
        return [f.text for f in content if isinstance(f, Paragraph)]

    def _table(self, content):
        tables = [f for f in content if isinstance(f, Table)]
        self.assertEqual(len(tables), 1)
        return tables[0]._cellvalues

    def test_renders_used_price_rows_with_free_tier(self):
        content = _build_pricing_basis_section(
            RENDER_PRICING, 1, self.styles, self.content_style
        )

        texts = self._texts(content)
        self.assertTrue(any("Appendix – Egress Prices" in text for text in texts))
        self.assertTrue(
            any(
                "Microsoft Azure list prices (Zone 1 – US/Europe, public "
                "internet routing):" in text
                for text in texts
            )
        )
        self.assertFalse(any("effective as of" in text for text in texts))
        cells = self._table(content)
        self.assertEqual(cells[0], ["#", "Component", "Tier", "Price"])
        self.assertEqual(cells[1][1], "Internet Egress")
        self.assertEqual(cells[1][2], "first 100 GiB / month")
        self.assertEqual(cells[1][3], "Free")
        self.assertEqual(cells[2][3], "$0.087 / GiB")
        self.assertEqual(cells[-1][1], "Archive Retrieval")
        self.assertEqual(cells[-1][2], "all volumes")
        self.assertEqual(cells[-1][3], "$0.02 / GiB")
        self.assertFalse(any("rehydration fees" in text for text in texts))
        self.assertTrue(
            any(
                "Source (Internet Egress): "
                "https://azure.microsoft.com/pricing/details/bandwidth/" in text
                for text in texts
            )
        )
        self.assertTrue(
            any(
                "Source (Archive Retrieval): "
                "https://azure.microsoft.com/pricing/details/storage/blobs/" in text
                for text in texts
            )
        )
        self.assertTrue(
            any("List prices at the time of the assessment" in text for text in texts)
        )

    def test_missing_retrieval_rows_add_note(self):
        content = _build_pricing_basis_section(
            AZURE_INTERNET_TIERS, 1, self.styles, self.content_style
        )

        texts = self._texts(content)
        self.assertTrue(
            any("rehydration fees are not included" in text for text in texts)
        )
        self.assertTrue(
            any(
                "Source: https://azure.microsoft.com/pricing/details/bandwidth/" in text
                for text in texts
            )
        )
        self.assertFalse(any("storage/blobs" in text for text in texts))

    def test_unused_zone_component_and_provider_rows_are_excluded(self):
        prices = RENDER_PRICING + [
            _price_row(1, "internet_egress", 0, None, 9.9, zone="zone3"),
            _price_row(1, "internet_egress_transit_isp", 0, None, 9.9),
            _price_row(2, "internet_egress", 0, None, 9.9),
        ]

        content = _build_pricing_basis_section(
            prices, 1, self.styles, self.content_style
        )

        cells = self._table(content)
        self.assertEqual(len(cells) - 1, len(RENDER_PRICING))

    def test_mixed_valid_from_adds_column_without_effective_date_claim(self):
        retrieval = dict(
            _price_row(1, "archive_retrieval", 0, None, 0.02),
            valid_from="2026-07-14",
        )
        prices = AZURE_INTERNET_TIERS + [retrieval]

        content = _build_pricing_basis_section(
            prices, 1, self.styles, self.content_style
        )

        texts = self._texts(content)
        self.assertFalse(any("effective as of" in text for text in texts))
        cells = self._table(content)
        self.assertEqual(cells[0][-1], "Valid from")
        self.assertEqual(cells[-1][-1], "2026-07-14")

    def test_empty_pricing_renders_nothing(self):
        self.assertEqual(
            _build_pricing_basis_section([], 1, self.styles, self.content_style), []
        )


class GenerateEgressPdfReportTests(unittest.TestCase):
    _PROVIDER_DETAILS = {
        "tenantId": "tenant-id",
        "subscriptionId": "sub-id",
        "resourceGroupName": "my-rg",
    }

    def _generate(self, payload):
        with tempfile.TemporaryDirectory() as tmp_dir:
            stage_report_assets(tmp_dir)
            json_path = os.path.join(tmp_dir, "egress_estimate.json")
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(payload, json_file)

            with patch(
                "core.utils_report_egress.load_data",
                side_effect=_fake_load_data,
            ):
                result = generate_egress_pdf_report(
                    tmp_dir, json_path, self._PROVIDER_DETAILS
                )

            pdf_size = 0
            if result["success"]:
                self.assertEqual(
                    result["pdf_path"], os.path.join(tmp_dir, "egress.pdf")
                )
                pdf_size = os.path.getsize(result["pdf_path"])
        return result, pdf_size

    def test_generates_pdf_document(self):
        rows = [
            _row("proddata", "Storage Account", "object", 2048 * GIB),
            _row(
                "vm-osdisk",
                "Managed Disk",
                "block",
                512 * GIB,
                flags=["allocated (upper bound)"],
            ),
            _row("vault1", "Recovery Services Vault", "backup", None),
        ]
        result, pdf_size = self._generate(_payload(rows))

        self.assertTrue(result["success"], result["logs"])
        self.assertGreater(pdf_size, 0)

    def test_free_tier_footprint_generates_pdf(self):
        rows = [_row("sa1", "Storage Account", "object", 5 * GIB)]
        result, pdf_size = self._generate(_payload(rows))

        self.assertTrue(result["success"], result["logs"])
        self.assertGreater(pdf_size, 0)

    def test_missing_json_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = generate_egress_pdf_report(
                tmp_dir, os.path.join(tmp_dir, "missing.json"), {}
            )

        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
