import sqlite3
from pathlib import Path

from tests.report_fixtures import stage_minimal_assets
from tests.sync_fixtures import build_run

FLOW_TABLES = """
DROP TABLE risk;
CREATE TABLE risk (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
    severity TEXT NOT NULL
);
CREATE TABLE risk_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, resource_type INTEGER, risk INTEGER
);
CREATE TABLE scoring_data (
    id INTEGER PRIMARY KEY, exit_score INTEGER, human_score INTEGER,
    technology_score INTEGER, operational_score INTEGER
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
CREATE TABLE alternativetechnologyorganization (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NULL,
    stability_tier INTEGER NULL, country_code TEXT NULL,
    years_in_business INTEGER NULL
);
"""

RISKS = [
    (1, "Few alternatives", "There are few alternatives.", "medium"),
    (2, "No alternatives", "There are no alternatives.", "high"),
    (3, "Few supported alternatives", "Few alternatives have support.", "low"),
    (4, "No supported alternatives", "No alternative has support.", "medium"),
    (5, "Many resources", "A large estate.", "low"),
    (6, "Very many resources", "A very large estate.", "medium"),
    (7, "Many resource types", "Many kinds of resources.", "low"),
    (8, "Very many resource types", "Very many kinds of resources.", "medium"),
]

# With no alternatives, every inventoried resource type carries risks 2 and 4.
LOCAL_RISKS = [(1, 2), (1, 4), (4, 2), (4, 4)]
SERVER_SCORES = (91, 92, 93, 94)


def build_flow_run(report_path, *, platform_scores=False, **kwargs):
    report = build_run(report_path, **kwargs)
    db_path = Path(report) / "data" / "assessment.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(FLOW_TABLES)
    conn.executemany("INSERT INTO risk VALUES (?, ?, ?, ?)", RISKS)
    conn.execute("UPDATE resourcetype SET icon = '/icons/aws/Compute/EC2.png'")
    if platform_scores:
        # What an earlier version stored after an upload: it must never be rendered
        conn.execute(
            "INSERT INTO scoring_data (exit_score, human_score, technology_score, "
            "operational_score) VALUES (?, ?, ?, ?)",
            SERVER_SCORES,
        )
    conn.commit()
    conn.close()
    stage_minimal_assets(report)
    return report
