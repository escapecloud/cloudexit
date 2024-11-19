#utils_aws.py
import boto3
import botocore
import json
import os
import time
import logging
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from botocore.exceptions import NoCredentialsError, ClientError

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

        # Load the ResourceType mapping to include both `id` and `name`
        with open("datasets/resourcetype.json", "r", encoding="utf-8") as f:
            resource_type_mapping = {
                item["code"]: {"id": item["id"], "name": item["name"]}
                for item in json.load(f)
                if item["csp"] == "2" and item["status"] == "t"
            }

        resource_summary = {}
        raw_data = []

        # Initialize a custom counter
        resource_inventory_id_counter = 1

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

                # Count resources and add to summary if count > 0
                resource_count = len(resources)
                if resource_count > 0:
                    resource_inventory_id = str(resource_inventory_id_counter)
                    resource_summary[resource_inventory_id] = {
                        "resource_name": resource_info["name"],
                        "resource_type": resource_info["id"],
                        "location": region,
                        "count": resource_count
                    }
                    resource_inventory_id_counter += 1

                # Store raw data
                raw_data.append({
                    "service": service_name,
                    "operation": operation_name,
                    "resources": resources
                })


                #logger.info(f"Processed {resource_count} resources for service {service_name} with operation {operation_name}")

            except (NoCredentialsError, ClientError, Exception) as e:
                #logger.error(f"Error while processing {service_name}: {str(e)}", exc_info=True)
                continue

        # Save raw data to a JSON file
        raw_data = convert_datetime(raw_data)

        raw_file_path = os.path.join(raw_data_path, "resource_inventory_raw_data.json")
        with open(raw_file_path, "w", encoding="utf-8") as raw_file:
            json.dump(raw_data, raw_file, indent=4)
        #logger.info(f"AWS raw resource inventory saved to {raw_file_path}")

        # Save structured data to a JSON file
        structured_file_path = os.path.join(report_path, "resource_inventory_standard_data.json")
        with open(structured_file_path, "w", encoding="utf-8") as structured_file:
            json.dump(resource_summary, structured_file, indent=4)
        #logger.info(f"AWS structured resource inventory saved to {structured_file_path}")

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

        cost_inventory_raw_path = os.path.join(report_path, "cost_inventory_raw_data.json")
        with open(cost_inventory_raw_path, "w", encoding="utf-8") as raw_file:
            json.dump(cost_and_usage, raw_file, indent=4)

        structured_costs = {}
        for result in cost_and_usage['ResultsByTime']:
            month_str = result['TimePeriod']['Start']
            total_cost = sum(float(group['Metrics']['UnblendedCost']['Amount']) for group in result['Groups'])
            currency = result['Groups'][0]['Metrics']['UnblendedCost']['Unit'] if result['Groups'] else 'USD'
            structured_costs[month_str] = {"cost": total_cost, "currency": currency}

        missing_months = get_missing_months_aws(structured_costs.keys(), 6)
        for missing_month in missing_months:
            structured_costs[missing_month.isoformat()] = {"cost": 0.00, "currency": currency}

        cost_inventory_standard_path = os.path.join(report_path, "cost_inventory_standard_data.json")
        with open(cost_inventory_standard_path, "w", encoding="utf-8") as structured_file:
            json.dump(structured_costs, structured_file, indent=4)

    except Exception as e:
        logger.error(f"Error creating AWS cost inventory: {str(e)}", exc_info=True)
        raise
