import gzip
import sqlite3
from pathlib import Path

from tests.make_smoke_dataset import main as make_smoke_dataset

VERSION = "2026-11-02"
RISK_CODES = {1: "LOCK-001", 2: "LOCK-002"}
CATALOGUE_SEVERITIES = {1: "low", 2: "critical"}


def _edit(path, statements):
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _with_codes(path):
    _edit(
        path,
        [
            "ALTER TABLE risk ADD COLUMN code TEXT",
            "ALTER TABLE risk ADD COLUMN catalogue_severity TEXT",
            *[
                f"UPDATE risk SET code = '{RISK_CODES[i]}', "
                f"catalogue_severity = '{CATALOGUE_SEVERITIES[i]}' WHERE id = {i}"
                for i in RISK_CODES
            ],
        ],
    )


def build_shape(directory, shape):
    # A generated dataset of one shape; returns the path to hand to --catalogue
    path = Path(directory) / f"{shape}.db"
    make_smoke_dataset(str(path))
    if shape == "legacy":
        _edit(path, ["DROP TABLE catalogue_meta"])
    elif shape in ("current", "future", "refused", "two_meta_rows", "truncated"):
        _with_codes(path)
    if shape == "future":
        _edit(
            path,
            [
                "CREATE TABLE a_future_table (id INTEGER PRIMARY KEY, note TEXT)",
                "INSERT INTO a_future_table (note) VALUES ('ignored')",
                "ALTER TABLE risk ADD COLUMN a_future_note TEXT",
                "ALTER TABLE resourcetype ADD COLUMN a_future_flag INTEGER",
                "ALTER TABLE catalogue_meta ADD COLUMN a_second_future_column TEXT",
            ],
        )
    elif shape == "refused":
        _edit(path, ["UPDATE catalogue_meta SET dataset_schema = 2"])
    elif shape == "two_meta_rows":
        _edit(path, ["INSERT INTO catalogue_meta SELECT * FROM catalogue_meta"])
    elif shape == "truncated":
        packed = path.with_suffix(".db.gz")
        data = gzip.compress(path.read_bytes())
        packed.write_bytes(data[: len(data) // 2])
        return packed
    elif shape == "not_sqlite":
        path.write_bytes(b"this is not a sqlite database")
    return path
