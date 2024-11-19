# utils.py
from datetime import datetime
import os

ascii_art = r"""
      _                 _           _ _
     | |               | |         (_) |
  ___| | ___  _   _  __| | _____  ___| |_
 / __| |/ _ \| | | |/ _` |/ _ \ \/ / | __|
| (__| | (_) | |_| | (_| |  __/>  <| | |_
 \___|_|\___/ \__,_|\__,_|\___/_/\_\_|\__|


"""

def create_directory(base_path="reports"):
    # Generate the main directory with a timestamp
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    directory_path = os.path.join(base_path, timestamp)

    # Create the main directory
    os.makedirs(directory_path, exist_ok=True)

    # Create the raw_data subdirectory within the main directory
    raw_data_path = os.path.join(directory_path, "raw_data")
    os.makedirs(raw_data_path, exist_ok=True)

    return directory_path, raw_data_path
