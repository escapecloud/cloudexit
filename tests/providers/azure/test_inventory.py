# tests/providers/azure/test_inventory.py
import unittest
from unittest.mock import MagicMock, patch

from azure.core.exceptions import AzureError, ClientAuthenticationError

from cloudexit.providers.azure.inventory import is_resource_inventory_empty


class IsResourceInventoryEmptyTests(unittest.TestCase):
    @patch("azure.mgmt.resource.resources.ResourceManagementClient")
    def test_returns_true_when_no_resources(self, mock_rmc_cls):
        mock_client = MagicMock()
        mock_rmc_cls.return_value = mock_client
        mock_client.resources.list_by_resource_group.return_value = iter([])

        result = is_resource_inventory_empty(MagicMock(), "sub-123", "rg-test")
        self.assertTrue(result)

    @patch("azure.mgmt.resource.resources.ResourceManagementClient")
    def test_returns_false_when_resources_exist(self, mock_rmc_cls):
        mock_client = MagicMock()
        mock_rmc_cls.return_value = mock_client
        mock_resource = MagicMock()
        mock_client.resources.list_by_resource_group.return_value = iter(
            [mock_resource]
        )

        result = is_resource_inventory_empty(MagicMock(), "sub-123", "rg-test")
        self.assertFalse(result)

    @patch("azure.mgmt.resource.resources.ResourceManagementClient")
    def test_raises_on_azure_error(self, mock_rmc_cls):
        mock_client = MagicMock()
        mock_rmc_cls.return_value = mock_client
        mock_client.resources.list_by_resource_group.side_effect = AzureError(
            "Connection failed"
        )

        with self.assertRaises(AzureError):
            is_resource_inventory_empty(MagicMock(), "sub-123", "rg-test")


class BuildAzureResourceInventoryErrorTests(unittest.TestCase):
    @patch("cloudexit.providers.azure.inventory.is_resource_inventory_empty")
    @patch("azure.identity.ClientSecretCredential")
    def test_auth_error_is_reraised(self, mock_cred_cls, mock_empty_check):
        mock_cred_cls.side_effect = ClientAuthenticationError(
            message="Invalid credentials"
        )

        from cloudexit.providers.azure.inventory import build_azure_resource_inventory

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

    @patch(
        "cloudexit.providers.azure.inventory.is_resource_inventory_empty",
        return_value=True,
    )
    @patch("azure.identity.ClientSecretCredential")
    def test_returns_early_when_inventory_empty(self, mock_cred_cls, mock_empty_check):
        from cloudexit.providers.azure.inventory import build_azure_resource_inventory

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


if __name__ == "__main__":
    unittest.main()
