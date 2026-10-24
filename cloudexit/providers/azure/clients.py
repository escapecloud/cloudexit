# cloudexit/providers/azure/clients.py
import logging
from typing import Any


from cloudexit.providers.azure.sdk import azure_import

logging.getLogger("azure").setLevel(logging.WARNING)


MANAGEMENT_URL = "https://management.azure.com"


def create_credential(provider_details: dict[str, Any]) -> Any:
    # A credential from the Azure CLI or the environment wins; otherwise the
    # service principal from the configuration is used.
    credential = provider_details.get("credential")
    if credential:
        return credential
    ClientSecretCredential = azure_import("azure.identity", "ClientSecretCredential")
    return ClientSecretCredential(
        tenant_id=provider_details["tenantId"],
        client_id=provider_details["clientId"],
        client_secret=provider_details["clientSecret"],
    )


def create_resource_client(credential: Any, subscription_id: str) -> Any:
    ResourceManagementClient = azure_import(
        "azure.mgmt.resource.resources", "ResourceManagementClient"
    )
    return ResourceManagementClient(credential, subscription_id)


def create_subscription_client(credential: Any) -> Any:
    SubscriptionClient = azure_import("azure.mgmt.subscription", "SubscriptionClient")
    return SubscriptionClient(credential)


def create_cost_client(credential: Any) -> Any:
    CostManagementClient = azure_import(
        "azure.mgmt.costmanagement", "CostManagementClient"
    )
    return CostManagementClient(credential, base_url=MANAGEMENT_URL)
