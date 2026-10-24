# cloudexit/providers/azure/costs.py
import json
import os
import logging
import sqlite3
import time
from typing import Any
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from utils.utils import print_step

from cloudexit.catalogue.db import connect
from cloudexit.providers.azure.sdk import azure_import
from cloudexit.providers.azure.clients import create_cost_client, create_credential
from cloudexit.providers.azure.inventory import plain_json

logger = logging.getLogger("core.engine.azure")


COST_RETRY_STATUSES = (429, 503)


COST_MAX_ATTEMPTS = 4


COST_MAX_WAIT_SECONDS = 180


COST_DEFAULT_WAITS = (20, 40, 60)


COST_RATE_LIMITED = (
    "Azure Cost Management kept rate limiting this subscription; "
    "try again in a few minutes."
)


class CostRateLimited(Exception):
    pass


def retry_wait(error: Any, attempt: int) -> float:
    # Azure says how long to wait in Retry-After or in one of its own
    # rate-limit headers; without either, wait a little longer each time.
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    waits = []
    for name, value in headers.items():
        lowered = str(name).lower()
        if lowered == "retry-after" or (
            lowered.startswith("x-ms-ratelimit-microsoft.costmanagement-")
            and lowered.endswith("-retry-after")
        ):
            try:
                waits.append(max(0.0, float(value)))
            except (TypeError, ValueError):
                continue
    if waits:
        return max(waits)
    return float(COST_DEFAULT_WAITS[min(attempt, len(COST_DEFAULT_WAITS) - 1)])


def query_costs(client: Any, scope: str, query: Any) -> Any:
    HttpResponseError = azure_import("azure.core.exceptions", "HttpResponseError")
    waited = 0.0
    for attempt in range(COST_MAX_ATTEMPTS):
        try:
            return client.query.usage(scope, query)
        except HttpResponseError as error:
            if getattr(error, "status_code", None) not in COST_RETRY_STATUSES:
                raise
            wait = retry_wait(error, attempt)
            if (
                attempt == COST_MAX_ATTEMPTS - 1
                or waited + wait > COST_MAX_WAIT_SECONDS
            ):
                raise CostRateLimited(COST_RATE_LIMITED) from error
            logger.debug(
                "Azure cost query answered %s; retrying in %.0f s (attempt %d of %d)",
                error.status_code,
                wait,
                attempt + 1,
                COST_MAX_ATTEMPTS,
            )
            print_step(
                f"Azure cost API is rate limited, retrying in {wait:.0f} s…",
                status="warning",
            )
            time.sleep(wait)
            waited += wait
    raise CostRateLimited(COST_RATE_LIMITED)  # pragma: no cover


def cost_result_dict(result: Any) -> dict[str, Any]:
    # The REST shape nests the columns and rows under "properties"; the raw
    # file keeps them at the top level.
    data = plain_json(result.as_dict())
    properties = data.pop("properties", None) or {}
    return {**data, **properties}


def get_missing_months_azure(processed_costs: set[str], months_back: int) -> set[date]:
    today = date.today()
    start_date = today.replace(day=1) - relativedelta(months=months_back - 1)
    all_months = {
        (start_date + relativedelta(months=i)).replace(day=1)
        for i in range(months_back)
    }

    processed_months = set()
    for month_str in processed_costs:
        try:
            # Attempt parsing with full timestamp format
            month_date = (
                datetime.strptime(month_str, "%Y-%m-%dT%H:%M:%S").date().replace(day=1)
            )
        except ValueError:
            # Fallback to date-only format if full timestamp fails
            month_date = datetime.strptime(month_str, "%Y-%m-%d").date().replace(day=1)
        processed_months.add(month_date)

    return all_months - processed_months


def build_azure_cost_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> None:
    QueryDefinition = azure_import(
        "azure.mgmt.costmanagement.models", "QueryDefinition"
    )
    TimeframeType = azure_import("azure.mgmt.costmanagement.models", "TimeframeType")
    try:
        credential = create_credential(provider_details)
        cost_management_client = create_cost_client(credential)

        db_path = os.path.join(report_path, "data", "assessment.db")

        end_time = date.today()
        months_back = 6
        start_time = end_time.replace(day=1) - relativedelta(months=months_back - 1)

        query = QueryDefinition(
            type="Usage",
            timeframe=TimeframeType.CUSTOM,
            time_period={
                "from": start_time.strftime("%Y-%m-%dT00:00:00Z"),
                "to": end_time.strftime("%Y-%m-%dT00:00:00Z"),
            },
            dataset={
                "granularity": "Monthly",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            },
        )

        cost_data = query_costs(
            cost_management_client,
            f'/subscriptions/{provider_details["subscriptionId"]}/resourceGroups/{provider_details["resourceGroupName"]}',
            query,
        )

        cost_inventory_raw_path = os.path.join(
            raw_data_path, "cost_inventory_raw_data.json"
        )
        with open(cost_inventory_raw_path, "w", encoding="utf-8") as raw_file:
            json.dump(cost_result_dict(cost_data), raw_file, indent=4)

        # Insert structured cost data into SQLite
        currency = "USD"
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()

            for row in cost_data.rows:
                cost, month_str, currency = row
                month_date = (
                    datetime.strptime(month_str, "%Y-%m-%dT%H:%M:%S")
                    .date()
                    .replace(day=1)
                    .isoformat()
                )

                # Insert or update cost data
                cursor.execute(
                    """
                    INSERT INTO cost_inventory (month, cost, currency)
                    VALUES (?, ?, ?)
                    ON CONFLICT(month) DO UPDATE SET
                        cost = excluded.cost,
                        currency = excluded.currency
                    """,
                    (month_date, cost, currency),
                )

            # Extract months already in the cost data
            structured_months = {
                datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%S").date()
                for row in cost_data.rows
            }

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
                    (missing_month.isoformat(), currency),
                )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"SQLite error: {str(e)}", exc_info=True)
        raise
    except CostRateLimited:
        # The console gets the one clear line; the traceback stays in run.log
        logger.debug("Azure cost query kept being rate limited", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error creating Azure cost inventory: {str(e)}", exc_info=True)
        raise
