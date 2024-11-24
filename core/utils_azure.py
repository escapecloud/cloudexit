#utils_azure.py
import json
import os
import logging
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import QueryDefinition, TimeframeType
from azure.core.exceptions import AzureError, ClientAuthenticationError

logger = logging.getLogger("core.engine.azure")
logging.getLogger("azure").setLevel(logging.WARNING)

def is_resource_inventory_empty(credential, subscription_id, resource_group_name):
    try:
        resource_client = ResourceManagementClient(credential, subscription_id)
        #logger.info("Checking Azure resource inventory...")
        resources = list(resource_client.resources.list_by_resource_group(resource_group_name))
        if not resources:
            #logger.info("No resources found in the resource group.")
            return True
        else:
            #logger.info("Resources found in the resource group.")
            return False
    except AzureError as e:
        logger.error(f"Error checking Azure resource inventory: {str(e)}", exc_info=True)
        raise

def build_azure_resource_inventory(cloud_service_provider, provider_details, report_path, raw_data_path):
    try:
        # Use DefaultAzureCredential if provided, otherwise fall back to ClientSecretCredential
        credential = provider_details.get("credential") or ClientSecretCredential(
            tenant_id=provider_details["tenantId"],
            client_id=provider_details["clientId"],
            client_secret=provider_details["clientSecret"]
        )
        subscription_id = provider_details["subscriptionId"]
        resource_group_name = provider_details["resourceGroupName"]

        # Check if resource inventory is empty
        if is_resource_inventory_empty(credential, subscription_id, resource_group_name):
            #logger.warning("Azure resource inventory is empty.")
            return

        resource_client = ResourceManagementClient(credential, subscription_id)
        #logger.info("Fetching Azure resources...")

        # Fetch resources and serialize to raw JSON
        resources = list(resource_client.resources.list_by_resource_group(resource_group_name))
        raw_data = [resource.serialize(True) for resource in resources]
        #logger.info(f"Serialized Resources RAW Data: {raw_data}")

        # Save raw data to a JSON file
        raw_file_path = os.path.join(raw_data_path, "resource_inventory_raw_data.json")
        with open(raw_file_path, "w", encoding="utf-8") as raw_file:
            json.dump(raw_data, raw_file, indent=4)
        #logger.info(f"Azure raw resource inventory saved to {raw_file_path}")

        # Load the ResourceType mapping from JSON
        with open("datasets/resourcetype.json", "r", encoding="utf-8") as f:
            resource_type_mapping = {
                item["code"].strip().lower(): {"id": item["id"], "name": item["name"]}
                for item in json.load(f)
            }

        # Process resources and create a structured summary
        # Process resources and create a structured summary
        resource_summary = {}
        resource_inventory_id_counter = 1  # Unique ID for each resource

        for resource in resources:
            resource_type_code = resource.type.strip().lower()
            resource_location = resource.location.strip().lower()

            # Map resource type code to resource_type_id and resource_name
            resource_info = resource_type_mapping.get(resource_type_code)
            if not resource_info:
                continue  # Skip if no matching ResourceType found

            resource_type_id = resource_info["id"]
            resource_name = resource_info["name"]

            # Check for duplicates and merge if necessary
            resource_key = (resource_name, resource_type_id, resource_location)  # Unique key for deduplication
            if resource_key in resource_summary:
                resource_summary[resource_key]["count"] += 1
            else:
                resource_summary[resource_key] = {
                    "resource_name": resource_name,
                    "resource_type": resource_type_id,
                    "location": resource_location,
                    "count": 1
                }

            # Convert resource_summary to the desired dictionary structure with numbered keys
            resource_summary_numbered = {
                str(idx): details
                for idx, details in enumerate(resource_summary.values(), start=1)
            }

        # Log the resource summary
        #logger.info(f"Resource summary: {resource_summary}")

        # Save structured data to a JSON file
        structured_file_path = os.path.join(report_path, "resource_inventory_standard_data.json")
        with open(structured_file_path, "w", encoding="utf-8") as structured_file:
            json.dump(resource_summary_numbered, structured_file, indent=4)

        #logger.info(f"Azure structured resource inventory saved to {structured_file_path}")

    except ClientAuthenticationError as e:
        logger.error(f"Azure authentication error: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Error fetching Azure resources: {str(e)}", exc_info=True)

def get_missing_months_azure(processed_costs, months_back):
    today = date.today()
    start_date = today.replace(day=1) - relativedelta(months=months_back)
    all_months = {(start_date + relativedelta(months=i)).replace(day=1) for i in range(months_back)}

    processed_months = set()
    for month_str in processed_costs:
        try:
            # Attempt parsing with full timestamp format
            month_date = datetime.strptime(month_str, '%Y-%m-%dT%H:%M:%S').date().replace(day=1)
        except ValueError:
            # Fallback to date-only format if full timestamp fails
            month_date = datetime.strptime(month_str, '%Y-%m-%d').date().replace(day=1)
        processed_months.add(month_date)

    return all_months - processed_months

def build_azure_cost_inventory(cloud_service_provider, provider_details, report_path, raw_data_path):
    try:
        # Use DefaultAzureCredential if provided, otherwise fall back to ClientSecretCredential
        credential = provider_details.get("credential") or ClientSecretCredential(
            tenant_id=provider_details["tenantId"],
            client_id=provider_details["clientId"],
            client_secret=provider_details["clientSecret"]
        )
        cost_management_client = CostManagementClient(credential, base_url="https://management.azure.com")

        end_time = date.today()
        start_time = end_time.replace(day=1) - timedelta(days=180)
        start_time = start_time.replace(day=1)

        query = QueryDefinition(
            type='Usage',
            timeframe=TimeframeType.CUSTOM,
            time_period={'from': start_time.strftime('%Y-%m-%dT00:00:00Z'), 'to': end_time.strftime('%Y-%m-%dT00:00:00Z')},
            dataset={
                'granularity': 'Monthly',
                'aggregation': {
                    'totalCost': {'name': 'Cost', 'function': 'Sum'}
                }
            }
        )

        cost_data = cost_management_client.query.usage(
            f'/subscriptions/{provider_details["subscriptionId"]}/resourceGroups/{provider_details["resourceGroupName"]}', query
        )

        cost_inventory_raw_path = os.path.join(raw_data_path, "cost_inventory_raw_data.json")
        with open(cost_inventory_raw_path, "w", encoding="utf-8") as raw_file:
            json.dump(cost_data.as_dict(), raw_file, indent=4)

        structured_costs = {}
        for row in cost_data.rows:
            cost, month_str, currency = row
            month_date = datetime.strptime(month_str, '%Y-%m-%dT%H:%M:%S').date().replace(day=1)
            structured_costs[month_date.isoformat()] = {"cost": cost, "currency": currency}

        missing_months = get_missing_months_azure(structured_costs.keys(), 6)
        for missing_month in missing_months:
            structured_costs[missing_month.isoformat()] = {"cost": 0.00, "currency": currency}

        cost_inventory_standard_path = os.path.join(report_path, "cost_inventory_standard_data.json")
        with open(cost_inventory_standard_path, "w", encoding="utf-8") as structured_file:
            json.dump(structured_costs, structured_file, indent=4)

    except Exception as e:
        logger.error(f"Error creating Azure cost inventory: {str(e)}", exc_info=True)
        raise
