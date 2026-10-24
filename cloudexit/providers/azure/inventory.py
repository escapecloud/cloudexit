# cloudexit/providers/azure/inventory.py
import json
import os
import logging
import sqlite3
from typing import Any
from datetime import date, datetime
from collections import defaultdict


from cloudexit.catalogue.db import connect, load_data
from cloudexit.providers.azure.sdk import azure_import
from cloudexit.providers.azure.clients import create_credential, create_resource_client

logger = logging.getLogger("core.engine.azure")


def plain_json(value: Any) -> Any:
    # SDK models are mappings (and datetimes appear in some of them); the raw
    # data files hold plain JSON.
    if hasattr(value, "keys"):
        return {key: plain_json(value[key]) for key in value.keys()}
    if isinstance(value, (list, tuple)):
        return [plain_json(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def is_resource_inventory_empty(
    credential: Any, subscription_id: str, resource_group_name: str
) -> bool:
    AzureError = azure_import("azure.core.exceptions", "AzureError")
    try:
        resource_client = create_resource_client(credential, subscription_id)
        # logger.info("Checking Azure resource inventory...")
        resources = list(
            resource_client.resources.list_by_resource_group(resource_group_name)
        )
        if not resources:
            # logger.info("No resources found in the resource group.")
            return True
        else:
            # logger.info("Resources found in the resource group.")
            return False
    except AzureError as e:
        # Re-raised to build_azure_resource_inventory, which logs at ERROR with
        # the traceback; keep this at DEBUG to avoid double-logging.
        logger.debug(
            f"Error checking Azure resource inventory: {str(e)}", exc_info=True
        )
        raise


def build_azure_resource_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> None:
    ClientAuthenticationError = azure_import(
        "azure.core.exceptions", "ClientAuthenticationError"
    )
    try:
        credential = create_credential(provider_details)
        subscription_id = provider_details["subscriptionId"]
        resource_group_name = provider_details["resourceGroupName"]

        db_path = os.path.join(report_path, "data", "assessment.db")

        # Check if resource inventory is empty
        if is_resource_inventory_empty(
            credential, subscription_id, resource_group_name
        ):
            logger.warning(
                "The selected resource group does not contain any resources."
            )
            return

        resource_client = create_resource_client(credential, subscription_id)

        # Fetch resources and serialize to raw JSON
        resources = list(
            resource_client.resources.list_by_resource_group(resource_group_name)
        )
        raw_data = [plain_json(resource) for resource in resources]

        # Save raw data to a JSON file
        raw_file_path = os.path.join(raw_data_path, "resource_inventory_raw_data.json")
        with open(raw_file_path, "w", encoding="utf-8") as raw_file:
            json.dump(raw_data, raw_file, indent=4)

        # Load resource type mapping from the assessment database
        resource_type_mapping = getattr(
            build_azure_resource_inventory, "_resource_type_cache", None
        )
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
            # Non-regional resources have no location; Azure calls them "global"
            resource_location = (resource.location or "").strip().lower() or "global"
            aggregated_resources[(resource_type_code, resource_location)] += 1

        # Insert data into SQLite
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()
            data_to_insert = [
                (
                    resource_type_mapping[resource_type_code]["id"],
                    resource_location,
                    resource_count,
                )
                for (
                    resource_type_code,
                    resource_location,
                ), resource_count in aggregated_resources.items()
                if resource_type_code in resource_type_mapping
            ]
            cursor.executemany(
                """
                INSERT INTO resource_inventory (resource_type, location, count)
                VALUES (?, ?, ?)
                ON CONFLICT(resource_type, location) DO UPDATE SET count = excluded.count
                """,
                data_to_insert,
            )
            conn.commit()

    except ClientAuthenticationError as e:
        logger.error(f"Azure authentication error: {str(e)}", exc_info=True)
        raise
    except sqlite3.Error as e:
        logger.error(f"SQLite error: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error fetching Azure resources: {str(e)}", exc_info=True)
        raise
