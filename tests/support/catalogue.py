import gzip
import hashlib
import sqlite3
import tempfile
from pathlib import Path

RESOURCE_TYPES = [
    (1, 2, "AWS.EC2.DescribeInstances", "EC2 Instance", "/i/ec2.png", "t"),
    (2, 2, "AWS.S3.ListBuckets", "S3 Bucket", "/i/s3.png", "f"),
    (3, 1, "Azure.Compute.VirtualMachines", "Virtual Machine", "/i/vm.png", "t"),
    (4, 2, "AWS.RDS.DescribeDBInstances", "RDS Instance", "/i/rds.png", "t"),
    (5, 1, "Azure.Storage.Accounts", "Storage Account", "/i/sa.png", "f"),
]


def build_catalogue(
    path,
    *,
    version="2026-11-02",
    sequence=41,
    meta=True,
    meta_rows=1,
    schema=1,
    core_tables=True,
    extras=False,
):
    conn = sqlite3.connect(path)
    if core_tables:
        conn.executescript("""
            CREATE TABLE resourcetype (
                id INTEGER PRIMARY KEY, csp INTEGER NOT NULL, code TEXT NOT NULL,
                name TEXT NOT NULL, icon TEXT NOT NULL, status TEXT NOT NULL,
                tf_code TEXT
            );
            CREATE TABLE risk (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            """)
        conn.executemany(
            "INSERT INTO resourcetype (id, csp, code, name, icon, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            RESOURCE_TYPES,
        )
        conn.execute("INSERT INTO risk (id, name) VALUES (1, 'Example risk')")
    if meta:
        conn.execute("""
            CREATE TABLE catalogue_meta (
                catalogue_version TEXT, catalogue_sequence INTEGER,
                catalogue_sha256 TEXT, dataset_schema INTEGER, exported_at TEXT,
                exporter_version TEXT, a_future_column TEXT
            )
            """)
        for _ in range(meta_rows):
            conn.execute(
                "INSERT INTO catalogue_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    version,
                    sequence,
                    "c" * 64,
                    schema,
                    f"{version[:10]}T05:00:00Z",
                    "1.0.0",
                    "ignored",
                ),
            )
    if extras:
        conn.execute("CREATE TABLE a_future_table (id INTEGER, note TEXT)")
    conn.commit()
    conn.close()


def catalogue_bytes(**kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "build.db"
        build_catalogue(path, **kwargs)
        return path.read_bytes()


def catalogue_gz(**kwargs):
    return gzip.compress(catalogue_bytes(**kwargs))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def manifest_entry(version, gz, *, sequence=41, file=None):
    return {
        "catalogue_version": version,
        "catalogue_sequence": sequence,
        "catalogue_sha256": "c" * 64,
        "file": file or f"releases/cloudexit-{version}.db.gz",
        "file_sha256": sha256(gz),
        "size_bytes": len(gz),
        "published_at": f"{version}T05:10:00Z",
    }


def manifest(latest, previous=(), schema=1):
    return {"dataset_schema": schema, "latest": latest, "previous": list(previous)}
