# cloudexit/cli/app.py
import logging
import contextlib
import json
import boto3
import tempfile
import time
import sys
import os
import traceback
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit
from rich.console import Console
from rich.logging import RichHandler
from datetime import datetime
from botocore.exceptions import NoCredentialsError, ProfileNotFound

# Import the functions
from cloudexit.engine import (
    verify_credentials,
    test_permissions,
    create_resource_inventory,
    create_cost_inventory,
    perform_risk_assessment,
    generate_report,
)
from cloudexit.providers.azure.clients import (
    create_resource_client,
    create_subscription_client,
)
from cloudexit.egress.estimate import estimate_egress
from cloudexit.platform.client import ApiError, check_key
from cloudexit.platform.run_output import (
    build_run_json,
    exit_code_of,
    run_json_fallback,
)
from cloudexit.platform.status import StatusReport, host_catalogue_lines, run_status
from cloudexit.platform.spool import (
    AUTO_FLUSH_BUDGET,
    DEFAULT_MAX_AGE_DAYS,
    Spool,
    flush,
    format_age,
    host_id,
    key_fingerprint,
    parse_age,
)
from cloudexit.platform.upload import (
    billing_line,
    error_failure,
    read_sync_files,
    report_info,
    upload,
    write_upload_record,
)
from cloudexit.platform.document import (
    RunContext,
    SyncDocumentError,
    new_uuid7,
    prepare_sync_document,
    validate_document,
)
from cloudexit.providers.azure.sdk import (
    AzureSdkError,
    azure_import,
    require_azure_sdk,
)
from cloudexit.providers.azure.cli_session import (
    select_subscription,
    select_resource_group,
    is_azure_cli_installed,
    is_azure_cli_logged_in,
    is_azure_cli_token_expired,
)
from cloudexit.providers.aws.cli_profile import (
    is_aws_cli_installed,
    is_aws_profile_valid,
)
from cloudexit.catalogue.store import (
    CatalogueError,
    CatalogueUnavailable,
    catalogue_base_url,
    catalogue_info,
    import_catalogue,
    installed_catalogue,
    set_active_catalogue,
    use_catalogue_file,
)
from cloudexit.catalogue.download import (
    ensure_catalogue,
    status_lines,
    update_catalogue,
)
from cloudexit.cli.parser import parse_arguments
from cloudexit.cli.prompts import (
    ascii_art,
    prompt_required_inputs,
    print_help_message,
)
from cloudexit.log_redaction import SecretRedactingFilter
from cloudexit.private_files import restrict_tree
from cloudexit.terminal import ask, print_step
from cloudexit.runconfig.loader import (
    build_config,
    load_config,
    require_env,
    require_env_int,
)
from cloudexit.settings import (
    SettingsError,
    config_file_path,
    describe,
    flags_from_args,
    load_settings,
)
from cloudexit.runconfig.validate import validate_region, validate_config
from cloudexit import codes

# Configure the logger (level is left to the handlers, see configure_logging)
logger = logging.getLogger(__name__)

# Initialize the console object
console = Console()

# The local report is always the Basic report, whatever depth was requested
LOCAL_REPORT_ASSESSMENT_TYPE = 1

# Third-party loggers kept quiet unless the user opts into deep verbosity (-vv)
_THIRD_PARTY_NOISY = ("botocore", "boto3", "azure", "urllib3")

# Loggers with no diagnostic value ever — pinned to WARNING regardless of verbosity,
#  so they never flood the -vv console or run.log. MSAL (behind the Azure
#  credentials) writes the client ID in its debug output, botocore's signing and
#  credential-resolution internals carry the credentials themselves, and run.log
#  is a file users share.
_ALWAYS_QUIET = ("PIL", "msal", "botocore.auth", "botocore.credentials")

# A last line of defence for the records that do stay on: both handlers drop
# credentials out of whatever reaches them.
_redacting_filter = SecretRedactingFilter()


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
    console_handler.addFilter(_redacting_filter)
    root.addHandler(console_handler)

    third_party_level = logging.DEBUG if verbose >= 2 else logging.WARNING
    for name in _THIRD_PARTY_NOISY:
        logging.getLogger(name).setLevel(third_party_level)

    for name in _ALWAYS_QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


_run_log_handler = None


def close_run_log_handler() -> None:
    # An open run.log keeps Windows from moving or deleting the report folder
    global _run_log_handler
    if _run_log_handler is not None:
        logging.getLogger().removeHandler(_run_log_handler)
        _run_log_handler.close()
        _run_log_handler = None


def add_run_log_handler(report_path: str) -> None:
    # A logging-setup failure must never abort the assessment, so degrade
    # gracefully if run.log cannot be created (e.g. dir missing or read-only).
    global _run_log_handler
    close_run_log_handler()
    try:
        file_handler = logging.FileHandler(
            os.path.join(report_path, "run.log"), encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        file_handler.addFilter(_redacting_filter)
        logging.getLogger().addHandler(file_handler)
        _run_log_handler = file_handler
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
            f"AWS profile '{profile}' is not configured. "
            f"Use `aws configure --profile {profile}`.",
            style="red",
            markup=False,
            emoji=False,
        )
        raise ConfigError
    try:
        session = boto3.Session(profile_name=profile)
        credentials = session.get_credentials()
        if credentials is None:
            console.print(
                f"AWS profile '{profile}' has no valid credentials. "
                f"Use `aws configure --profile {profile}`.",
                style="red",
                markup=False,
                emoji=False,
            )
            raise ConfigError
        region = session.region_name or "us-east-1"
    except (NoCredentialsError, ProfileNotFound) as e:
        console.print(
            f"AWS profile error: {str(e)}. Use `aws configure` to set up a profile.",
            style="red",
            markup=False,
            emoji=False,
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
        console.print(
            f"AWS_DEFAULT_REGION: {e}", style="red", markup=False, emoji=False
        )
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
        access_key = ask("Enter AWS Access Key: ").strip()
        secret_key = ask("Enter AWS Secret Key (input hidden): ", secret=True).strip()

        # Validate AWS region input
        while True:
            region = ask("Enter AWS region: ").strip()
            try:
                validate_region(region)
                break
            except ValueError as e:
                console.print(
                    f"{e} Please enter a valid AWS region.",
                    style="red",
                    markup=False,
                    emoji=False,
                )
    except Exception as e:
        console.print(
            f"Error during manual AWS configuration: {e}",
            style="red",
            markup=False,
            emoji=False,
        )
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
            settings=getattr(args, "settings", None),
            emit_payload=getattr(args, "emit_payload", None),
            egress_names=getattr(args, "egress_names", "plain"),
            spool_on_error=getattr(args, "spool_on_error", False),
            flush_spool=not getattr(args, "no_flush", False),
            strict=getattr(args, "strict", False),
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
        exit_strategy, assessment_type = prompt_required_inputs(
            _asks_assessment_type(args)
        )
        provider_details = {"tfstatePath": tfstate_path}
    elif args.profile:
        provider_details = _aws_provider_from_profile(args.profile)
        exit_strategy, assessment_type = prompt_required_inputs(
            _asks_assessment_type(args)
        )
    else:
        exit_strategy, assessment_type = prompt_required_inputs(
            _asks_assessment_type(args)
        )
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
        settings=getattr(args, "settings", None),
        emit_payload=getattr(args, "emit_payload", None),
        egress_names=getattr(args, "egress_names", "plain"),
        spool_on_error=getattr(args, "spool_on_error", False),
        flush_spool=not getattr(args, "no_flush", False),
        strict=getattr(args, "strict", False),
    )


def _require_azure_sdk() -> None:
    try:
        require_azure_sdk()
    except AzureSdkError as e:
        console.print(f"{e}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)


def _azure_cli_credential() -> Any:
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
    DefaultAzureCredential = azure_import("azure.identity", "DefaultAzureCredential")
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
            ClientSecretCredential = azure_import(
                "azure.identity", "ClientSecretCredential"
            )
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
            DefaultAzureCredential = azure_import(
                "azure.identity", "DefaultAzureCredential"
            )
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
        tenant_id = ask("Enter Azure Tenant ID: ").strip()
        subscription_client = create_subscription_client(credential)
        subscriptions = list(subscription_client.subscriptions.list())
        if not subscriptions:
            logger.error("No subscriptions found for the provided Azure credentials.")
            console.print(
                "[red]No subscriptions found for the provided credentials.[/red]"
            )
            raise ConfigError

        selected_subscription = select_subscription(subscriptions)
        subscription_id = selected_subscription.subscription_id

        resource_client = create_resource_client(credential, subscription_id)
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
        console.print(f"An error occurred: {e}", style="red", markup=False, emoji=False)
        raise ConfigError

    return {
        "credential": credential,
        "tenantId": tenant_id,
        "subscriptionId": subscription_id,
        "resourceGroupName": resource_group_name,
    }


def _azure_provider_from_prompt() -> dict:
    tenant_id = ask("Enter Azure Tenant ID: ").strip()
    client_id = ask("Enter Service Principal / Client ID: ").strip()
    client_secret = ask("Enter Client Secret (input hidden): ", secret=True).strip()

    ClientSecretCredential = azure_import("azure.identity", "ClientSecretCredential")
    try:
        credential = ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )
        subscription_client = create_subscription_client(credential)

        subscriptions = list(subscription_client.subscriptions.list())
        if not subscriptions:
            console.print(
                "[red]No subscriptions found. Please check your credentials.[/red]"
            )
            raise ConfigError

        selected_subscription = select_subscription(subscriptions)
        subscription_id = selected_subscription.subscription_id

        resource_client = create_resource_client(credential, subscription_id)
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
        console.print(f"An error occurred: {e}", style="red", markup=False, emoji=False)
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
    # Fail before any prompt; a config file is checked when the run starts
    if not tfstate_path and not args.config:
        _require_azure_sdk()

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
            settings=getattr(args, "settings", None),
            emit_payload=getattr(args, "emit_payload", None),
            egress_names=getattr(args, "egress_names", "plain"),
            spool_on_error=getattr(args, "spool_on_error", False),
            flush_spool=not getattr(args, "no_flush", False),
            strict=getattr(args, "strict", False),
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
        exit_strategy, assessment_type = prompt_required_inputs(
            _asks_assessment_type(args)
        )
        provider_details = {"tfstatePath": tfstate_path}
    elif args.cli:
        provider_details = _azure_provider_from_cli()
        exit_strategy, assessment_type = prompt_required_inputs(
            _asks_assessment_type(args)
        )
    else:
        exit_strategy, assessment_type = prompt_required_inputs(
            _asks_assessment_type(args)
        )
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
        settings=getattr(args, "settings", None),
        emit_payload=getattr(args, "emit_payload", None),
        egress_names=getattr(args, "egress_names", "plain"),
        spool_on_error=getattr(args, "spool_on_error", False),
        flush_spool=not getattr(args, "no_flush", False),
        strict=getattr(args, "strict", False),
    )


def create_directory(base_path="reports"):
    # The timestamp keeps the name readable; the random suffix and the exclusive
    # creation mean two runs started in the same second never share a folder.
    # The folder is for its owner only.
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    os.makedirs(base_path, mode=0o700, exist_ok=True)
    directory_path = tempfile.mkdtemp(prefix=f"{timestamp}-", dir=base_path)

    raw_data_path = os.path.join(directory_path, "raw_data")
    os.mkdir(raw_data_path, 0o700)

    return directory_path, raw_data_path


RUN = {}


def _missing_platform(host, key):
    if host and not key:
        return "A host is configured, but no API key."
    if key and not host:
        return "An API key is configured, but no host."
    return "No host and no API key are configured."


def _host_display(host):
    # The origin only: a path, a query or credentials have no place in a status
    # line the user reads on every run
    parts = urlsplit(host or "")
    if not parts.hostname:
        return host
    origin = parts.hostname if not parts.port else f"{parts.hostname}:{parts.port}"
    return f"{parts.scheme}://{origin}" if parts.scheme else origin


def _asks_assessment_type(args):
    # Standard needs an upload, so the question is only real when one will happen
    settings = getattr(args, "settings", None)
    if settings is None or getattr(args, "dry_run", False):
        return False
    if settings.upload.value == "off":
        return False
    return bool(settings.host.value and settings.api_key.value)


def _upload_mode(settings, dry_run):
    mode = settings.upload.value if settings else "auto"
    if not dry_run:
        return mode
    # --dry-run is --upload=off; only an explicit flag can contradict it
    if mode != "off" and settings and settings.upload.source.startswith("flag"):
        print_step(
            "Configuration validation failed.",
            status="error",
            logs=f"--dry-run cannot be combined with --upload={mode}.",
        )
        sys.exit(codes.CONFIG)
    return "off"


def _count(number, noun):
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _report_flush(result):
    if result.uploaded:
        print_step(
            f"Delivered {_count(len(result.uploaded), 'spooled sync')} from earlier runs.",
            status="ok",
        )
    if result.rejected:
        print_step(
            f"{_count(len(result.rejected), 'spooled sync')} will be rejected: "
            "too old for the platform.",
            status="warning",
            logs="see `cloudexit spool list`; `cloudexit spool purge` removes them",
        )
    if result.failed:
        print_step(
            f"{_count(len(result.failed), 'spooled sync')} could not be delivered.",
            status="warning",
            logs=", ".join(sorted(set(result.failed.values()))),
        )
    if result.other_key:
        print_step(
            f"{_count(len(result.other_key), 'spooled sync')} for another API key "
            "left in the spool.",
            status="warning",
            logs="not delivered with this key; run again with that key, "
            "or `cloudexit spool purge` removes them",
        )
    if result.pending:
        print_step(
            f"{_count(len(result.pending), 'spooled sync')} still waiting.",
            status="warning",
            logs=f"{result.stopped or 'unavailable'}; tried again at the next run",
        )


def _flush_earlier_syncs(settings):
    # Deliver what earlier runs could not, before the new run starts. Whatever
    # goes wrong here never blocks the new run.
    try:
        spool = Spool.from_settings(settings)
        if not spool.entries(host_id(settings.host.value)):
            return
        result = flush(settings, spool, budget=AUTO_FLUSH_BUDGET)
    except ApiError as e:
        print_step("Delivering earlier syncs failed.", status="warning", logs=e.message)
        return
    except Exception as e:
        logger.debug("Flushing the spool failed", exc_info=True)
        print_step(
            "Delivering earlier syncs failed.",
            status="warning",
            logs=f"{type(e).__name__}",
        )
        return
    _report_flush(result)


def run_assessment(
    config,
    provider_name,
    *,
    dry_run=False,
    non_interactive=False,
    egress=False,
    settings=None,
    emit_payload=None,
    egress_names="plain",
    spool_on_error=False,
    flush_spool=True,
    strict=False,
):
    # Record the assessment start time to propagate across stages. The sync id
    # is created here, at collection start, and identifies the whole run.
    started_at = int(time.time())
    sync_id = new_uuid7()
    RUN.clear()

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
        if config["cloudServiceProvider"] == 1 and not is_tfstate:
            _require_azure_sdk()
        if is_tfstate and egress:
            print_step(
                "Configuration validation failed.",
                status="error",
                logs="--egress requires live cloud API access and cannot be used with a Terraform state file.",
            )
            sys.exit(codes.CONFIG)

        # Detect a configured platform
        mode = _upload_mode(settings, dry_run)
        host = settings.host.value if settings else None
        key = settings.api_key.value if settings else None
        configured = bool(host and key)
        if mode == "required" and not configured:
            print_step(
                "Upload is required, but no platform is configured.",
                status="error",
                logs=f"{_missing_platform(host, key)} Set both, or use --upload=auto or --upload=off.",
            )
            sys.exit(codes.CONFIG)
        if mode == "auto" and bool(host) != bool(key):
            # Half a configuration is a mistake, not a reason to run offline
            print_step(
                "The platform configuration is incomplete.",
                status="error",
                logs=f"{_missing_platform(host, key)} Set both, or use --upload=off to run without a platform.",
            )
            sys.exit(codes.CONFIG)
        uploading = configured and mode != "off"
        signin_failure = None
        if dry_run:
            print_step("Dry run mode – no remote sync.", status="ok")
            console.print(
                "   ↳ --dry-run is --upload=off plus the sync document", style="dim"
            )
        elif mode == "off":
            print_step("Upload is off – nothing is sent to a platform.", status="ok")
            # Without an upload only the Basic assessment is possible
            if config["assessmentType"] != 1:
                print_step("Basic assessment: Standard needs an upload.", status="ok")
                config["assessmentType"] = 1
        elif configured:
            # A key that cannot be right is a failed upload, found before the
            # collection starts. It does not drop the run to Basic or discard
            # the local report.
            try:
                check_key(settings.api_key.value)
                print_step(f"Platform configured: {_host_display(host)}.", status="ok")
                if flush_spool:
                    _flush_earlier_syncs(settings)
            except ApiError as error:
                signin_failure = error_failure(error)
                print_step(
                    "Platform configured, but the API key is not usable.",
                    status="warning",
                    logs=f"{error.message}. The report is still written locally; the upload counts as failed.",
                )
        else:
            print_step(
                "Offline run: no platform configured; the report is written locally.",
                status="ok",
            )
            # Without a platform only the Basic assessment is possible
            if config["assessmentType"] != 1:
                print_step("Basic assessment: Standard needs an upload.", status="ok")
                config["assessmentType"] = 1
        RUN["skipped_reason"] = (
            "dry_run" if dry_run else "off" if mode == "off" else "offline"
        )

        # Create directories
        try:
            report_path, raw_data_path = create_directory()
            RUN["report_dir"] = report_path
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
                console.print(f"   ↳ {logs}", style="dim", markup=False, emoji=False)
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

        # Stage 5: Egress Estimation (opt-in via --egress).
        # A failure here must not discard the completed assessment.
        egress_failed = False
        egress_collected = False
        egress_json_path = None
        if egress:
            console.print("Stage #5 – Egress Estimation", style="bold")

            with console.status("Estimating egress data...", spinner="dots"):
                egress_result = estimate_egress(
                    config["cloudServiceProvider"],
                    config["providerDetails"],
                    raw_data_path,
                    report_path=report_path,
                    name=name,
                    exit_strategy=config["exitStrategy"],
                    assessment_type=config["assessmentType"],
                )

            if egress_result["success"]:
                print_step("Estimating egress data...", status="ok")
                egress_json_path = egress_result.get("json_path")
                egress_collected = True
            else:
                print_step(
                    "Estimating egress data...",
                    status="error",
                    logs=egress_result["logs"],
                )
                egress_failed = True
        if egress:
            console.print("-------------------------------------------")

        # Stage 6: Local risk assessment. Every run computes the Basic results
        # locally, connected or not; platform results are never used here.
        console.print("Stage #6 – Risk Assessment", style="bold")

        with console.status("Performing risk assessment...", spinner="dots"):
            risk_result = perform_risk_assessment(
                exit_strategy=config["exitStrategy"],
                report_path=report_path,
            )

        status = "ok" if risk_result["success"] else "error"
        print_step(
            "Performing risk assessment...", status=status, logs=risk_result["logs"]
        )
        if not risk_result["success"]:
            sys.exit(codes.RISK_ASSESSMENT)

        console.print("-------------------------------------------")

        # Sync document: the run's result in the API v2 shape. Without
        # --dry-run or --emit-payload a problem here does not discard the
        # finished report.
        sync_output = None
        sync_failed = False
        try:
            sync_output = prepare_sync_document(
                report_path,
                RunContext(
                    sync_id=sync_id,
                    started_at=started_at,
                    cloud_service_provider=config["cloudServiceProvider"],
                    assessment_type=config["assessmentType"],
                    provider_details=config["providerDetails"],
                    egress_collected=egress_collected,
                    egress_names=egress_names,
                ),
                emit_dir=emit_payload,
            )
        except (SyncDocumentError, OSError) as e:
            wanted = bool(dry_run or emit_payload)
            console.print("-------------------------------------------")
            print_step(
                "Writing the sync document...",
                status="error" if wanted else "warning",
                logs=str(e),
            )
            for problem in getattr(e, "problems", [])[:10]:
                console.print(f"   ↳ {problem}", style="dim", markup=False, emoji=False)
            sync_failed = wanted

        RUN["sync_json"] = str(sync_output.path) if sync_output else None

        # Stage 7: Upload, only when a platform is configured. A failure never
        # discards the local report: it is written next, with a banner.
        upload_result = signin_failure
        if signin_failure or (uploading and not dry_run):
            console.print("Stage #7 – Upload", style="bold")
            if upload_result is None:
                with console.status("Uploading to the platform...", spinner="dots"):
                    upload_result = upload(
                        sync_output.document if sync_output else None,
                        settings,
                        name=name,
                        meta={
                            "cloud_service_provider": config["cloudServiceProvider"],
                            "exit_strategy": config["exitStrategy"],
                            "assessment_type": config["assessmentType"],
                        },
                        spool_on_error=spool_on_error,
                    )
            if upload_result.sync_id is None:
                upload_result = replace(upload_result, sync_id=sync_id)
            if upload_result.success and not upload_result.pending:
                print_step("Uploading to the platform...", status="ok")
            elif upload_result.success:
                print_step(
                    "Uploading to the platform...",
                    status="warning",
                    logs="Accepted by the platform, still processing.",
                )
            else:
                print_step(
                    "Uploading to the platform...",
                    status="error",
                    logs=f"{upload_result.error_code or upload_result.reason}: {upload_result.message}",
                )
                if upload_result.request_id:
                    console.print(
                        f"   ↳ request id: {upload_result.request_id}",
                        style="dim",
                        markup=False,
                        emoji=False,
                    )
                if upload_result.spooled:
                    print_step(
                        "Sync saved for a later upload.",
                        status="warning",
                        logs="spooled; run `cloudexit spool flush`",
                    )
            for warning in upload_result.warnings:
                print_step("Platform warning", status="warning", logs=warning)
            write_upload_record(raw_data_path, upload_result)
            RUN["upload"] = upload_result
            console.print("-------------------------------------------")

        # Stage 8: Generate the local report (always Basic, connected or not)
        console.print("Stage #8 - Generate Report", style="bold")

        # Use a spinner to indicate progress
        with console.status("Generating report...", spinner="dots"):
            report_status = generate_report(
                config["cloudServiceProvider"],
                config["providerDetails"],
                config["exitStrategy"],
                LOCAL_REPORT_ASSESSMENT_TYPE,
                name,
                report_path,
                raw_data_path,
                upload=report_info(upload_result),
                sync_id=sync_id,
                egress_json_path=egress_json_path,
            )

        # Handle the result
        if report_status["success"]:
            print_step("Generating report...", status="ok")
        else:
            print_step(
                "Generating report...", status="error", logs=report_status["logs"]
            )
            sys.exit(codes.REPORT)

        # The egress PDF is written with the report, after the upload, so it can
        # show the run and the platform link. Its failure is an egress failure,
        # not a report failure.
        egress_pdf_path = report_status.get("reports", {}).get("EGRESS_PDF")
        if egress_pdf_path:
            print_step("Generating egress PDF...", status="ok")
        elif egress_json_path:
            print_step(
                "Generating egress PDF...",
                status="error",
                logs=report_status.get("egress_pdf_logs", "no egress PDF was written"),
            )
            egress_failed = True

        # Output the report path after the separator
        console.print("-------------------------------------------")
        console.print("Outputs:", style="bold")
        if upload_result and upload_result.success:
            console.print(
                (
                    f"Platform: {upload_result.url}"
                    if upload_result.url
                    else "Uploaded to the platform."
                ),
                style="cyan",
                markup=False,
                emoji=False,
            )
            charge = billing_line(upload_result.billing)
            if charge:
                console.print(charge, style="cyan")
        if sync_output:
            console.print(
                f"Sync document: {sync_output.path}",
                style="cyan",
                markup=False,
                emoji=False,
            )
            if sync_output.emitted:
                console.print(
                    f"Emitted copy: {sync_output.emitted}",
                    style="cyan",
                    markup=False,
                    emoji=False,
                )
        html_report_path = report_status.get("reports", {}).get("HTML")
        if html_report_path:
            console.print(
                f"HTML Report: {html_report_path}",
                style="cyan",
                markup=False,
                emoji=False,
            )
        pdf_report_path = report_status.get("reports", {}).get("PDF")
        if pdf_report_path:
            console.print(
                f"PDF Report: {pdf_report_path}",
                style="cyan",
                markup=False,
                emoji=False,
            )
        json_report_path = report_status.get("reports", {}).get("JSON")
        if json_report_path:
            console.print(
                f"JSON Report: {json_report_path}",
                style="cyan",
                markup=False,
                emoji=False,
            )
        if egress_pdf_path:
            console.print(
                f"Egress PDF: {egress_pdf_path}",
                style="cyan",
                markup=False,
                emoji=False,
            )
        console.print("-------------------------------------------")

        try:
            RUN["catalogue_version"] = catalogue_info(
                os.path.join(report_path, "data", "assessment.db")
            ).version
        except (CatalogueError, AttributeError):
            RUN["catalogue_version"] = None

        # Collection problems win over upload codes
        if sync_failed:
            sys.exit(codes.REPORT)
        if egress_failed:
            sys.exit(codes.EGRESS)
        if upload_result and upload_result.failed:
            sys.exit(upload_result.exit_code)
        if strict and upload_result and upload_result.warning_items:
            print_step(
                "Strict mode: the platform reported warnings.",
                status="error",
                logs="; ".join(
                    str(item.get("code") or item.get("message"))
                    for item in upload_result.warning_items
                ),
            )
            sys.exit(codes.UPLOAD_REJECTED)

    except Exception as e:
        console.print(f"Unexpected error: {e}", style="red", markup=False, emoji=False)
        # Persist the full traceback so a one-line error is actionable. Falls
        # back to the cwd when the crash happened before the report dir existed.
        target_dir = report_path or os.getcwd()
        try:
            log_file = os.path.join(target_dir, f"error-{int(time.time())}.log")
            with open(log_file, "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
            console.print(
                f"Full traceback written to: {log_file}",
                style="yellow",
                markup=False,
                emoji=False,
            )
        except OSError:
            console.print(
                "[yellow]Could not write a traceback file; see run.log.[/yellow]"
            )
        # Also funnel to run.log at DEBUG (kept off the default console).
        logger.debug("Unexpected error", exc_info=True)
        sys.exit(codes.UNEXPECTED)
    finally:
        close_run_log_handler()
        if report_path:
            # Everything the run wrote, however it was written, is for its owner
            restrict_tree(report_path)


def _catalogue_settings(args=None):
    try:
        settings = load_settings(flags_from_args(args) if args else None)
        return (
            catalogue_base_url(settings),
            settings.ca_bundle.value or True,
            settings,
        )
    except SettingsError as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)


def prepare_catalogue(args):
    settings = getattr(args, "settings", None)

    if getattr(args, "catalogue", None):
        try:
            path, info = use_catalogue_file(args.catalogue)
        except CatalogueError as e:
            console.print(f"{str(e)}", style="red", markup=False, emoji=False)
            sys.exit(codes.CONFIG)
        set_active_catalogue(path)
        print_step(
            f"Using catalogue {info.version if info else 'unknown'} from "
            f"{os.path.basename(args.catalogue)} (not installed).",
            status="ok",
        )
        return

    try:
        base_url = catalogue_base_url(settings or load_settings())
        result = ensure_catalogue(
            base_url, verify=(settings.ca_bundle.value if settings else None) or True
        )
    except SettingsError as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)
    except CatalogueUnavailable as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)

    version = result.version or "unknown"
    if result.status == "failed":
        print_step(
            f"Catalogue update failed, using {version} from {result.date}.",
            status="warning",
            logs=result.reason,
        )
    elif result.status == "updated":
        print_step(f"Catalogue updated to {version}.", status="ok")
    else:
        print_step(f"Catalogue {version} is up to date.", status="ok")


def handle_catalogue(args):
    base_url, verify, settings = _catalogue_settings(args)
    try:
        if args.catalogue_command == "status":
            for line in status_lines(base_url, verify=verify):
                print(line)
            installed = installed_catalogue()
            for line in host_catalogue_lines(
                settings, installed.version if installed else None
            ):
                print(line)
        elif args.catalogue_command == "update":
            result = update_catalogue(base_url, version=args.version, verify=verify)
            version = result.info.version if result.info else "unknown"
            if result.status == "current":
                print(f"Catalogue {version} is already installed.")
            else:
                print(f"Catalogue updated to {version}.")
        else:
            info = import_catalogue(args.file, sha256=args.sha256)
            print(f"Catalogue {info.version if info else 'unknown'} installed.")
    except (CatalogueError, OSError) as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)


def _settings_or_exit(args):
    try:
        return load_settings(flags_from_args(args))
    except SettingsError as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)


def _platform_or_exit(settings):
    if not (settings.host.value and settings.api_key.value):
        console.print(
            "[red]No platform is configured: set a host and an API key "
            "(see `cloudexit config show`).[/red]"
        )
        sys.exit(codes.UPLOAD_AUTH)


def handle_upload(args):
    settings = _settings_or_exit(args)
    _platform_or_exit(settings)
    try:
        check_key(settings.api_key.value)
    except ApiError as e:
        console.print(f"{e.message}", style="red", markup=False, emoji=False)
        sys.exit(e.exit_code)

    files, problems = read_sync_files(args.paths)
    exit_code = codes.SUCCESS
    for path, message in problems:
        console.print(f"{str(path)}: {message}", style="red", markup=False, emoji=False)
        exit_code = exit_code or codes.UPLOAD_REJECTED
    for path, document in files:
        try:
            validate_document(document)
        except SyncDocumentError as e:
            console.print(
                f"{str(path)}: {str(e)}", style="red", markup=False, emoji=False
            )
            exit_code = exit_code or codes.UPLOAD_REJECTED
            continue
        result = upload(
            document,
            settings,
            name=args.name or f"Exit Assessment {document['sync']['id'][:8]}",
            meta={"exit_strategy": args.exit_strategy},
            spool_on_error=args.spool_on_error,
        )
        shown = str(path)
        if result.success:
            console.print(
                f"{shown}: {result.message}"
                + (f" ({result.url})" if result.url else ""),
                style="cyan",
                markup=False,
                emoji=False,
            )
            charge = billing_line(result.billing)
            if charge:
                console.print(f"  {charge}", style="cyan", markup=False, emoji=False)
            for warning in result.warnings:
                console.print(
                    f"  warning: {warning}", style="dim", markup=False, emoji=False
                )
            continue
        exit_code = exit_code or result.exit_code
        console.print(
            f"{shown}: {result.error_code or result.reason or ''}: "
            f"{result.message}",
            style="red",
            markup=False,
            emoji=False,
        )
        if result.request_id:
            console.print(
                f"  request id: {result.request_id}",
                style="dim",
                markup=False,
                emoji=False,
            )
        if result.spooled:
            console.print("  spooled; run `cloudexit spool flush`", style="dim")
    if exit_code:
        sys.exit(exit_code)


def handle_spool(args):
    command = args.spool_command
    settings = _settings_or_exit(args)
    spool = Spool.from_settings(settings)
    flag_host = getattr(args, "host", None)
    try:
        only = host_id(flag_host) if flag_host else None
    except SettingsError as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)

    if command == "list":
        entries = spool.entries(only)
        if not entries:
            print("The spool is empty.")
            return
        now = time.time()
        print(f"{'SYNC ID':<38} {'HOST':<17} {'AGE':<8} {'ATTEMPTS':<9} LAST ERROR")
        key_id = key_fingerprint(settings.api_key.value)
        for entry in entries:
            print(
                f"{entry.sync_id:<38} {entry.host_id:<17} "
                f"{format_age(now - entry.created):<8} "
                f"{entry.meta.get('attempts', 0):<9} "
                f"{entry.meta.get('last_error') or '-'}"
                + (
                    "  will be rejected (too old)"
                    if now - entry.created > DEFAULT_MAX_AGE_DAYS * 86400
                    else ""
                )
                + ("" if entry.belongs_to(key_id) else "  not for the key in use")
            )
        return

    if command == "flush":
        _platform_or_exit(settings)
        try:
            check_key(settings.api_key.value)
            result = flush(settings, spool, host=flag_host)
        except ApiError as e:
            console.print(f"{e.message}", style="red", markup=False, emoji=False)
            sys.exit(e.exit_code)
        except SettingsError as e:
            console.print(f"{str(e)}", style="red", markup=False, emoji=False)
            sys.exit(codes.CONFIG)
        if result.empty:
            print("Nothing to deliver for this host.")
            return
        _report_flush(result)
        if result.pending or result.failed:
            sys.exit(codes.UPLOAD_UNAVAILABLE)
        return

    try:
        older_than = parse_age(args.older_than) if args.older_than else None
    except ValueError as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)
    now = time.time()
    due = [
        entry
        for entry in spool.entries(only)
        if older_than is None or now - entry.created >= older_than
    ]
    if not due:
        print("Nothing to purge.")
        return
    if not args.yes:
        answer = ask(
            f"Delete {_count(len(due), 'spooled sync')}? [y/N]: ",
            no_terminal_message=(
                "Purging asks for confirmation, but no terminal is attached. "
                "Add --yes."
            ),
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return
    for entry in due:
        spool.remove(entry)
    print(f"Deleted {_count(len(due), 'spooled sync')}.")


def handle_status(args):
    settings = _settings_or_exit(args)
    try:
        report = run_status(settings)
    except Exception as e:
        # Whatever the platform sent, the answer is still a report
        logger.debug("The status check failed", exc_info=True)
        report = StatusReport()
        report.problem(codes.UNEXPECTED, f"the status check failed: {type(e).__name__}")
    if args.output == "json":
        print(json.dumps(report.as_json(), indent=2))
    else:
        for line in report.lines():
            print(line)
    if report.exit_code:
        sys.exit(report.exit_code)


def handle_config(args):
    if args.config_command == "path":
        print(config_file_path())
        return

    try:
        settings = load_settings(flags_from_args(args))
    except SettingsError as e:
        console.print(f"{str(e)}", style="red", markup=False, emoji=False)
        sys.exit(codes.CONFIG)
    for line in describe(settings):
        print(line)


def _dispatch(args):
    # Ctrl+C anywhere below exits with a distinct code, whether the CLI is run
    # as `python3 main.py` or through the `cloudexit` console script.
    try:
        # The config and catalogue helpers print plain text and need no banner.
        if args.cloud_provider == "config":
            handle_config(args)
            return
        if args.cloud_provider == "catalogue":
            handle_catalogue(args)
            return
        if args.cloud_provider == "upload":
            handle_upload(args)
            return
        if args.cloud_provider == "spool":
            handle_spool(args)
            return
        if args.cloud_provider == "status":
            handle_status(args)
            return

        # Print ASCII art
        console.print(ascii_art, style="bold cyan")

        # Nothing to do without a subcommand — show help before any dataset download.
        if not args.cloud_provider:
            print_help_message()
            return

        # Resolve platform settings up front so a bad value fails before any work
        if args.cloud_provider in ("aws", "azure"):
            try:
                args.settings = load_settings(flags_from_args(args))
            except SettingsError as e:
                console.print(f"{str(e)}", style="red", markup=False, emoji=False)
                sys.exit(codes.CONFIG)

            # Make sure a catalogue is available before any work starts
            prepare_catalogue(args)

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
    except KeyboardInterrupt:
        console.print(
            "\n[bold yellow]Operation cancelled by user (Ctrl+C). Exiting gracefully.[/bold yellow]"
        )
        sys.exit(codes.INTERRUPTED)


def _absolute(path):
    return os.path.abspath(path) if path else None


def _emit_run_json(stream, exit_code):
    # Absolute, so a script can use them from any directory
    try:
        summary = build_run_json(
            exit_code=exit_code,
            report_dir=_absolute(RUN.get("report_dir")),
            sync_json=_absolute(RUN.get("sync_json")),
            upload=RUN.get("upload"),
            skipped_reason=RUN.get("skipped_reason"),
            catalogue_version=RUN.get("catalogue_version"),
        )
        text = json.dumps(summary, indent=2, allow_nan=False)
    except Exception as e:
        # One object on stdout whatever happened, so a script can always read it
        logger.debug("The run summary could not be built", exc_info=True)
        summary = run_json_fallback(
            exit_code=exit_code,
            report_dir=_absolute(RUN.get("report_dir")),
            sync_json=_absolute(RUN.get("sync_json")),
            error=f"the run summary could not be built: {type(e).__name__}",
        )
        text = json.dumps(summary, indent=2)
    stream.write(text + "\n")
    stream.flush()


def _dispatch_json(args):
    # stdout carries the summary and nothing else: the step output, the banner
    # and every message go to stderr while the run lasts.
    stdout = sys.stdout
    exit_code = codes.UNEXPECTED
    try:
        with contextlib.redirect_stdout(sys.stderr):
            try:
                _dispatch(args)
                exit_code = codes.SUCCESS
            except SystemExit as e:
                exit_code = exit_code_of(e.code)
                raise
            except Exception as e:
                console.print(
                    f"Unexpected error: {str(e)}",
                    style="red",
                    markup=False,
                    emoji=False,
                )
                sys.exit(codes.UNEXPECTED)
    finally:
        _emit_run_json(stdout, exit_code)


def _use_utf8_output():
    # The steps use arrows and dashes; a pipe or file whose encoding cannot
    # hold them (cp1252 on Windows) must not stop a run
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None) or "utf-8"
        if encoding.lower().replace("-", "") != "utf8":
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure:
                reconfigure(encoding="utf-8", errors="replace")


def main():
    # Parse arguments first so --version/--help exit before any side effects
    # (ASCII art, dataset download).
    args = parse_arguments()
    _use_utf8_output()

    # Configure logging before any side effects so verbosity applies everywhere.
    configure_logging(getattr(args, "verbose", 0))

    if (
        args.cloud_provider in ("aws", "azure")
        and getattr(args, "output", "") == "json"
    ):
        _dispatch_json(args)
    else:
        _dispatch(args)


def run():
    # `python -m cloudexit` and `python3 main.py` end here; the console script
    # calls main() directly and does not get this last-resort message.
    try:
        main()
    except Exception as e:
        # logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        console.print(f"Unexpected error: {e}", style="red", markup=False, emoji=False)
        sys.exit(1)
