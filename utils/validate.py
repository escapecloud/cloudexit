# validate.py
from .constants import REGION_CHOICES, REQUIRED_FIELDS_AZURE, REQUIRED_FIELDS_AWS

def validate_region(region):
    valid_regions = [choice[0] for choice in REGION_CHOICES]
    if region not in valid_regions:
        raise ValueError(f"Invalid AWS region. Choose from: {', '.join(valid_regions)}")

def validate_config(config):
    try:
        # Cast key values to integers to handle string input gracefully
        assessment_type = int(config.get("assessmentType", 0))
        cloud_service_provider = int(config.get("cloudServiceProvider", 0))
        exit_strategy = int(config.get("exitStrategy", 0))
    except ValueError:
        raise ValueError("Invalid input: assessmentType, cloudServiceProvider, and exitStrategy must be integers.")

    # Validate assessmentType
    if assessment_type != 1:
        raise ValueError("Invalid assessmentType. Must be 1.")

    # Validate cloudServiceProvider
    if cloud_service_provider not in [1, 2]:
        raise ValueError("Invalid cloudServiceProvider. Must be 1 (Azure) or 2 (AWS).")

    # Validate providerDetails based on cloudServiceProvider
    provider_details = config.get("providerDetails", {})
    if cloud_service_provider == 1:  # Azure
        missing_fields = [field for field in REQUIRED_FIELDS_AZURE if field not in provider_details]
    elif cloud_service_provider == 2:  # AWS
        missing_fields = [field for field in REQUIRED_FIELDS_AWS if field not in provider_details]
        if "region" in provider_details:
            validate_region(provider_details["region"])
    else:
        raise ValueError(f"Invalid cloudServiceProvider: {cloud_service_provider}. Supported values: 1 (Azure), 2 (AWS).")

    if missing_fields:
        raise ValueError(f"Missing required fields in providerDetails: {', '.join(missing_fields)}")

    return True
