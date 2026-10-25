from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


from cloudexit.settings import Settings, SettingsError, normalize_host

CATALOGUE_BASE_URL = "https://cloudexit-oss-data-eu.fsn1.your-objectstorage.com"
SUPPORTED_SCHEMA = 1
REQUIRED_TABLES = ("resourcetype", "risk")
PROVIDER_IDS = {"azure": 1, "aws": 2}
GZIP_MAGIC = b"\x1f\x8b"
# A dataset unpacks to a few times its download size. The limit is that ratio
# of the download (or of the size the manifest announces), never below the
# floor and never above the ceiling.
UNPACK_RATIO = 10
UNPACK_FLOOR = 16 * 1024 * 1024
UNPACK_CEILING = 512 * 1024 * 1024


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
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    # Nothing the file's own SQL defines is trusted to run
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def _check_schema_objects(conn: sqlite3.Connection) -> None:
    # A catalogue holds tables and indexes. A trigger or a view runs SQL of its
    # own on the data a run adds to the same database, so none is accepted.
    found = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type NOT IN ('table', 'index') ORDER BY type, name"
    ).fetchall()
    if found:
        names = ", ".join(f"{kind} {name}" for kind, name in found[:3])
        more = f" and {len(found) - 3} more" if len(found) > 3 else ""
        raise CatalogueError(
            f"the catalogue holds {names}{more}, which a catalogue does not have; "
            "it was not used"
        )


def validate_schema(path: str | Path) -> None:
    # For a database that is about to be written to, or that has been
    try:
        conn = _open_readonly(Path(path))
        try:
            _check_schema_objects(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise CatalogueError(f"not a valid catalogue database: {exc}") from exc


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
            _check_schema_objects(conn)
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


def _is_gzip(data: bytes) -> bool:
    return data[:2] == GZIP_MAGIC


def unpack_limit(download_size: int | None) -> int:
    size = download_size if isinstance(download_size, int) and download_size > 0 else 0
    return min(UNPACK_CEILING, max(UNPACK_FLOOR, UNPACK_RATIO * size))


def _decompress(data: bytes, limit: int | None = None) -> bytes:
    if not _is_gzip(data):
        return data
    limit = unpack_limit(len(data)) if limit is None else limit
    chunks: list[bytes] = []
    total = 0
    rest = data
    # Member by member, so a small archive cannot expand past the limit in memory
    while rest:
        inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
        try:
            part = inflater.decompress(rest, limit + 1 - total)
        except zlib.error as exc:
            raise CatalogueError(
                f"the file is not a valid gzip archive: {exc}"
            ) from exc
        total += len(part)
        if total > limit or inflater.unconsumed_tail:
            raise CatalogueError(
                f"the file unpacks to more than {limit // (1024 * 1024)} MB, "
                "which no catalogue does; it was not installed"
            )
        if not inflater.eof:
            raise CatalogueError(
                "the file is not a valid gzip archive: it ends before the "
                "end-of-stream marker"
            )
        chunks.append(part)
        rest = inflater.unused_data
    return b"".join(chunks)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install_dataset(
    data: bytes,
    directory: Path,
    *,
    sha256: str | None,
    expect_version: str | None = None,
    require_meta: bool = False,
    unpack_size: int | None = None,
) -> CatalogueInfo | None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=directory, prefix="data.db.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_decompress(data, unpack_limit(unpack_size or len(data))))
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


def check_checksum(data: bytes, expected: str, what: str) -> str:
    actual = _sha256(data)
    if actual != expected.strip().lower():
        raise CatalogueError(
            f"checksum mismatch for {what}: expected {expected}, got {actual}"
        )
    return actual


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
        check_checksum(data, sha256, str(path))
    return install_dataset(data, directory or catalogue_dir(), sha256=digest)


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


CATALOGUE_VERSION_PARTS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:\.(\d+))?$")


def parse_catalogue_version(text: str | None) -> tuple[int, int, int, int] | None:
    # A date, and an optional numeric suffix that tells releases of one day apart
    match = CATALOGUE_VERSION_PARTS.match(str(text or "").strip())
    if not match:
        return None
    year, month, day, suffix = match.groups()
    return int(year), int(month), int(day), int(suffix or 0)


def compare_catalogue_versions(local: str | None, other: str | None) -> str | None:
    # "older", "same" or "newer": how the local catalogue stands against the
    # other one; None when either version cannot be read.
    first, second = parse_catalogue_version(local), parse_catalogue_version(other)
    if first is None or second is None:
        return None
    if first == second:
        return "same"
    return "older" if first < second else "newer"
