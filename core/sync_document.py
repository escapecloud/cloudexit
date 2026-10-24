from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cloudexit.catalogue.store import (
    CatalogueError,
    active_resource_type_ids,
    catalogue_dir,
    catalogue_info,
)
from cloudexit.version import __version__

from cloudexit.catalogue.db import load_data

logger = logging.getLogger("core.engine.sync_document")

SCHEMA_VERSION = "2.0"
COLLECTOR_NAME = "cloudexit"
PROVIDERS = {1: "azure", 2: "aws"}

MAX_BODY_BYTES = 1_000_000
MAX_RESOURCE_ITEMS = 1000
MAX_COUNT_PER_ITEM = 10000
MAX_LOCATION_CHARS = 255
MAX_TYPE_CODE_CHARS = 255
MAX_COST_ITEMS = 12
MAX_EGRESS_ITEMS = 1000
MAX_NAME_CHARS = 255
MAX_FLAGS = 10
MAX_FLAG_CHARS = 64
MAX_NOTES = 10
MAX_NOTE_CHARS = 255
MAX_TIERS = 10
MAX_TIER_CHARS = 50
MAX_CATALOGUE_TYPE_IDS = 5000
MAX_SIZE_BYTES = 2**53 - 1
MAX_EPOCH = 4102444800

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(f"^{UUID}$")
UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
AMOUNT_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")
CATALOGUE_VERSION_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(\.[0-9]+)?$")
AWS_REGION_RE = re.compile(r"^(\*|[a-z]{2}(-[a-z]+)+-[0-9]+)$")
AZURE_GROUP_RE = re.compile(r"^[A-Za-z0-9_()\-.]{1,90}$")
SCOPE_KEY_MAX = 255


class SyncDocumentError(Exception):
    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


class ScopeError(ValueError):
    def __init__(self, message: str, pointer: str = ""):
        super().__init__(message)
        self.pointer = pointer


@dataclass
class RunContext:
    sync_id: str
    started_at: int
    cloud_service_provider: int
    assessment_type: int
    provider_details: dict[str, Any]
    egress_collected: bool = False
    egress_names: str = "plain"
    completed_at: int | None = None
    target: dict[str, Any] | None = None
    ci: dict[str, Any] | None = None


@dataclass(frozen=True)
class SyncOutput:
    path: Path
    size: int
    sha256: str
    emitted: Path | None
    summary: str
    document: dict[str, Any]


def new_uuid7(now_ms: int | None = None) -> str:
    if now_ms is None:
        native = getattr(uuid, "uuid7", None)
        if native is not None:
            return str(native())
        now_ms = time.time_ns() // 1_000_000

    value = (now_ms & (2**48 - 1)) << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return str(uuid.UUID(int=value))


def scope_key(scope: dict[str, Any]) -> str:
    if not isinstance(scope, dict):
        raise ScopeError("the scope must be an object")
    provider = scope.get("provider")
    if provider not in ("aws", "azure"):
        raise ScopeError("the provider must be aws or azure", "/provider")

    if "tfstate" in scope:
        for block in ("aws", "azure"):
            if block in scope:
                raise ScopeError(
                    "a Terraform state scope has no provider block", f"/{block}"
                )
        tfstate = scope["tfstate"] if isinstance(scope["tfstate"], dict) else {}
        lineage = tfstate.get("lineage")
        if not isinstance(lineage, str) or not UUID_RE.match(lineage):
            raise ScopeError("the lineage must be a UUID", "/tfstate/lineage")
        key = f"tfstate:{lineage.lower()}"
    elif provider == "aws":
        key = _aws_key(_block(scope, "aws"))
    else:
        key = _azure_key(_block(scope, "azure"))

    if len(key) > SCOPE_KEY_MAX:
        raise ScopeError("the scope key is too long")
    return key


def _block(scope: dict[str, Any], name: str) -> dict[str, Any]:
    block = scope.get(name)
    if not isinstance(block, dict):
        raise ScopeError(f"the {name} block is required", f"/{name}")
    return block


def _aws_key(aws: dict[str, Any]) -> str:
    account = aws.get("account_id")
    if not isinstance(account, str) or not re.fullmatch(r"[0-9]{12}", account):
        raise ScopeError("the account id must be 12 digits", "/aws/account_id")

    regions = aws.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ScopeError("at least one region is required", "/aws/regions")

    distinct = []
    for index, region in enumerate(regions):
        if not isinstance(region, str) or not AWS_REGION_RE.match(region):
            raise ScopeError("not a region", f"/aws/regions/{index}")
        if region not in distinct:
            distinct.append(region)
    region = distinct[0] if len(distinct) == 1 else "*"
    return f"aws:{account}:{region}"


def _azure_key(azure: dict[str, Any]) -> str:
    parts = []
    for name in ("tenant_id", "subscription_id"):
        value = azure.get(name)
        if not isinstance(value, str) or not UUID_RE.match(value):
            raise ScopeError("must be a GUID", f"/azure/{name}")
        parts.append(value.lower())

    group = azure.get("resource_group")
    if group is None:
        group_key = "*"
    elif (
        not isinstance(group, str)
        or not AZURE_GROUP_RE.match(group)
        or group.endswith(".")
    ):
        raise ScopeError("not a resource group name", "/azure/resource_group")
    else:
        group_key = group.lower()
    return f"azure:{parts[0]}:{parts[1]}:{group_key}"


def decimal_amount(value: Any) -> str:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        raise SyncDocumentError(f"cost amount {value!r} is not a number") from None
    if not amount.is_finite():
        raise SyncDocumentError(f"cost amount {value!r} is not a finite number")

    try:
        amount = amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise SyncDocumentError(f"cost amount {value!r} is too large") from None
    if amount == 0:
        return "0"
    return format(amount.normalize(), "f")


def build_scope(context: RunContext, raw_data_path: str) -> dict[str, Any]:
    provider = PROVIDERS.get(context.cloud_service_provider)
    if provider is None:
        raise SyncDocumentError(
            f"unsupported cloud service provider {context.cloud_service_provider}"
        )
    details = context.provider_details

    if details.get("tfstatePath"):
        manifest_path = os.path.join(raw_data_path, "tfstate_manifest.json")
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                state_scope = json.load(handle).get("scope") or {}
        except (OSError, ValueError) as exc:
            raise SyncDocumentError(
                f"cannot read the Terraform state scope: {exc}"
            ) from exc
        serial = state_scope.get("serial")
        return {
            "provider": provider,
            "tfstate": {
                "lineage": state_scope.get("lineage"),
                "serial": serial if isinstance(serial, int) else None,
            },
        }

    if provider == "aws":
        return {
            "provider": "aws",
            "aws": {
                "account_id": details.get("accountId"),
                "regions": [details.get("region")],
            },
        }
    return {
        "provider": "azure",
        "azure": {
            "tenant_id": details.get("tenantId"),
            "subscription_id": details.get("subscriptionId"),
            "resource_group": details.get("resourceGroupName") or None,
        },
    }


def _cut(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


def _resources(db_path: str) -> list[dict[str, Any]]:
    codes = {
        row["id"]: row["code"] for row in load_data("resourcetype", db_path=db_path)
    }
    rows = sorted(
        load_data("resource_inventory", db_path=db_path),
        key=lambda row: (int(row["resource_type"]), row["location"] or ""),
    )

    items = []
    for row in rows:
        type_id = int(row["resource_type"])
        location = _cut(row["location"] or "unknown", MAX_LOCATION_CHARS)[0]
        code = codes.get(type_id)
        remaining = int(row["count"])
        # Counts above the per-item limit are split; the host merges items with
        # the same type and location by summing their counts.
        while True:
            chunk = min(remaining, MAX_COUNT_PER_ITEM)
            item = {"type_id": type_id}
            if code:
                item["type_code"] = _cut(code, MAX_TYPE_CODE_CHARS)[0]
            item["location"] = location
            item["count"] = chunk
            items.append(item)
            remaining -= chunk
            if remaining <= 0:
                break
    return items


def _costs(db_path: str, completed_at: int) -> list[dict[str, Any]]:
    current_month = datetime.fromtimestamp(completed_at, timezone.utc).strftime("%Y-%m")
    items = []
    for row in load_data("cost_inventory", db_path=db_path):
        month = str(row["month"])[:7]
        currency = str(row["currency"])
        if not MONTH_RE.match(month) or not CURRENCY_RE.match(currency):
            raise SyncDocumentError(
                "a stored cost row has an invalid month or currency"
            )
        items.append(
            {
                "month": month,
                "amount": decimal_amount(row["cost"]),
                "currency": currency.upper(),
                "final": month < current_month,
            }
        )

    items.sort(key=lambda item: item["month"])
    if len(items) > MAX_COST_ITEMS:
        logger.warning(
            "Cost data trimmed to the API v2 limit: %d month(s) dropped.",
            len(items) - MAX_COST_ITEMS,
        )
        items = items[-MAX_COST_ITEMS:]
    return items


def salt_path(key: str) -> Path:
    name = re.sub(r'[<>:"/\\|?*]', "_", key) if sys.platform == "win32" else key
    return catalogue_dir().parent / "salts" / name


def load_salt(key: str) -> bytes:
    path = salt_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            return bytes.fromhex(path.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            raise SyncDocumentError(f"the salt file {path} is unusable: {exc}") from exc
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32) + "\n")
    raise SyncDocumentError(f"cannot create the salt file {path}")


def hash_name(name: str, salt: bytes) -> str:
    return hmac.new(salt, name.encode("utf-8"), hashlib.sha256).hexdigest()


def _json_list(raw: Any) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(entry) for entry in value] if isinstance(value, list) else []


def _egress(db_path: str, context: RunContext, key: str) -> dict[str, Any]:
    if not context.egress_collected:
        return {"collected": False, "truncated": False, "names": "plain", "items": []}

    hashed = context.egress_names == "hashed"
    salt = load_salt(key) if hashed else b""

    tiers_by_row: dict[int, list[dict[str, Any]]] = {}
    for tier in load_data("egress_inventory_tier", db_path=db_path):
        tiers_by_row.setdefault(tier["egress_inventory_id"], []).append(tier)

    trimmed = {
        "items dropped": 0,
        "names shortened": 0,
        "sizes capped": 0,
        "flags dropped": 0,
        "notes dropped": 0,
        "text shortened": 0,
        "tiers dropped": 0,
    }

    def size_of(value: Any) -> int | None:
        if value is None:
            return None
        number = max(0, int(value))
        if number > MAX_SIZE_BYTES:
            trimmed["sizes capped"] += 1
            return MAX_SIZE_BYTES
        return number

    records = load_data("egress_inventory", db_path=db_path)
    items = []
    for record in records:
        name = str(record["name"])
        if hashed:
            name = hash_name(name, salt)
        else:
            name, cut = _cut(name, MAX_NAME_CHARS)
            trimmed["names shortened"] += cut

        size = None if record["size_unknown"] else size_of(record["size_bytes"])
        item: dict[str, Any] = {
            "type_id": int(record["resource_type"]),
            "name": name,
            "size_bytes": size,
        }

        for field, limit, chars in (
            ("flags", MAX_FLAGS, MAX_FLAG_CHARS),
            ("notes", MAX_NOTES, MAX_NOTE_CHARS),
        ):
            values = _json_list(record[field])
            trimmed[f"{field} dropped"] += max(0, len(values) - limit)
            kept = []
            for value in values[:limit]:
                value, cut = _cut(value, chars)
                trimmed["text shortened"] += cut
                kept.append(value)
            item[field] = kept

        tiers = sorted(
            tiers_by_row.get(record["id"], []),
            key=lambda tier: (-(tier["size_bytes"] or 0), str(tier["tier"])),
        )
        trimmed["tiers dropped"] += max(0, len(tiers) - MAX_TIERS)
        item["tiers"] = []
        for tier in tiers[:MAX_TIERS]:
            label, cut = _cut(str(tier["tier"]), MAX_TIER_CHARS)
            trimmed["text shortened"] += cut
            item["tiers"].append(
                {
                    "tier": label,
                    "size_bytes": size_of(tier["size_bytes"]),
                    "is_archive": bool(tier["is_archive"]),
                }
            )
        items.append(item)

    items.sort(
        key=lambda item: (
            item["size_bytes"] is None,
            -(item["size_bytes"] or 0),
            item["type_id"],
            item["name"],
        )
    )
    if len(items) > MAX_EGRESS_ITEMS:
        trimmed["items dropped"] = len(items) - MAX_EGRESS_ITEMS
        items = items[:MAX_EGRESS_ITEMS]

    counts = {label: count for label, count in trimmed.items() if count}
    if counts:
        logger.warning(
            "Egress data trimmed to the API v2 limits: %s.",
            ", ".join(f"{count} {label}" for label, count in counts.items()),
        )
    return {
        "collected": True,
        "truncated": bool(counts),
        "names": "hashed" if hashed else "plain",
        "items": items,
    }


def _catalogue_claim(db_path: str, provider: str) -> dict[str, Any] | None:
    try:
        info = catalogue_info(db_path)
    except CatalogueError:
        return None
    if (
        info is None
        or not CATALOGUE_VERSION_RE.match(info.version)
        or not isinstance(info.sequence, int)
        or not isinstance(info.sha256, str)
        or not SHA256_RE.match(info.sha256)
    ):
        # An older dataset has no usable catalogue_meta: the version is unknown.
        return None
    return {
        "version": info.version,
        "sequence": info.sequence,
        "sha256": info.sha256,
        "resource_type_ids": active_resource_type_ids(provider, db_path)[
            :MAX_CATALOGUE_TYPE_IDS
        ],
    }


def build_sync_document(report_path: str, context: RunContext) -> dict[str, Any]:
    db_path = os.path.join(report_path, "data", "assessment.db")
    raw_data_path = os.path.join(report_path, "raw_data")
    provider = PROVIDERS.get(context.cloud_service_provider, "")
    completed_at = context.completed_at or int(time.time())

    scope = build_scope(context, raw_data_path)
    try:
        key = scope_key(scope)
    except ScopeError as exc:
        raise SyncDocumentError(
            f"the scope is not valid ({exc.pointer or 'scope'}: {exc})"
        ) from exc

    collector: dict[str, Any] = {
        "name": COLLECTOR_NAME,
        "version": __version__,
        "mode": "tfstate" if "tfstate" in scope else "api",
    }
    try:
        claim = _catalogue_claim(db_path, provider)
    except sqlite3.Error as exc:
        raise SyncDocumentError(f"cannot read the run's catalogue: {exc}") from exc
    if claim:
        collector["catalogue"] = claim

    sync: dict[str, Any] = {
        "id": context.sync_id,
        "started_at": context.started_at,
        "completed_at": max(completed_at, context.started_at),
        "collector": collector,
    }
    if context.ci:
        sync["ci"] = context.ci

    document: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "sync": sync}
    if context.target:
        document["target"] = context.target
    document["assessment_type"] = (
        3 if context.egress_collected else min(max(context.assessment_type, 1), 3)
    )
    document["scope"] = scope
    try:
        document["inventory"] = {
            "resources": _resources(db_path),
            "costs": _costs(db_path, sync["completed_at"]),
            "egress": _egress(db_path, context, key),
        }
    except sqlite3.Error as exc:
        raise SyncDocumentError(f"cannot read the run's data: {exc}") from exc
    return document


def _is_int(value: Any, low: int = 0, high: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= low
        and (high is None or value <= high)
    )


def _check_text(
    problems: list[str], pointer: str, value: Any, limit: int, low: int = 0
) -> None:
    if not isinstance(value, str) or not low <= len(value) <= limit:
        problems.append(f"{pointer}: must be a string of {low} to {limit} characters")


def validate_document(document: dict[str, Any]) -> None:
    problems: list[str] = []

    def require(pointer: str, ok: bool, message: str) -> None:
        if not ok:
            problems.append(f"{pointer}: {message}")

    for name in ("schema_version", "sync", "assessment_type", "scope", "inventory"):
        require(f"/{name}", name in document, "is required")
    if problems:
        raise SyncDocumentError("the sync document is invalid", problems)

    require(
        "/schema_version",
        isinstance(document["schema_version"], str)
        and re.match(r"^2\.[0-9]+$", document["schema_version"]),
        "must look like 2.<minor>",
    )
    require(
        "/assessment_type",
        _is_int(document["assessment_type"], 1, 3),
        "must be 1, 2 or 3",
    )

    sync = document["sync"]
    require("/sync/id", bool(UUID7_RE.match(str(sync.get("id")))), "must be a UUIDv7")
    started, completed = sync.get("started_at"), sync.get("completed_at")
    require("/sync/started_at", _is_int(started, 0, MAX_EPOCH), "must be epoch seconds")
    require(
        "/sync/completed_at", _is_int(completed, 0, MAX_EPOCH), "must be epoch seconds"
    )
    if _is_int(started) and _is_int(completed):
        require("/sync/completed_at", completed >= started, "is before started_at")

    collector = sync.get("collector") or {}
    _check_text(problems, "/sync/collector/name", collector.get("name"), 64, 1)
    _check_text(problems, "/sync/collector/version", collector.get("version"), 64, 1)
    require(
        "/sync/collector/mode",
        collector.get("mode") in ("api", "tfstate"),
        "must be api or tfstate",
    )
    claim = collector.get("catalogue")
    if claim is not None:
        ids = claim.get("resource_type_ids")
        require(
            "/sync/collector/catalogue",
            bool(CATALOGUE_VERSION_RE.match(str(claim.get("version"))))
            and _is_int(claim.get("sequence"))
            and bool(SHA256_RE.match(str(claim.get("sha256"))))
            and isinstance(ids, list)
            and len(ids) <= MAX_CATALOGUE_TYPE_IDS
            and len(set(ids)) == len(ids)
            and all(_is_int(entry, 1) for entry in ids),
            "needs version, sequence, sha256 and unique resource_type_ids",
        )

    scope = document["scope"]
    try:
        scope_key(scope)
    except ScopeError as exc:
        problems.append(f"/scope{exc.pointer}: {exc}")
    require(
        "/scope",
        ("tfstate" in scope) == (collector.get("mode") == "tfstate"),
        "tfstate must be sent exactly when the collector mode is tfstate",
    )
    require(
        "/scope/provider",
        scope.get("provider") in ("aws", "azure"),
        "must be aws or azure",
    )

    inventory = document["inventory"]
    resources = inventory.get("resources")
    if not isinstance(resources, list) or len(resources) > MAX_RESOURCE_ITEMS:
        problems.append(
            f"/inventory/resources: needs at most {MAX_RESOURCE_ITEMS} items"
        )
        resources = []
    for index, item in enumerate(resources):
        pointer = f"/inventory/resources/{index}"
        require(f"{pointer}/type_id", _is_int(item.get("type_id"), 1), "must be >= 1")
        require(
            f"{pointer}/count",
            _is_int(item.get("count"), 0, MAX_COUNT_PER_ITEM),
            f"must be 0 to {MAX_COUNT_PER_ITEM}",
        )
        _check_text(problems, f"{pointer}/location", item.get("location"), 255)
        if "type_code" in item:
            _check_text(problems, f"{pointer}/type_code", item["type_code"], 255)

    costs = inventory.get("costs", [])
    if not isinstance(costs, list) or len(costs) > MAX_COST_ITEMS:
        problems.append(f"/inventory/costs: needs at most {MAX_COST_ITEMS} items")
        costs = []
    for index, item in enumerate(costs):
        pointer = f"/inventory/costs/{index}"
        require(
            f"{pointer}/month",
            bool(MONTH_RE.match(str(item.get("month")))),
            "is not YYYY-MM",
        )
        amount = item.get("amount")
        require(
            f"{pointer}/amount",
            isinstance(amount, str)
            and len(amount) <= 40
            and bool(AMOUNT_RE.match(amount)),
            "must be a decimal string",
        )
        require(
            f"{pointer}/currency",
            bool(CURRENCY_RE.match(str(item.get("currency")))),
            "must be three letters",
        )
        require(
            f"{pointer}/final",
            isinstance(item.get("final", False), bool),
            "must be a boolean",
        )

    egress = inventory.get("egress")
    if egress is not None:
        _validate_egress(egress, require, problems)

    body = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        problems.append(f"/: the document is larger than {MAX_BODY_BYTES} bytes")

    if problems:
        raise SyncDocumentError("the sync document is invalid", problems)


def _validate_egress(egress: dict[str, Any], require: Any, problems: list[str]) -> None:
    require(
        "/inventory/egress/collected",
        isinstance(egress.get("collected"), bool),
        "must be a boolean",
    )
    require(
        "/inventory/egress/truncated",
        isinstance(egress.get("truncated", False), bool),
        "must be a boolean",
    )
    require(
        "/inventory/egress/names",
        egress.get("names", "plain") in ("plain", "hashed"),
        "must be plain or hashed",
    )

    items = egress.get("items", [])
    if not isinstance(items, list) or len(items) > MAX_EGRESS_ITEMS:
        problems.append(
            f"/inventory/egress/items: needs at most {MAX_EGRESS_ITEMS} items"
        )
        return
    for index, item in enumerate(items):
        pointer = f"/inventory/egress/items/{index}"
        require(f"{pointer}/type_id", _is_int(item.get("type_id"), 1), "must be >= 1")
        _check_text(problems, f"{pointer}/name", item.get("name"), MAX_NAME_CHARS)
        size = item.get("size_bytes")
        require(
            f"{pointer}/size_bytes",
            size is None or _is_int(size, 0, MAX_SIZE_BYTES),
            "must be null or 0 to 2^53-1",
        )
        for field, limit, chars in (
            ("flags", MAX_FLAGS, MAX_FLAG_CHARS),
            ("notes", MAX_NOTES, MAX_NOTE_CHARS),
        ):
            values = item.get(field, [])
            if not isinstance(values, list) or len(values) > limit:
                problems.append(f"{pointer}/{field}: needs at most {limit} items")
                continue
            for position, value in enumerate(values):
                _check_text(problems, f"{pointer}/{field}/{position}", value, chars)
        tiers = item.get("tiers", [])
        if not isinstance(tiers, list) or len(tiers) > MAX_TIERS:
            problems.append(f"{pointer}/tiers: needs at most {MAX_TIERS} items")
            continue
        for position, tier in enumerate(tiers):
            tier_pointer = f"{pointer}/tiers/{position}"
            _check_text(
                problems, f"{tier_pointer}/tier", tier.get("tier"), MAX_TIER_CHARS, 1
            )
            tier_size = tier.get("size_bytes")
            require(
                f"{tier_pointer}/size_bytes",
                tier_size is None or _is_int(tier_size, 0, MAX_SIZE_BYTES),
                "must be null or 0 to 2^53-1",
            )
            require(
                f"{tier_pointer}/is_archive",
                isinstance(tier.get("is_archive", False), bool),
                "must be a boolean",
            )


def summary_line(document: dict[str, Any], size: int, digest: str) -> str:
    inventory = document["inventory"]
    egress = inventory["egress"]
    return (
        f"Sync document {document['sync']['id']}: "
        f"{len(inventory['resources'])} resource item(s), "
        f"{len(inventory['costs'])} cost month(s), "
        f"egress collected={str(egress['collected']).lower()} "
        f"truncated={str(egress['truncated']).lower()} "
        f"({len(egress['items'])} item(s)), {size} bytes, sha256 {digest}"
    )


def write_sync_document(
    document: dict[str, Any], raw_data_path: str, emit_dir: str | None = None
) -> SyncOutput:
    data = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()

    path = Path(raw_data_path) / "sync.json"
    path.write_bytes(data)

    emitted = None
    if emit_dir:
        emitted = Path(emit_dir) / f"{document['sync']['id']}.json"
        emitted.parent.mkdir(parents=True, exist_ok=True)
        emitted.write_bytes(data)

    summary = summary_line(document, len(data), digest)
    logger.info(summary)
    return SyncOutput(path, len(data), digest, emitted, summary, document)


def prepare_sync_document(
    report_path: str, context: RunContext, emit_dir: str | None = None
) -> SyncOutput:
    document = build_sync_document(report_path, context)
    validate_document(document)
    return write_sync_document(
        document, os.path.join(report_path, "raw_data"), emit_dir
    )
