# cloudexit/providers/azure/sdk.py
import importlib
import logging
from importlib.metadata import PackageNotFoundError, requires
from typing import Any

logger = logging.getLogger(__name__)


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
