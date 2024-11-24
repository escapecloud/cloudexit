# azure.py
import logging
import subprocess
from rich.console import Console
from azure.identity import DefaultAzureCredential, AzureCliCredential
from azure.mgmt.resource import SubscriptionClient, ResourceManagementClient

logger = logging.getLogger("main.utils.azure")
console = Console()

def is_azure_cli_logged_in():
    try:
        # Run the 'az account show' command to check if the user is logged in
        subprocess.run(["az", "account", "show"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def select_subscription(subscriptions):
    #logger.info("Listing available subscriptions for selection.")
    console.print("Available Subscriptions:")
    for idx, sub in enumerate(subscriptions, start=1):
        console.print(f"{idx}. {sub.display_name} ({sub.subscription_id})")
    while True:
        try:
            selection = int(input("Select a subscription by number: ").strip())
            if not (1 <= selection <= len(subscriptions)):
                raise ValueError("Invalid subscription selection.")
            selected_subscription = subscriptions[selection - 1]
            #logger.info(f"Subscription selected: {selected_subscription.display_name} ({selected_subscription.subscription_id})")
            return selected_subscription
        except ValueError as e:
            logger.warning(f"Invalid subscription selection: {e}")
            console.print(f"[red]{e} Please select a valid number.[/red]")

def select_resource_group(resource_groups):
    #logger.info("Listing available resource groups for selection.")
    console.print("Available Resource Groups:")
    for idx, rg in enumerate(resource_groups, start=1):
        console.print(f"{idx}. {rg.name}")
    while True:
        try:
            selection = int(input("Select a resource group by number: ").strip())
            if not (1 <= selection <= len(resource_groups)):
                raise ValueError("Invalid resource group selection.")
            selected_resource_group = resource_groups[selection - 1].name
            #logger.info(f"Resource Group selected: {selected_resource_group}")
            return selected_resource_group
        except ValueError as e:
            logger.warning(f"Invalid resource group selection: {e}")
            console.print(f"[red]{e} Please select a valid number.[/red]")
