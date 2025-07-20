# main.py
import logging
import argparse
import boto3
import time
import sys
from rich.console import Console
from datetime import datetime
from botocore.exceptions import NoCredentialsError, ProfileNotFound
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.mgmt.resource import SubscriptionClient, ResourceManagementClient

# Import the functions
from core.engine import (
    verify_credentials,
    test_permissions,
    create_resource_inventory,
    create_cost_inventory,
    perform_risk_assessment,
    sync_assessment,
    generate_report,
)
from utils.azure import select_subscription, select_resource_group, is_azure_cli_installed, is_azure_cli_logged_in, is_azure_cli_token_expired
from utils.aws import is_aws_cli_installed, is_aws_profile_valid
from utils.connection import resolve_mode
from utils.data import initialize_dataset
from utils.utils import ascii_art, create_directory, load_config, prompt_required_inputs, print_help_message, print_step
from utils.validate import validate_region, validate_config

# Configure the root logger to ensure logs propagate from all modules
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("kaleido").setLevel(logging.WARNING)
logging.getLogger("choreographer").setLevel(logging.WARNING)

# Configure the logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize the console object
console = Console()

def handle_aws(args):
    config = {}

    cloud_provider = 2

    if args.config:
        #logger.info(f"AWS --config argument detected with path: {args.config}")
        config = load_config(args.config)

        if not config:
            console.print("[red]Invalid or missing AWS configuration file.[/red]")
            return

        # Handle name field logic (priority: --name > config name > fallback)
        if args.name:
            config["name"] = args.name.strip()

        if "name" not in config or not config["name"].strip():
            config["name"] = f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"

    elif args.profile:
        # Check if aws cli available
        if not is_aws_cli_installed():
            #logger.error("AWS CLI is not installed.")
            console.print("[red]AWS CLI is not installed. Install it from https://aws.amazon.com/cli/[/red]")
            return
        # Check if aws cli profile is valid
        if not is_aws_profile_valid(args.profile):
            #logger.error(f"AWS profile '{args.profile}' is not configured.")
            console.print(f"[red]AWS profile '{args.profile}' is not configured. Use `aws configure --profile {args.profile}`.[/red]")
            return

        #logger.info(f"AWS --profile argument detected with profile: {args.profile}")
        try:
            session = boto3.Session(profile_name=args.profile)
            credentials = session.get_credentials()
            if credentials is None:
                #logger.error(f"AWS profile '{args.profile}' has no valid credentials.")
                console.print(f"[red]AWS profile '{args.profile}' has no valid credentials. Use `aws configure --profile {args.profile}`.[/red]")
                return
            region = session.region_name or "us-east-1"
            #logger.info(f"Using AWS profile '{args.profile}' with region '{region}'.")

            exit_strategy, assessment_type = prompt_required_inputs()
            config = {
                "name": args.name.strip() if args.name else f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "cloudServiceProvider": cloud_provider,
                "exitStrategy": exit_strategy,
                "assessmentType": assessment_type,
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
        exit_strategy, assessment_type = prompt_required_inputs()
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
                "name": args.name.strip() if args.name else f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "cloudServiceProvider": cloud_provider,
                "exitStrategy": exit_strategy,
                "assessmentType": assessment_type,
                "providerDetails": {
                    "accessKey": access_key,
                    "secretKey": secret_key,
                    "region": region,
                },
            }
        except Exception as e:
            console.print(f"[red]Error during manual AWS configuration: {e}[/red]")
            return

    # Run the AWS assessment pipeline
    run_assessment(config, "aws")

def handle_azure(args):
    config = {}

    cloud_provider = 1

    if args.config:
        #logger.info(f"Azure --config argument detected with path: {args.config}")
        config = load_config(args.config)

        if not config:
            console.print("[red]Invalid or missing Azure configuration file.[/red]")
            return

        # Handle name field logic (priority: --name > config name > fallback)
        if args.name:
            config["name"] = args.name.strip()

        if "name" not in config or not config["name"].strip():
            config["name"] = f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"

    elif args.cli:
        #logger.info("Azure --cli argument detected. Using Azure CLI credentials.")
        # Check if az cli available
        if not is_azure_cli_installed():
            #logger.error("Azure CLI is not installed.")
            console.print("[red]Azure CLI is not installed. Install it from https://aka.ms/install-azure-cli.[/red]")
            return

        # Check if the user is logged in to Azure CLI
        if not is_azure_cli_logged_in():
            #logger.error("User is not logged in to Azure CLI")
            console.print("[red]You are not logged in to Azure CLI. Please run 'az login' and try again.[/red]")
            return

        # Check if the cli token is expired
        if is_azure_cli_token_expired():
            #logger.error("Azure CLI token is expired.")
            console.print("[red]Your Azure CLI token has expired. Please run:[/red]")
            console.print("[bold cyan]az login --scope https://management.azure.com/.default[/bold cyan]")
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
            exit_strategy, assessment_type = prompt_required_inputs()
            config = {
                "name": args.name.strip() if args.name else f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "cloudServiceProvider": cloud_provider,
                "exitStrategy": exit_strategy,
                "assessmentType": assessment_type,
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
        exit_strategy, assessment_type = prompt_required_inputs()

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
                "name": args.name.strip() if args.name else f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "cloudServiceProvider": cloud_provider,
                "exitStrategy": exit_strategy,
                "assessmentType": assessment_type,
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
    # Record the assessment start time to propagate across stages
    started_at = int(time.time())

    try:
        # Preliminary Stage: Validate configuration & create directory
        console.print("-------------------------------------------")
        console.print("Preliminary Stage", style="bold")
        try:
            validate_config(config)
            print_step("Configuration successfully validated.", status="ok")
        except ValueError as e:
            print_step("Configuration validation failed.", status="error", logs=str(e))
            return

        # Detect ExitCloud Integration
        mode, jwt = resolve_mode()
        if mode == "online":
            print_step("ExitCloud integration configured.", status="ok")
        else:
            print_step("ExitCloud integration not configured.", status="warning")
            # Overwrite assessment type to basic
            if config["assessmentType"] != 1:
                print_step("Forcing Basic Assessment due to offline mode.", status="warning")
                config["assessmentType"] = 1

        # Create directories
        try:
            report_path, raw_data_path = create_directory()
            print_step("Directory successfully created.", status="ok")
        except RuntimeError as e:
            print_step("Directory creation failed.", status="error", logs=str(e))
            return

        # Handle the result
        provider_name = (
            "Microsoft Azure" if config["cloudServiceProvider"] == 1
            else "AWS" if config["cloudServiceProvider"] == 2
            else "Unknown"
        )

        # Stage 1: Verify Credentials
        console.print("-------------------------------------------")
        console.print("Stage #1 - Validate Credentials", style="bold")
        # Test Connection
        connection_success, logs = verify_credentials(config["cloudServiceProvider"], config["providerDetails"])
        if connection_success:
            print_step(f"Connecting to {provider_name}...", status="ok")
        else:
            print_step(f"Connecting to {provider_name}...", status="error")
            console.print(f"   ↳ {logs}", style="dim")
            logger.error(f"Credential verification failed: {logs}")
            return
        console.print("-------------------------------------------")

        # Stage 2: Test Permissions
        console.print("Stage #2 - Validate Permissions", style="bold")

        # Labels for permission types
        permission_reader_label = "Reader" if config["cloudServiceProvider"] == 1 else "ViewOnlyAccess"
        permission_cost_label = "Cost Management Reader" if config["cloudServiceProvider"] == 1 else "AWSBillingReadOnlyAccess"

        # Test permissions with spinners
        with console.status("Validating permissions...", spinner="dots"):
            permission_valid, permission_reader, permission_cost, logs = test_permissions(
                config["cloudServiceProvider"], config["providerDetails"]
            )

        # Output results for permission checks
        if permission_reader:
            print_step(f"Checking {permission_reader_label}...", status="ok")
        else:
            print_step(f"Checking {permission_reader_label}...", status="error", logs=logs)

        if permission_cost:
            print_step(f"Checking {permission_cost_label}...", status="ok")
        else:
            print_step(f"Checking {permission_cost_label}...", status="error", logs=logs)

        # Exit if permissions are invalid
        if not permission_valid:
            logger.error(f"Permission validation failed: {logs}")
            return

        console.print("-------------------------------------------")

        # Stage 3: Build Resource Inventory
        console.print("Stage #3 - Build Resource Inventory", style="bold")

        # Use a spinner to indicate progress
        with console.status(f"Building resource inventory for {provider_name}...", spinner="dots"):
            result = create_resource_inventory(
                config["cloudServiceProvider"],
                config["providerDetails"],
                report_path,
                raw_data_path,
            )

        if result["success"]:
            print_step(f"Building resource inventory for {provider_name}...", status="ok")
        else:
            print_step(f"Building resource inventory for {provider_name}...", status="error", logs=result["logs"])
            return

        console.print("-------------------------------------------")

        # Stage 4: Build Cost Inventory
        console.print("Stage #4 - Build Cost Inventory", style="bold")

        # Use a spinner to indicate progress
        with console.status(f"Building cost inventory for {provider_name}...", spinner="dots"):
            cost_result = create_cost_inventory(
                config["cloudServiceProvider"],
                config["providerDetails"],
                report_path,
                raw_data_path,
            )

        # Handle the result
        if cost_result["success"]:
            print_step(f"Building cost inventory for {provider_name}...", status="ok")
        else:
            print_step(f"Building cost inventory for {provider_name}...", status="error", logs=cost_result["logs"])
            return

        console.print("-------------------------------------------")

        name = config.get("name") or f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Stage 5 – Online / Offline Risk Assessment
        if mode == "online":
            console.print("Stage #5 – Online Risk Assessment", style="bold")

            sync_result = sync_assessment(
                name=name,
                started_at=started_at,
                report_path=report_path,
                metadata={
                    "cloud_service_provider": config["cloudServiceProvider"],
                    "exit_strategy":         config["exitStrategy"],
                    "assessment_type":       config["assessmentType"],
                },
                mode=mode,
                token=jwt,
            )

            status = "ok" if sync_result["success"] else "error"
            print_step("Sync assessment...", status=status, logs=sync_result["logs"])

        elif mode == "offline":
            console.print("Stage #5 – Offline Risk Assessment", style="bold")

            with console.status("Performing risk assessment...", spinner="dots"):
                risk_result = perform_risk_assessment(
                    exit_strategy=config["exitStrategy"],
                    report_path=report_path,
                    mode=mode,
                )

            status = "ok" if risk_result["success"] else "error"
            print_step("Performing risk assessment...", status=status, logs=risk_result["logs"])

        console.print("-------------------------------------------")

        # Stage 6: Generate Report
        console.print("Stage #6 - Generate Report", style="bold")

        # Use a spinner to indicate progress
        with console.status("Generating report...", spinner="dots"):
            report_status = generate_report(
                config["cloudServiceProvider"],
                config["providerDetails"],
                config["exitStrategy"],
                config["assessmentType"],
                name,
                report_path,
                raw_data_path
            )

        # Handle the result
        if report_status["success"]:
            print_step("Generating report...", status="ok")
        else:
            print_step("Generating report...", status="error", logs=report_status["logs"])
            return

        # Output the report path after the separator
        console.print("-------------------------------------------")
        console.print("Outputs:", style="bold")
        html_report_path = report_status.get("reports", {}).get("HTML")
        if html_report_path:
            console.print(f"HTML Report: {html_report_path}", style="cyan")
        pdf_report_path = report_status.get("reports", {}).get("PDF")
        if pdf_report_path:
            console.print(f"PDF Report: {pdf_report_path}", style="cyan")
        json_report_path = report_status.get("reports", {}).get("JSON")
        if html_report_path:
            console.print(f"JSON Report: {json_report_path}", style="cyan")
        console.print("-------------------------------------------")

    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="EscapeCloud - Community Edition",
        epilog=(
            "Example usage:\n"
            "  python3 main.py aws                        # Use manual input for AWS\n"
            "  python3 main.py aws --config config.json   # Use a configuration file for AWS\n"
            "  python3 main.py aws --profile PROFILE      # Use an AWS CLI profile\n"
            "  python3 main.py aws --name 'DMS System'    # Use a pre-defined assessment name\n"
            "  python3 main.py azure                      # Use manual input for Azure\n"
            "  python3 main.py azure --config config.json # Use a configuration file for Azure\n"
            "  python3 main.py azure --cli                # Use Azure CLI credentials\n"
            "  python3 main.py azure --name 'DMS System'  # Use a pre-defined assessment name\n"

        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="cloud_provider", help="Specify the cloud provider (aws or azure).")

    # Subparser for AWS
    aws_parser = subparsers.add_parser("aws", help="Perform an AWS assessment.")
    aws_group = aws_parser.add_mutually_exclusive_group(required=False)
    aws_group.add_argument("--config", type=str, help="Path to the configuration file (JSON format).")
    aws_group.add_argument("--profile", type=str, help="AWS profile name to use credentials from ~/.aws/credentials.")
    aws_parser.add_argument("--name", type=str, help="Assessment Name (Optional / Max. 50 characters).")


    # Subparser for Azure
    azure_parser = subparsers.add_parser("azure", help="Perform an Azure assessment.")
    azure_group = azure_parser.add_mutually_exclusive_group(required=False)
    azure_group.add_argument("--config", type=str, help="Path to the configuration file (JSON format).")
    azure_group.add_argument("--cli", action="store_true", help="Use Azure CLI credentials for authentication.")
    azure_parser.add_argument("--name", type=str, help="Assessment Name (Optional / Max. 50 characters).")

    return parser.parse_args()

def main():
    # Print ASCII art
    console.print(ascii_art, style="bold cyan")

    # Ensure latest dataset is available before proceeding
    initialize_dataset()

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
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Operation cancelled by user (Ctrl+C). Exiting gracefully.[/bold yellow]")
        #logger.warning("Process interrupted by user via KeyboardInterrupt.")
        sys.exit(0)
    except Exception as e:
        #logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)
