# utils/validate.py
from typing import Dict, Any
from .constants import REGION_CHOICES, REQUIRED_FIELDS_AZURE, REQUIRED_FIELDS_AWS


def validate_region(region: str) -> None:
    valid_regions = [choice[0] for choice in REGION_CHOICES]
    if region not in valid_regions:
        raise ValueError(f"Invalid AWS region. Choose from: {', '.join(valid_regions)}")


def validate_config(config: Dict[str, Any]) -> bool:
    try:
        # Cast key values to integers to handle string input gracefully
        assessment_type = int(config.get("assessmentType", 0))
        cloud_service_provider = int(config.get("cloudServiceProvider", 0))
        exit_strategy = int(config.get("exitStrategy", 0))
    except ValueError:
        raise ValueError(
            "Invalid input: assessmentType, cloudServiceProvider, and exitStrategy must be integers."
        )

    # Validate assessmentType
    if assessment_type not in [1, 2]:
        raise ValueError("Invalid assessmentType. Must be 1 (Basic) or 2 (Standard).")

    # Validate cloudServiceProvider
    if cloud_service_provider not in [1, 2]:
        raise ValueError("Invalid cloudServiceProvider. Must be 1 (Azure) or 2 (AWS).")

    # Validate exitStrategy
    if exit_strategy not in [1, 2, 3]:
        raise ValueError(
            "Invalid exitStrategy. Must be 1 (Repatriation to On-Premises), 2 (Hybrid Cloud Adoption) or 3 (Migration to Alternate Cloud)."
        )

    # Validate name
    name = config.get("name", "").strip()
    if len(name) > 50:
        raise ValueError("Assessment name cannot exceed 50 characters.")
    if not all(c.isalnum() or c in " ._-()" for c in name):
        raise ValueError(
            "Assessment name contains invalid characters. Only letters, numbers, spaces, . _ - ( ) are allowed."
        )

    # Validate providerDetails based on cloudServiceProvider
    provider_details = config.get("providerDetails", {})
    if cloud_service_provider == 1:  # Azure
        # Skip validation of clientId and clientSecret if using CLI credentials
        if provider_details.get("credential") is not None:
            required_fields = ["tenantId", "subscriptionId", "resourceGroupName"]
        else:
            required_fields = REQUIRED_FIELDS_AZURE
        missing_fields = [
            field for field in required_fields if field not in provider_details
        ]
    elif cloud_service_provider == 2:  # AWS
        missing_fields = [
            field for field in REQUIRED_FIELDS_AWS if field not in provider_details
        ]
        if "region" in provider_details:
            validate_region(provider_details["region"])
    else:
        raise ValueError(
            f"Invalid cloudServiceProvider: {cloud_service_provider}. Supported values: 1 (Azure), 2 (AWS)."
        )

    if missing_fields:
        raise ValueError(
            f"Missing required fields in providerDetails: {', '.join(missing_fields)}"
        )

    return True
