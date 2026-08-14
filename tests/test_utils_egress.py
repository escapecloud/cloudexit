# tests/test_utils_egress.py
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.utils_egress import (
    GIB,
    compute_totals,
    estimate_egress,
    format_bytes,
    new_row,
    read_egress_inventory,
    write_egress_inventory,
)

# Same envelope as the assessment JSON report (see generate_json_report).
EXPECTED_META_KEYS = [
    "assessment_type",
    "cloud_service_provider",
    "exit_strategy",
    "name",
    "timestamp",
]
EXPECTED_DATA_KEYS = ["resources", "totals"]


def _sample_rows():
    storage = new_row("/sa1", "sa1", "some/type", "Storage", "object")
    storage["size_bytes"] = 60 * GIB
    storage["tier_bytes"] = {"Hot": 50 * GIB, "Archive": 10 * GIB}
    disk = new_row("/disk1", "disk1", "some/type", "Disk", "block")
    disk["size_bytes"] = 40 * GIB
    unknown = new_row("/db1", "db1", "some/type", "Database", "database")
    unknown["size_unknown"] = True
    return [storage, disk, unknown]


class FormatBytesTests(unittest.TestCase):
    def test_formats_binary_units_and_none(self):
        self.assertEqual(format_bytes(None), "n/a")
        self.assertEqual(format_bytes(512), "512.0 B")
        self.assertEqual(format_bytes(GIB), "1.0 GiB")
        self.assertEqual(format_bytes(1536 * GIB), "1.5 TiB")


class ComputeTotalsTests(unittest.TestCase):
    def test_sums_known_sizes_archive_tiers_and_unknowns(self):
        totals = compute_totals(_sample_rows(), archive_tiers={"Archive"})

        self.assertEqual(totals["known_size_bytes"], 100 * GIB)
        self.assertEqual(totals["archive_tier_bytes"], 10 * GIB)
        self.assertEqual(totals["resources_discovered"], 3)
        self.assertEqual(totals["resources_with_unknown_size"], 1)

    def test_archive_tier_set_controls_what_counts_as_archive(self):
        rows = [new_row("/b1", "b1", "some/type", "Bucket", "object")]
        rows[0]["size_bytes"] = 30 * GIB
        rows[0]["tier_bytes"] = {
            "Standard": 10 * GIB,
            "Glacier": 15 * GIB,
            "Deep Archive": 5 * GIB,
        }

        totals = compute_totals(rows, archive_tiers={"Glacier", "Deep Archive"})

        self.assertEqual(totals["archive_tier_bytes"], 20 * GIB)


# The sample rows all share one type; one catalogue entry covers them.
_SAMPLE_REGISTRY = {
    "some/type": {
        "resource_type_id": 1,
        "category": "object",
        "label": "Storage",
        "strategy": "whatever",
    }
}


_EGRESS_TABLES = """
CREATE TABLE egress_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, resource_type INTEGER NOT NULL,
    name TEXT NOT NULL, size_bytes INTEGER,
    size_unknown INTEGER NOT NULL DEFAULT 0, flags TEXT, notes TEXT);
CREATE TABLE egress_inventory_tier (
    id INTEGER PRIMARY KEY AUTOINCREMENT, egress_inventory_id INTEGER NOT NULL,
    tier TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    is_archive INTEGER NOT NULL DEFAULT 0);
"""


class EgressInventoryRoundTripTests(unittest.TestCase):
    _CATALOGUE = """
CREATE TABLE resourcetype (
    id INTEGER PRIMARY KEY, csp INTEGER NOT NULL, code TEXT NOT NULL,
    name TEXT NOT NULL, icon TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE resourcetype_data (
    id INTEGER PRIMARY KEY, resource_type INTEGER NOT NULL,
    data_category TEXT NOT NULL, strategy TEXT NOT NULL, params TEXT,
    status TEXT NOT NULL);
"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "assessment.db")
        conn = sqlite3.connect(self.db_path)
        conn.executescript(self._CATALOGUE + _EGRESS_TABLES)
        conn.execute(
            "INSERT INTO resourcetype VALUES (7, 1, 'some/type', 'Storage', 'i', 't')"
        )
        conn.execute(
            "INSERT INTO resourcetype_data VALUES (1, 7, 'object', 's', '{}', 't')"
        )
        conn.commit()
        conn.close()

    def _round_trip(self, rows, archive_tiers):
        write_egress_inventory(rows, {"some/type": 7}, archive_tiers, self.db_path)
        return read_egress_inventory(self.db_path)

    def test_rows_survive_storage_unchanged(self):
        original = _sample_rows()

        stored, _ = self._round_trip(original, {"Archive"})

        self.assertEqual(len(stored), len(original))
        for before, after in zip(original, stored):
            for field in (
                "name",
                "size_bytes",
                "size_unknown",
                "tier_bytes",
                "flags",
                "notes",
            ):
                self.assertEqual(after[field], before[field], field)

    def test_label_and_category_come_back_from_the_catalogue(self):
        stored, _ = self._round_trip(_sample_rows(), set())

        self.assertEqual(stored[0]["label"], "Storage")
        self.assertEqual(stored[0]["category"], "object")
        self.assertEqual(stored[0]["type"], "some/type")

    def test_totals_are_identical_before_and_after_storage(self):
        original = _sample_rows()
        archive_tiers = {"Archive"}

        stored, stored_tiers = self._round_trip(original, archive_tiers)

        # What the report headlines show must not change with the store.
        self.assertEqual(
            compute_totals(stored, stored_tiers),
            compute_totals(original, archive_tiers),
        )

    def test_is_archive_marks_only_the_archive_tiers(self):
        _, stored_tiers = self._round_trip(_sample_rows(), {"Archive"})

        self.assertEqual(stored_tiers, {"Archive"})

    def test_row_without_a_catalogue_entry_warns_and_is_skipped(self):
        orphan = new_row("/x", "x", "unmapped/type", "Nope", "object")

        with self.assertLogs("core.engine.egress", level="WARNING") as captured:
            write_egress_inventory([orphan], {"some/type": 7}, set(), self.db_path)

        rows, _ = read_egress_inventory(self.db_path)
        self.assertEqual(rows, [])
        self.assertIn("unmapped/type", captured.output[0])


class EstimateEgressDispatchTests(unittest.TestCase):
    def _run(self, cloud_service_provider, provider_details):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_dir = os.path.join(tmp_dir, "data")
            os.makedirs(db_dir)
            conn = sqlite3.connect(os.path.join(db_dir, "assessment.db"))
            conn.executescript(_EGRESS_TABLES)
            conn.commit()
            conn.close()

            with patch(
                "core.utils_egress.load_egress_registry",
                return_value=_SAMPLE_REGISTRY,
            ):
                result = estimate_egress(
                    cloud_service_provider,
                    provider_details,
                    tmp_dir,
                    report_path=tmp_dir,
                    name="Exit Assessment Test",
                    exit_strategy=3,
                    assessment_type=1,
                )
            payload = None
            if result["success"]:
                with open(result["json_path"], encoding="utf-8") as json_file:
                    payload = json.load(json_file)
                self.assertEqual(
                    result["json_path"],
                    os.path.join(tmp_dir, "egress_inventory_raw_data.json"),
                )
                stored = (
                    sqlite3.connect(os.path.join(tmp_dir, "data", "assessment.db"))
                    .execute("SELECT COUNT(*) FROM egress_inventory")
                    .fetchone()[0]
                )
                self.assertEqual(stored, len(payload["data"]["resources"]))
        return result, payload

    @patch("core.utils_egress_azure.collect_azure_egress")
    def test_azure_dispatch_and_json_schema(self, mock_collect):
        mock_collect.return_value = (_sample_rows(), {"Archive"})

        result, payload = self._run(1, {"any": "details"})

        self.assertTrue(result["success"])
        mock_collect.assert_called_once_with({"any": "details"})
        # Same meta/data envelope as the assessment JSON report; cost
        # scenarios and findings belong to the Platform offering and must
        # not leak into the JSON output.
        self.assertEqual(sorted(payload.keys()), ["data", "meta"])
        self.assertEqual(sorted(payload["meta"].keys()), EXPECTED_META_KEYS)
        self.assertEqual(sorted(payload["data"].keys()), EXPECTED_DATA_KEYS)
        self.assertEqual(payload["meta"]["name"], "Exit Assessment Test")
        self.assertEqual(payload["meta"]["cloud_service_provider"], 1)
        self.assertEqual(payload["meta"]["exit_strategy"], 3)
        self.assertEqual(payload["meta"]["assessment_type"], 1)
        self.assertEqual(payload["data"]["totals"]["known_size_bytes"], 100 * GIB)
        self.assertEqual(payload["data"]["totals"]["archive_tier_bytes"], 10 * GIB)

    @patch("core.utils_egress_azure.collect_azure_egress")
    def test_json_row_shape(self, mock_collect):
        rows = _sample_rows()
        mock_collect.return_value = (rows, {"Archive"})

        _, payload = self._run(1, {"any": "details"})

        resource = payload["data"]["resources"][0]
        self.assertEqual(
            list(resource),
            [
                "id",
                "name",
                "code",
                "category",
                "size_bytes",
                "size_unknown",
                "tier_bytes",
                "flags",
                "notes",
            ],
        )
        self.assertEqual(resource["id"], "/sa1")
        self.assertEqual(resource["name"], "sa1")
        # "type" is published as "code"; the label lives in the catalogue.
        self.assertEqual(resource["code"], "some/type")
        self.assertNotIn("label", resource)
        self.assertNotIn("type", resource)
        self.assertEqual(resource["tier_bytes"], {"Hot": 50 * GIB, "Archive": 10 * GIB})

    @patch("core.utils_egress_aws.collect_aws_egress")
    def test_aws_dispatch_and_json_schema(self, mock_collect):
        mock_collect.return_value = (_sample_rows(), {"Archive"})

        result, payload = self._run(2, {"region": "eu-central-1"})

        self.assertTrue(result["success"])
        mock_collect.assert_called_once_with({"region": "eu-central-1"})
        self.assertEqual(sorted(payload.keys()), ["data", "meta"])
        self.assertEqual(sorted(payload["meta"].keys()), EXPECTED_META_KEYS)
        self.assertEqual(sorted(payload["data"].keys()), EXPECTED_DATA_KEYS)
        self.assertEqual(payload["meta"]["cloud_service_provider"], 2)

    def test_unsupported_provider_fails_without_raising(self):
        result, payload = self._run(3, {})

        self.assertFalse(result["success"])
        self.assertIn("Unsupported cloud service provider", result["logs"])
        self.assertIsNone(payload)

    @patch("core.utils_egress_azure.collect_azure_egress")
    def test_collection_error_returns_failure(self, mock_collect):
        mock_collect.side_effect = RuntimeError("enumeration failed")

        result, payload = self._run(1, {})

        self.assertFalse(result["success"])
        self.assertEqual(result["logs"], "enumeration failed")


if __name__ == "__main__":
    unittest.main()
