# tests/test_utils_egress_azure.py
import unittest
from unittest.mock import MagicMock, patch

import requests

from core.utils_egress import GIB, format_bytes
from core.utils_egress_azure import (
    build_egress_inventory,
    collect_azure_egress,
    fetch_monitor_metrics,
    filter_data_bearing_resources,
)


def _mock_resource(resource_type, name, resource_id, sku_name=None):
    resource = MagicMock()
    resource.type = resource_type
    resource.name = name
    resource.id = resource_id
    if sku_name:
        resource.sku = MagicMock()
        resource.sku.name = sku_name
    else:
        resource.sku = None
    return resource


def _mock_credential():
    credential = MagicMock()
    credential.get_token.return_value = MagicMock(token="fake-token")
    return credential


class RegistryFilteringTests(unittest.TestCase):
    def test_picks_data_bearing_types_out_of_mixed_list(self):
        resources = [
            _mock_resource("Microsoft.Storage/storageAccounts", "sa1", "/sa1"),
            _mock_resource("Microsoft.Network/virtualNetworks", "vnet1", "/vnet1"),
            _mock_resource("Microsoft.Compute/disks", "disk1", "/disk1"),
            _mock_resource("Microsoft.Compute/virtualMachines", "vm1", "/vm1"),
            _mock_resource("Microsoft.RecoveryServices/vaults", "vault1", "/vault1"),
        ]

        matched = filter_data_bearing_resources(resources)
        matched_names = [resource.name for resource, _ in matched]

        self.assertEqual(matched_names, ["sa1", "disk1", "vault1"])

    def test_matching_is_case_insensitive(self):
        resources = [
            _mock_resource("MICROSOFT.STORAGE/StorageAccounts", "sa1", "/sa1"),
        ]

        matched = filter_data_bearing_resources(resources)

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1]["strategy"], "storage_account_metrics")

    def test_returns_empty_for_no_data_bearing_resources(self):
        resources = [
            _mock_resource("Microsoft.Network/networkInterfaces", "nic1", "/nic1"),
        ]

        self.assertEqual(filter_data_bearing_resources(resources), [])


class FetchMonitorMetricsTests(unittest.TestCase):
    @patch("core.utils_egress_azure.requests.get")
    def test_parses_normal_response_using_latest_datapoint(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "value": [
                {
                    "name": {"value": "UsedCapacity"},
                    "timeseries": [
                        {
                            "metadatavalues": [],
                            "data": [
                                {"average": 50.0},
                                {"average": 123.0},
                                {"average": None},
                            ],
                        }
                    ],
                }
            ]
        }
        mock_get.return_value = mock_response

        result = fetch_monitor_metrics(_mock_credential(), "/sa1", ["UsedCapacity"])

        self.assertEqual(
            result, {"UsedCapacity": [{"dimension": None, "value": 123.0}]}
        )

    @patch("core.utils_egress_azure.requests.get")
    def test_parses_dimension_split_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "value": [
                {
                    "name": {"value": "BlobCapacity"},
                    "timeseries": [
                        {
                            "metadatavalues": [
                                {"name": {"value": "tier"}, "value": "Hot"}
                            ],
                            "data": [{"average": 10.0}],
                        },
                        {
                            "metadatavalues": [
                                {"name": {"value": "tier"}, "value": "Archive"}
                            ],
                            "data": [{"average": 20.0}],
                        },
                    ],
                }
            ]
        }
        mock_get.return_value = mock_response

        result = fetch_monitor_metrics(
            _mock_credential(),
            "/sa1/blobServices/default",
            ["BlobCapacity"],
            dimension="Tier",
        )

        self.assertEqual(
            result,
            {
                "BlobCapacity": [
                    {"dimension": "Hot", "value": 10.0},
                    {"dimension": "Archive", "value": 20.0},
                ]
            },
        )
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["$filter"], "Tier eq '*'")

    @patch("core.utils_egress_azure.requests.get")
    def test_handles_empty_series_without_treating_as_zero(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "value": [{"name": {"value": "storage"}, "timeseries": []}]
        }
        mock_get.return_value = mock_response

        result = fetch_monitor_metrics(_mock_credential(), "/db1", ["storage"])

        self.assertEqual(result, {"storage": []})

    @patch("core.utils_egress_azure.requests.get")
    def test_handles_missing_datapoints_without_treating_as_zero(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "value": [
                {
                    "name": {"value": "storage"},
                    "timeseries": [{"metadatavalues": [], "data": [{"average": None}]}],
                }
            ]
        }
        mock_get.return_value = mock_response

        result = fetch_monitor_metrics(_mock_credential(), "/db1", ["storage"])

        self.assertEqual(result, {"storage": []})

    @patch("core.utils_egress_azure.requests.get")
    def test_returns_none_on_http_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403")
        mock_get.return_value = mock_response

        result = fetch_monitor_metrics(_mock_credential(), "/sa1", ["UsedCapacity"])

        self.assertIsNone(result)

    @patch("core.utils_egress_azure.requests.get")
    def test_returns_none_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("network down")

        result = fetch_monitor_metrics(_mock_credential(), "/sa1", ["UsedCapacity"])

        self.assertIsNone(result)


class StorageAccountTierSplitTests(unittest.TestCase):
    @patch("core.utils_egress_azure.fetch_monitor_metrics")
    def test_hot_cool_archive_split_and_archive_flag(self, mock_fetch):
        mock_fetch.side_effect = [
            {"UsedCapacity": [{"dimension": None, "value": float(60 * GIB)}]},
            {
                "BlobCapacity": [
                    {"dimension": "Hot", "value": float(30 * GIB)},
                    {"dimension": "Cool", "value": float(20 * GIB)},
                    {"dimension": "Archive", "value": float(10 * GIB)},
                ]
            },
        ]
        resource = _mock_resource(
            "Microsoft.Storage/storageAccounts", "sa1", "/sa1", sku_name="Standard_LRS"
        )

        rows, findings = build_egress_inventory(MagicMock(), MagicMock(), [resource])

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["size_bytes"], 60 * GIB)
        self.assertEqual(
            row["tier_bytes"],
            {"Hot": 30 * GIB, "Cool": 20 * GIB, "Archive": 10 * GIB},
        )
        self.assertTrue(any("Archive" in flag for flag in row["flags"]))
        self.assertEqual(findings, [])

    @patch("core.utils_egress_azure.fetch_monitor_metrics")
    def test_geo_replicated_sku_adds_informational_finding(self, mock_fetch):
        mock_fetch.side_effect = [
            {"UsedCapacity": [{"dimension": None, "value": float(GIB)}]},
            {"BlobCapacity": []},
        ]
        resource = _mock_resource(
            "Microsoft.Storage/storageAccounts",
            "sa1",
            "/sa1",
            sku_name="Standard_RAGRS",
        )

        rows, findings = build_egress_inventory(MagicMock(), MagicMock(), [resource])

        self.assertTrue(any("geo-replicated" in note for note in rows[0]["notes"]))
        info_findings = [f for f in findings if f["severity"] == "info"]
        self.assertEqual(len(info_findings), 1)
        self.assertIn("not additional data to egress", info_findings[0]["message"])

    @patch("core.utils_egress_azure.fetch_monitor_metrics", return_value=None)
    def test_no_datapoints_records_unknown_size(self, mock_fetch):
        resource = _mock_resource("Microsoft.Storage/storageAccounts", "sa1", "/sa1")

        rows, findings = build_egress_inventory(MagicMock(), MagicMock(), [resource])

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertTrue(rows[0]["size_unknown"])
        self.assertEqual(format_bytes(rows[0]["size_bytes"]), "n/a")
        self.assertTrue(any("size unknown" in f["message"] for f in findings))


class AllocatedSizeTests(unittest.TestCase):
    def test_disk_size_read_from_property_and_flagged_as_upper_bound(self):
        resource = _mock_resource("Microsoft.Compute/disks", "disk1", "/disk1")
        resource_client = MagicMock()
        full_resource = MagicMock()
        full_resource.properties = {"diskSizeGB": 128}
        resource_client.resources.get_by_id.return_value = full_resource

        rows, findings = build_egress_inventory(
            MagicMock(), resource_client, [resource]
        )

        self.assertEqual(rows[0]["size_bytes"], 128 * GIB)
        self.assertIn("allocated (upper bound)", rows[0]["flags"])
        self.assertEqual(findings, [])

    def test_collector_error_is_contained_as_finding(self):
        resource = _mock_resource("Microsoft.Compute/disks", "disk1", "/disk1")
        resource_client = MagicMock()
        resource_client.resources.get_by_id.side_effect = RuntimeError("api error")

        rows, findings = build_egress_inventory(
            MagicMock(), resource_client, [resource]
        )

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertIn("size unavailable", rows[0]["flags"])
        self.assertTrue(any("size lookup failed" in f["message"] for f in findings))


class VaultDetectionTests(unittest.TestCase):
    @patch("core.utils_egress_azure.fetch_monitor_metrics")
    def test_vault_produces_warning_finding_and_no_size(self, mock_fetch):
        resource = _mock_resource("Microsoft.RecoveryServices/vaults", "vault1", "/v1")
        resource_client = MagicMock()

        rows, findings = build_egress_inventory(
            MagicMock(), resource_client, [resource]
        )

        self.assertIsNone(rows[0]["size_bytes"])
        self.assertFalse(rows[0]["size_unknown"])
        warnings = [f for f in findings if f["severity"] == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("cannot be exported from Azure", warnings[0]["message"])
        mock_fetch.assert_not_called()
        resource_client.resources.get_by_id.assert_not_called()


class CollectAzureEgressTests(unittest.TestCase):
    @patch("core.utils_egress_azure.fetch_monitor_metrics")
    @patch("core.utils_egress_azure.ResourceManagementClient")
    def test_returns_rows_and_archive_tiers(self, mock_rmc_cls, mock_fetch):
        mock_fetch.side_effect = [
            {"UsedCapacity": [{"dimension": None, "value": float(10 * GIB)}]},
            {"BlobCapacity": []},
        ]
        resource = _mock_resource("Microsoft.Storage/storageAccounts", "sa1", "/sa1")
        mock_client = MagicMock()
        mock_client.resources.list_by_resource_group.return_value = [resource]
        mock_rmc_cls.return_value = mock_client

        rows, archive_tiers = collect_azure_egress(
            {
                "credential": _mock_credential(),
                "subscriptionId": "sub-id",
                "resourceGroupName": "rg",
            }
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "sa1")
        self.assertEqual(rows[0]["size_bytes"], 10 * GIB)
        self.assertNotIn("findings", rows[0])
        self.assertEqual(archive_tiers, {"Archive"})


if __name__ == "__main__":
    unittest.main()
