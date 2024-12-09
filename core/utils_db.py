#utils_db.py
import sqlite3
import logging

# Configure logger for database operations
logger = logging.getLogger("core.engine.db")
logger.setLevel(logging.INFO)

# Default master database
MASTER_DATABASE = "datasets/data.db"

def connect(db_path=MASTER_DATABASE):
    try:
        conn = sqlite3.connect(db_path)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to database: {e}")
        raise

def load_data(table_name, db_path=MASTER_DATABASE):
    try:
        conn = connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table_name}")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        return [dict(zip(columns, row)) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"Error loading data from table '{table_name}': {e}")
        raise

def execute_query(query, params=None, db_path=MASTER_DATABASE):
    try:
        conn = connect(db_path)
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        conn.commit()
        rowcount = cursor.rowcount
        conn.close()
        return rowcount
    except sqlite3.Error as e:
        logger.error(f"Error executing query: {e}")
        raise

def fetch_one(query, params=None, db_path=MASTER_DATABASE):
    try:
        conn = connect(db_path)
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        row = cursor.fetchone()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        return dict(zip(columns, row)) if row else None
    except sqlite3.Error as e:
        logger.error(f"Error fetching data: {e}")
        raise

def fetch_all(query, params=None, db_path=MASTER_DATABASE):
    """Fetch all rows matching a query."""
    try:
        conn = connect(db_path)
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"Error fetching data: {e}")
        raise
