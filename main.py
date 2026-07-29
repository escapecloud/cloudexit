# main.py
import logging
import argparse
import boto3
import time
import sys
import os
import getpass
import traceback
from rich.console import Console
from rich.logging import RichHandler
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
from core.utils_egress import estimate_egress
from core.utils_report_egress import (
    generate_egress_html_report,
    generate_egress_pdf_report,
)
from core.utils_sync import write_assessment_payload
from utils.azure import (
    select_subscription,
    select_resource_group,
    is_azure_cli_installed,
    is_azure_cli_logged_in,
    is_azure_cli_token_expired,
)
from utils.aws import is_aws_cli_installed, is_aws_profile_valid
from utils.connection import resolve_mode
from utils.data import initialize_dataset
from utils.utils import (
    ascii_art,
    build_config,
    create_directory,
    load_config,
    prompt_required_inputs,
    print_help_message,
    print_step,
    require_env,
    require_env_int,
)
from utils.validate import validate_region, validate_config
from utils import codes
from utils.version import __version__

# Configure the logger (level is left to the handlers, see configure_logging)
logger = logging.getLogger(__name__)

# Initialize the console object
console = Console()

# Third-party loggers kept quiet unless the user opts into deep verbosity (-vv)
_THIRD_PARTY_NOISY = ("botocore", "boto3", "azure")

# Loggers with no diagnostic value ever — pinned to WARNING regardless of verbosity,
#  so they never flood the -vv console or run.log.
_ALWAYS_QUIET = ("PIL",)


def configure_logging(verbose: int = 0) -> None:
    """
    verbose == 0 -> WARNING (default; the Rich step UI is the primary output)
    verbose == 1 -> INFO    (--verbose: show our interaction narrative)
    verbose >= 2 -> DEBUG   (-vv: also un-mute third-party libraries)
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    if verbose == 0:
        console_level = logging.WARNING
    elif verbose == 1:
        console_level = logging.INFO
    else:
        console_level = logging.DEBUG

    console_handler = RichHandler(
        console=console, show_path=False, rich_tracebacks=True
    )
    console_handler.setLevel(console_level)
    root.addHandler(console_handler)

    third_party_level = logging.DEBUG if verbose >= 2 else logging.WARNING
    for name in _THIRD_PARTY_NOISY:
        logging.getLogger(name).setLevel(third_party_level)

    for name in _ALWAYS_QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


def add_run_log_handler(report_path: str) -> None:
    # A logging-setup failure must never abort the assessment, so degrade
    # gracefully if run.log cannot be created (e.g. dir missing or read-only).
    try:
        file_handler = logging.FileHandler(os.path.join(report_path, "run.log"))
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logging.getLogger().addHandler(file_handler)
    except OSError as exc:
        logger.warning("Could not create run.log in %s: %s", report_path, exc)


class ConfigError(Exception):
    pass


def _reject_egress_with_tfstate(args) -> None:
    # --egress needs live API access, so it cannot run against a state file.
    if getattr(args, "tfstate", None) and getattr(args, "egress", False):
        console.print(
            "[red]--egress cannot be combined with --tfstate. Egress estimation "
            "requires live cloud API access.[/red]"
        )
        sys.exit(codes.CONFIG)


def _aws_provider_from_profile(profile: str) -> dict:
    if not is_aws_cli_installed():
        console.print(
            "[red]AWS CLI is not installed. Install it from https://aws.amazon.com/cli/[/red]"
        )
        raise ConfigError
    if not is_aws_profile_valid(profile):
        console.print(
            f"[red]AWS profile '{profile}' is not configured. "
            f"Use `aws configure --profile {profile}`.[/red]"
        )
        raise ConfigError
    try:
        session = boto3.Session(profile_name=profile)
        credentials = session.get_credentials()
        if credentials is None:
            console.print(
                f"[red]AWS profile '{profile}' has no valid credentials. "
                f"Use `aws configure --profile {profile}`.[/red]"
            )
            raise ConfigError
        region = session.region_name or "us-east-1"
    except (NoCredentialsError, ProfileNotFound) as e:
        console.print(
            f"[red]AWS profile error: {str(e)}. Use `aws configure` to set up a profile.[/red]"
        )
        raise ConfigError

    provider_details = {
        "accessKey": credentials.access_key,
        "secretKey": credentials.secret_key,
        "region": region,
    }
    if getattr(credentials, "token", None):
        provider_details["sessionToken"] = credentials.token
    return provider_details


def _aws_provider_from_env() -> dict:
    access_key = require_env("AWS_ACCESS_KEY_ID", "AWS access key")
    secret_key = require_env("AWS_SECRET_ACCESS_KEY", "AWS secret key")
    region = require_env("AWS_DEFAULT_REGION", "AWS region")
    session_token = os.environ.get("AWS_SESSION_TOKEN", "").strip()
    try:
        validate_region(region)
    except ValueError as e:
        console.print(f"[red]AWS_DEFAULT_REGION: {e}[/red]")
        raise ConfigError

    provider_details = {
        "accessKey": access_key,
        "secretKey": secret_key,
        "region": region,
    }
    if session_token:
        provider_details["sessionToken"] = session_token
    return provider_details


def _aws_provider_from_prompt() -> dict:
    try:
        access_key = input("Enter AWS Access Key: ").strip()
        secret_key = getpass.getpass("Enter AWS Secret Key (input hidden): ").strip()

        # Validate AWS region input
        while True:
            region = input("Enter AWS region: ").strip()
            try:
                validate_region(region)
                break
            except ValueError as e:
                console.print(f"[red]{e} Please enter a valid AWS region.[/red]")
    except Exception as e:
        console.print(f"[red]Error during manual AWS configuration: {e}[/red]")
        raise ConfigError

    return {
        "accessKey": access_key,
        "secretKey": secret_key,
        "region": region,
    }


def handle_aws(args):
    cloud_provider = 2

    _reject_egress_with_tfstate(args)
    tfstate_path = getattr(args, "tfstate", None)

    if args.config:
        config = load_config(args.config)
        if not config:
            console.print("[red]Invalid or missing AWS configuration file.[/red]")
            raise ConfigError

        # Handle name field logic (priority: --name > config name > fallback)
        if args.name:
            config["name"] = args.name.strip()
        if "name" not in config or not config["name"].strip():
            config["name"] = (
                f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

        run_assessment(
            config,
            "aws",
            dry_run=args.dry_run,
            non_interactive=args.non_interactive,
            egress=args.egress,
        )
        return

    if args.non_interactive:
        exit_strategy = require_env_int(
            "ESC_EXIT_STRATEGY", "exit strategy (1 or 3)", {1, 3}
        )
        assessment_type = require_env_int(
            "ESC_ASSESSMENT_TYPE", "assessment type (1 or 2)", {1, 2}
        )
        if tfstate_path:
            provider_details = {"tfstatePath": tfstate_path}
        elif args.profile:
            provider_details = _aws_provider_from_profile(args.profile)
        else:
            provider_details = _aws_provider_from_env()
    elif tfstate_path:
        exit_strategy, assessment_type = prompt_required_inputs()
        provider_details = {"tfstatePath": tfstate_path}
    elif args.profile:
        provider_details = _aws_provider_from_profile(args.profile)
        exit_strategy, assessment_type = prompt_required_inputs()
    else:
        exit_strategy, assessment_type = prompt_required_inputs()
        provider_details = _aws_provider_from_prompt()

    config = build_config(
        cloud_provider, exit_strategy, assessment_type, provider_details, args
    )
    run_assessment(
        config,
        "aws",
        dry_run=args.dry_run,
        non_interactive=args.non_interactive,
        egress=args.egress,
    )


def _azure_cli_credential() -> DefaultAzureCredential:
    if not is_azure_cli_installed():
        console.print(
            "[red]Azure CLI is not installed. Install it from https://aka.ms/install-azure-cli.[/red]"
        )
        raise ConfigError
    if not is_azure_cli_logged_in():
        console.print(
            "[red]You are not logged in to Azure CLI. Please run 'az login' and try again.[/red]"
        )
        raise ConfigError
    if is_azure_cli_token_expired():
        console.print("[red]Your Azure CLI token has expired. Please run:[/red]")
        console.print(
            "[bold cyan]az login --scope https://management.azure.com/.default[/bold cyan]"
        )
        raise ConfigError
    return DefaultAzureCredential()


def _azure_provider_noninteractive(args) -> dict:
    subscription_id = require_env("ESC_SUBSCRIPTION_ID", "Azure subscription ID")
    resource_group = require_env("ESC_RESOURCE_GROUP", "Azure resource group")
    tenant_id = require_env("AZURE_TENANT_ID", "Azure tenant ID")
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

    if args.cli:
        credential = _azure_cli_credential()
    else:
        if client_secret:
            if not client_id:
                console.print(
                    "[red]--non-interactive with AZURE_CLIENT_SECRET also requires AZURE_CLIENT_ID.[/red]"
                )
                raise ConfigError
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
        else:
            if not client_id:
                console.print(
                    "[red]--non-interactive Azure OIDC requires AZURE_CLIENT_ID when AZURE_CLIENT_SECRET is not set.[/red]"
                )
                raise ConfigError
            credential = DefaultAzureCredential()

    provider_details = {
        "credential": credential,
        "tenantId": tenant_id,
        "subscriptionId": subscription_id,
        "resourceGroupName": resource_group,
    }
    if client_id:
        provider_details["clientId"] = client_id
    if not args.cli and client_secret:
        provider_details["clientSecret"] = client_secret
    return provider_details


def _azure_provider_from_cli() -> dict:
    credential = _azure_cli_credential()
    try:
        tenant_id = input("Enter Azure Tenant ID: ").strip()
        subscription_client = SubscriptionClient(credential)
        subscriptions = list(subscription_client.subscriptions.list())
        if not subscriptions:
            logger.error("No subscriptions found for the provided Azure credentials.")
            console.print(
                "[red]No subscriptions found for the provided credentials.[/red]"
            )
            raise ConfigError

        selected_subscription = select_subscription(subscriptions)
        subscription_id = selected_subscription.subscription_id

        resource_client = ResourceManagementClient(credential, subscription_id)
        resource_groups = list(resource_client.resource_groups.list())
        if not resource_groups:
            logger.error("No resource groups found in the selected subscription.")
            console.print(
                "[red]No resource groups found in the selected subscription.[/red]"
            )
            raise ConfigError

        resource_group_name = select_resource_group(resource_groups)
    except ConfigError:
        raise
    except Exception as e:
        logger.error(f"Error during Azure CLI processing: {e}", exc_info=True)
        console.print(f"[red]An error occurred: {e}[/red]")
        raise ConfigError

    return {
        "credential": credential,
        "tenantId": tenant_id,
        "subscriptionId": subscription_id,
        "resourceGroupName": resource_group_name,
    }


def _azure_provider_from_prompt() -> dict:
    tenant_id = input("Enter Azure Tenant ID: ").strip()
    client_id = input("Enter Service Principal / Client ID: ").strip()
    client_secret = getpass.getpass("Enter Client Secret (input hidden): ").strip()

    try:
        credential = ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )
        subscription_client = SubscriptionClient(credential)

        subscriptions = list(subscription_client.subscriptions.list())
        if not subscriptions:
            console.print(
                "[red]No subscriptions found. Please check your credentials.[/red]"
            )
            raise ConfigError

        selected_subscription = select_subscription(subscriptions)
        subscription_id = selected_subscription.subscription_id

        resource_client = ResourceManagementClient(credential, subscription_id)
        resource_groups = list(resource_client.resource_groups.list())
        if not resource_groups:
            console.print(
                "[red]No resource groups found in the selected subscription.[/red]"
            )
            raise ConfigError

        resource_group_name = select_resource_group(resource_groups)
    except ConfigError:
        raise
    except Exception as e:
        logger.error(f"Error during manual Azure configuration: {e}", exc_info=True)
        console.print(f"[red]An error occurred: {e}[/red]")
        raise ConfigError

    return {
        "tenantId": tenant_id,
        "clientId": client_id,
        "clientSecret": client_secret,
        "subscriptionId": subscription_id,
        "resourceGroupName": resource_group_name,
    }


def handle_azure(args):
    cloud_provider = 1

    _reject_egress_with_tfstate(args)
    tfstate_path = getattr(args, "tfstate", None)

    if args.config:
        config = load_config(args.config)
        if not config:
            console.print("[red]Invalid or missing Azure configuration file.[/red]")
            raise ConfigError

        # Handle name field logic (priority: --name > config name > fallback)
        if args.name:
            config["name"] = args.name.strip()
        if "name" not in config or not config["name"].strip():
            config["name"] = (
                f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

        run_assessment(
            config,
            "azure",
            dry_run=args.dry_run,
            non_interactive=args.non_interactive,
            egress=args.egress,
        )
        return

    if args.non_interactive:
        exit_strategy = require_env_int(
            "ESC_EXIT_STRATEGY", "exit strategy (1 or 3)", {1, 3}
        )
        assessment_type = require_env_int(
            "ESC_ASSESSMENT_TYPE", "assessment type (1 or 2)", {1, 2}
        )
        if tfstate_path:
            provider_details = {"tfstatePath": tfstate_path}
        else:
            provider_details = _azure_provider_noninteractive(args)
    elif tfstate_path:
        exit_strategy, assessment_type = prompt_required_inputs()
        provider_details = {"tfstatePath": tfstate_path}
    elif args.cli:
        provider_details = _azure_provider_from_cli()
        exit_strategy, assessment_type = prompt_required_inputs()
    else:
        exit_strategy, assessment_type = prompt_required_inputs()
        provider_details = _azure_provider_from_prompt()

    config = build_config(
        cloud_provider, exit_strategy, assessment_type, provider_details, args
    )
    run_assessment(
        config,
        "azure",
        dry_run=args.dry_run,
        non_interactive=args.non_interactive,
        egress=args.egress,
    )


def run_assessment(
    config, provider_name, *, dry_run=False, non_interactive=False, egress=False
):
    # Record the assessment start time to propagate across stages
    started_at = int(time.time())

    # Bound up front so the error handler can reference it even if the crash
    # happens before the report directory is created.
    report_path = None

    try:
        # Preliminary Stage: Validate configuration & create directory
        console.print("-------------------------------------------")
        console.print("Preliminary Stage", style="bold")
        try:
            validate_config(config)
            print_step("Configuration successfully validated.", status="ok")
        except ValueError as e:
            print_step("Configuration validation failed.", status="error", logs=str(e))
            sys.exit(codes.CONFIG)

        # tfstate mode reads a local state file: no credentials, no permission
        # check, no cost data — and no egress estimation, which needs live APIs.
        is_tfstate = bool(config["providerDetails"].get("tfstatePath"))
        if is_tfstate and egress:
            print_step(
                "Configuration validation failed.",
                status="error",
                logs="--egress requires live cloud API access and cannot be used with a Terraform state file.",
            )
            sys.exit(codes.CONFIG)

        # Detect ExitCloud Integration
        mode, jwt = resolve_mode()
        if dry_run:
            print_step("Dry run mode – no remote sync.", status="ok")
        elif mode == "online":
            print_step("ExitCloud integration configured.", status="ok")
        else:
            print_step("ExitCloud integration not configured.", status="warning")
            # Overwrite assessment type to basic
            if config["assessmentType"] != 1:
                print_step(
                    "Forcing Basic Assessment due to offline mode.", status="warning"
                )
                config["assessmentType"] = 1

        # Create directories
        try:
            report_path, raw_data_path = create_directory()
            add_run_log_handler(report_path)
            print_step("Directory successfully created.", status="ok")
        except RuntimeError as e:
            print_step("Directory creation failed.", status="error", logs=str(e))
            sys.exit(codes.CONFIG)

        # Handle the result
        provider_name = (
            "Microsoft Azure"
            if config["cloudServiceProvider"] == 1
            else "AWS" if config["cloudServiceProvider"] == 2 else "Unknown"
        )

        # Stage 1: Verify Credentials
        console.print("-------------------------------------------")
        console.print("Stage #1 - Validate Credentials", style="bold")
        if is_tfstate:
            print_step(
                "Skipped - tfstate mode (no cloud credentials used).", status="warning"
            )
        else:
            # Test Connection
            connection_success, logs = verify_credentials(
                config["cloudServiceProvider"], config["providerDetails"]
            )
            if connection_success:
                print_step(f"Connecting to {provider_name}...", status="ok")
            else:
                print_step(f"Connecting to {provider_name}...", status="error")
                console.print(f"   ↳ {logs}", style="dim")
                logger.error(f"Credential verification failed: {logs}")
                sys.exit(codes.CREDENTIALS)
        console.print("-------------------------------------------")

        # Stage 2: Test Permissions
        console.print("Stage #2 - Validate Permissions", style="bold")

        if is_tfstate:
            print_step(
                "Skipped - tfstate mode (no cloud permissions needed).",
                status="warning",
            )
        else:
            # Labels for permission types
            permission_reader_label = (
                "Reader" if config["cloudServiceProvider"] == 1 else "ViewOnlyAccess"
            )
            permission_cost_label = (
                "Cost Management Reader"
                if config["cloudServiceProvider"] == 1
                else "AWSBillingReadOnlyAccess"
            )

            # Test permissions with spinners
            with console.status("Validating permissions...", spinner="dots"):
                permission_valid, permission_reader, permission_cost, logs = (
                    test_permissions(
                        config["cloudServiceProvider"], config["providerDetails"]
                    )
                )

            # Output results for permission checks
            if permission_reader:
                print_step(f"Checking {permission_reader_label}...", status="ok")
            else:
                print_step(
                    f"Checking {permission_reader_label}...", status="error", logs=logs
                )

            if permission_cost:
                print_step(f"Checking {permission_cost_label}...", status="ok")
            else:
                print_step(
                    f"Checking {permission_cost_label}...", status="error", logs=logs
                )

            # Exit if permissions are invalid
            if not permission_valid:
                logger.error(f"Permission validation failed: {logs}")
                sys.exit(codes.PERMISSIONS)

        console.print("-------------------------------------------")

        # Stage 3: Build Resource Inventory
        console.print("Stage #3 - Build Resource Inventory", style="bold")

        # Use a spinner to indicate progress
        with console.status(
            f"Building resource inventory for {provider_name}...", spinner="dots"
        ):
            result = create_resource_inventory(
                config["cloudServiceProvider"],
                config["providerDetails"],
                report_path,
                raw_data_path,
            )

        if result["success"]:
            # In tfstate mode, resources belonging to another cloud provider are
            # dropped silently by design. Report the exclusion at step level so a
            # partially-assessed state cannot pass for a complete one.
            coverage = result.get("coverage") or {}
            excluded = coverage.get("instances_excluded_other_provider", 0)
            if excluded:
                print_step(
                    f"Building resource inventory for {provider_name}...",
                    status="warning",
                    logs=(
                        f"Assessed {coverage['instances_counted']} of "
                        f"{coverage['instances_total']} resources; {excluded} excluded "
                        f"(other cloud provider). See raw_data/tfstate_manifest.json."
                    ),
                )
            else:
                print_step(
                    f"Building resource inventory for {provider_name}...", status="ok"
                )
        else:
            print_step(
                f"Building resource inventory for {provider_name}...",
                status="error",
                logs=result["logs"],
            )
            sys.exit(codes.RESOURCE_INVENTORY)

        console.print("-------------------------------------------")

        # Stage 4: Build Cost Inventory
        console.print("Stage #4 - Build Cost Inventory", style="bold")

        if is_tfstate:
            print_step(
                "Skipped - tfstate mode (no billing data available).", status="warning"
            )
        else:
            # Use a spinner to indicate progress
            with console.status(
                f"Building cost inventory for {provider_name}...", spinner="dots"
            ):
                cost_result = create_cost_inventory(
                    config["cloudServiceProvider"],
                    config["providerDetails"],
                    report_path,
                    raw_data_path,
                )

            # Handle the result
            if cost_result["success"]:
                print_step(
                    f"Building cost inventory for {provider_name}...", status="ok"
                )
            else:
                print_step(
                    f"Building cost inventory for {provider_name}...",
                    status="error",
                    logs=cost_result["logs"],
                )
                sys.exit(codes.COST_INVENTORY)

        console.print("-------------------------------------------")

        name = (
            config.get("name")
            or f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        payload_path = None
        if dry_run:
            payload_path = write_assessment_payload(
                raw_data_path,
                report_path=report_path,
                name=name,
                started_at=started_at,
                exit_strategy=config["exitStrategy"],
                cloud_service_provider=config["cloudServiceProvider"],
                assessment_type=config["assessmentType"],
            )

        # Stage 5 – Online / Offline Risk Assessment
        if dry_run or mode == "offline":
            console.print("Stage #5 – Offline Risk Assessment", style="bold")

            with console.status("Performing risk assessment...", spinner="dots"):
                risk_result = perform_risk_assessment(
                    exit_strategy=config["exitStrategy"],
                    report_path=report_path,
                    mode="offline",
                )

            status = "ok" if risk_result["success"] else "error"
            print_step(
                "Performing risk assessment...", status=status, logs=risk_result["logs"]
            )
            if not risk_result["success"]:
                sys.exit(codes.RISK_ASSESSMENT)

        elif mode == "online":
            console.print("Stage #5 – Online Risk Assessment", style="bold")

            sync_result = sync_assessment(
                name=name,
                started_at=started_at,
                report_path=report_path,
                metadata={
                    "cloud_service_provider": config["cloudServiceProvider"],
                    "exit_strategy": config["exitStrategy"],
                    "assessment_type": config["assessmentType"],
                },
                mode=mode,
                token=jwt,
            )

            status = "ok" if sync_result["success"] else "error"
            print_step("Sync assessment...", status=status, logs=sync_result["logs"])
            if not sync_result["success"]:
                sys.exit(codes.RISK_ASSESSMENT)

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
                raw_data_path,
            )

        # Handle the result
        if report_status["success"]:
            print_step("Generating report...", status="ok")
        else:
            print_step(
                "Generating report...", status="error", logs=report_status["logs"]
            )
            sys.exit(codes.REPORT)

        # Stage 7: Egress Estimation (opt-in via --egress).
        # A failure here must not discard the completed assessment.
        egress_failed = False
        egress_json_path = None
        egress_html_path = None
        egress_pdf_path = None
        if egress:
            console.print("-------------------------------------------")
            console.print("Stage #7 – Egress Estimation", style="bold")

            with console.status("Estimating egress data...", spinner="dots"):
                egress_result = estimate_egress(
                    config["cloudServiceProvider"],
                    config["providerDetails"],
                    raw_data_path,
                    name=name,
                    exit_strategy=config["exitStrategy"],
                    assessment_type=config["assessmentType"],
                )

            if egress_result["success"]:
                print_step("Estimating egress data...", status="ok")
                egress_json_path = egress_result.get("json_path")

                with console.status("Generating egress report...", spinner="dots"):
                    egress_report_result = generate_egress_html_report(
                        report_path, egress_json_path
                    )

                if egress_report_result["success"]:
                    print_step("Generating egress report...", status="ok")
                    egress_html_path = egress_report_result.get("html_path")
                else:
                    print_step(
                        "Generating egress report...",
                        status="error",
                        logs=egress_report_result["logs"],
                    )
                    egress_failed = True

                with console.status("Generating egress PDF...", spinner="dots"):
                    egress_pdf_result = generate_egress_pdf_report(
                        report_path, egress_json_path, config["providerDetails"]
                    )

                if egress_pdf_result["success"]:
                    print_step("Generating egress PDF...", status="ok")
                    egress_pdf_path = egress_pdf_result.get("pdf_path")
                else:
                    print_step(
                        "Generating egress PDF...",
                        status="error",
                        logs=egress_pdf_result["logs"],
                    )
                    egress_failed = True
            else:
                print_step(
                    "Estimating egress data...",
                    status="error",
                    logs=egress_result["logs"],
                )
                egress_failed = True

        # Output the report path after the separator
        if not (dry_run or non_interactive):
            console.print("-------------------------------------------")
            console.print(
                "Need to see how a regulatory-aligned report looks (DORA / FINMA / UK PRA evidence support)?"
            )
            console.print(
                "We can generate one from your --dry-run output and share a sample with you -- or forward this to your risk or compliance team."
            )
            console.print(
                "Point-in-time evidence only; not a compliance certification or a complete exit strategy."
            )
            console.print("Contact: request_report@escapecloud.io")
        console.print("-------------------------------------------")
        console.print("Outputs:", style="bold")
        if payload_path:
            console.print(f"Payload: {payload_path}", style="cyan")
        html_report_path = report_status.get("reports", {}).get("HTML")
        if html_report_path:
            console.print(f"HTML Report: {html_report_path}", style="cyan")
        pdf_report_path = report_status.get("reports", {}).get("PDF")
        if pdf_report_path:
            console.print(f"PDF Report: {pdf_report_path}", style="cyan")
        json_report_path = report_status.get("reports", {}).get("JSON")
        if json_report_path:
            console.print(f"JSON Report: {json_report_path}", style="cyan")
        if egress_html_path:
            console.print(f"Egress Report: {egress_html_path}", style="cyan")
        if egress_pdf_path:
            console.print(f"Egress PDF: {egress_pdf_path}", style="cyan")
        console.print("-------------------------------------------")

        if egress_failed:
            sys.exit(codes.EGRESS)

    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        # Persist the full traceback so a one-line error is actionable. Falls
        # back to the cwd when the crash happened before the report dir existed.
        target_dir = report_path or os.getcwd()
        try:
            log_file = os.path.join(target_dir, f"error-{int(time.time())}.log")
            with open(log_file, "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
            console.print(f"[yellow]Full traceback written to: {log_file}[/yellow]")
        except OSError:
            console.print(
                "[yellow]Could not write a traceback file; see run.log.[/yellow]"
            )
        # Also funnel to run.log at DEBUG (kept off the default console).
        logger.debug("Unexpected error", exc_info=True)
        sys.exit(codes.UNEXPECTED)


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
            "  python3 main.py aws --config config.json --dry-run  # Local report + payload.json, no remote sync\n"
            "  python3 main.py azure --config config.json --dry-run\n"
            "  python3 main.py aws --config config.json --egress    # Estimate egress data volume\n"
            "  python3 main.py azure --config config.json --egress\n"
            "  python3 main.py aws --tfstate infra.tfstate          # Assess a Terraform/OpenTofu state file\n"
            "  python3 main.py azure --tfstate infra.tfstate --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"cloudexit v{__version__}",
        help="Show the CLI version and exit.",
    )

    subparsers = parser.add_subparsers(
        dest="cloud_provider", help="Specify the cloud provider (aws or azure)."
    )

    # Shared options available on every subcommand (e.g. `aws --verbose`)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v for INFO, -vv for DEBUG + third-party).",
    )

    # Subparser for AWS
    aws_parser = subparsers.add_parser(
        "aws", parents=[common], help="Perform an AWS assessment."
    )
    aws_group = aws_parser.add_mutually_exclusive_group(required=False)
    aws_group.add_argument(
        "--config", type=str, help="Path to the configuration file (JSON format)."
    )
    aws_group.add_argument(
        "--profile",
        type=str,
        help="AWS profile name to use credentials from ~/.aws/credentials.",
    )
    aws_group.add_argument(
        "--tfstate",
        type=str,
        help=(
            "Path to a Terraform/OpenTofu state file. Builds the inventory from "
            "the state instead of the AWS APIs; no credentials are used."
        ),
    )
    aws_parser.add_argument(
        "--name", type=str, help="Assessment Name (Optional / Max. 50 characters)."
    )
    aws_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without prompts; read all inputs from environment variables (for CI use).",
    )
    aws_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run a local assessment and also write raw_data/payload.json without "
            "remote sync."
        ),
    )
    aws_parser.add_argument(
        "--egress",
        action="store_true",
        help=(
            "Estimate how much data lives in the region and " "would need to move out."
        ),
    )

    # Subparser for Azure
    azure_parser = subparsers.add_parser(
        "azure", parents=[common], help="Perform an Azure assessment."
    )
    azure_group = azure_parser.add_mutually_exclusive_group(required=False)
    azure_group.add_argument(
        "--config", type=str, help="Path to the configuration file (JSON format)."
    )
    azure_group.add_argument(
        "--cli",
        action="store_true",
        help="Use Azure CLI credentials for authentication.",
    )
    azure_group.add_argument(
        "--tfstate",
        type=str,
        help=(
            "Path to a Terraform/OpenTofu state file. Builds the inventory from "
            "the state instead of the Azure APIs; no credentials are used."
        ),
    )
    azure_parser.add_argument(
        "--name", type=str, help="Assessment Name (Optional / Max. 50 characters)."
    )
    azure_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without prompts; read all inputs from environment variables (for CI use).",
    )
    azure_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run a local assessment and also write raw_data/payload.json without "
            "remote sync."
        ),
    )
    azure_parser.add_argument(
        "--egress",
        action="store_true",
        help=(
            "Estimate how much data lives in the resource group and "
            "would need to move out."
        ),
    )

    return parser.parse_args()


def main():
    # Parse arguments first so --version/--help exit before any side effects
    # (ASCII art, dataset download).
    args = parse_arguments()

    # Configure logging before any side effects so verbosity applies everywhere.
    configure_logging(getattr(args, "verbose", 0))

    # Print ASCII art
    console.print(ascii_art, style="bold cyan")

    # Nothing to do without a subcommand — show help before any dataset download.
    if not args.cloud_provider:
        print_help_message()
        return

    # Ensure latest dataset is available before proceeding
    initialize_dataset()

    # Dispatch based on provided arguments
    try:
        if args.cloud_provider == "aws":
            handle_aws(args)
        elif args.cloud_provider == "azure":
            handle_azure(args)
        else:
            console.print(
                "[red]Invalid command. Use 'aws' or 'azure' as the first argument.[/red]"
            )
            console.print(
                "[green]Run 'python3 main.py --help' for usage instructions.[/green]"
            )
    except ConfigError:
        sys.exit(codes.CONFIG)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print(
            "\n[bold yellow]Operation cancelled by user (Ctrl+C). Exiting gracefully.[/bold yellow]"
        )
        # logger.warning("Process interrupted by user via KeyboardInterrupt.")
        sys.exit(0)
    except Exception as e:
        # logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)
