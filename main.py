import logging
import os
import json
import argparse
from pathlib import Path
from rich.console import Console
from rich.text import Text
from datetime import datetime

# Import the functions
from core.engine import verify_credentials, test_permissions, create_resource_inventory, create_cost_inventory, perform_risk_assessment, conduct_alternative_technology_analysis, generate_report
from utils.utils import ascii_art, create_directory
from utils.constants import REGION_CHOICES, REQUIRED_FIELDS_AZURE, REQUIRED_FIELDS_AWS
from utils.validate import validate_region, validate_config

# Configure the root logger to ensure logs propagate from all modules
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Configure the logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize the console object
console = Console()

def load_config(file_path):
    try:
        with open(file_path, 'r') as f:
            config = json.load(f)
        return config
    except Exception as e:
        console.print(f"Error loading config file: {e}")
        return None

def console_input():
    config = {
        "cloudServiceProvider": None,
        "exitStrategy": None,
        "assessmentType": 1,
        "providerDetails": {}
    }

    # Validate cloudServiceProvider input
    while True:
        try:
            user_input = input("Enter Cloud Service Provider (1 for Azure, 2 for AWS): ").strip()

            # Check if input is empty
            if not user_input:
                console.print("Input cannot be empty. Please enter 1 or 2.")
                continue

            # Check if input is a valid integer
            config["cloudServiceProvider"] = int(user_input)

            # Check if the input is valid
            if config["cloudServiceProvider"] not in [1, 2]:
                console.print("Invalid cloudServiceProvider. Must be 1 (Azure) or 2 (AWS).")
                continue
            break
        except ValueError:
            console.print("Invalid input. Please enter a number (1 for Azure, 2 for AWS).")

    # Validate exitStrategy input
    while True:
        try:
            user_input = input("Enter Exit Strategy (1 for 'Repatriation to On-Premises', 3 for 'Migration to Alternate Cloud'): ").strip()

            # Check if input is empty
            if not user_input:
                console.print("Input cannot be empty. Please enter 1 or 3.")
                continue

            # Check if input is a valid integer
            config["exitStrategy"] = int(user_input)

            # Check if the input is valid
            if config["exitStrategy"] not in [1, 3]:
                console.print("Invalid exitStrategy. Must be 1 or 3.")
                continue
            break
        except ValueError:
            console.print("Invalid input. Please enter a number (1 for 'Repatriation to On-Premises', 3 for 'Migration to Alternate Cloud').")

    console.print("-------------------------------------------")

    if config["cloudServiceProvider"] == 1:  # Azure
        config["providerDetails"]["tenantId"] = input("Enter Azure Tenant ID: ")
        config["providerDetails"]["clientId"] = input("Enter Service Principal / Client ID: ")
        config["providerDetails"]["clientSecret"] = input("Enter Client Secret: ")
        config["providerDetails"]["subscriptionId"] = input("Enter Subscription ID: ")
        config["providerDetails"]["resourceGroupName"] = input("Enter Resource Group Name: ")
    else:  # AWS
        config["providerDetails"]["accessKey"] = input("Enter Access Key: ")
        config["providerDetails"]["secretKey"] = input("Enter Secret Key: ")

        # Loop until a valid AWS region is provided
        while True:
            region = input("Enter AWS region: ")
            try:
                validate_region(region)
                config["providerDetails"]["region"] = region
                break
            except ValueError as e:
                console.print(f"[red]{e}[/red]")

    return config

def main():
    # Print ASCII art
    console.print(ascii_art, style="bold cyan")

    # Set up the argument parser
    parser = argparse.ArgumentParser(
        epilog="Example usage: python3 main.py --config config/aws.json",
    )

    # Add arguments
    parser.add_argument(
        "--config",
        type=str,
        help="Path to the configuration file (JSON format)",
        required=False,
    )

    # Parse arguments
    args = parser.parse_args()

    # Check if --help is invoked (handled automatically by argparse)
    config = None
    if args.config:
        # Validate the config file path
        config_path = Path(args.config)
        if config_path.is_file():
            config = load_config(config_path)
        else:
            console.print("Error: Config file not found. Please check the file path.")
            return

    if not config:
        console.print("No valid config file provided. Please input details manually.")
        config = console_input()

    try:
        validate_config(config)

        # Set up the directory for assessment
        report_path, raw_data_path = create_directory()

        console.print("-------------------------------------------")
        console.print("Preliminary Stage", style="bold")
        console.print("✓ | Directory successfully created.")
        console.print("✓ | Configuration successfully validated.")

        # Run Stage 1: Verify Credentials
        connection_success, logs = verify_credentials(
            config["cloudServiceProvider"], config["providerDetails"]
        )

        # Use `rich` to print the results of the credential verification
        console.print("-------------------------------------------")
        console.print("Stage #1 - Validate Credentials", style="bold")
        if connection_success:
            console.print("✓ | Connection successful.")
        else:
            console.print("- | Connection failed:")
            console.print(f"{logs}")
            return
        console.print("-------------------------------------------")

        # Run Stage 2: Test Permissions
        permission_valid, permission_reader, permission_cost, logs = test_permissions(
            config["cloudServiceProvider"], config["providerDetails"]
        )

        if config["cloudServiceProvider"] == 1:  # Azure
            permission_reader_label = "Reader"
            permission_cost_label = "Cost Management Reader"
        elif config["cloudServiceProvider"] == 2:  # AWS
            permission_reader_label = "ViewOnlyAccess"
            permission_cost_label = "AWSBillingReadOnlyAccess"
        else:  # Default case
            permission_reader_label = "Reader"
            permission_cost_label = "Cost Management Reader"

        console.print("Stage #2 - Validate Permissions", style="bold")
        if permission_reader:
            console.print(f"✓ | {permission_reader_label}")
        else:
            console.print(f"- | {permission_reader_label}")

        if permission_cost:
            console.print(f"✓ | {permission_cost_label}")
        else:
            console.print(f"- | {permission_cost_label}")

        if not permission_valid:
            console.print("Failed to validate all required permissions. Exiting.")
            console.print(f"{logs}")
            return
        console.print("-------------------------------------------")

        # Run Stage 3: Build Resource Inventory
        console.print("Stage #3 - Build Resource Inventory", style="bold")

        # Use a temporary "In progress..." message with a spinner
        with console.status("In progress...", spinner="dots"):
            result = create_resource_inventory(config["cloudServiceProvider"], config["providerDetails"], report_path, raw_data_path)

        # Check if the resource inventory was created successfully
        if config["cloudServiceProvider"] == 1:
            if result["success"]:
                console.print("✓ | Microsoft Azure")
            else:
                console.print("- | Microsoft Azure")
                console.print(f"Log: {result['logs']}")
        elif config["cloudServiceProvider"] == 2:
            if result["success"]:
                console.print("✓ | Amazon Web Services")
            else:
                console.print("- | Amazon Web Services")
                console.print(f"Log: {result['logs']}")

        # If the resource inventory creation failed, print the error and exit
        if not result["success"]:
            console.print("Failed to create resource inventory. Exiting.")
            console.print(f"Log: {result['logs']}")
            return

        console.print("-------------------------------------------")

        # Run Stage 4: Build Cost Inventory
        console.print("Stage #4 - Build Cost Inventory", style="bold")

        # Use a temporary "In progress..." message with a spinner
        with console.status("In progress...", spinner="dots"):
            cost_result = create_cost_inventory(config["providerDetails"], config["cloudServiceProvider"], report_path, raw_data_path)

        # Check if the cost inventory was created successfully
        if config["cloudServiceProvider"] == 1:
            if cost_result["success"]:
                console.print("✓ | Microsoft Azure")
            else:
                console.print("- | Microsoft Azure")
        elif config["cloudServiceProvider"] == 2:
            if cost_result["success"]:
                console.print("✓ | Amazon Web Services")
            else:
                console.print("- | Amazon Web Services")

        # If the cost inventory creation failed, print the error and exit
        if not cost_result["success"]:
            console.print("Failed to create cost inventory. Exiting.")
            console.print(f"Log: {cost_result['logs']}")
            return

        console.print("-------------------------------------------")

        # Run Stage 5: Perform Risk Assessment
        console.print("Stage #5 - Perform Risk Assessment", style="bold")

        # Use a temporary "In progress..." message with a spinner
        with console.status("In progress...", spinner="dots"):
            risk_result = perform_risk_assessment(config["exitStrategy"], report_path)

        # Check if the risk assessment was performed successfully
        if risk_result["success"]:
            console.print("✓ | Risk Assessment.")
        else:
            console.print("- | Risk Assessment.")

        # If the risk assessment failed, print the error and exit
        if not risk_result["success"]:
            console.print("Failed to perform risk assessment. Exiting.")
            console.print(f"Log: {risk_result['logs']}")
            return

        console.print("-------------------------------------------")

        # Run Stage 6: Conduct Alternative Technology Analysis
        console.print("Stage #6 - Conduct Alternative Technology Analysis", style="bold")

        # Use a temporary "In progress..." message with a spinner
        with console.status("In progress...", spinner="dots"):
            alttech_result = conduct_alternative_technology_analysis(config["cloudServiceProvider"], config["exitStrategy"], report_path)

        # Check if the risk assessment was performed successfully
        if alttech_result["success"]:
            console.print("✓ | Alternative Technology Analysis.")
        else:
            console.print("- | Alternative Technology Analysis.")

        # If the risk assessment failed, print the error and exit
        if not alttech_result["success"]:
            console.print("Failed to conduct alternative technology analysis. Exiting.")
            console.print(f"Log: {alttech_result['logs']}")
            return

        console.print("-------------------------------------------")

        # Run Stage 7: Generate Report
        console.print("Stage #7 - Generate Report", style="bold")

        # Use a temporary "In progress..." message with a spinner
        with console.status("In progress...", spinner="dots"):
            report_status = generate_report(config["cloudServiceProvider"], config["exitStrategy"], config["assessmentType"], report_path,)

        # Check if the report was generated successfully
        if report_status["success"]:
            console.print("✓ | Report generated successfully.")
        else:
            console.print("- | Report generation failed.")

        # If report generation failed, print the error and exit
        if not report_status["success"]:
            console.print("Failed to generate report. Exiting.")
            console.print(f"Log: {report_status['logs']}")
            return

        console.print("-------------------------------------------")

    except ValueError as e:
            console.print(f"Configuration error: {e}")
    except Exception as e:
            console.print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
