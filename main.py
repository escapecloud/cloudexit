import logging
import os
import json
import argparse
import boto3
import botocore
from pathlib import Path
from rich.console import Console
from rich.text import Text
from datetime import datetime
from botocore.exceptions import NoCredentialsError, ClientError
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.mgmt.resource import SubscriptionClient, ResourceManagementClient
from azure.core.exceptions import ClientAuthenticationError

# Import the functions
from core.engine import (
    verify_credentials,
    test_permissions,
    create_resource_inventory,
    create_cost_inventory,
    perform_risk_assessment,
    conduct_alternative_technology_analysis,
    generate_report,
)
from utils.utils import ascii_art, create_directory, load_config, prompt_required_inputs, print_help_message
from utils.constants import REGION_CHOICES, REQUIRED_FIELDS_AZURE, REQUIRED_FIELDS_AWS
from utils.validate import validate_region, validate_config
from utils.azure import select_subscription, select_resource_group, is_azure_cli_logged_in

# Configure the root logger to ensure logs propagate from all modules
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)

# Configure the logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize the console object
console = Console()

def handle_aws(args):
    config = {}

    if args.config:
        #logger.info(f"AWS --config argument detected with path: {args.config}")
        config = load_config(args.config)
        if not config:
            console.print("[red]Invalid or missing AWS configuration file.[/red]")
            return
    elif args.profile:
        #logger.info(f"AWS --profile argument detected with profile: {args.profile}")
        try:
            session = boto3.Session(profile_name=args.profile)
            credentials = session.get_credentials()
            region = session.region_name or "us-east-1"
            #logger.info(f"Using AWS profile '{args.profile}' with region '{region}'.")

            #exit_strategy, assessment_type = prompt_required_inputs()
            exit_strategy = prompt_required_inputs()
            config = {
                "cloudServiceProvider": 2,
                "exitStrategy": exit_strategy,
                "assessmentType": 1,
                "providerDetails": {
                    "accessKey": credentials.access_key,
                    "secretKey": credentials.secret_key,
                    "region": region,
                },
            }
        except (NoCredentialsError, ProfileNotFound) as e:
            #logger.error(f"AWS profile error: {e}", exc_info=True)
            console.print(f"[red]AWS profile error: {str(e)}. Use `aws configure` to set up a profile.[/red]")
            return
    else:
        exit_strategy = prompt_required_inputs()
        # Prompt for manual input
        try:
            access_key = input("Enter AWS Access Key: ").strip()
            secret_key = input("Enter AWS Secret Key: ").strip()

            # Validate AWS region input
            while True:
                region = input("Enter AWS region: ").strip()
                try:
                    validate_region(region)
                    break
                except ValueError as e:
                    console.print(f"[red]{e} Please enter a valid AWS region.[/red]")

            config = {
                "cloudServiceProvider": 2,
                "exitStrategy": exit_strategy,
                "assessmentType": 1,
                "providerDetails": {
                    "accessKey": access_key,
                    "secretKey": secret_key,
                    "region": region,
                },
            }
        except Exception as e:
            console.print(f"[red]Error during manual AWS configuration: {e}[/red]")
            logger.error(f"Error during manual AWS configuration: {e}", exc_info=True)
            return

    # Run the AWS assessment pipeline
    run_assessment(config, "aws")

def handle_azure(args):
    config = {}
    if args.config:
        #logger.info(f"Azure --config argument detected with path: {args.config}")
        config = load_config(args.config)
        if not config:
            console.print("[red]Invalid or missing Azure configuration file.[/red]")
            return
    elif args.cli:
        #logger.info("Azure --cli argument detected. Using Azure CLI credentials.")
        # Check if the user is logged in to Azure CLI
        if not is_azure_cli_logged_in():
            console.print("[red]You are not logged in to Azure CLI. Please run 'az login' and try again.[/red]")
            return

        try:
            credential = DefaultAzureCredential()
            tenant_id = input("Enter Azure Tenant ID: ").strip()
            subscription_client = SubscriptionClient(credential)
            subscriptions = list(subscription_client.subscriptions.list())
            if not subscriptions:
                logger.error("No subscriptions found for the provided Azure credentials.")
                console.print("[red]No subscriptions found for the provided credentials.[/red]")
                return

            selected_subscription = select_subscription(subscriptions)
            subscription_id = selected_subscription.subscription_id

            resource_client = ResourceManagementClient(credential, subscription_id)
            resource_groups = list(resource_client.resource_groups.list())
            if not resource_groups:
                logger.error("No resource groups found in the selected subscription.")
                console.print("[red]No resource groups found in the selected subscription.[/red]")
                return

            resource_group_name = select_resource_group(resource_groups)
            #exit_strategy, assessment_type = prompt_required_inputs()
            exit_strategy = prompt_required_inputs()
            config = {
                "cloudServiceProvider": 1,
                "exitStrategy": exit_strategy,
                "assessmentType": 1,
                "providerDetails": {
                    "credential": credential,
                    "tenantId": tenant_id,
                    "subscriptionId": subscription_id,
                    "resourceGroupName": resource_group_name,
                },
            }
        except Exception as e:
            logger.error(f"Error during Azure CLI processing: {e}", exc_info=True)
            console.print(f"[red]An error occurred: {e}[/red]")
    else:
        # Prompt for exit strategy
        exit_strategy = prompt_required_inputs()

        tenant_id = input("Enter Azure Tenant ID: ").strip()
        client_id = input("Enter Service Principal / Client ID: ").strip()
        client_secret = input("Enter Client Secret: ").strip()

        try:
            # Authenticate using the provided credentials
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret
            )
            subscription_client = SubscriptionClient(credential)

            # Fetch and prompt the user to select a subscription
            subscriptions = list(subscription_client.subscriptions.list())
            if not subscriptions:
                console.print("[red]No subscriptions found. Please check your credentials.[/red]")
                return

            selected_subscription = select_subscription(subscriptions)
            subscription_id = selected_subscription.subscription_id

            # Fetch and prompt the user to select a resource group
            resource_client = ResourceManagementClient(credential, subscription_id)
            resource_groups = list(resource_client.resource_groups.list())
            if not resource_groups:
                console.print("[red]No resource groups found in the selected subscription.[/red]")
                return

            resource_group_name = select_resource_group(resource_groups)

            # Build the configuration
            config = {
                "cloudServiceProvider": 1,
                "exitStrategy": exit_strategy,
                "assessmentType": 1,
                "providerDetails": {
                    "tenantId": tenant_id,
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "subscriptionId": subscription_id,
                    "resourceGroupName": resource_group_name,
                },
            }
        except Exception as e:
            logger.error(f"Error during manual Azure configuration: {e}", exc_info=True)
            console.print(f"[red]An error occurred: {e}[/red]")
            return

    # Run the Azure assessment pipeline
    #logger.info("Starting Azure assessment pipeline.")
    run_assessment(config, "azure")

def run_assessment(config, provider_name):
    try:
        validate_config(config)

        # Set up the directory for assessment
        report_path, raw_data_path = create_directory()
        #logger.info(f"Assessment directories created: Report Path: {report_path}, Raw Data Path: {raw_data_path}")

        console.print("-------------------------------------------")
        console.print("Preliminary Stage", style="bold")
        console.print("✓ | Directory successfully created.")
        console.print("✓ | Configuration successfully validated.")

        # Stage 1: Verify Credentials
        connection_success, logs = verify_credentials(
            config["cloudServiceProvider"], config["providerDetails"]
        )

        console.print("-------------------------------------------")
        console.print("Stage #1 - Validate Credentials", style="bold")
        if connection_success:
            console.print("✓ | Connection successful.")
        else:
            console.print("- | Connection failed:")
            console.print(f"{logs}")
            logger.error(f"Credential verification failed: {logs}")
            return
        console.print("-------------------------------------------")

        # Stage 2: Test Permissions
        permission_valid, permission_reader, permission_cost, logs = test_permissions(
            config["cloudServiceProvider"], config["providerDetails"]
        )

        permission_reader_label = "Reader" if config["cloudServiceProvider"] == 1 else "ViewOnlyAccess"
        permission_cost_label = "Cost Management Reader" if config["cloudServiceProvider"] == 1 else "AWSBillingReadOnlyAccess"

        console.print("Stage #2 - Validate Permissions", style="bold")
        console.print(f"✓ | {permission_reader_label}" if permission_reader else f"- | {permission_reader_label}")
        console.print(f"✓ | {permission_cost_label}" if permission_cost else f"- | {permission_cost_label}")

        if not permission_valid:
            console.print("Failed to validate all required permissions. Exiting.")
            console.print(f"{logs}")
            logger.error(f"Permission validation failed: {logs}")
            return
        console.print("-------------------------------------------")

        # Stage 3: Build Resource Inventory
        console.print("Stage #3 - Build Resource Inventory", style="bold")
        with console.status("In progress...", spinner="dots"):
            result = create_resource_inventory(config["cloudServiceProvider"], config["providerDetails"], report_path, raw_data_path)

        if result["success"]:
            console.print(f"✓ | {provider_name.title()}")
        else:
            console.print(f"- | {provider_name.title()}")
            console.print(f"Log: {result['logs']}")
            logger.error(f"Resource inventory creation failed: {result['logs']}")
            return

        console.print("-------------------------------------------")

        # Stage 4: Build Cost Inventory
        console.print("Stage #4 - Build Cost Inventory", style="bold")
        with console.status("In progress...", spinner="dots"):
            cost_result = create_cost_inventory(config["providerDetails"], config["cloudServiceProvider"], report_path, raw_data_path)

        if cost_result["success"]:
            console.print(f"✓ | {provider_name.title()}")
        else:
            console.print(f"- | {provider_name.title()}")
            console.print(f"Log: {cost_result['logs']}")
            logger.error(f"Cost inventory creation failed: {cost_result['logs']}")
            return

        console.print("-------------------------------------------")

        # Stage 5: Perform Risk Assessment
        console.print("Stage #5 - Perform Risk Assessment", style="bold")
        with console.status("In progress...", spinner="dots"):
            risk_result = perform_risk_assessment(config["exitStrategy"], report_path)

        if risk_result["success"]:
            console.print("✓ | Risk Assessment.")
        else:
            console.print("- | Risk Assessment.")
            console.print(f"Log: {risk_result['logs']}")
            logger.error(f"Risk assessment failed: {risk_result['logs']}")
            return

        console.print("-------------------------------------------")

        # Stage 6: Conduct Alternative Technology Analysis
        console.print("Stage #6 - Conduct Alternative Technology Analysis", style="bold")
        with console.status("In progress...", spinner="dots"):
            alttech_result = conduct_alternative_technology_analysis(config["cloudServiceProvider"], config["exitStrategy"], report_path)

        if alttech_result["success"]:
            console.print("✓ | Alternative Technology Analysis.")
        else:
            console.print("- | Alternative Technology Analysis.")
            console.print(f"Log: {alttech_result['logs']}")
            logger.error(f"Alternative technology analysis failed: {alttech_result['logs']}")
            return

        console.print("-------------------------------------------")

        # Stage 7: Generate Report
        console.print("Stage #7 - Generate Report", style="bold")
        with console.status("In progress...", spinner="dots"):
            report_status = generate_report(config["cloudServiceProvider"], config["exitStrategy"], config["assessmentType"], report_path)

        if report_status["success"]:
            console.print("✓ | Report generated successfully.")
            console.print(f"{report_status['logs']}", style="cyan")
        else:
            console.print("- | Report generation failed.")
            console.print(f"Log: {report_status['logs']}")
            logger.error(f"Report generation failed: {report_status['logs']}")
            return

        console.print("-------------------------------------------")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        console.print(f"[red]Unexpected error: {e}[/red]")

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="EscapeCloud - Community Edition",
        epilog=(
            "Example usage:\n"
            "  python3 main.py aws                        # Use manual input for AWS\n"
            "  python3 main.py aws --config config.json   # Use a configuration file for AWS\n"
            "  python3 main.py aws --profile PROFILE      # Use an AWS CLI profile\n"
            "  python3 main.py azure                      # Use manual input for Azure\n"
            "  python3 main.py azure --config config.json # Use a configuration file for Azure\n"
            "  python3 main.py azure --cli                # Use Azure CLI credentials\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="cloud_provider", help="Specify the cloud provider (aws or azure).")

    # Subparser for AWS
    aws_parser = subparsers.add_parser("aws", help="Perform an AWS assessment.")
    aws_group = aws_parser.add_mutually_exclusive_group(required=False)
    aws_group.add_argument("--config", type=str, help="Path to the configuration file (JSON format).")
    aws_group.add_argument("--profile", type=str, help="AWS profile name to use credentials from ~/.aws/credentials.")

    # Subparser for Azure
    azure_parser = subparsers.add_parser("azure", help="Perform an Azure assessment.")
    azure_group = azure_parser.add_mutually_exclusive_group(required=False)
    azure_group.add_argument("--config", type=str, help="Path to the configuration file (JSON format).")
    azure_group.add_argument("--cli", action="store_true", help="Use Azure CLI credentials for authentication.")

    return parser.parse_args()

def main():
    # Print ASCII art
    console.print(ascii_art, style="bold cyan")

    args = parse_arguments()

    # Check if the cloud provider is specified
    if not args.cloud_provider:
        print_help_message()
        return

    # Dispatch based on provided arguments
    if args.cloud_provider == "aws":
        handle_aws(args)
    elif args.cloud_provider == "azure":
        handle_azure(args)
    else:
        console.print("[red]Invalid command. Use 'aws' or 'azure' as the first argument.[/red]")
        console.print("[green]Run 'python3 main.py --help' for usage instructions.[/green]")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        console.print(f"[red]Unexpected error: {e}[/red]")
