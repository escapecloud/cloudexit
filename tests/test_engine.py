import unittest
from unittest.mock import MagicMock, patch

from core.engine import test_permissions


class TestPermissionsAwsHybridMode(unittest.TestCase):
    def setUp(self):
        self.provider_details = {
            "accessKey": "AKIAEXAMPLE",
            "secretKey": "secret-example",
            "sessionToken": "session-token",
            "region": "eu-central-1",
        }

    @patch("core.engine.boto3.client")
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

    @patch("core.engine.boto3.client")
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

    @patch("core.engine.boto3.client")
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


if __name__ == "__main__":
    unittest.main()
