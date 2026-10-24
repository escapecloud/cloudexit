# cloudexit/providers/aws/costs.py
import boto3
import json
import os
import logging
import sqlite3
from typing import Any
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta

from cloudexit.catalogue.db import connect
from cloudexit.providers.aws.client import create_client

logger = logging.getLogger("core.engine.aws")


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
        cost_explorer = create_client(session, "ce", "us-east-1")

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
