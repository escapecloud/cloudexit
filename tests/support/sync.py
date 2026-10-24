import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from tests.support.catalogue import build_catalogue
from tests.support.paths import DEFINITION

STARTED_AT = int(datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).timestamp())
COMPLETED_AT = STARTED_AT + 90
SYNC_ID = "01939b8e-7c2a-7d41-9a51-3c8f0a6e21d4"

ACCESS_KEY = "AKIAFAKEACCESSKEY1234"
SECRET_KEY = "fake-secret-key-AAAABBBBCCCC"
SESSION_TOKEN = "fake-session-token-XYZXYZXYZ"
CLIENT_SECRET = "fake-client-secret-0123456789"
SECRETS = (ACCESS_KEY, SECRET_KEY, SESSION_TOKEN, CLIENT_SECRET)

RUN_TABLES = """
CREATE TABLE resource_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, resource_type INTEGER NOT NULL,
    location TEXT NOT NULL, count INTEGER NOT NULL, UNIQUE(resource_type, location)
);
CREATE TABLE cost_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, cost REAL NOT NULL,
    currency TEXT NOT NULL, UNIQUE(month)
);
CREATE TABLE egress_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, resource_type INTEGER NOT NULL,
    name TEXT NOT NULL, size_bytes INTEGER, size_unknown INTEGER NOT NULL DEFAULT 0,
    flags TEXT, notes TEXT
);
CREATE TABLE egress_inventory_tier (
    id INTEGER PRIMARY KEY AUTOINCREMENT, egress_inventory_id INTEGER NOT NULL,
    tier TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    is_archive INTEGER NOT NULL DEFAULT 0
);
"""


def egress_row(type_id, name, size=None, flags=(), notes=(), tiers=()):
    return {
        "type_id": type_id,
        "name": name,
        "size": size,
        "flags": list(flags),
        "notes": list(notes),
        "tiers": list(tiers),
    }


def build_run(
    report_path,
    *,
    resources=(),
    costs=(),
    egress=(),
    tfstate_scope=None,
    **catalogue,
):
    report = Path(report_path)
    (report / "data").mkdir(parents=True, exist_ok=True)
    (report / "raw_data").mkdir(parents=True, exist_ok=True)

    db_path = report / "data" / "assessment.db"
    build_catalogue(db_path, **catalogue)
    conn = sqlite3.connect(db_path)
    conn.executescript(RUN_TABLES)
    conn.executemany(
        "INSERT INTO resource_inventory (resource_type, location, count) "
        "VALUES (?, ?, ?)",
        resources,
    )
    conn.executemany(
        "INSERT INTO cost_inventory (month, cost, currency) VALUES (?, ?, ?)", costs
    )
    for row in egress:
        cursor = conn.execute(
            "INSERT INTO egress_inventory "
            "(resource_type, name, size_bytes, size_unknown, flags, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["type_id"],
                row["name"],
                row["size"],
                int(row["size"] is None),
                json.dumps(row["flags"]),
                json.dumps(row["notes"]),
            ),
        )
        conn.executemany(
            "INSERT INTO egress_inventory_tier "
            "(egress_inventory_id, tier, size_bytes, is_archive) VALUES (?, ?, ?, ?)",
            [(cursor.lastrowid, *tier) for tier in row["tiers"]],
        )
    conn.commit()
    conn.close()

    if tfstate_scope is not None:
        manifest = report / "raw_data" / "tfstate_manifest.json"
        manifest.write_text(json.dumps({"scope": tfstate_scope}), encoding="utf-8")
    return str(report)


def request_validator():
    spec = yaml.safe_load((DEFINITION / "openapi.yaml").read_text(encoding="utf-8"))
    root = {
        "$ref": "#/components/schemas/SyncRequest",
        "components": spec["components"],
    }
    return Draft202012Validator(
        root, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def schema_errors(document):
    validator = request_validator()
    return sorted(
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in validator.iter_errors(document)
    )
