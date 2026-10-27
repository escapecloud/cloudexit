import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cloudexit.engine import (
    _load_tfstate_scope,
    test_permissions,
    verify_credentials,
)


class TestPermissionsAwsHybridMode(unittest.TestCase):
    def setUp(self):
        self.provider_details = {
            "accessKey": "AKIAEXAMPLE",
            "secretKey": "secret-example",
            "sessionToken": "session-token",
            "region": "eu-central-1",
        }

    @patch("cloudexit.engine.boto3.client")
    def test_iam_user_keeps_policy_based_validation(self, mock_boto_client):
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::123456789012:user/test-user"
        }
        iam_client = MagicMock()
        iam_client.list_attached_user_policies.return_value = {
            "AttachedPolicies": [
                {"PolicyName": "ViewOnlyAccess"},
                {"PolicyName": "AWSBillingReadOnlyAccess"},
            ]
        }

        def client_side_effect(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            raise AssertionError(f"Unexpected service requested: {service_name}")

        mock_boto_client.side_effect = client_side_effect

        permission_valid, permission_reader, permission_cost, logs = test_permissions(
            2, self.provider_details
        )

        self.assertTrue(permission_valid)
        self.assertTrue(permission_reader)
        self.assertTrue(permission_cost)
        self.assertIn("policies validated", logs)

    @patch("cloudexit.engine.boto3.client")
    def test_assumed_role_uses_capability_checks(self, mock_boto_client):
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:sts::123456789012:assumed-role/GitHub_Actions/runner"
        }
        ec2_client = MagicMock()
        ce_client = MagicMock()

        def client_side_effect(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "ec2":
                return ec2_client
            if service_name == "ce":
                return ce_client
            raise AssertionError(f"Unexpected service requested: {service_name}")

        mock_boto_client.side_effect = client_side_effect

        permission_valid, permission_reader, permission_cost, logs = test_permissions(
            2, self.provider_details
        )

        self.assertTrue(permission_valid)
        self.assertTrue(permission_reader)
        self.assertTrue(permission_cost)
        self.assertIn("capability checks validated", logs)

    @patch("cloudexit.engine.boto3.client")
    def test_assumed_role_cost_capability_failure(self, mock_boto_client):
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:sts::123456789012:assumed-role/GitHub_Actions/runner"
        }
        ec2_client = MagicMock()
        ce_client = MagicMock()
        ce_client.get_cost_and_usage.side_effect = Exception("AccessDenied")

        def client_side_effect(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "ec2":
                return ec2_client
            if service_name == "ce":
                return ce_client
            raise AssertionError(f"Unexpected service requested: {service_name}")

        mock_boto_client.side_effect = client_side_effect

        permission_valid, permission_reader, permission_cost, logs = test_permissions(
            2, self.provider_details
        )

        self.assertFalse(permission_valid)
        self.assertTrue(permission_reader)
        self.assertFalse(permission_cost)
        self.assertIn("ce:GetCostAndUsage failed", logs)


class _DummyAuthError(Exception):
    pass


class TestVerifyCredentialsAzure(unittest.TestCase):
    def setUp(self):
        self.provider_details = {
            "subscriptionId": "00000000-0000-0000-0000-000000000000",
            "resourceGroupName": "rg-demo",
            "tenantId": "11111111-1111-1111-1111-111111111111",
            "clientId": "22222222-2222-2222-2222-222222222222",
            "clientSecret": "secret-example",
        }

    @patch("cloudexit.engine.create_resource_client")
    @patch("cloudexit.engine.create_credential")
    @patch("cloudexit.engine.azure_import")
    def test_connection_check_targets_only_the_configured_group(
        self, mock_azure_import, mock_create_credential, mock_create_resource_client
    ):
        mock_azure_import.return_value = _DummyAuthError
        resource_client = MagicMock()
        mock_create_resource_client.return_value = resource_client

        success, logs = verify_credentials(1, self.provider_details)

        self.assertTrue(success)
        resource_client.resource_groups.get.assert_called_once_with("rg-demo")
        resource_client.resource_groups.list.assert_not_called()

    @patch("cloudexit.engine.create_resource_client")
    @patch("cloudexit.engine.create_credential")
    @patch("cloudexit.engine.azure_import")
    def test_missing_group_fails_without_listing_the_subscription(
        self, mock_azure_import, mock_create_credential, mock_create_resource_client
    ):
        mock_azure_import.return_value = _DummyAuthError
        resource_client = MagicMock()
        resource_client.resource_groups.get.side_effect = Exception(
            "Resource group 'rg-demo' could not be found."
        )
        mock_create_resource_client.return_value = resource_client

        success, logs = verify_credentials(1, self.provider_details)

        self.assertFalse(success)
        self.assertIn("rg-demo", logs)
        resource_client.resource_groups.list.assert_not_called()


class LoadTfstateScopeTests(unittest.TestCase):
    def _write_manifest(self, directory, payload):
        path = Path(directory) / "tfstate_manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_returns_scope_block_from_manifest(self):
        scope = {"file": "infra.tfstate", "serial": 7, "locations": ["eu-central-1"]}
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._write_manifest(tmp_dir, {"scope": scope, "counted": []})

            self.assertEqual(_load_tfstate_scope(tmp_dir), (scope, None))

    def test_returns_the_coverage_block_with_the_scope(self):
        scope = {"file": "infra.tfstate"}
        coverage = {
            "instances_total": 9,
            "instances_counted": 5,
            "instances_excluded_other_provider": 3,
            "instances_excluded_unmapped": 1,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._write_manifest(tmp_dir, {"scope": scope, "coverage": coverage})

            self.assertEqual(_load_tfstate_scope(tmp_dir), (scope, coverage))

    def test_missing_manifest_returns_nothing_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(_load_tfstate_scope(tmp_dir), (None, None))

    def test_corrupt_manifest_returns_nothing_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "tfstate_manifest.json"
            path.write_text("{not json", encoding="utf-8")

            self.assertEqual(_load_tfstate_scope(tmp_dir), (None, None))

    def test_manifest_without_scope_returns_nothing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._write_manifest(tmp_dir, {"counted": []})

            self.assertEqual(_load_tfstate_scope(tmp_dir), (None, None))


if __name__ == "__main__":
    unittest.main()
