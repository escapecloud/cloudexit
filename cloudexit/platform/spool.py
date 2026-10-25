from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from cloudexit import codes
from cloudexit.settings import normalize_host

from cloudexit.platform.client import ApiClient, ApiError

logger = logging.getLogger(__name__)

DEFAULT_PORTS = {"https": 443, "http": 80}
DEFAULT_MAX_AGE_DAYS = 30
AUTO_FLUSH_BUDGET = 60.0
META_SUFFIX = ".meta.json"
SYNC_ID = re.compile(r"^[0-9A-Za-z-]{8,64}$")
AGE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# A failure that every other entry would share: stop instead of trying each
STOP_EXIT_CODES = (
    codes.UPLOAD_AUTH,
    codes.UPLOAD_UNAVAILABLE,
    codes.UPLOAD_QUOTA,
    codes.UPLOAD_UNSUPPORTED,
)


def host_id(host: str) -> str:
    # A hash of the normalised host, so the host name is not in the path
    parts = urlsplit(normalize_host(host))
    netloc = (parts.hostname or "").lower()
    if parts.port and parts.port != DEFAULT_PORTS.get(parts.scheme):
        netloc = f"{netloc}:{parts.port}"
    return hashlib.sha256(f"{parts.scheme}://{netloc}".encode("utf-8")).hexdigest()[:16]


def key_fingerprint(key: str | None) -> str | None:
    # Which API key spooled a sync, without keeping the key: a hash, never the key
    if not key:
        return None
    return hashlib.sha256(b"cloudexit spool key\0" + key.encode("utf-8")).hexdigest()[
        :16
    ]


def state_dir(
    environ: Mapping[str, str] | None = None, *, platform: str = sys.platform
) -> Path:
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "cloudexit" / "state"
    xdg = environ.get("XDG_STATE_HOME", "")
    base = xdg if xdg and os.path.isabs(xdg) else str(Path.home() / ".local" / "state")
    return Path(base) / "cloudexit"


def spool_root(settings: Any = None, environ: Mapping[str, str] | None = None) -> Path:
    configured = getattr(getattr(settings, "spool_dir", None), "value", None)
    if configured:
        return Path(configured)
    return state_dir(environ) / "spool"


def parse_age(text: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*", str(text).lower())
    if not match:
        raise ValueError(f"{text!r} is not an age such as 30d, 12h or 90m")
    return float(match.group(1)) * AGE_UNITS[match.group(2)]


def format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


@dataclass
class SpoolEntry:
    sync_id: str
    host_id: str
    document_path: Path
    meta_path: Path
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def created(self) -> float:
        return float(self.meta.get("created") or 0)

    @property
    def key_id(self) -> str | None:
        value = self.meta.get("key_id")
        return value if isinstance(value, str) and value else None

    def belongs_to(self, key_id: str | None) -> bool:
        # An entry without a recorded key cannot be matched to any key
        return key_id is not None and self.key_id == key_id


@dataclass
class FlushResult:
    uploaded: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    stopped: str | None = None
    # Spooled with another API key: left where they are
    other_key: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.uploaded
            or self.pending
            or self.rejected
            or self.failed
            or self.other_key
        )


class Spool:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Any = None, **options: Any) -> Spool:
        return cls(spool_root(settings), **options)

    def _directory(self, host: str) -> Path:
        return self.root / host

    def _write(self, path: Path, text: str) -> None:
        # Owner only, and never a half-written file under the final name
        temporary = path.with_name(f".{path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _ensure(self, directory: Path) -> None:
        for path in (self.root, directory):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def add(
        self,
        host: str,
        document: Mapping[str, Any],
        *,
        error: ApiError | None,
        attempts: int,
        name: str | None,
        exit_strategy: int | None,
        assessment_id: str | None,
        key_id: str | None = None,
    ) -> SpoolEntry:
        sync_id = ((document.get("sync") or {}).get("id")) or ""
        if not isinstance(sync_id, str) or not SYNC_ID.match(sync_id):
            raise ValueError("the sync document has no usable sync id")
        hid = host_id(host)
        directory = self._directory(hid)
        self._ensure(directory)
        document_path = directory / f"{sync_id}.json"
        meta_path = directory / f"{sync_id}{META_SUFFIX}"

        now = int(self._clock())
        previous = _read_json(meta_path)
        meta = {
            "sync_id": sync_id,
            "host_id": hid,
            "created": previous.get("created", now),
            "first_attempt": previous.get("first_attempt", now),
            "last_attempt": now,
            "attempts": int(previous.get("attempts", 0)) + max(0, attempts),
            "last_error": error.code if error else None,
            "request_id": error.request_id if error else None,
            "name": name,
            "exit_strategy": exit_strategy,
            "assessment_id": assessment_id,
            "key_id": key_id,
        }
        # The document only: never the key, never a response
        self._write(
            document_path,
            json.dumps(document, separators=(",", ":"), ensure_ascii=False),
        )
        self._write(meta_path, json.dumps(meta, indent=2) + "\n")
        return SpoolEntry(sync_id, hid, document_path, meta_path, meta)

    def entries(self, host: str | None = None) -> list[SpoolEntry]:
        # Oldest first. host is a host id; None lists every host.
        found = []
        if not self.root.is_dir():
            return found
        directories = (
            [self._directory(host)]
            if host
            else sorted(p for p in self.root.iterdir() if p.is_dir())
        )
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                if path.name.endswith(META_SUFFIX) or path.name.startswith("."):
                    continue
                sync_id = path.stem
                meta_path = directory / f"{sync_id}{META_SUFFIX}"
                meta = _read_json(meta_path)
                if not meta:
                    # A document whose meta file was lost still gets delivered
                    meta = {"sync_id": sync_id, "created": int(path.stat().st_mtime)}
                meta.setdefault("host_id", directory.name)
                found.append(SpoolEntry(sync_id, directory.name, path, meta_path, meta))
        return sorted(found, key=lambda entry: (entry.created, entry.sync_id))

    def read(self, entry: SpoolEntry) -> dict[str, Any]:
        return json.loads(entry.document_path.read_text(encoding="utf-8"))

    def update(
        self, entry: SpoolEntry, *, error: ApiError | None, attempts: int
    ) -> None:
        entry.meta.update(
            {
                "last_attempt": int(self._clock()),
                "attempts": int(entry.meta.get("attempts", 0)) + max(0, attempts),
                "last_error": error.code if error else None,
                "request_id": error.request_id if error else None,
            }
        )
        entry.meta.setdefault("first_attempt", entry.meta["last_attempt"])
        self._write(entry.meta_path, json.dumps(entry.meta, indent=2) + "\n")

    def remove(self, entry: SpoolEntry) -> None:
        for path in (entry.document_path, entry.meta_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            entry.document_path.parent.rmdir()
        except OSError:
            pass

    def find(self, host: str, sync_id: str) -> SpoolEntry | None:
        return next(
            (e for e in self.entries(host_id(host)) if e.sync_id == sync_id), None
        )

    def purge(
        self, *, older_than: float | None = None, host: str | None = None
    ) -> list[SpoolEntry]:
        removed = []
        now = self._clock()
        for entry in self.entries(host):
            if older_than is not None and now - entry.created < older_than:
                continue
            self.remove(entry)
            removed.append(entry)
        return removed


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def max_collection_age_days(client: ApiClient) -> int:
    try:
        limits = client.meta().get("limits") or {}
    except ApiError:
        return DEFAULT_MAX_AGE_DAYS
    value = limits.get("max_collection_age_days")
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok else DEFAULT_MAX_AGE_DAYS


def too_old(document: Mapping[str, Any], days: int, now: float) -> bool:
    completed_at = (document.get("sync") or {}).get("completed_at")
    return isinstance(completed_at, int) and now - completed_at > days * 86400


def flush(
    settings: Any,
    spool: Spool,
    *,
    host: str | None = None,
    budget: float | None = None,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    on_delivered: Callable[[SpoolEntry, Any], None] | None = None,
    **client_options: Any,
) -> FlushResult:
    # Deliver what an earlier run could not, oldest first. budget is the time
    # for the whole flush; without it every entry gets the upload budget.
    host = host or settings.host.value
    result = FlushResult()
    key_id = key_fingerprint(settings.api_key.value)
    found = spool.entries(host_id(host))
    # A sync goes out only with the key it was spooled with
    entries = [entry for entry in found if entry.belongs_to(key_id)]
    result.other_key = [entry.sync_id for entry in found if entry not in entries]
    if not entries:
        return result

    client = ApiClient.from_settings(
        settings, clock=clock, monotonic=monotonic, **client_options
    )
    days = max_collection_age_days(client)
    deadline = None if budget is None else monotonic() + budget

    for index, entry in enumerate(entries):
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                result.pending += [e.sync_id for e in entries[index:]]
                result.stopped = "budget"
                break
            client.budget = remaining
        try:
            document = spool.read(entry)
        except (OSError, ValueError):
            result.failed[entry.sync_id] = "unreadable"
            continue
        if too_old(document, days, clock()):
            result.rejected.append(entry.sync_id)
            continue
        try:
            pushed = client.push(
                document,
                name=entry.meta.get("name") or f"Exit Assessment {entry.sync_id[:8]}",
                exit_strategy=entry.meta.get("exit_strategy"),
                assessment_id=entry.meta.get("assessment_id"),
            )
        except ApiError as error:
            spool.update(entry, error=error, attempts=error.attempts)
            if error.retryable and error.exit_code == codes.UPLOAD_UNAVAILABLE:
                result.pending += [e.sync_id for e in entries[index:]]
                result.stopped = error.code
                break
            result.failed[entry.sync_id] = error.code
            if error.exit_code in STOP_EXIT_CODES:
                result.pending += [e.sync_id for e in entries[index + 1 :]]
                result.stopped = error.code
                break
            continue
        spool.remove(entry)
        result.uploaded.append(entry.sync_id)
        if on_delivered:
            on_delivered(entry, pushed)
    return result
