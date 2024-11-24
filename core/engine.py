import logging
import json
import os
import time
import boto3
import botocore

from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from jinja2 import Template
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.core.exceptions import ClientAuthenticationError
from azure.mgmt.authorization import AuthorizationManagementClient
from botocore.exceptions import NoCredentialsError, ClientError
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import QueryDefinition, TimeframeType

from .utils import copy_assets, get_cost_summary, get_risk_summary
from .utils_aws import build_aws_resource_inventory, build_aws_cost_inventory
from .utils_azure import build_azure_resource_inventory, build_azure_cost_inventory

# Configure the logger
logger = logging.getLogger("core.engine")

# Stage 1
def verify_credentials(cloud_service_provider, provider_details):
    connection_success = False
    logs = ""

    if cloud_service_provider == 1:  # Azure
        try:
            # Use DefaultAzureCredential if provided, else use client secrets
            credential = provider_details.get("credential") or ClientSecretCredential(
                tenant_id=provider_details["tenantId"],
                client_id=provider_details["clientId"],
                client_secret=provider_details["clientSecret"]
            )
            resource_client = ResourceManagementClient(credential, provider_details["subscriptionId"])
            list(resource_client.resource_groups.list())  # Benign call to verify credentials
            connection_success = True
            logs = "Azure connection successful."
        except ClientAuthenticationError as e:
            logs = f"Azure credentials validation failed: {str(e)}"
            logger.error(logs)
        except Exception as e:
            logs = f"Azure connection test failed: {str(e)}"
            logger.error(logs)

    elif cloud_service_provider == 2:  # AWS
        try:
            client = boto3.client(
                'ec2',
                aws_access_key_id=provider_details["accessKey"],
                aws_secret_access_key=provider_details["secretKey"],
                region_name=provider_details["region"]
            )
            client.describe_regions()  # Benign call to verify credentials
            connection_success = True
            logs = "AWS connection successful."
        except NoCredentialsError as e:
            logs = f"AWS credentials validation failed: {str(e)}"
            logger.error(logs)
        except Exception as e:
            logs = f"AWS connection test failed: {str(e)}"
            logger.error(logs)

    return connection_success, logs

# Stage 2
def test_permissions(cloud_service_provider, provider_details):
    permission_valid = False
    permission_reader = False
    permission_cost = False
    logs = ""

    if cloud_service_provider == 1:  # Azure
        try:
            # Use DefaultAzureCredential if provided, else use client secrets
            credential = provider_details.get("credential") or ClientSecretCredential(
                tenant_id=provider_details["tenantId"],
                client_id=provider_details["clientId"],
                client_secret=provider_details["clientSecret"]
            )
            resource_group_scope = f"/subscriptions/{provider_details['subscriptionId']}/resourceGroups/{provider_details['resourceGroupName']}"

            # Check role assignments
            auth_client = AuthorizationManagementClient(credential, provider_details["subscriptionId"])
            role_assignments = auth_client.role_assignments.list_for_scope(scope=resource_group_scope)

            for role_assignment in role_assignments:
                role_definition_id = role_assignment.role_definition_id
                if role_definition_id.endswith("acdd72a7-3385-48ef-bd42-f606fba81ae7"):  # Reader role
                    permission_reader = True
                if role_definition_id.endswith("72fafb9e-0641-4937-9268-a91bfd8191a3"):  # Cost Management Reader
                    permission_cost = True

            if permission_reader and permission_cost:
                permission_valid = True
                logs = "Reader and Cost Management Reader roles validated."
            elif permission_reader:
                logs = "Reader role validated, but Cost Management Reader role validation failed."
            elif permission_cost:
                logs = "Cost Management Reader role validated, but Reader role validation failed."
            else:
                logs = "Both Reader and Cost Management Reader roles validation failed."

        except ClientAuthenticationError as e:
            logs = f"Azure credentials validation failed: {str(e)}"
            logger.error(logs)
        except Exception as e:
            logs = f"Azure permission test failed: {str(e)}"
            logger.error(logs)

    elif cloud_service_provider == 2:  # AWS
        try:
            sts_client = boto3.client(
                'sts',
                aws_access_key_id=provider_details["accessKey"],
                aws_secret_access_key=provider_details["secretKey"],
                region_name=provider_details["region"]
            )
            identity = sts_client.get_caller_identity()
            user_arn = identity['Arn']
            user_name = user_arn.split('/')[-1]

            iam_client = boto3.client(
                'iam',
                aws_access_key_id=provider_details["accessKey"],
                aws_secret_access_key=provider_details["secretKey"],
                region_name=provider_details["region"]
            )
            policies = iam_client.list_attached_user_policies(UserName=user_name)
            policy_names = [policy['PolicyName'] for policy in policies['AttachedPolicies']]

            permission_reader = "ViewOnlyAccess" in policy_names
            permission_cost = "AWSBillingReadOnlyAccess" in policy_names

            if permission_reader and permission_cost:
                permission_valid = True
                logs = "ViewOnlyAccess and AWSBillingReadOnlyAccess policies validated."
            elif permission_reader:
                logs = "ViewOnlyAccess policy validated, but AWSBillingReadOnlyAccess policy validation failed."
            elif permission_cost:
                logs = "AWSBillingReadOnlyAccess policy validated, but ViewOnlyAccess policy validation failed."
            else:
                logs = "Both ViewOnlyAccess and AWSBillingReadOnlyAccess policy validations failed."

        except NoCredentialsError as e:
            logs = f"AWS credentials validation failed: {str(e)}"
            logger.error(logs)
        except Exception as e:
            logs = f"AWS permission test failed: {str(e)}"
            logger.error(logs)

    permission_valid = permission_reader and permission_cost

    return permission_valid, permission_reader, permission_cost, logs

# Stage 3
def create_resource_inventory(cloud_service_provider, provider_details, report_path, raw_data_path):
    try:

        if cloud_service_provider == 1:  # Azure
            build_azure_resource_inventory(cloud_service_provider, provider_details, report_path, raw_data_path)
        elif cloud_service_provider == 2:  # AWS
            build_aws_resource_inventory(cloud_service_provider, provider_details, report_path, raw_data_path)

        return {"success": True, "logs": "Resource inventory created successfully."}

    except Exception as e:
        logger.error(f"Error creating resource inventory: {str(e)}", exc_info=True)
        # Do not raise the exception here; just return the error information
        return {"success": False, "logs": str(e)}

# Stage 4
def create_cost_inventory(provider_details, cloud_service_provider, report_path, raw_data_path):
    try:
        if cloud_service_provider == 1:  # Azure
            build_azure_cost_inventory(cloud_service_provider, provider_details, report_path, raw_data_path)
        elif cloud_service_provider == 2:  # AWS
            build_aws_cost_inventory(cloud_service_provider, provider_details, report_path, raw_data_path)

        return {"success": True, "logs": "Cost inventory created successfully."}

    except Exception as e:
        logger.error(f"Error creating cost inventory: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}

# Stage 5
def perform_risk_assessment(exit_strategy, report_path):
    try:
        # Load JSON files
        with open("datasets/risk.json", "r", encoding="utf-8") as risk_file:
            risks = json.load(risk_file)

        with open(os.path.join(report_path, "resource_inventory_standard_data.json"), "r", encoding="utf-8") as resource_file:
            resource_inventory = json.load(resource_file)

        with open("datasets/alternative.json", "r", encoding="utf-8") as alt_file:
            alternatives = json.load(alt_file)

        with open("datasets/alternativetechnology.json", "r", encoding="utf-8") as tech_file:
            alternative_technologies = json.load(tech_file)

        # Initialize risk inventory
        risk_inventory = []

        # Calculate the total count of resources across all types
        total_resource_count = sum(item["count"] for item in resource_inventory.values())

        # Calculate total number of distinct resource types
        distinct_resource_types = set(resource_data["resource_type"] for resource_data in resource_inventory.values())
        total_resource_types = len(distinct_resource_types)

        # Process each resource by `resource_type`
        for resource_code, resource_data in resource_inventory.items():
            resource_type_id = str(resource_data["resource_type"])  # Convert to string for consistent comparison

            # Debugging: Log type and value to ensure consistency
            #logger.info(f"Resource Type (ID): {resource_type_id} (Type: {type(resource_type_id)}), Exit Strategy: {exit_strategy}")

            # Filter alternatives for the current resource_type and exit strategy
            relevant_alternatives = [
                alt for alt in alternatives
                if str(alt["resource_type"]) == resource_type_id and str(alt["strategy_type"]) == str(exit_strategy)
            ]
            alternative_count = len(relevant_alternatives)

            # Count alternatives with support
            support_count = sum(
                1 for alt in relevant_alternatives
                if any(tech["id"] == alt["alternative_technology"] and tech["support_plan"] == "t" for tech in alternative_technologies)
            )

            # Debugging: Log the counts to verify
            #logger.info(f"Resource Type: {resource_type_id}, Relevant Alternatives: {relevant_alternatives}")
            #logger.info(f"Resource Type: {resource_type_id}, Support Count: {support_count}, Alternative Count: {alternative_count}")

            # Determine risks based on criteria, using resource_type_id in output
            if 1 <= alternative_count < 3:
                risk_inventory.append({"resource_type": resource_type_id, "risk": "1"})
            if alternative_count == 0:
                risk_inventory.append({"resource_type": resource_type_id, "risk": "2"})
            if 1 <= support_count < 3:
                risk_inventory.append({"resource_type": resource_type_id, "risk": "3"})
            if support_count == 0:
                risk_inventory.append({"resource_type": resource_type_id, "risk": "4"})

        # Check for risks based on total resource count across all types
        if 15 < total_resource_count <= 30:
            risk_inventory.append({"resource_type": "null", "risk": "5"})
        elif total_resource_count > 30:
            risk_inventory.append({"resource_type": "null", "risk": "6"})

        # Check for risks based on total number of resource types
        if 15 < total_resource_types <= 30:
            risk_inventory.append({"resource_type": "null", "risk": "7"})
        elif total_resource_types > 30:
            risk_inventory.append({"resource_type": "null", "risk": "8"})

        # Save results to risk_inventory_standard_data.json
        risk_inventory_path = os.path.join(report_path, "risk_inventory_standard_data.json")
        with open(risk_inventory_path, "w", encoding="utf-8") as risk_file:
            json.dump(risk_inventory, risk_file, indent=4)

        return {"success": True, "logs": "Risk assessment completed successfully."}

    except Exception as e:
        logger.error(f"Error performing risk assessment: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}

# Stage 6
def conduct_alternative_technology_analysis(cloud_service_provider, exit_strategy, report_path):
    try:
        # Load resource inventory data
        with open(os.path.join(report_path, "resource_inventory_standard_data.json"), 'r', encoding='utf-8') as file:
            resource_inventory = json.load(file)

        # Load alternatives and alternative technologies datasets
        with open("datasets/alternative.json", 'r', encoding='utf-8') as file:
            alternatives = json.load(file)
        with open("datasets/alternativetechnology.json", 'r', encoding='utf-8') as file:
            alternative_technologies = json.load(file)

        # Collect unique resource types from resource inventory
        resource_types_in_use = {resource["resource_type"] for resource in resource_inventory.values()}

        # Prepare a dictionary to group alternative technologies by resource_type
        grouped_alternatives = {}

        # Filter alternatives based on the resource types and exit strategy
        for resource_type in resource_types_in_use:
            # Filter for alternatives matching the resource type and exit strategy
            filtered_alternatives = [
                alt for alt in alternatives
                if alt["resource_type"] == resource_type and str(alt["strategy_type"]) == str(exit_strategy)
            ]

            # Get alternative technology details for each filtered alternative
            alternative_details = [
                tech for alt in filtered_alternatives
                for tech in alternative_technologies
                if tech["id"] == alt["alternative_technology"] and tech["status"] == "t"
            ]

            # Assign the collected alternatives to the grouped dictionary by resource type
            grouped_alternatives[resource_type] = alternative_details

        # Format the grouped data for JSON serialization
        grouped_alternatives_list = [
            {"resource_type": resource_type, "alternatives": alternatives}
            for resource_type, alternatives in grouped_alternatives.items()
        ]

        # Write the grouped alternative technologies to a JSON file
        alt_tech_path = os.path.join(report_path, "alt_tech_standard_data.json")
        with open(alt_tech_path, 'w', encoding='utf-8') as file:
            json.dump(grouped_alternatives_list, file, indent=4)

        #logger.info(f"Alternative technologies report saved to {alt_tech_path}")
        return {"success": True, "logs": "Alternative technology analysis completed successfully."}

    except Exception as e:
        logger.error(f"Error generating alternative technologies report: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}

# Stage 7
def generate_report(cloud_service_provider, exit_strategy, assessment_type, report_path):

    copy_assets(report_path)

    # Load data directly in generate_report
    with open(os.path.join(report_path, "risk_inventory_standard_data.json"), 'r', encoding='utf-8') as file:
        risk_data = json.load(file)
    with open("datasets/risk.json", "r", encoding="utf-8") as file:
        risk_definitions = json.load(file)
    with open(os.path.join(report_path, "resource_inventory_standard_data.json"), 'r', encoding='utf-8') as file:
        resource_inventory = json.load(file)
    with open("datasets/resourcetype.json", "r", encoding="utf-8") as f:
        resource_type_mapping = {str(item["id"]): item for item in json.load(f)}
    with open(os.path.join(report_path, "cost_inventory_standard_data.json"), 'r', encoding='utf-8') as file:
        cost_data = json.load(file)
    with open(os.path.join(report_path, "alt_tech_standard_data.json"), 'r', encoding='utf-8') as file:
        alt_tech_data = json.load(file)

    # Prepare risk data
    risks, severity_counts = get_risk_summary(risk_data, risk_definitions, resource_inventory)

    # Prepare cost data
    months, cost_values, total_cost, currency_symbol = get_cost_summary(cost_data)

    # Prepare resource data with names and icons
    resource_counts = []
    for resource_id, resource in resource_inventory.items():
        resource_type = resource.get("resource_type")
        count = resource.get("count", 0)

        # Get resource info from resource_type_mapping based on resource_type
        resource_info = resource_type_mapping.get(str(resource_type), {})
        name = resource_info.get("name", "Unknown Resource")
        icon = resource_info.get("icon", "assets/icons/default.png")

        # Construct the relative path for the icon
        icon = icon.lstrip('/')  # Remove leading slash for a relative path if needed

        resource_counts.append({
            "resource_type": resource_type,
            "name": name,
            "icon": icon,
            "count": count
        })

    # Get the total resources count
    total_resources = sum(item["count"] for item in resource_counts)

    # Flatten and format the alternative technology data for easier use in the template
    alternative_technologies = []
    for alternatives in alt_tech_data:
        resource_type = alternatives.get("resource_type")
        for tech in alternatives.get("alternatives", []):
            alternative_technologies.append({
                "resource_type_id": resource_type,
                "product_name": tech.get("product_name"),
                "product_description": tech.get("product_description"),
                "product_url": tech.get("product_url"),
                "open_source": tech.get("open_source") == "t",  # Convert 't'/'f' to boolean
                "support_plan": tech.get("support_plan") == "t",  # Convert 't'/'f' to boolean
                "status": tech.get("status") == "t"
            })

    assessment_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    assessment_type = assessment_type or "Not Specified"
    
    # Render and save the HTML template
    try:
        template_path = os.path.join("assets", "template", "index.html")
        with open(template_path, 'r') as file:
            template_content = file.read()
        template = Template(template_content)
        html_content = template.render(
            cloud_service_provider=cloud_service_provider,
            exit_strategy=exit_strategy,
            assessment_type=assessment_type,
            assessment_ts=assessment_ts,
            risks=risks,
            high_risk_count=severity_counts['high'],
            medium_risk_count=severity_counts['medium'],
            low_risk_count=severity_counts['low'],
            total_cost=total_cost,
            months_json=json.dumps(months),
            costs_json=json.dumps(cost_values),
            currency_symbol=currency_symbol,
            total_resources=total_resources,
            resource_inventory=resource_counts,
            alternative_technologies=alternative_technologies,
        )
        report_file_path = os.path.join(report_path, "index.html")
        with open(report_file_path, 'w') as report_file:
            report_file.write(html_content)
        #logger.info(f"Report generated at: {report_file_path}")
    except Exception as e:
        #logger.error(f"Error generating report: {str(e)}")
        return {"success": False, "logs": f"Error generating report: {str(e)}"}

    return {"success": True, "logs": f"Report generated at: {report_file_path}"}
