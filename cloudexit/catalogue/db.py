# cloudexit/catalogue/db.py
import sqlite3
import logging

from cloudexit.catalogue.store import active_catalogue_path

# Configure logger for database operations (level left to the root handlers)
logger = logging.getLogger(__name__)

ALLOWED_TABLES = {
    "resourcetype",
    "resourcetype_data",
    "resource_inventory",
    "egress_inventory",
    "egress_inventory_tier",
    "cost_inventory",
    "risk_inventory",
    "scoring_data",
    "alternative",
    "alternativetechnology",
    "alternativetechnologyorganization",
    "risk",
    "egresspricing",
}


class _Connection(sqlite3.Connection):
    # `with connect(...)` commits or rolls back like any connection, and also
    # closes it: an open handle keeps Windows from removing the report folder.
    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect(db_path=None):
    # No path means the catalogue dataset this run uses
    db_path = db_path or active_catalogue_path()
    try:
        conn = sqlite3.connect(db_path, factory=_Connection)
        return conn
    except sqlite3.Error as e:
        logger.debug(f"Error connecting to database: {e}")
        raise


def load_data(table_name, db_path=None):
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Disallowed table name: {table_name}")
    try:
        with connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table_name}")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    except sqlite3.Error as e:
        logger.debug(f"Error loading data from table '{table_name}': {e}")
        raise


def execute_query(query, params=None, db_path=None):
    try:
        with connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            rowcount = cursor.rowcount
        return rowcount
    except sqlite3.Error as e:
        logger.debug(f"Error executing query: {e}")
        raise


def fetch_one(query, params=None, db_path=None):
    try:
        with connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            row = cursor.fetchone()
            columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row)) if row else None
    except sqlite3.Error as e:
        logger.debug(f"Error fetching data: {e}")
        raise


def fetch_all(query, params=None, db_path=None):
    try:
        with connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    except sqlite3.Error as e:
        logger.debug(f"Error fetching data: {e}")
        raise
