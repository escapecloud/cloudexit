# tests/test_utils_egress.py
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.utils_egress import (
    GIB,
    compute_totals,
    estimate_egress,
    format_bytes,
    new_row,
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


class EstimateEgressDispatchTests(unittest.TestCase):
    def _run(self, cloud_service_provider, provider_details):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = estimate_egress(
                cloud_service_provider,
                provider_details,
                tmp_dir,
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
                    os.path.join(tmp_dir, "egress_estimate.json"),
                )
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
