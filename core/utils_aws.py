# core/utils_aws.py
import boto3
import json
import os
import logging
import sqlite3
from typing import Any
from datetime import date, datetime, timezone
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from botocore.config import Config

from .utils_db import connect, load_data

logger = logging.getLogger("core.engine.aws")

AWS_RETRY_CONFIG = Config(retries={"mode": "adaptive", "max_attempts": 8})

# Upper bound on the items collected for a single resource type. Some list
# operations (e.g. describe_images, describe_snapshots) return every publicly
# shared resource in the region when called without an owner filter, which would
# otherwise stall the scan and blow up the raw-data file.
MAX_ITEMS_PER_RESOURCE_TYPE = 50_000


def extract_result_path(container: Any, result_path: list) -> list:
    if isinstance(result_path, str):
        result_path = [result_path]
    value = container
    for key in result_path:
        if not isinstance(value, dict):
            return []
        value = value.get(key)
    return value if isinstance(value, list) else []


def paginate(
    client: Any,
    operation_name: str,
    result_path: list,
    max_items: int = MAX_ITEMS_PER_RESOURCE_TYPE,
    **kwargs: Any,
) -> list:
    items: list = []
    for page in client.get_paginator(operation_name).paginate(**kwargs):
        items.extend(extract_result_path(page, result_path))
        if len(items) >= max_items:
            return items[:max_items]
    return items


def paginate_or_call(
    client: Any,
    operation_name: str,
    result_path: list,
    max_items: int = MAX_ITEMS_PER_RESOURCE_TYPE,
    **kwargs: Any,
) -> list:
    """paginate() when boto3 supports it for this operation, else a single call."""
    if client.can_paginate(operation_name):
        return paginate(client, operation_name, result_path, max_items, **kwargs)
    response = getattr(client, operation_name)(**kwargs)
    return extract_result_path(response, result_path)[:max_items]


def parse_resource_type_params(raw_params: Any) -> dict:
    if isinstance(raw_params, dict):
        return raw_params
    try:
        parsed = json.loads(raw_params or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_aws_call_spec(resource_type_code: str, params: dict) -> dict | None:
    service = params.get("service")
    operation = params.get("operation")
    if service and operation:
        return {
            "source": "params",
            "service": service,
            "operation": operation,
            "result_path": list(params.get("result_path") or []),
            "kwargs": dict(params.get("kwargs") or {}),
        }

    parts = resource_type_code.split(".")
    if len(parts) >= 4 and parts[0] == "AWS":
        return {
            "source": "code",
            "service": parts[1],
            "operation": parts[2],
            "result_path": [part.strip() for part in parts[3:]],
            "kwargs": {},
        }

    return None


def convert_datetime(obj: Any) -> Any:
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = convert_datetime(v)
    elif isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = convert_datetime(obj[i])
    elif isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def build_aws_resource_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> None:
    try:
        access_key = provider_details["accessKey"]
        secret_key = provider_details["secretKey"]
        region = provider_details["region"]

        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=provider_details.get("sessionToken"),
            region_name=region,
        )

        db_path = os.path.join(report_path, "data", "assessment.db")

        # Load the ResourceType mapping
        resource_type_mapping = {
            item["code"]: {
                "id": item["id"],
                "name": item["name"],
                "params": item.get("params"),
            }
            for item in load_data("resourcetype")
            if item["csp"] == 2 and item["status"] == "t"
        }

        # Save raw data for debugging and auditing purposes
        raw_data = []

        # Aggregate resources by type and location
        aggregated_resources = defaultdict(int)

        # How each catalogue row was resolved, summarised into run.log once the
        # sweep is done. Without it a scan result cannot be traced back to the
        # master data that produced it.
        spec_sources = defaultdict(int)

        # Iterate through each resource type in the JSON
        for idx, (resource_type_code, resource_info) in enumerate(
            resource_type_mapping.items(), start=1
        ):
            params = parse_resource_type_params(resource_info.get("params"))
            spec = resolve_aws_call_spec(resource_type_code, params)
            if spec is None:
                if len(resource_type_code.split(".")) == 2:
                    # Service-level placeholder (e.g. AWS.iam) with nothing to
                    # call yet -- intentional, so keep it off the console.
                    spec_sources["placeholder"] += 1
                    logger.debug(
                        "No call spec for placeholder resource type %s. Skipping.",
                        resource_type_code,
                    )
                else:
                    spec_sources["invalid"] += 1
                    logger.warning(
                        "Invalid resource type format: %s. Skipping.",
                        resource_type_code,
                    )
                continue

            service_name = spec["service"]
            operation_name = spec["operation"]
            result_path = spec["result_path"]
            call_kwargs = spec["kwargs"]

            spec_sources[spec["source"]] += 1
            logger.debug(
                "Resolved %s from %s -> %s.%s result_path=%s kwargs=%s",
                resource_type_code,
                spec["source"],
                service_name,
                operation_name,
                result_path,
                call_kwargs,
            )

            try:
                client = session.client(
                    service_name, region_name=region, config=AWS_RETRY_CONFIG
                )
                if not hasattr(client, operation_name):
                    # logger.error(f"Operation {operation_name} does not exist for service {service_name}")
                    continue

                resources = paginate_or_call(
                    client,
                    operation_name,
                    result_path,
                    MAX_ITEMS_PER_RESOURCE_TYPE,
                    **call_kwargs,
                )
                if len(resources) >= MAX_ITEMS_PER_RESOURCE_TYPE:
                    logger.warning(
                        "Item cap of %d reached for %s.%s; results truncated.",
                        MAX_ITEMS_PER_RESOURCE_TYPE,
                        service_name,
                        operation_name,
                    )

                # Aggregate the resources
                for resource in resources:
                    aggregated_resources[(resource_type_code, region)] += 1

                # Store raw data
                raw_data.append(
                    {
                        "service": service_name,
                        "operation": operation_name,
                        "resources": resources,
                    }
                )

            except Exception as exc:
                # Expected for services the caller can't access or that aren't
                # available in a region. Keep at DEBUG (run.log only) so it never
                # floods the console, while still distinguishing a failed service
                # from an empty one. The message alone is the useful signal; the
                # botocore stack is noise, so no exc_info here.
                logger.debug(
                    "Error processing %s.%s in %s: %s",
                    service_name,
                    operation_name,
                    region,
                    exc,
                )
                continue

        logger.info(
            "Resource type resolution: %d of %d rows scanned (%d from params, "
            "%d from code), %d placeholders skipped, %d invalid.",
            spec_sources["params"] + spec_sources["code"],
            len(resource_type_mapping),
            spec_sources["params"],
            spec_sources["code"],
            spec_sources["placeholder"],
            spec_sources["invalid"],
        )

        # Save raw data to a JSON file
        raw_data = convert_datetime(raw_data)

        raw_file_path = os.path.join(raw_data_path, "resource_inventory_raw_data.json")
        with open(raw_file_path, "w", encoding="utf-8") as raw_file:
            json.dump(raw_data, raw_file, indent=4)

        # Insert aggregated data into SQLite
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()

            for (
                resource_type_code,
                resource_location,
            ), resource_count in aggregated_resources.items():
                try:
                    # Map resource type code to resource_type_id
                    resource_info = resource_type_mapping.get(resource_type_code)
                    if not resource_info:
                        # logger.warning(f"Resource type {resource_type_code} not found in resourcetype mapping. Skipping.")
                        continue

                    resource_type_id = resource_info["id"]

                    cursor.execute(
                        """
                        INSERT INTO resource_inventory (resource_type, location, count)
                        VALUES (?, ?, ?)
                        ON CONFLICT(resource_type, location) DO UPDATE SET count = excluded.count
                        """,
                        (resource_type_id, resource_location, resource_count),
                    )
                except sqlite3.Error as e:
                    logger.error(
                        f"SQLite error while processing aggregated resource: {e}",
                        exc_info=True,
                    )
                except Exception as e:
                    logger.error(
                        f"Unexpected error while processing aggregated resource: {e}",
                        exc_info=True,
                    )

            conn.commit()

    except Exception as e:
        logger.error(f"Error creating AWS resource inventory: {str(e)}", exc_info=True)


def get_missing_months_aws(processed_costs: set[str], max_months: int) -> list[date]:
    current_date = datetime.now(timezone.utc).date().replace(day=1)
    processed_months = {
        datetime.strptime(month_str, "%Y-%m-%d").date().replace(day=1)
        for month_str in processed_costs
    }
    missing_months = []

    for i in range(max_months):
        check_date = current_date - relativedelta(months=i)
        if check_date not in processed_months:
            missing_months.append(check_date)

    return missing_months


def build_aws_cost_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> None:
    try:
        session = boto3.Session(
            aws_access_key_id=provider_details["accessKey"],
            aws_secret_access_key=provider_details["secretKey"],
            aws_session_token=provider_details.get("sessionToken"),
            region_name=provider_details["region"],
        )
        cost_explorer = session.client(
            "ce", region_name="us-east-1", config=AWS_RETRY_CONFIG
        )

        db_path = os.path.join(report_path, "data", "assessment.db")

        end_time = date.today().replace(day=1) + relativedelta(months=1)
        start_time = end_time - relativedelta(months=6)

        cost_and_usage = cost_explorer.get_cost_and_usage(
            TimePeriod={
                "Start": start_time.strftime("%Y-%m-%d"),
                "End": end_time.strftime("%Y-%m-%d"),
            },
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            Filter={
                "Dimensions": {"Key": "REGION", "Values": [provider_details["region"]]}
            },
        )

        cost_inventory_raw_path = os.path.join(
            raw_data_path, "cost_inventory_raw_data.json"
        )
        with open(cost_inventory_raw_path, "w", encoding="utf-8") as raw_file:
            json.dump(cost_and_usage, raw_file, indent=4)

        # Insert structured data into SQLite
        currency = "USD"
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()

            for result in cost_and_usage["ResultsByTime"]:
                month_str = result["TimePeriod"]["Start"]
                total_cost = sum(
                    float(group["Metrics"]["UnblendedCost"]["Amount"])
                    for group in result["Groups"]
                )
                currency = (
                    result["Groups"][0]["Metrics"]["UnblendedCost"]["Unit"]
                    if result["Groups"]
                    else "USD"
                )
                month_date = (
                    datetime.strptime(month_str, "%Y-%m-%d")
                    .date()
                    .replace(day=1)
                    .isoformat()
                )

                # Insert or update the cost data for the month
                cursor.execute(
                    """
                    INSERT INTO cost_inventory (month, cost, currency)
                    VALUES (?, ?, ?)
                    ON CONFLICT(month) DO UPDATE SET
                        cost = excluded.cost,
                        currency = excluded.currency
                    """,
                    (month_date, total_cost, currency),
                )

            # Handle missing months
            structured_months = {
                datetime.strptime(result["TimePeriod"]["Start"], "%Y-%m-%d").date()
                for result in cost_and_usage["ResultsByTime"]
            }
            missing_months = get_missing_months_aws(
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
                    (missing_month.isoformat(), currency),
                )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"SQLite error: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Error creating AWS cost inventory: {str(e)}", exc_info=True)
        raise
