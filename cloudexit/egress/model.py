# cloudexit/egress/model.py
import json
import logging
from typing import Any

from cloudexit.catalogue.db import connect, load_data

logger = logging.getLogger(__name__)


GIB = 1024**3


def _parse_params(raw_params: Any) -> dict[str, Any]:
    if isinstance(raw_params, dict):
        return raw_params
    try:
        parsed = json.loads(raw_params or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_egress_registry(csp: int) -> dict[str, dict[str, Any]]:
    # Master data is the only source of truth: a missing table or an empty
    # result fails the egress stage rather than producing a wrong estimate.
    data_rows = load_data("resourcetype_data")
    resource_types = load_data("resourcetype")

    types_by_id = {rt["id"]: rt for rt in resource_types}

    registry: dict[str, dict[str, Any]] = {}
    for row in data_rows:
        if row["status"] != "t":
            continue
        resource_type = types_by_id.get(row["resource_type"])
        if resource_type is None or resource_type["csp"] != csp:
            continue

        # resourcetype.status is deliberately not filtered. Rows such as EBS
        # Snapshots and Managed Disks ship as 'f' so they stay out of the
        # inventory catalogue, but they still carry data that has to be egressed
        # -- excluding them would silently drop a whole category from the
        # estimate.
        code = resource_type["code"]
        # Azure matches against ARM resource.type, which is case-insensitive;
        # AWS codes are handed to the collectors as the row identifier verbatim.
        key = code.strip().lower() if csp == 1 else code

        params = _parse_params(row["params"])
        registry[key] = {
            **params,
            # Carried so the egress_inventory writer has the FK without a
            # reverse lookup -- the Azure key is lowercased, the code is not.
            "resource_type_id": resource_type["id"],
            "category": row["data_category"],
            "label": resource_type["name"],
            "strategy": row["strategy"],
        }

    if not registry:
        raise ValueError(
            f"No enabled resourcetype_data rows for CSP {csp}; "
            "cannot build the egress registry."
        )

    return registry


def new_row(
    resource_id: str, name: str, resource_type: str, label: str, category: str
) -> dict[str, Any]:
    return {
        "id": resource_id,
        "name": name,
        "type": resource_type,
        "label": label,
        "category": category,
        "size_bytes": None,
        "size_unknown": False,
        "tier_bytes": None,
        "flags": [],
        "notes": [],
    }


def format_bytes(size_bytes: int | float | None) -> str:
    if size_bytes is None:
        return "n/a"
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def compute_totals(
    rows: list[dict[str, Any]], archive_tiers: set[str]
) -> dict[str, Any]:
    known_size_bytes = sum(
        row["size_bytes"] for row in rows if row["size_bytes"] is not None
    )
    archive_tier_bytes = sum(
        size
        for row in rows
        for tier, size in (row["tier_bytes"] or {}).items()
        if tier in archive_tiers
    )
    unknown_count = sum(1 for row in rows if row["size_unknown"])
    return {
        "known_size_bytes": known_size_bytes,
        "archive_tier_bytes": archive_tier_bytes,
        "resources_discovered": len(rows),
        "resources_with_unknown_size": unknown_count,
    }


def resource_type_id_for(
    resource_type_ids: dict[str, int], row_type: str
) -> int | None:
    # AWS rows carry the code verbatim; Azure rows carry the ARM resource.type,
    # which the registry keys in lowercase.
    return resource_type_ids.get(row_type) or resource_type_ids.get(
        row_type.strip().lower()
    )


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    # The label is dropped: it is resourcetype.name, already in the database.
    return {
        "id": row["id"],
        "name": row["name"],
        "code": row["type"],
        "category": row["category"],
        "size_bytes": row["size_bytes"],
        "size_unknown": row["size_unknown"],
        "tier_bytes": row["tier_bytes"],
        "flags": row["flags"],
        "notes": row["notes"],
    }


def write_egress_inventory(
    rows: list[dict[str, Any]],
    resource_type_ids: dict[str, int],
    archive_tiers: set[str],
    db_path: str,
) -> None:
    conn = connect(db_path)
    try:
        cursor = conn.cursor()
        for row in rows:
            resource_type_id = resource_type_id_for(resource_type_ids, row["type"])
            if resource_type_id is None:
                # Should not happen: every row is built from a registry entry.
                logger.warning(
                    "No resourcetype id for %s; row not stored.", row["type"]
                )
                continue
            cursor.execute(
                "INSERT INTO egress_inventory "
                "(resource_type, name, size_bytes, size_unknown, flags, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    resource_type_id,
                    row["name"],
                    row["size_bytes"],
                    int(bool(row["size_unknown"])),
                    json.dumps(row["flags"]),
                    json.dumps(row["notes"]),
                ),
            )
            inventory_id = cursor.lastrowid
            for tier, size_bytes in (row["tier_bytes"] or {}).items():
                cursor.execute(
                    "INSERT INTO egress_inventory_tier "
                    "(egress_inventory_id, tier, size_bytes, is_archive) "
                    "VALUES (?, ?, ?, ?)",
                    (inventory_id, tier, size_bytes, int(tier in archive_tiers)),
                )
        conn.commit()
    finally:
        conn.close()


def read_egress_inventory(db_path: str) -> tuple[list[dict[str, Any]], set[str]]:
    # Rebuilds the row shape the report already consumes, so everything
    # downstream of _load_estimate is unchanged.
    resource_types = {rt["id"]: rt for rt in load_data("resourcetype", db_path=db_path)}
    categories = {
        rtd["resource_type"]: rtd["data_category"]
        for rtd in load_data("resourcetype_data", db_path=db_path)
    }

    tiers_by_row: dict[int, dict[str, int]] = {}
    archive_tiers: set[str] = set()
    for tier_row in load_data("egress_inventory_tier", db_path=db_path):
        tiers_by_row.setdefault(tier_row["egress_inventory_id"], {})[
            tier_row["tier"]
        ] = tier_row["size_bytes"]
        if tier_row["is_archive"]:
            archive_tiers.add(tier_row["tier"])

    rows = []
    for record in load_data("egress_inventory", db_path=db_path):
        resource_type = resource_types[record["resource_type"]]
        row = new_row(
            record["id"],
            record["name"],
            resource_type["code"],
            resource_type["name"],
            categories.get(record["resource_type"], ""),
        )
        row["size_bytes"] = record["size_bytes"]
        row["size_unknown"] = bool(record["size_unknown"])
        row["tier_bytes"] = tiers_by_row.get(record["id"]) or None
        row["flags"] = json.loads(record["flags"] or "[]")
        row["notes"] = json.loads(record["notes"] or "[]")
        rows.append(row)
    return rows, archive_tiers
