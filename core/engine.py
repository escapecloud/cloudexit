# core/engine.py
import json
import logging
import os
import boto3
from datetime import datetime, timezone, timedelta
from typing import Any
from azure.identity import ClientSecretCredential
from azure.core.exceptions import ClientAuthenticationError
from azure.mgmt.authorization import AuthorizationManagementClient
from botocore.exceptions import NoCredentialsError

from .utils import copy_assets
from .utils_aws import build_aws_resource_inventory, build_aws_cost_inventory
from .utils_azure import (
    build_azure_cost_inventory,
    build_azure_resource_inventory,
    create_resource_client,
)
from .utils_db import connect, load_data
from .utils_tfstate import build_tfstate_resource_inventory
from .report.context import build_context, collect_provenance
from .report.executive_summary import generate_executive_summary
from .report.html_sections import html_sections
from .utils_report import (
    generate_html_report,
    generate_json_report,
)

# Configure the logger
logger = logging.getLogger("core.engine")


# Stage 1
def verify_credentials(
    cloud_service_provider: int, provider_details: dict[str, Any]
) -> tuple[bool, str]:
    connection_success = False
    logs = ""

    if cloud_service_provider == 1:  # Azure
        try:
            # Use DefaultAzureCredential if provided, else use client secrets
            credential = provider_details.get("credential") or ClientSecretCredential(
                tenant_id=provider_details["tenantId"],
                client_id=provider_details["clientId"],
                client_secret=provider_details["clientSecret"],
            )
            resource_client = create_resource_client(
                credential, provider_details["subscriptionId"]
            )
            list(
                resource_client.resource_groups.list()
            )  # Benign call to verify credentials
            connection_success = True
            logs = "Azure connection successful."
        except ClientAuthenticationError as e:
            logs = f"Azure credentials validation failed: {str(e)}"
            # logger.error(logs)
        except Exception as e:
            logs = f"Azure connection test failed: {str(e)}"
            # logger.error(logs)

    elif cloud_service_provider == 2:  # AWS
        try:
            client = boto3.client(
                "ec2",
                aws_access_key_id=provider_details["accessKey"],
                aws_secret_access_key=provider_details["secretKey"],
                aws_session_token=provider_details.get("sessionToken"),
                region_name=provider_details["region"],
            )
            client.describe_regions()  # Benign call to verify credentials
            connection_success = True
            logs = "AWS connection successful."
        except NoCredentialsError as e:
            logs = f"AWS credentials validation failed: {str(e)}"
            # logger.error(logs)
        except Exception as e:
            logs = f"AWS connection test failed: {str(e)}"
            # logger.error(logs)

    return connection_success, logs


# Stage 2
def test_permissions(
    cloud_service_provider: int, provider_details: dict[str, Any]
) -> tuple[bool, bool, bool, str]:
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
                client_secret=provider_details["clientSecret"],
            )
            resource_group_scope = f"/subscriptions/{provider_details['subscriptionId']}/resourceGroups/{provider_details['resourceGroupName']}"

            # Check role assignments
            auth_client = AuthorizationManagementClient(
                credential, provider_details["subscriptionId"]
            )
            role_assignments = auth_client.role_assignments.list_for_scope(
                scope=resource_group_scope
            )

            for role_assignment in role_assignments:
                role_definition_id = role_assignment.role_definition_id
                if role_definition_id.endswith(
                    "acdd72a7-3385-48ef-bd42-f606fba81ae7"
                ):  # Reader role
                    permission_reader = True
                if role_definition_id.endswith(
                    "72fafb9e-0641-4937-9268-a91bfd8191a3"
                ):  # Cost Management Reader
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
            # Expected outcome of a permission test; surfaced to the user via `logs`.
            logs = f"Azure credentials validation failed: {str(e)}"
            logger.warning(logs)
        except Exception as e:
            logs = f"Azure permission test failed: {str(e)}"
            logger.error(logs)

    elif cloud_service_provider == 2:  # AWS
        try:
            sts_client = boto3.client(
                "sts",
                aws_access_key_id=provider_details["accessKey"],
                aws_secret_access_key=provider_details["secretKey"],
                aws_session_token=provider_details.get("sessionToken"),
                region_name=provider_details["region"],
            )
            identity = sts_client.get_caller_identity()
            caller_arn = identity.get("Arn", "")
            # The account id is the scope of the sync document
            if identity.get("Account"):
                provider_details["accountId"] = str(identity["Account"])

            # IAM users keep existing policy-name validation for backwards compatibility.
            if ":user/" in caller_arn:
                user_name = caller_arn.split("/")[-1]
                iam_client = boto3.client(
                    "iam",
                    aws_access_key_id=provider_details["accessKey"],
                    aws_secret_access_key=provider_details["secretKey"],
                    aws_session_token=provider_details.get("sessionToken"),
                    region_name=provider_details["region"],
                )
                policies = iam_client.list_attached_user_policies(UserName=user_name)
                policy_names = [
                    policy["PolicyName"] for policy in policies["AttachedPolicies"]
                ]

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
            else:
                # Assumed roles/federated identities (e.g. GitHub OIDC) are validated by capabilities.
                reader_error = ""
                cost_error = ""

                try:
                    ec2_client = boto3.client(
                        "ec2",
                        aws_access_key_id=provider_details["accessKey"],
                        aws_secret_access_key=provider_details["secretKey"],
                        aws_session_token=provider_details.get("sessionToken"),
                        region_name=provider_details["region"],
                    )
                    ec2_client.describe_regions()
                    permission_reader = True
                except Exception as e:
                    reader_error = str(e)

                try:
                    ce_client = boto3.client(
                        "ce",
                        aws_access_key_id=provider_details["accessKey"],
                        aws_secret_access_key=provider_details["secretKey"],
                        aws_session_token=provider_details.get("sessionToken"),
                        region_name="us-east-1",
                    )
                    today = datetime.now(timezone.utc).date()
                    ce_client.get_cost_and_usage(
                        TimePeriod={
                            "Start": today.replace(day=1).isoformat(),
                            "End": (today + timedelta(days=1)).isoformat(),
                        },
                        Granularity="MONTHLY",
                        Metrics=["UnblendedCost"],
                    )
                    permission_cost = True
                except Exception as e:
                    cost_error = str(e)

                if permission_reader and permission_cost:
                    permission_valid = True
                    logs = (
                        "AWS role/federated capability checks validated "
                        "(ec2:DescribeRegions and ce:GetCostAndUsage)."
                    )
                elif permission_reader:
                    logs = "AWS capability checks: ec2:DescribeRegions validated, but ce:GetCostAndUsage failed."
                    if cost_error:
                        logs += f" Details: {cost_error}"
                elif permission_cost:
                    logs = "AWS capability checks: ce:GetCostAndUsage validated, but ec2:DescribeRegions failed."
                    if reader_error:
                        logs += f" Details: {reader_error}"
                else:
                    logs = "AWS capability checks failed for both ec2:DescribeRegions and ce:GetCostAndUsage."
                    details = ", ".join(d for d in [reader_error, cost_error] if d)
                    if details:
                        logs += f" Details: {details}"

        except NoCredentialsError as e:
            # Expected outcome of a permission test; surfaced to the user via `logs`.
            logs = f"AWS credentials validation failed: {str(e)}"
            logger.warning(logs)
        except Exception as e:
            logs = f"AWS permission test failed: {str(e)}"
            logger.error(logs)

    permission_valid = permission_reader and permission_cost

    return permission_valid, permission_reader, permission_cost, logs


# Stage 3
def create_resource_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> dict[str, Any]:
    # Copy assets and datasets folders data
    copy_assets(report_path, cloud_service_provider)

    try:

        if provider_details.get("tfstatePath"):  # Terraform / OpenTofu state file
            coverage = build_tfstate_resource_inventory(
                cloud_service_provider, provider_details, report_path, raw_data_path
            )
            return {
                "success": True,
                "logs": "Resource inventory created successfully.",
                "coverage": coverage,
            }
        elif cloud_service_provider == 1:  # Azure
            build_azure_resource_inventory(
                cloud_service_provider, provider_details, report_path, raw_data_path
            )
        elif cloud_service_provider == 2:  # AWS
            build_aws_resource_inventory(
                cloud_service_provider, provider_details, report_path, raw_data_path
            )

        return {"success": True, "logs": "Resource inventory created successfully."}

    except Exception as e:
        logger.error(f"Error creating resource inventory: {str(e)}", exc_info=True)
        # Do not raise the exception here; just return the error information
        return {"success": False, "logs": str(e)}


# Stage 4
def create_cost_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> dict[str, Any]:
    try:
        if cloud_service_provider == 1:  # Azure
            build_azure_cost_inventory(
                cloud_service_provider, provider_details, report_path, raw_data_path
            )
        elif cloud_service_provider == 2:  # AWS
            build_aws_cost_inventory(
                cloud_service_provider, provider_details, report_path, raw_data_path
            )

        return {"success": True, "logs": "Cost inventory created successfully."}

    except Exception as e:
        logger.error(f"Error creating cost inventory: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}


# Stage 6 - Local risk assessment (every run, connected or not)
def perform_risk_assessment(exit_strategy: int, report_path: str) -> dict[str, Any]:
    try:
        # Define the database path
        db_path = os.path.join(report_path, "data", "assessment.db")

        # Load data from the database
        resource_inventory = load_data("resource_inventory", db_path=db_path)
        alternatives = load_data("alternative", db_path=db_path)
        alternative_technologies = load_data("alternativetechnology", db_path=db_path)

        # Initialize risk inventory
        risk_inventory = []

        # Calculate the total count of resources across all types
        total_resource_count = sum(item["count"] for item in resource_inventory)

        # Calculate total number of distinct resource types
        distinct_resource_types = set(
            item["resource_type"] for item in resource_inventory
        )
        total_resource_types = len(distinct_resource_types)

        # Process each resource by `resource_type`
        for resource_data in resource_inventory:
            resource_type_id = str(
                resource_data["resource_type"]
            )  # Convert to string for consistent comparison

            # Filter alternatives for the current resource_type and exit strategy
            relevant_alternatives = [
                alt
                for alt in alternatives
                if str(alt["resource_type"]) == resource_type_id
                and str(alt["strategy_type"]) == str(exit_strategy)
            ]
            alternative_count = len(relevant_alternatives)

            # Count alternatives with support
            support_count = sum(
                1
                for alt in relevant_alternatives
                if any(
                    tech["id"] == alt["alternative_technology"]
                    and tech["support_plan"] == "t"
                    for tech in alternative_technologies
                )
            )

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

        # Insert risk inventory into the database
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO risk_inventory (resource_type, risk)
                VALUES (?, ?)
                """,
                [(entry["resource_type"], entry["risk"]) for entry in risk_inventory],
            )
            conn.commit()

        return {"success": True, "logs": "Risk assessment completed successfully."}

    except Exception as e:
        logger.error(f"Error performing risk assessment: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}


def _load_tfstate_scope(raw_data_path: str) -> dict[str, Any] | None:
    # A missing or unreadable manifest must not fail report generation.
    manifest_path = os.path.join(raw_data_path, "tfstate_manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            return json.load(manifest_file).get("scope")
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read the tfstate manifest for report scope: %s", e)
        return None


# Stage 8
def generate_report(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    exit_strategy: int,
    assessment_type: int,
    name: str,
    report_path: str,
    raw_data_path: str,
    upload: dict[str, str] | None = None,
    sync_id: str | None = None,
) -> dict[str, Any]:
    try:
        db_path = os.path.join(report_path, "data", "assessment.db")

        # Load data
        resource_type_mapping = {
            str(item["id"]): item for item in load_data("resourcetype", db_path=db_path)
        }
        risk_definitions = load_data("risk", db_path=db_path)
        alternatives = load_data("alternative", db_path=db_path)
        alternative_technologies = load_data("alternativetechnology", db_path=db_path)
        alternative_technology_organizations = load_data(
            "alternativetechnologyorganization", db_path=db_path
        )
        resource_inventory = load_data("resource_inventory", db_path=db_path)
        cost_data = load_data("cost_inventory", db_path=db_path)
        risk_data = load_data("risk_inventory", db_path=db_path)
        # The local report is always the Basic report: platform results are
        # never rendered here, whatever the database holds.
        scoring_data = None

        # Timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        metadata = {
            "name": name,
            "cloud_service_provider": cloud_service_provider,
            "exit_strategy": exit_strategy,
            "assessment_type": assessment_type,
            "timestamp": timestamp,
        }
        if upload:
            # Only the link (or why there is none): never platform results
            metadata["upload"] = upload

        # Generate Outputs
        reports = {}

        # One context feeds all three reports, so they say the same things
        tfstate_scope = (
            _load_tfstate_scope(raw_data_path)
            if provider_details.get("tfstatePath")
            else None
        )
        provenance = collect_provenance(db_path, run_id=sync_id, upload=upload)
        context = build_context(
            metadata=metadata,
            provider_details=provider_details,
            resource_type_mapping=resource_type_mapping,
            resource_inventory=resource_inventory,
            cost_data=cost_data,
            risk_data=risk_data,
            risk_definitions=risk_definitions,
            alternatives=alternatives,
            alternative_technologies=alternative_technologies,
            alternative_technology_organizations=alternative_technology_organizations,
            exit_strategy=exit_strategy,
            tfstate_scope=tfstate_scope,
            provenance=provenance,
            report_path=report_path,
        )

        # Generate HTML report
        reports["HTML"] = generate_html_report(
            report_path,
            metadata,
            resource_type_mapping,
            resource_inventory,
            cost_data,
            scoring_data,
            risk_data,
            risk_definitions,
            alternatives,
            alternative_technologies,
            exit_strategy,
            alternative_technology_organizations,
            summary=html_sections(context),
        )

        # Generate PDF report
        reports["PDF"] = generate_executive_summary(context)

        # Generate JSON report
        reports["JSON"] = generate_json_report(
            raw_data_path,
            metadata,
            resource_type_mapping,
            resource_inventory,
            cost_data,
            scoring_data,
            risk_data,
            risk_definitions,
            alternatives,
            alternative_technologies,
            exit_strategy,
            provenance=provenance.as_dict(),
        )

        return {"success": True, "reports": reports}

    except Exception as e:
        return {"success": False, "logs": f"Error generating report: {str(e)}"}
