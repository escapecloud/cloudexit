# core/utils_tfstate.py
import hashlib
import json
import os
import logging
import re
import sqlite3
from typing import Any
from collections import defaultdict

from .utils_db import connect, load_data

logger = logging.getLogger("core.engine.tfstate")

# Terraform >= 0.12 and every OpenTofu release write state format version 4.
SUPPORTED_STATE_VERSION = 4

# Attribute keys that carry the region/location of an instance, in lookup order.
# AWS providers expose "region", Azure providers expose "location".
LOCATION_KEYS = ("region", "location")

ARN_KEY = "arn"

# arn:partition:service:region:account-id:resource
ARN_REGION_INDEX = 3
ARN_MIN_FIELDS = 6

# Placeholder used when an instance carries no usable location attribute.
UNKNOWN_LOCATION = "unknown"

# Terraform type prefixes per CSP. A state file can hold resources from any
# number of providers, so these decide which ones belong to the assessment.
CSP_TYPE_PREFIXES = {1: "azurerm_", 2: "aws_"}
CSP_NAMES = {1: "Azure", 2: "AWS"}
CSP_SUBCOMMANDS = {1: "azure", 2: "aws"}

# Azure resource ids carry the subscription and resource group that define the
# assessed boundary: /subscriptions/<sub>/resourceGroups/<rg>/providers/...
AZURE_RESOURCE_ID_PATTERN = re.compile(
    r"^/subscriptions/([^/]+)/resourceGroups/([^/]+)/", re.IGNORECASE
)


def parse_tfstate(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except OSError as e:
        raise ValueError(f"Could not read Terraform state file '{path}': {e}")
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Terraform state file '{path}' is not valid JSON: {e}. "
            "For a remote backend, export it first: "
            "`terraform state pull > infra.tfstate`."
        )

    if not isinstance(state, dict):
        raise ValueError(
            f"Terraform state file '{path}' is not a state document "
            "(expected a JSON object at the top level)."
        )

    version = state.get("version")
    if version != SUPPORTED_STATE_VERSION:
        raise ValueError(
            f"Unsupported Terraform state version {version!r} in '{path}'. "
            f"Only state format version {SUPPORTED_STATE_VERSION} is supported "
            "(Terraform >= 0.12 and all OpenTofu versions produce version 4). "
            "For a remote backend, export the current state with "
            "`terraform state pull > infra.tfstate`."
        )

    return state


def _region_from_arn(arn: Any) -> str:
    if not isinstance(arn, str) or not arn.startswith("arn:"):
        return ""

    fields = arn.split(":")
    if len(fields) < ARN_MIN_FIELDS:
        return ""

    return fields[ARN_REGION_INDEX].strip().lower()


def _instance_location(attributes: Any) -> str:
    if not isinstance(attributes, dict):
        return UNKNOWN_LOCATION

    for key in LOCATION_KEYS:
        value = attributes.get(key)
        if isinstance(value, str):
            location = value.strip().lower()
            if location:
                return location

    return _region_from_arn(attributes.get(ARN_KEY)) or UNKNOWN_LOCATION


def _instance_address(
    module: str | None, resource_type: str, name: str, index_key: Any
) -> str:
    address = f"{resource_type}.{name}"
    if module:
        address = f"{module}.{address}"
    if index_key is not None:
        address = f"{address}[{index_key}]"
    return address


def extract_managed_resources(state: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    for resource in state.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        if resource.get("mode") != "managed":
            continue

        resource_type = resource.get("type")
        name = resource.get("name")
        if not resource_type or not name:
            logger.debug("Skipping state entry without a type/name: %r", resource)
            continue

        module = resource.get("module")

        for instance in resource.get("instances") or []:
            if not isinstance(instance, dict):
                continue
            records.append(
                {
                    "address": _instance_address(
                        module, resource_type, name, instance.get("index_key")
                    ),
                    "type": resource_type,
                    "location": _instance_location(instance.get("attributes")),
                }
            )

    return records


def file_sha256(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as state_file:
            for chunk in iter(lambda: state_file.read(65536), b""):
                digest.update(chunk)
    except OSError as e:
        logger.warning("Could not hash the Terraform state file: %s", e)
        return None
    return digest.hexdigest()


def extract_state_scope(
    state: dict[str, Any], cloud_service_provider: int
) -> dict[str, list[str]]:
    subscriptions: set[str] = set()
    resource_groups: set[str] = set()

    if cloud_service_provider == 1:  # Azure
        for resource in state.get("resources") or []:
            if not isinstance(resource, dict) or resource.get("mode") != "managed":
                continue
            for instance in resource.get("instances") or []:
                if not isinstance(instance, dict):
                    continue
                attributes = instance.get("attributes")
                if not isinstance(attributes, dict):
                    continue
                resource_id = attributes.get("id")
                if not isinstance(resource_id, str):
                    continue
                match = AZURE_RESOURCE_ID_PATTERN.match(resource_id)
                if match:
                    subscriptions.add(match.group(1))
                    resource_groups.add(match.group(2))

    return {
        "subscriptions": sorted(subscriptions),
        "resource_groups": sorted(resource_groups),
    }


def _build_tf_code_mapping(
    cloud_service_provider: int, db_path: str
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()

    for item in load_data("resourcetype", db_path=db_path):
        if item["csp"] != cloud_service_provider or item["status"] != "t":
            continue

        tf_code = (item.get("tf_code") or "").strip()
        if not tf_code:
            continue

        existing = mapping.get(tf_code)
        if existing is None:
            mapping[tf_code] = {"id": item["id"], "name": item["name"]}
            continue

        if tf_code not in duplicates:
            duplicates.add(tf_code)
            logger.warning(
                "Terraform type '%s' maps to multiple resource types; "
                "using the lowest id.",
                tf_code,
            )
        if item["id"] < existing["id"]:
            mapping[tf_code] = {"id": item["id"], "name": item["name"]}

    return mapping


def _foreign_provider_summary(foreign_types: dict[str, int]) -> str:
    for csp, prefix in CSP_TYPE_PREFIXES.items():
        count = sum(n for t, n in foreign_types.items() if t.startswith(prefix))
        if count:
            return f"{count} {CSP_NAMES[csp]} resources"

    total = sum(foreign_types.values())
    sample = ", ".join(sorted(foreign_types)[:3])
    return f"{total} resources from other providers ({sample})"


def _no_matching_provider_error(
    cloud_service_provider: int, tfstate_path: str, foreign_types: dict[str, int]
) -> ValueError:
    own_name = CSP_NAMES.get(cloud_service_provider, "matching")
    message = (
        f"No {own_name} resources found in '{os.path.basename(tfstate_path)}'. "
        f"The state contains {_foreign_provider_summary(foreign_types)}."
    )

    # Point at the other subcommand only when we could actually assess it.
    for csp, prefix in CSP_TYPE_PREFIXES.items():
        if csp == cloud_service_provider:
            continue
        if any(t.startswith(prefix) for t in foreign_types):
            message += (
                f" Did you mean: python3 main.py {CSP_SUBCOMMANDS[csp]} "
                f"--tfstate {tfstate_path}"
            )
            break

    return ValueError(message)


def build_tfstate_resource_inventory(
    cloud_service_provider: int,
    provider_details: dict[str, Any],
    report_path: str,
    raw_data_path: str,
) -> dict[str, Any]:
    tfstate_path = provider_details["tfstatePath"]
    state = parse_tfstate(tfstate_path)
    instances = extract_managed_resources(state)

    db_path = os.path.join(report_path, "data", "assessment.db")
    resource_type_mapping = _build_tf_code_mapping(cloud_service_provider, db_path)

    # A state file may hold resources from any number of providers. Split them
    # up front so a foreign resource is never silently lumped in with the
    # same-provider glue types that legitimately have no dataset row.
    own_prefix = CSP_TYPE_PREFIXES.get(cloud_service_provider, "")
    foreign_types: defaultdict[str, int] = defaultdict(int)
    own_instances = []
    for instance in instances:
        if own_prefix and not instance["type"].startswith(own_prefix):
            foreign_types[instance["type"]] += 1
        else:
            own_instances.append(instance)

    # Nothing for the selected provider, but resources for another one: the
    # wrong subcommand or the wrong file. An empty state is left alone.
    if instances and not own_instances:
        raise _no_matching_provider_error(
            cloud_service_provider, tfstate_path, dict(foreign_types)
        )

    # Aggregate matched instances, and count the types we have no mapping for.
    aggregated_resources: defaultdict[tuple[int, str], int] = defaultdict(int)
    unmapped_types: defaultdict[str, int] = defaultdict(int)
    counted: list[dict[str, Any]] = []

    for instance in own_instances:
        resource_info = resource_type_mapping.get(instance["type"])
        if not resource_info:
            unmapped_types[instance["type"]] += 1
            continue

        resource_type_id = resource_info["id"]
        aggregated_resources[(resource_type_id, instance["location"])] += 1
        counted.append(
            {
                "address": instance["address"],
                "type": instance["type"],
                "resource_type_id": resource_type_id,
                "location": instance["location"],
            }
        )

    # Insert aggregated data into SQLite
    try:
        with connect(db_path=db_path) as conn:
            cursor = conn.cursor()
            for (
                resource_type_id,
                resource_location,
            ), resource_count in aggregated_resources.items():
                try:
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
            conn.commit()
    except sqlite3.Error as e:
        logger.error(
            f"Error writing the tfstate resource inventory: {e}", exc_info=True
        )
        raise

    # Counts are per instance, not per type: "9 types skipped" can hide any
    # number of dropped resources, which is exactly what needs surfacing.
    excluded_foreign = sum(foreign_types.values())
    excluded_unmapped = sum(unmapped_types.values())
    coverage = {
        "instances_total": len(instances),
        "instances_counted": len(counted),
        "instances_excluded_other_provider": excluded_foreign,
        "instances_excluded_unmapped": excluded_unmapped,
    }

    # What the report's Scope of Assessment section renders from. The hash ties
    # a report back to the exact file it was produced from.
    scope = {
        "file": os.path.basename(tfstate_path),
        "sha256": file_sha256(tfstate_path),
        "lineage": state.get("lineage"),
        "serial": state.get("serial"),
        "locations": sorted({entry["location"] for entry in counted}),
        **extract_state_scope(state, cloud_service_provider),
    }

    # Manifest of what was counted. Instance attributes carry secrets
    # (passwords, connection strings, keys) and are deliberately never written.
    manifest = {
        "source_file": os.path.basename(tfstate_path),
        "terraform_version": state.get("terraform_version"),
        "state_serial": state.get("serial"),
        "scope": scope,
        "coverage": coverage,
        "counted": counted,
        "unmapped_types": dict(unmapped_types),
        "other_provider_types": dict(foreign_types),
    }

    manifest_path = os.path.join(raw_data_path, "tfstate_manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as manifest_file:
            json.dump(manifest, manifest_file, indent=4)
    except OSError as e:
        logger.error(f"Could not write the tfstate manifest: {e}", exc_info=True)

    if unmapped_types:
        logger.warning(
            "%d Terraform resource type(s) had no matching resource type and were "
            "skipped; see raw_data/tfstate_manifest.json for details.",
            len(unmapped_types),
        )

    if excluded_foreign:
        logger.warning(
            "%d resource(s) in the state belong to another cloud provider and were "
            "not assessed.",
            excluded_foreign,
        )

    return coverage
