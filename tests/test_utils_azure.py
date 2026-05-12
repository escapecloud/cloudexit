# tests/test_utils_azure.py
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from azure.core.exceptions import AzureError, ClientAuthenticationError

from core.utils_azure import (
    get_missing_months_azure,
    is_resource_inventory_empty,
)


class GetMissingMonthsAzureTests(unittest.TestCase):
    def test_returns_missing_months(self):
        today = date.today()
        # Provide 3 months, expect 3 missing from the 6-month window
        processed = {
            today.replace(day=1).isoformat(),
        }
        missing = get_missing_months_azure(processed, 6)
        self.assertEqual(len(missing), 5)

    def test_returns_empty_when_all_present(self):
        from dateutil.relativedelta import relativedelta

        today = date.today()
        start = today.replace(day=1) - relativedelta(months=5)
        processed = set()
        for i in range(6):
            m = (start + relativedelta(months=i)).replace(day=1)
            processed.add(m.isoformat())

        missing = get_missing_months_azure(processed, 6)
        self.assertEqual(len(missing), 0)

    def test_handles_timestamp_format(self):
        from dateutil.relativedelta import relativedelta

        today = date.today()
        start = today.replace(day=1) - relativedelta(months=5)
        processed = set()
        for i in range(6):
            m = (start + relativedelta(months=i)).replace(day=1)
            processed.add(f"{m.isoformat()}T00:00:00")

        missing = get_missing_months_azure(processed, 6)
        self.assertEqual(len(missing), 0)

    def test_returns_all_when_none_processed(self):
        missing = get_missing_months_azure(set(), 6)
        self.assertEqual(len(missing), 6)


class IsResourceInventoryEmptyTests(unittest.TestCase):
    @patch("core.utils_azure.ResourceManagementClient")
    def test_returns_true_when_no_resources(self, mock_rmc_cls):
        mock_client = MagicMock()
        mock_rmc_cls.return_value = mock_client
        mock_client.resources.list_by_resource_group.return_value = iter([])

        result = is_resource_inventory_empty(MagicMock(), "sub-123", "rg-test")
        self.assertTrue(result)

    @patch("core.utils_azure.ResourceManagementClient")
    def test_returns_false_when_resources_exist(self, mock_rmc_cls):
        mock_client = MagicMock()
        mock_rmc_cls.return_value = mock_client
        mock_resource = MagicMock()
        mock_client.resources.list_by_resource_group.return_value = iter(
            [mock_resource]
        )

        result = is_resource_inventory_empty(MagicMock(), "sub-123", "rg-test")
        self.assertFalse(result)

    @patch("core.utils_azure.ResourceManagementClient")
    def test_raises_on_azure_error(self, mock_rmc_cls):
        mock_client = MagicMock()
        mock_rmc_cls.return_value = mock_client
        mock_client.resources.list_by_resource_group.side_effect = AzureError(
            "Connection failed"
        )

        with self.assertRaises(AzureError):
            is_resource_inventory_empty(MagicMock(), "sub-123", "rg-test")


class BuildAzureResourceInventoryErrorTests(unittest.TestCase):
    @patch("core.utils_azure.is_resource_inventory_empty")
    @patch("core.utils_azure.ClientSecretCredential")
    def test_auth_error_is_reraised(self, mock_cred_cls, mock_empty_check):
        mock_cred_cls.side_effect = ClientAuthenticationError(
            message="Invalid credentials"
        )

        from core.utils_azure import build_azure_resource_inventory

        with self.assertRaises(ClientAuthenticationError):
            build_azure_resource_inventory(
                1,
                {
                    "tenantId": "t",
                    "clientId": "c",
                    "clientSecret": "s",
                    "subscriptionId": "sub",
                    "resourceGroupName": "rg",
                },
                "/fake/report",
                "/fake/raw",
            )

    @patch("core.utils_azure.is_resource_inventory_empty", return_value=True)
    @patch("core.utils_azure.ClientSecretCredential")
    def test_returns_early_when_inventory_empty(self, mock_cred_cls, mock_empty_check):
        from core.utils_azure import build_azure_resource_inventory

        # Should not raise, returns early
        result = build_azure_resource_inventory(
            1,
            {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "subscriptionId": "sub",
                "resourceGroupName": "rg",
            },
            "/fake/report",
            "/fake/raw",
        )
        self.assertIsNone(result)
        mock_empty_check.assert_called_once()


class BuildAzureCostInventoryErrorTests(unittest.TestCase):
    @patch("core.utils_azure.ClientSecretCredential")
    def test_auth_error_is_reraised(self, mock_cred_cls):
        mock_cred_cls.side_effect = ClientAuthenticationError(
            message="Invalid credentials"
        )

        from core.utils_azure import build_azure_cost_inventory

        with self.assertRaises(ClientAuthenticationError):
            build_azure_cost_inventory(
                1,
                {
                    "tenantId": "t",
                    "clientId": "c",
                    "clientSecret": "s",
                    "subscriptionId": "sub",
                    "resourceGroupName": "rg",
                },
                "/fake/report",
                "/fake/raw",
            )

    @patch("core.utils_azure.connect")
    @patch("core.utils_azure.CostManagementClient")
    @patch("core.utils_azure.ClientSecretCredential")
    def test_sqlite_error_is_reraised(self, mock_cred_cls, mock_cost_cls, mock_connect):
        import sqlite3

        mock_cost_client = MagicMock()
        mock_cost_cls.return_value = mock_cost_client

        mock_cost_data = MagicMock()
        mock_cost_data.rows = [
            [42.5, "2026-01-01T00:00:00", "USD"],
        ]
        mock_cost_data.as_dict.return_value = {}
        mock_cost_client.query.usage.return_value = mock_cost_data

        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = sqlite3.Error("disk I/O error")

        from core.utils_azure import build_azure_cost_inventory
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report")
            raw_data_path = os.path.join(tmp, "raw")
            os.makedirs(os.path.join(report_path, "data"), exist_ok=True)
            os.makedirs(raw_data_path, exist_ok=True)

            with self.assertRaises(sqlite3.Error):
                build_azure_cost_inventory(
                    1,
                    {
                        "tenantId": "t",
                        "clientId": "c",
                        "clientSecret": "s",
                        "subscriptionId": "sub",
                        "resourceGroupName": "rg",
                    },
                    report_path,
                    raw_data_path,
                )


if __name__ == "__main__":
    unittest.main()
