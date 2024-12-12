#utils.py
import os
import shutil
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger("core.engine.utils")

def copy_assets(report_path):
    assets_folders = ["css", "img", "icons"]
    assets_path = os.path.join(report_path, "assets")

    # Create the 'assets' directory if it doesn't exist
    os.makedirs(assets_path, exist_ok=True)

    for folder in assets_folders:
        src_path = os.path.join("assets", folder)
        dest_path = os.path.join(assets_path, folder)

        # Only copy if the destination doesn't already exist
        if not os.path.exists(dest_path):
            shutil.copytree(src_path, dest_path, dirs_exist_ok=True)

    # Copy datasets/data.db to data/assessment.db
    db_src_path = "datasets/data.db"
    db_dest_dir = os.path.join(report_path, "data")
    db_dest_path = os.path.join(db_dest_dir, "assessment.db")

    # Create the 'data' directory if it doesn't exist
    os.makedirs(db_dest_dir, exist_ok=True)

    # Only copy if the destination doesn't already exist
    if not os.path.exists(db_dest_path):
        shutil.copyfile(db_src_path, db_dest_path)
