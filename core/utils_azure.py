#utils_azure.py
import json
import os
import logging
import sqlite3
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import QueryDefinition, TimeframeType
from azure.core.exceptions import AzureError, ClientAuthenticationError

from .utils_db import connect, load_data

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

        db_path = os.path.join(report_path, "data", "assessment.db")

        # Check if resource inventory is empty
        if is_resource_inventory_empty(credential, subscription_id, resource_group_name):
            logger.warning("The selected resource group does not contain any resources.")
            return

        resource_client = ResourceManagementClient(credential, subscription_id)

        # Fetch resources and serialize to raw JSON
        resources = list(resource_client.resources.list_by_resource_group(resource_group_name))
        raw_data = [resource.serialize(True) for resource in resources]

        # Save raw data to a JSON file
        raw_file_path = os.path.join(raw_data_path, "resource_inventory_raw_data.json")
        with open(raw_file_path, "w", encoding="utf-8") as raw_file:
            json.dump(raw_data, raw_file, indent=4)

        # Load resource type mapping from the assessment database
        resource_type_mapping = getattr(build_azure_resource_inventory, "_resource_type_cache", None)
        if resource_type_mapping is None:
            resource_type_mapping = {
                item["code"].strip().lower(): {"id": item["id"], "name": item["name"]}
                for item in load_data("resourcetype", db_path=db_path)
                if item["csp"] == 1 and item["status"] == "t"
            }
            build_azure_resource_inventory._resource_type_cache = resource_type_mapping

        # Aggregate resources by type and location
        aggregated_resources = defaultdict(int)
        for resource in resources:
            resource_type_code = resource.type.strip().lower()
            resource_location = resource.location.strip().lower()
            aggregated_resources[(resource_type_code, resource_location)] += 1

        # Insert data into SQLite
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()
            data_to_insert = [
                (resource_type_mapping[resource_type_code]["id"], resource_location, resource_count)
                for (resource_type_code, resource_location), resource_count in aggregated_resources.items()
                if resource_type_code in resource_type_mapping
            ]
            cursor.executemany(
                """
                INSERT INTO resource_inventory (resource_type, location, count)
                VALUES (?, ?, ?)
                ON CONFLICT(resource_type, location) DO UPDATE SET count = excluded.count
                """,
                data_to_insert
            )
            conn.commit()

    except ClientAuthenticationError as e:
        logger.error(f"Azure authentication error: {str(e)}", exc_info=True)
    except sqlite3.Error as e:
        logger.error(f"SQLite error: {str(e)}", exc_info=True)
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

        db_path = os.path.join(report_path, "data", "assessment.db")

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

        # Insert structured cost data into SQLite
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()

            for row in cost_data.rows:
                cost, month_str, currency = row
                month_date = datetime.strptime(month_str, '%Y-%m-%dT%H:%M:%S').date().replace(day=1).isoformat()

                # Insert or update cost data
                cursor.execute(
                    """
                    INSERT INTO cost_inventory (month, cost, currency)
                    VALUES (?, ?, ?)
                    ON CONFLICT(month) DO UPDATE SET
                        cost = excluded.cost,
                        currency = excluded.currency
                    """,
                    (month_date, cost, currency)
                )

            # Extract months already in the cost data
            structured_months = {datetime.strptime(row[1], '%Y-%m-%dT%H:%M:%S').date() for row in cost_data.rows}

            # Identify missing months and insert with zero cost
            missing_months = get_missing_months_azure(
                {month.isoformat() for month in structured_months}, 6
            )
            for missing_month in missing_months:
                cursor.execute(
                    """
                    INSERT INTO cost_inventory (month, cost, currency)
                    VALUES (?, 0.00, ?)
                    ON CONFLICT(month) DO UPDATE SET
                        currency = excluded.currency
                    """,
                    (missing_month.isoformat(), currency)
                )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"SQLite error: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Error creating Azure cost inventory: {str(e)}", exc_info=True)
        raise
