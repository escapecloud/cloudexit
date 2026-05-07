import unittest

from utils.validate import validate_config, validate_region


def build_aws_config():
    return {
        "name": "Example Assessment",
        "assessmentType": 1,
        "cloudServiceProvider": 2,
        "exitStrategy": 1,
        "providerDetails": {
            "accessKey": "AKIA_TEST",
            "secretKey": "SECRET_TEST",
            "region": "eu-central-1",
        },
    }


def build_azure_config():
    return {
        "name": "Example Assessment",
        "assessmentType": 2,
        "cloudServiceProvider": 1,
        "exitStrategy": 3,
        "providerDetails": {
            "tenantId": "tenant-id",
            "clientId": "client-id",
            "clientSecret": "client-secret",
            "subscriptionId": "subscription-id",
            "resourceGroupName": "resource-group",
        },
    }


class ValidateRegionTests(unittest.TestCase):
    def test_accepts_known_region(self):
        self.assertIsNone(validate_region("eu-central-1"))

    def test_rejects_unknown_region(self):
        with self.assertRaisesRegex(ValueError, "Invalid AWS region"):
            validate_region("moon-central-1")


class ValidateConfigTests(unittest.TestCase):
    def test_accepts_valid_aws_config(self):
        self.assertTrue(validate_config(build_aws_config()))

    def test_accepts_valid_azure_service_principal_config(self):
        self.assertTrue(validate_config(build_azure_config()))

    def test_accepts_valid_azure_cli_config(self):
        config = build_azure_config()
        config["providerDetails"] = {
            "credential": object(),
            "tenantId": "tenant-id",
            "subscriptionId": "subscription-id",
            "resourceGroupName": "resource-group",
        }

        self.assertTrue(validate_config(config))

    def test_rejects_azure_config_without_client_credentials(self):
        config = build_azure_config()
        del config["providerDetails"]["clientId"]
        del config["providerDetails"]["clientSecret"]

        with self.assertRaisesRegex(ValueError, "Missing required fields in providerDetails"):
            validate_config(config)

    def test_rejects_invalid_assessment_type(self):
        config = build_aws_config()
        config["assessmentType"] = 9

        with self.assertRaisesRegex(ValueError, "Invalid assessmentType"):
            validate_config(config)

    def test_rejects_non_integer_top_level_fields(self):
        config = build_aws_config()
        config["assessmentType"] = "basic"

        with self.assertRaisesRegex(ValueError, "must be integers"):
            validate_config(config)

    def test_rejects_invalid_name_characters(self):
        config = build_aws_config()
        config["name"] = "Bad/Name"

        with self.assertRaisesRegex(ValueError, "Assessment name contains invalid characters"):
            validate_config(config)

    def test_rejects_too_long_name(self):
        config = build_aws_config()
        config["name"] = "a" * 51

        with self.assertRaisesRegex(ValueError, "cannot exceed 50 characters"):
            validate_config(config)

    def test_rejects_aws_config_with_invalid_region(self):
        config = build_aws_config()
        config["providerDetails"]["region"] = "invalid-region"

        with self.assertRaisesRegex(ValueError, "Invalid AWS region"):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
