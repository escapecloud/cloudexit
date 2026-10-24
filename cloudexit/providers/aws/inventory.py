# cloudexit/providers/aws/inventory.py
import boto3
import json
import os
import logging
import sqlite3
from typing import Any
from collections import defaultdict
from botocore.exceptions import (
    UnknownServiceError,
)

from cloudexit.catalogue.db import connect, load_data
from cloudexit.providers.aws.client import (
    CONNECTION_ERRORS,
    MAX_ITEMS_PER_RESOURCE_TYPE,
    SERVICE_NOT_OFFERED,
    SERVICE_OK,
    SERVICE_UNKNOWN,
    convert_datetime,
    create_client,
    paginate_or_call,
    parse_resource_type_params,
    resolve_aws_call_spec,
    service_status,
)

logger = logging.getLogger("core.engine.aws")


def _names(services: dict[str, int]) -> str:
    return ", ".join(sorted(services)) or "none"


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

        # Calls that were not made or did not work, by reason and service
        skipped = {
            SERVICE_NOT_OFFERED: defaultdict(int),
            SERVICE_UNKNOWN: defaultdict(int),
            "failed": defaultdict(int),
        }
        unreachable: set[str] = set()

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

            if service_name in skipped[SERVICE_UNKNOWN]:
                skipped[SERVICE_UNKNOWN][service_name] += 1
                continue
            status = service_status(service_name, region)
            if status != SERVICE_OK:
                if service_name not in skipped[status]:
                    logger.debug(
                        "Skipping %s: %s",
                        service_name,
                        (
                            f"not offered in {region}"
                            if status == SERVICE_NOT_OFFERED
                            else "unknown to the installed boto3"
                        ),
                    )
                skipped[status][service_name] += 1
                continue
            if service_name in unreachable:
                logger.debug(
                    "Skipping %s.%s: the %s endpoint could not be reached",
                    service_name,
                    operation_name,
                    service_name,
                )
                skipped["failed"][service_name] += 1
                continue

            try:
                client = create_client(session, service_name, region)
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

            except UnknownServiceError:
                if service_name not in skipped[SERVICE_UNKNOWN]:
                    logger.debug(
                        "Skipping %s: unknown to the installed boto3", service_name
                    )
                skipped[SERVICE_UNKNOWN][service_name] += 1
                continue
            except CONNECTION_ERRORS as exc:
                unreachable.add(service_name)
                skipped["failed"][service_name] += 1
                logger.debug(
                    "Cannot reach %s in %s, skipping the service: %s",
                    service_name,
                    region,
                    exc,
                )
                continue
            except Exception as exc:
                skipped["failed"][service_name] += 1
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

        skipped_calls = sum(sum(counts.values()) for counts in skipped.values())
        if skipped_calls:
            logger.info(
                "%d service calls skipped (not offered in %s: %s; unknown to boto3: "
                "%s; failed: %s)",
                skipped_calls,
                region,
                _names(skipped[SERVICE_NOT_OFFERED]),
                _names(skipped[SERVICE_UNKNOWN]),
                _names(skipped["failed"]),
            )

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
