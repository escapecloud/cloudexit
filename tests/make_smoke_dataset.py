import sqlite3
import sys

from tests.support.catalogue import build_catalogue

RESOURCE_TYPES = [
    (
        1,
        2,
        "AWS.EC2.DescribeInstances",
        "EC2 Instance",
        "/icons/aws/Compute/EC2.png",
        "aws_instance",
    ),
    (
        2,
        2,
        "AWS.S3.ListBuckets",
        "S3 Bucket",
        "/icons/aws/Storage/S3-on-Outposts.png",
        "aws_s3_bucket",
    ),
    (
        3,
        2,
        "AWS.Lambda.ListFunctions",
        "Lambda Function",
        "/icons/aws/Compute/Lambda.png",
        "aws_lambda_function",
    ),
    (
        4,
        2,
        "AWS.DynamoDB.ListTables",
        "DynamoDB Table",
        "/icons/aws/Database/DynamoDB.png",
        "aws_dynamodb_table",
    ),
]

TABLES = """
DROP TABLE risk;
CREATE TABLE risk (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
    severity TEXT NOT NULL
);
CREATE TABLE risk_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, resource_type INTEGER, risk INTEGER
);
CREATE TABLE alternative (
    id INTEGER PRIMARY KEY, resource_type INTEGER NOT NULL,
    alternative_technology INTEGER NOT NULL, strategy_type INTEGER NOT NULL
);
CREATE TABLE alternativetechnology (
    id INTEGER PRIMARY KEY, product_name TEXT NOT NULL, product_description TEXT,
    product_url TEXT, open_source TEXT NOT NULL, support_plan TEXT NOT NULL,
    status TEXT NOT NULL, organization_id INTEGER NULL
);
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
CREATE TABLE alternativetechnologyorganization (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NULL,
    stability_tier INTEGER NULL, country_code TEXT NULL,
    years_in_business INTEGER NULL
);
"""

RISKS = [
    (1, "Few alternatives", "There are few alternatives.", "medium"),
    (2, "No alternatives", "There are no alternatives.", "high"),
]


def main(path):
    build_catalogue(path, version="2026-11-02")
    conn = sqlite3.connect(path)
    conn.executescript(TABLES)
    conn.execute("DELETE FROM resourcetype")
    conn.executemany(
        "INSERT INTO resourcetype (id, csp, code, name, icon, status, tf_code) "
        "VALUES (?, ?, ?, ?, ?, 't', ?)",
        RESOURCE_TYPES,
    )
    conn.executemany("INSERT INTO risk VALUES (?, ?, ?, ?)", RISKS)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1])
