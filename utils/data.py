from __future__ import annotations

import atexit
import gzip
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import requests

from utils.settings import Settings, SettingsError, normalize_host

logger = logging.getLogger("main.utils.data")

CATALOGUE_BASE_URL = "https://cloudexit-oss-data-eu.fsn1.your-objectstorage.com"
MANIFEST_NAME = "cloudexit-manifest.json"
LATEST_NAME = "cloudexit-latest.db.gz"
SUPPORTED_SCHEMA = 1
MANIFEST_TIMEOUT = 10
DOWNLOAD_TIMEOUT = 30
REQUIRED_TABLES = ("resourcetype", "risk")
LEGACY_DATABASE = Path("datasets") / "data.db"
MISSING_STATUSES = (403, 404, 410)
PROVIDER_IDS = {"azure": 1, "aws": 2}
GZIP_MAGIC = b"\x1f\x8b"


class CatalogueError(Exception):
    pass


class CatalogueUnavailable(CatalogueError):
    pass


@dataclass(frozen=True)
class CatalogueInfo:
    version: str
    sequence: int | None = None
    sha256: str | None = None
    dataset_schema: int | None = None
    exported_at: str | None = None
    exporter_version: str | None = None


@dataclass(frozen=True)
class Installed:
    path: Path
    info: CatalogueInfo | None
    state: dict[str, Any]

    @property
    def version(self) -> str | None:
        return self.info.version if self.info else None

    @property
    def date(self) -> str:
        raw = self.state.get("downloaded_at") or (
            self.info.exported_at if self.info else None
        )
        if raw:
            return str(raw)[:10]
        modified = datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc)
        return modified.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class UpdateResult:
    status: str
    info: CatalogueInfo | None


@dataclass(frozen=True)
class EnsureResult:
    path: Path
    status: str
    version: str | None
    date: str
    reason: str | None = None
    migrated: bool = False


def catalogue_dir(
    environ: Mapping[str, str] | None = None, *, platform: str = sys.platform
) -> Path:
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        xdg = environ.get("XDG_DATA_HOME", "")
        base = (
            xdg if xdg and os.path.isabs(xdg) else str(Path.home() / ".local" / "share")
        )
    return Path(base) / "cloudexit" / "catalogue"


_active_override: Path | None = None


def set_active_catalogue(path: str | Path | None) -> None:
    global _active_override
    _active_override = Path(path) if path else None


def active_catalogue_path() -> Path:
    return _active_override or catalogue_dir() / "data.db"


def catalogue_base_url(settings: Settings) -> str:
    value = settings.catalogue_url.value
    if not value:
        return CATALOGUE_BASE_URL
    try:
        return normalize_host(value)
    except SettingsError as exc:
        raise SettingsError(
            f"catalogue URL ({settings.catalogue_url.source}): {exc}"
        ) from exc


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def _read_meta(conn: sqlite3.Connection, tables: set[str]) -> CatalogueInfo | None:
    if "catalogue_meta" not in tables:
        return None

    cursor = conn.execute("SELECT * FROM catalogue_meta")
    columns = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise CatalogueError(
            f"catalogue_meta must have exactly one row, found {len(rows)}"
        )

    row = dict(zip(columns, rows[0]))
    if not row.get("catalogue_version"):
        raise CatalogueError("catalogue_meta has no catalogue_version")
    schema = row.get("dataset_schema")
    if isinstance(schema, int) and schema > SUPPORTED_SCHEMA:
        raise CatalogueError(
            f"catalogue uses dataset schema {schema}; this cloudexit supports "
            f"up to {SUPPORTED_SCHEMA}. Update cloudexit."
        )
    return CatalogueInfo(
        version=str(row["catalogue_version"]),
        sequence=row.get("catalogue_sequence"),
        sha256=row.get("catalogue_sha256"),
        dataset_schema=schema,
        exported_at=row.get("exported_at"),
        exporter_version=row.get("exporter_version"),
    )


def catalogue_info(db_path: str | Path | None = None) -> CatalogueInfo | None:
    path = Path(db_path) if db_path else active_catalogue_path()
    try:
        conn = _open_readonly(path)
        try:
            return _read_meta(conn, _table_names(conn))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise CatalogueError(f"cannot read catalogue {path}: {exc}") from exc


def active_resource_type_ids(
    provider: str | int, db_path: str | Path | None = None
) -> list[int]:
    if isinstance(provider, str):
        try:
            csp = PROVIDER_IDS[provider.lower()]
        except KeyError:
            raise ValueError(f"unknown cloud provider: {provider!r}") from None
    else:
        csp = int(provider)

    path = Path(db_path) if db_path else active_catalogue_path()
    conn = _open_readonly(path)
    try:
        rows = conn.execute(
            "SELECT id FROM resourcetype WHERE csp = ? AND status = 't' ORDER BY id",
            (csp,),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def validate_database(
    path: str | Path,
    *,
    expect_version: str | None = None,
    require_meta: bool = False,
) -> CatalogueInfo | None:
    try:
        conn = _open_readonly(Path(path))
        try:
            tables = _table_names(conn)
            missing = [name for name in REQUIRED_TABLES if name not in tables]
            if missing:
                raise CatalogueError(
                    f"not a catalogue database: missing table(s) {', '.join(missing)}"
                )
            info = _read_meta(conn, tables)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise CatalogueError(f"not a valid catalogue database: {exc}") from exc

    if require_meta and info is None:
        raise CatalogueError("the catalogue has no catalogue_meta table")
    if expect_version is not None and info and info.version != expect_version:
        raise CatalogueError(
            f"catalogue version {info.version} does not match the expected "
            f"{expect_version}"
        )
    return info


def read_state(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(directory: Path, state: dict[str, Any]) -> None:
    temp = directory / "state.json.tmp"
    temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, directory / "state.json")


def installed_catalogue(directory: Path | None = None) -> Installed | None:
    directory = directory or catalogue_dir()
    path = directory / "data.db"
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        info = catalogue_info(path)
    except CatalogueError:
        info = None
    return Installed(path, info, read_state(directory))


def migrate_legacy_dataset(
    directory: Path | None = None, legacy: Path | None = None
) -> bool:
    directory = directory or catalogue_dir()
    legacy = legacy or LEGACY_DATABASE
    if installed_catalogue(directory) or not legacy.is_file():
        return False
    try:
        info = validate_database(legacy)
    except CatalogueError:
        return False

    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, directory / "data.db")
    modified = datetime.fromtimestamp(legacy.stat().st_mtime, timezone.utc)
    _write_state(
        directory,
        {
            "version": info.version if info else None,
            "sha256": None,
            "downloaded_at": modified.isoformat(timespec="seconds"),
        },
    )
    return True


def _is_gzip(data: bytes) -> bool:
    return data[:2] == GZIP_MAGIC


def _decompress(data: bytes) -> bytes:
    if not _is_gzip(data):
        return data
    try:
        return gzip.decompress(data)
    except (OSError, EOFError, zlib.error) as exc:
        raise CatalogueError(f"the file is not a valid gzip archive: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _install(
    data: bytes,
    directory: Path,
    *,
    sha256: str | None,
    expect_version: str | None = None,
    require_meta: bool = False,
) -> CatalogueInfo | None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=directory, prefix="data.db.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_decompress(data))
        info = validate_database(
            temp, expect_version=expect_version, require_meta=require_meta
        )

        current = directory / "data.db"
        if current.exists():
            shutil.copy2(current, directory / "data.db.previous")
        os.replace(temp, current)
        _write_state(
            directory,
            {
                "version": info.version if info else None,
                "sha256": sha256,
                "downloaded_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            },
        )
        return info
    finally:
        temp.unlink(missing_ok=True)


def _get(url: str, *, timeout: float, verify: str | bool) -> requests.Response:
    try:
        return requests.get(url, timeout=timeout, verify=verify)
    except requests.RequestException as exc:
        logger.debug("Catalogue request to %s failed: %s", url, exc)
        reason = str(exc) if len(str(exc)) <= 80 else type(exc).__name__
        raise CatalogueError(
            f"cannot reach the catalogue server at {urlsplit(url).netloc} ({reason})"
        ) from exc


def _download(url: str, *, verify: str | bool) -> bytes:
    response = _get(url, timeout=DOWNLOAD_TIMEOUT, verify=verify)
    if not response.ok:
        raise CatalogueError(
            f"download of {url} failed with HTTP {response.status_code}"
        )
    return response.content


def _check_checksum(data: bytes, expected: str, what: str) -> str:
    actual = _sha256(data)
    if actual != expected.strip().lower():
        raise CatalogueError(
            f"checksum mismatch for {what}: expected {expected}, got {actual}"
        )
    return actual


def _check_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise CatalogueError("the manifest is not a JSON object")
    schema = manifest.get("dataset_schema")
    if isinstance(schema, int) and schema > SUPPORTED_SCHEMA:
        raise CatalogueError(
            f"the catalogue uses dataset schema {schema}; this cloudexit supports "
            f"up to {SUPPORTED_SCHEMA}. Update cloudexit."
        )
    latest = manifest.get("latest")
    if not isinstance(latest, dict) or not all(
        isinstance(latest.get(key), str)
        for key in ("catalogue_version", "file", "file_sha256")
    ):
        raise CatalogueError("the manifest has no usable 'latest' entry")
    return manifest


def fetch_manifest(manifest_url: str, *, verify: str | bool = True) -> dict | None:
    response = _get(manifest_url, timeout=MANIFEST_TIMEOUT, verify=verify)
    if response.status_code in MISSING_STATUSES:
        return None
    if not response.ok:
        raise CatalogueError(
            f"manifest request failed with HTTP {response.status_code}"
        )
    try:
        return _check_manifest(response.json())
    except ValueError as exc:
        raise CatalogueError("the manifest is not valid JSON") from exc


def resolve_file_url(manifest_url: str, file: str) -> str:
    url = urljoin(manifest_url, file)
    base, target = urlsplit(manifest_url), urlsplit(url)
    folder = base.path.rsplit("/", 1)[0] + "/"
    if (
        (target.scheme, target.netloc) != (base.scheme, base.netloc)
        or not target.path.startswith(folder)
        or ".." in target.path.split("/")
    ):
        raise CatalogueError(
            f"the manifest file {file!r} points outside the catalogue location"
        )
    return url


def _select_entry(manifest: dict[str, Any], version: str | None) -> dict[str, Any]:
    if version is None:
        return manifest["latest"]
    entries = [manifest["latest"], *manifest.get("previous", [])]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("catalogue_version") == version:
            return entry
    available = sorted(
        str(entry["catalogue_version"])
        for entry in entries
        if isinstance(entry, dict) and "catalogue_version" in entry
    )
    raise CatalogueError(
        f"version {version} is not in the manifest; available: {', '.join(available)}"
    )


def _update_from_latest(
    base_url: str, directory: Path, *, force: bool, verify: str | bool
) -> UpdateResult:
    base = base_url.rstrip("/")
    response = _get(
        f"{base}/{LATEST_NAME}.sha256", timeout=MANIFEST_TIMEOUT, verify=verify
    )
    if response.status_code in MISSING_STATUSES:
        raise CatalogueError(f"no catalogue manifest or latest file found at {base}")
    if not response.ok or not response.text.strip():
        raise CatalogueError(
            f"checksum request failed with HTTP {response.status_code}"
        )
    expected = response.text.split()[0].lower()

    installed = installed_catalogue(directory)
    if not force and installed and installed.state.get("sha256") == expected:
        return UpdateResult("current", installed.info)

    data = _download(f"{base}/{LATEST_NAME}", verify=verify)
    digest = _check_checksum(data, expected, LATEST_NAME)
    return UpdateResult("updated", _install(data, directory, sha256=digest))


def update_catalogue(
    base_url: str,
    *,
    directory: Path | None = None,
    version: str | None = None,
    force: bool = False,
    verify: str | bool = True,
) -> UpdateResult:
    directory = directory or catalogue_dir()
    manifest_url = f"{base_url.rstrip('/')}/{MANIFEST_NAME}"
    manifest = fetch_manifest(manifest_url, verify=verify)

    if manifest is None:
        if version is not None:
            raise CatalogueError(
                "no manifest is published here, so a specific version cannot be chosen"
            )
        return _update_from_latest(base_url, directory, force=force, verify=verify)

    entry = _select_entry(manifest, version)
    target = entry["catalogue_version"]
    installed = installed_catalogue(directory)
    if not force and installed and installed.version == target:
        return UpdateResult("current", installed.info)

    data = _download(resolve_file_url(manifest_url, entry["file"]), verify=verify)
    digest = _check_checksum(data, entry["file_sha256"], entry["file"])
    info = _install(
        data, directory, sha256=digest, expect_version=target, require_meta=True
    )
    return UpdateResult("updated", info)


def import_catalogue(
    file: str | Path, *, directory: Path | None = None, sha256: str | None = None
) -> CatalogueInfo | None:
    path = Path(file)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CatalogueError(f"cannot read {path}: {exc}") from exc
    digest = _sha256(data)
    if sha256 is not None:
        _check_checksum(data, sha256, str(path))
    return _install(data, directory or catalogue_dir(), sha256=digest)


def use_catalogue_file(file: str | Path) -> tuple[Path, CatalogueInfo | None]:
    path = Path(file)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CatalogueError(f"cannot read {path}: {exc}") from exc

    if _is_gzip(data):
        temp_dir = tempfile.mkdtemp(prefix="cloudexit-catalogue-")
        atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
        path = Path(temp_dir) / "data.db"
        path.write_bytes(_decompress(data))
    return path, validate_database(path)


def ensure_catalogue(
    base_url: str, *, directory: Path | None = None, verify: str | bool = True
) -> EnsureResult:
    directory = directory or catalogue_dir()
    migrated = migrate_legacy_dataset(directory)
    try:
        result = update_catalogue(base_url, directory=directory, verify=verify)
    except (CatalogueError, OSError) as exc:
        installed = installed_catalogue(directory)
        if installed is None:
            raise CatalogueUnavailable(
                f"no catalogue is installed and the update failed: {exc}"
            ) from exc
        return EnsureResult(
            installed.path,
            "failed",
            installed.version,
            installed.date,
            str(exc),
            migrated,
        )

    installed = installed_catalogue(directory)
    return EnsureResult(
        installed.path,
        result.status,
        installed.version,
        installed.date,
        None,
        migrated,
    )


def _age(date_text: str) -> str:
    try:
        days = (
            datetime.now(timezone.utc).date()
            - datetime.strptime(date_text[:10], "%Y-%m-%d").date()
        ).days
    except ValueError:
        return "age unknown"
    if days <= 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"


def _describe(info: CatalogueInfo | None) -> str:
    if info is None:
        return "unknown (the dataset has no catalogue_meta)"
    if info.sequence is None:
        return info.version
    return f"{info.version} (sequence {info.sequence})"


def status_lines(
    base_url: str, *, directory: Path | None = None, verify: str | bool = True
) -> list[str]:
    directory = directory or catalogue_dir()
    installed = installed_catalogue(directory)
    if installed is None:
        lines = [f"installed: none ({directory})"]
    else:
        lines = [
            f"installed: {_describe(installed.info)}",
            f"updated:   {installed.date} ({_age(installed.date)})",
            f"location:  {installed.path}",
        ]

    try:
        manifest = fetch_manifest(
            f"{base_url.rstrip('/')}/{MANIFEST_NAME}", verify=verify
        )
    except CatalogueError as exc:
        lines.append(f"latest:    unavailable ({exc})")
        return lines

    if manifest is None:
        lines.append("latest:    unknown (no manifest is published)")
        return lines

    latest = manifest["latest"]
    sequence = latest.get("catalogue_sequence")
    suffix = f" (sequence {sequence})" if sequence is not None else ""
    lines.append(f"latest:    {latest['catalogue_version']}{suffix}")
    if installed is None or installed.version != latest["catalogue_version"]:
        lines.append("an update is available: run `cloudexit catalogue update`")
    return lines
