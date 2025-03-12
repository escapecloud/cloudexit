import os
import gzip
import shutil
import hashlib
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from requests.exceptions import RequestException, ConnectionError, Timeout

# Constants
DATASET_FOLDER = Path("datasets")
REMOTE_STORAGE_URL = "https://cloudexit-oss-data-eu.fsn1.your-objectstorage.com"

def get_monday_date():
    now = datetime.utcnow()
    monday = now - timedelta(days=now.weekday())

    if now.weekday() == 0 and now.hour < 8:
        last_monday = monday - timedelta(days=7)
        return last_monday.strftime("cloudexit-%Y-%m-%d.db.gz")
    else:
        return monday.strftime("cloudexit-%Y-%m-%d.db.gz")

def compute_file_hash(filepath):
    hash_sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def download_file(url, destination, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            with open(destination, "wb") as f:
                shutil.copyfileobj(response.raw, f)

            print(f"[INFO] Download successful: {destination}")
            return True

        except ConnectionError:
            print(f"[ERROR] Connection failed while downloading {url}. Retrying ({attempt + 1}/{retries})...")
        except Timeout:
            print(f"[ERROR] Request timed out while downloading {url}. Retrying ({attempt + 1}/{retries})...")
        except RequestException as e:
            print(f"[ERROR] Failed to download {url}: {e}")
            break

        time.sleep(delay)

    print(f"[ERROR] Unable to download file after {retries} attempts: {url}")
    return False

def fetch_remote_checksum(checksum_url, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(checksum_url, timeout=10)
            response.raise_for_status()
            return response.text.strip().split()[0]

        except ConnectionError:
            print(f"[ERROR] Connection failed when fetching {checksum_url}. Retrying ({attempt + 1}/{retries})...")
        except Timeout:
            print(f"[ERROR] Request timed out when fetching {checksum_url}. Retrying ({attempt + 1}/{retries})...")
        except RequestException as e:
            print(f"[ERROR] Failed to fetch {checksum_url}: {e}")
            break

        time.sleep(delay)

    print(f"[ERROR] Unable to fetch remote checksum after {retries} attempts.")
    return None

def initialize_dataset():
    DATASET_FOLDER.mkdir(exist_ok=True)

    latest_file = get_monday_date()
    latest_file_url = f"{REMOTE_STORAGE_URL}/{latest_file}"
    latest_checksum_url = f"{REMOTE_STORAGE_URL}/{latest_file}.sha256"
    latest_symlink_file = f"{REMOTE_STORAGE_URL}/cloudexit-latest.db.gz"
    latest_symlink_checksum_url = f"{REMOTE_STORAGE_URL}/cloudexit-latest.db.gz.sha256"

    local_db_path = DATASET_FOLDER / "data.db"
    local_compressed_path = DATASET_FOLDER / latest_file

    # Fetch checksum for the date-based file
    remote_checksum = fetch_remote_checksum(latest_checksum_url)
    if not remote_checksum:
        print(f"[INFO] Unable to fetch remote checksum from {latest_checksum_url}.")
        print(f"[INFO] Trying latest symlink from {latest_symlink_checksum_url}...")
        remote_checksum = fetch_remote_checksum(latest_symlink_checksum_url)
        latest_file_url = latest_symlink_file
        latest_file = "cloudexit-latest.db.gz"
        local_compressed_path = DATASET_FOLDER / latest_file

    if not remote_checksum:
        print("[ERROR] Unable to fetch any remote checksum. Skipping update.")

    else:
        # Check if local compressed file exists
        if local_compressed_path.exists():
            local_checksum = compute_file_hash(local_compressed_path)
            if local_checksum == remote_checksum:
                print("[INFO] Local dataset is up-to-date. No download needed.")
                return
            else:
                print("[INFO] Local dataset is outdated. Removing old files and downloading new dataset...")

                # Remove all old compressed and extracted files
                for file in DATASET_FOLDER.glob("cloudexit-*.db.gz"):
                    os.remove(file)
                if local_db_path.exists():
                    os.remove(local_db_path)

        # Download and extract dataset
        if download_file(latest_file_url, local_compressed_path):
            print(f"[INFO] Download successful. Extracting dataset from {latest_file}...")

            with gzip.open(local_compressed_path, "rb") as f_in, open(local_db_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

            print("[INFO] Dataset updated successfully.")

    if not any(DATASET_FOLDER.iterdir()):
        print("[ERROR] Dataset folder is empty! Cannot proceed without data.")
        exit(1)
