# cloudexit/providers/aws/client.py
import botocore.session
import json
from typing import Any
from datetime import datetime
from functools import lru_cache
from botocore.config import Config
from botocore.exceptions import (
    ConnectTimeoutError,
    EndpointConnectionError,
)

# Throttling keeps its eight adaptive attempts; an endpoint that cannot be
# reached is given up on much sooner (see limit_connection_retries).
AWS_RETRY_CONFIG = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"mode": "adaptive", "max_attempts": 8},
)


CONNECTION_ERRORS = (ConnectTimeoutError, EndpointConnectionError)


CONNECTION_ATTEMPTS = 2


SERVICE_OK = "ok"


SERVICE_NOT_OFFERED = "not_offered"


SERVICE_UNKNOWN = "unknown"


# Upper bound on the items collected for a single resource type. Some list
# operations (e.g. describe_images, describe_snapshots) return every publicly
# shared resource in the region when called without an owner filter, which would
# otherwise stall the scan and blow up the raw-data file.
MAX_ITEMS_PER_RESOURCE_TYPE = 50_000


def limit_connection_retries(
    attempts: int, caught_exception: Any = None, **kwargs: Any
) -> None:
    # botocore retries a connection error as often as it retries throttling,
    # which makes a dead endpoint cost minutes. Raising from the retry check
    # ends the call with the original error after the second attempt.
    if attempts >= CONNECTION_ATTEMPTS and isinstance(
        caught_exception, CONNECTION_ERRORS
    ):
        raise caught_exception


def create_client(session: Any, service_name: str, region: str) -> Any:
    client = session.client(service_name, region_name=region, config=AWS_RETRY_CONFIG)
    client.meta.events.register_first("needs-retry", limit_connection_retries)
    return client


@lru_cache(maxsize=None)
def _service_regions(service_name: str) -> tuple[frozenset[str], bool] | None:
    # What the installed botocore knows about where a service is offered; None
    # when it does not know the service at all.
    session = botocore.session.get_session()
    if service_name not in session.get_available_services():
        return None
    regional: set[str] = set()
    global_endpoint = False
    for partition in session.get_available_partitions():
        regions = set(session.get_available_regions(service_name, partition))
        everything = set(
            session.get_available_regions(
                service_name, partition, allow_non_regional=True
            )
        )
        regional |= regions
        global_endpoint = global_endpoint or bool(everything - regions)
    return frozenset(regional), global_endpoint


@lru_cache(maxsize=None)
def _known_regions() -> frozenset[str]:
    session = botocore.session.get_session()
    return frozenset(
        region
        for partition in session.get_available_partitions()
        for region in session.get_available_regions("ec2", partition)
    )


def service_status(service_name: str, region: str) -> str:
    info = _service_regions(service_name)
    if info is None:
        return SERVICE_UNKNOWN
    regional, global_endpoint = info
    # A global service is called wherever the user is; a region the installed
    # botocore has never heard of is left to the call itself.
    if global_endpoint or region in regional or region not in _known_regions():
        return SERVICE_OK
    return SERVICE_NOT_OFFERED


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
