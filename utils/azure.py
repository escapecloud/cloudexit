# utils/azure.py
import importlib
import logging
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, requires
from typing import Any
from rich.console import Console

logger = logging.getLogger("main.utils.azure")
console = Console()

# The SDK classes the Azure paths use, and the distribution that provides each
SDK_SYMBOLS = (
    ("azure.identity", "AzureCliCredential"),
    ("azure.identity", "ClientSecretCredential"),
    ("azure.identity", "DefaultAzureCredential"),
    ("azure.core.exceptions", "AzureError"),
    ("azure.core.exceptions", "ClientAuthenticationError"),
    ("azure.mgmt.resource.resources", "ResourceManagementClient"),
    ("azure.mgmt.subscription", "SubscriptionClient"),
    ("azure.mgmt.costmanagement", "CostManagementClient"),
    ("azure.mgmt.costmanagement.models", "QueryDefinition"),
    ("azure.mgmt.costmanagement.models", "TimeframeType"),
    ("azure.mgmt.authorization", "AuthorizationManagementClient"),
)
SDK_DISTRIBUTIONS = {
    "azure.identity": "azure-identity",
    "azure.core": "azure-core",
    "azure.mgmt.resource": "azure-mgmt-resource",
    "azure.mgmt.subscription": "azure-mgmt-subscription",
    "azure.mgmt.costmanagement": "azure-mgmt-costmanagement",
    "azure.mgmt.authorization": "azure-mgmt-authorization",
}


class AzureSdkError(Exception):
    def __init__(self, module: str, name: str):
        self.module = module
        self.name = name
        super().__init__(
            f"The Azure SDK in this environment is incomplete (cannot import "
            f"{module}.{name}). Reinstall it with: pip install --force-reinstall "
            f"--no-deps {_requirement(module)}, or install cloudexit into a new "
            "virtual environment."
        )


def _requirement(module: str) -> str:
    distribution = next(
        (
            name
            for prefix, name in SDK_DISTRIBUTIONS.items()
            if module == prefix or module.startswith(f"{prefix}.")
        ),
        module,
    )
    try:
        # The pinned version this installation of cloudexit asks for
        for line in requires("cloudexit") or []:
            requirement = line.split(";")[0].replace(" ", "")
            if requirement.lower().startswith(f"{distribution}=="):
                return requirement
    except PackageNotFoundError:
        pass
    return distribution


def azure_import(module: str, name: str) -> Any:
    # The Azure SDK is imported where it is used, so a broken or missing SDK
    # cannot stop commands that never touch Azure.
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as exc:
        logger.debug("Cannot import %s.%s: %s", module, name, exc)
        raise AzureSdkError(module, name) from None


def require_azure_sdk() -> None:
    for module, name in SDK_SYMBOLS:
        azure_import(module, name)


def is_azure_cli_installed() -> bool:
    return shutil.which("az") is not None


def is_azure_cli_logged_in() -> bool:
    try:
        # Run the 'az account show' command to check if the user is logged in
        subprocess.run(
            ["az", "account", "show"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def is_azure_cli_token_expired() -> bool:
    AzureCliCredential = azure_import("azure.identity", "AzureCliCredential")
    ClientAuthenticationError = azure_import(
        "azure.core.exceptions", "ClientAuthenticationError"
    )
    credential = AzureCliCredential()
    try:
        credential.get_token("https://management.azure.com/.default")
        return False  # Token is valid
    except ClientAuthenticationError as e:
        if "AADSTS700082" in str(e):
            return True  # Token expired
        return False


def select_subscription(subscriptions: list[Any]) -> Any:
    # logger.info("Listing available subscriptions for selection.")
    console.print("Available Subscriptions:")
    for idx, sub in enumerate(subscriptions, start=1):
        console.print(f"{idx}. {sub.display_name} ({sub.subscription_id})")
    while True:
        try:
            selection = int(input("Select a subscription by number: ").strip())
            if not (1 <= selection <= len(subscriptions)):
                raise ValueError("Invalid subscription selection.")
            selected_subscription = subscriptions[selection - 1]
            # logger.info(f"Subscription selected: {selected_subscription.display_name} ({selected_subscription.subscription_id})")
            return selected_subscription
        except ValueError as e:
            logger.debug(f"Invalid subscription selection: {e}")
            console.print(f"[red]{e} Please select a valid number.[/red]")


def select_resource_group(resource_groups: list[Any]) -> str:
    # logger.info("Listing available resource groups for selection.")
    console.print("Available Resource Groups:")
    for idx, rg in enumerate(resource_groups, start=1):
        console.print(f"{idx}. {rg.name}")
    while True:
        try:
            selection = int(input("Select a resource group by number: ").strip())
            if not (1 <= selection <= len(resource_groups)):
                raise ValueError("Invalid resource group selection.")
            selected_resource_group = resource_groups[selection - 1].name
            # logger.info(f"Resource Group selected: {selected_resource_group}")
            return selected_resource_group
        except ValueError as e:
            logger.debug(f"Invalid resource group selection: {e}")
            console.print(f"[red]{e} Please select a valid number.[/red]")
