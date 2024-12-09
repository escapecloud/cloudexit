#utils_aws.py
import boto3
import botocore
import json
import os
import time
import logging
from datetime import date, datetime, timedelta
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from botocore.exceptions import NoCredentialsError, ClientError

from .utils_db import connect, load_data

logger = logging.getLogger("core.engine.aws")

def aws_api_call_with_retry(client, function_name, parameters, max_retries, retry_delay):
    def api_call(*args, **kwargs):
        for attempt in range(max_retries):
            try:
                function_to_call = getattr(client, function_name)
                if parameters:
                    return function_to_call(**parameters, **kwargs)
                else:
                    return function_to_call(**kwargs)
            except botocore.exceptions.ClientError as error:
                error_code = error.response['Error']['Code']
                #logger.warning(f"ClientError: {error_code}. Attempt {attempt + 1} of {max_retries}. Retrying in {retry_delay} seconds.")
                if error_code in ['Throttling', 'RequestLimitExceeded']:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                else:
                    raise
            except botocore.exceptions.BotoCoreError as error:
                #logger.warning(f"BotoCoreError: {str(error)}. Attempt {attempt + 1} of {max_retries}. Retrying in {retry_delay} seconds.")
                time.sleep(retry_delay * (2 ** attempt))
                continue
        raise Exception(f"Failed to call {function_name} after {max_retries} attempts")

    return api_call  # Return the callable function

def convert_datetime(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = convert_datetime(v)
    elif isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = convert_datetime(obj[i])
    elif isinstance(obj, datetime):
        return obj.isoformat()
    return obj

def build_aws_resource_inventory(cloud_service_provider, provider_details, report_path, raw_data_path):
    try:
        access_key = provider_details["accessKey"]
        secret_key = provider_details["secretKey"]
        region = provider_details["region"]

        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )

        db_path = os.path.join(report_path, "data", "assessment.db")

        # Load the ResourceType mapping
        resource_type_mapping = {
            item["code"]: {"id": item["id"], "name": item["name"]}
            for item in load_data("resourcetype")
            if item["csp"] == 2 and item["status"] == "t"
        }

        # Save raw data for debugging and auditing purposes
        raw_data = []

        # Aggregate resources by type and location
        aggregated_resources = defaultdict(int)

        # Iterate through each resource type in the JSON
        for idx, (resource_type_code, resource_info) in enumerate(resource_type_mapping.items(), start=1):
            parts = resource_type_code.split('.')
            if len(parts) != 4 or parts[0] != "AWS":
                #logger.warning(f"Invalid resource type format: {resource_type_code}. Skipping.")
                continue

            # Extract service name, operation name, and result key
            service_name, operation_name, result_key = parts[1], parts[2], parts[3]

            #logger.info(f"Processing service {service_name} with operation {operation_name}")

            try:
                client = session.client(service_name, region_name=region)
                if not hasattr(client, operation_name):
                    #logger.error(f"Operation {operation_name} does not exist for service {service_name}")
                    continue

                # Make the API call
                api_call = aws_api_call_with_retry(client, operation_name, {}, max_retries=3, retry_delay=2)
                response = api_call()

                if isinstance(response, dict):
                    response.pop("ResponseMetadata", None)
                    resources = response.get(result_key.strip(), [])
                    # Handle paginated results
                    while 'NextToken' in response:
                        next_token = response['NextToken']
                        response = api_call(NextToken=next_token)
                        response.pop("ResponseMetadata", None)
                        resources.extend(response.get(result_key.strip(), []))
                else:
                    #logger.warning(f"No valid response found for {service_name} operation {operation_name}. Skipping.")
                    continue

                # Aggregate the resources
                for resource in resources:
                    aggregated_resources[(resource_type_code, region)] += 1

                # Store raw data
                raw_data.append({
                    "service": service_name,
                    "operation": operation_name,
                    "resources": resources
                })

            except (NoCredentialsError, ClientError, Exception) as e:
                #logger.error(f"Error while processing {service_name}: {str(e)}", exc_info=True)
                continue

        # Save raw data to a JSON file
        raw_data = convert_datetime(raw_data)

        raw_file_path = os.path.join(raw_data_path, "resource_inventory_raw_data.json")
        with open(raw_file_path, "w", encoding="utf-8") as raw_file:
            json.dump(raw_data, raw_file, indent=4)

        # Insert aggregated data into SQLite
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()

            for (resource_type_code, resource_location), resource_count in aggregated_resources.items():
                try:
                    # Map resource type code to resource_type_id
                    resource_info = resource_type_mapping.get(resource_type_code)
                    if not resource_info:
                        #logger.warning(f"Resource type {resource_type_code} not found in resourcetype mapping. Skipping.")
                        continue

                    resource_type_id = resource_info["id"]

                    cursor.execute(
                        """
                        INSERT INTO resource_inventory (resource_type, location, count)
                        VALUES (?, ?, ?)
                        ON CONFLICT(resource_type, location) DO UPDATE SET count = excluded.count
                        """,
                        (resource_type_id, resource_location, resource_count)
                    )
                except sqlite3.Error as e:
                    logger.error(f"SQLite error while processing aggregated resource: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"Unexpected error while processing aggregated resource: {e}", exc_info=True)

            conn.commit()

    except Exception as e:
        logger.error(f"Error creating AWS resource inventory: {str(e)}", exc_info=True)

def get_missing_months_aws(processed_costs, max_months):
    current_date = datetime.utcnow().date().replace(day=1)
    processed_months = {datetime.strptime(month_str, '%Y-%m-%d').date().replace(day=1) for month_str in processed_costs}
    missing_months = []

    for i in range(max_months):
        check_date = current_date - relativedelta(months=i)
        if check_date not in processed_months:
            missing_months.append(check_date)

    return missing_months

def build_aws_cost_inventory(cloud_service_provider, provider_details, report_path, raw_data_path):
    try:
        session = boto3.Session(
            aws_access_key_id=provider_details["accessKey"],
            aws_secret_access_key=provider_details["secretKey"],
            region_name=provider_details["region"]
        )
        cost_explorer = session.client('ce', region_name='us-east-1')

        db_path = os.path.join(report_path, "data", "assessment.db")

        end_time = date.today()
        start_time = end_time.replace(day=1) - timedelta(days=180)
        start_time = start_time.replace(day=1)

        cost_and_usage = cost_explorer.get_cost_and_usage(
            TimePeriod={'Start': start_time.strftime('%Y-%m-%d'), 'End': end_time.strftime('%Y-%m-%d')},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
            Filter={
                'Dimensions': {
                    'Key': 'REGION',
                    'Values': [provider_details["region"]]
                }
            }
        )

        cost_inventory_raw_path = os.path.join(raw_data_path, "cost_inventory_raw_data.json")
        with open(cost_inventory_raw_path, "w", encoding="utf-8") as raw_file:
            json.dump(cost_and_usage, raw_file, indent=4)

        # Insert structured data into SQLite
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()

            for result in cost_and_usage['ResultsByTime']:
                month_str = result['TimePeriod']['Start']
                total_cost = sum(float(group['Metrics']['UnblendedCost']['Amount']) for group in result['Groups'])
                currency = result['Groups'][0]['Metrics']['UnblendedCost']['Unit'] if result['Groups'] else 'USD'
                month_date = datetime.strptime(month_str, '%Y-%m-%d').date().replace(day=1).isoformat()

                # Insert or update the cost data for the month
                cursor.execute(
                    """
                    INSERT INTO cost_inventory (month, cost, currency)
                    VALUES (?, ?, ?)
                    ON CONFLICT(month) DO UPDATE SET
                        cost = excluded.cost,
                        currency = excluded.currency
                    """,
                    (month_date, total_cost, currency)
                )

            # Handle missing months
            structured_months = {datetime.strptime(result['TimePeriod']['Start'], '%Y-%m-%d').date() for result in cost_and_usage['ResultsByTime']}
            missing_months = get_missing_months_aws({month.isoformat() for month in structured_months}, 6)

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
        logger.error(f"Error creating AWS cost inventory: {str(e)}", exc_info=True)
        raise

    except Exception as e:
        logger.error(f"Error creating AWS cost inventory: {str(e)}", exc_info=True)
        raise
