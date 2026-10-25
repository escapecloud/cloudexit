# cloudexit/catalogue/download.py
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import requests

from cloudexit.catalogue.store import (
    SUPPORTED_SCHEMA,
    CatalogueError,
    CatalogueInfo,
    CatalogueUnavailable,
    EnsureResult,
    UpdateResult,
    catalogue_dir,
    check_checksum,
    install_dataset,
    installed_catalogue,
)

logger = logging.getLogger(__name__)


MANIFEST_NAME = "cloudexit-manifest.json"


LATEST_NAME = "cloudexit-latest.db.gz"


MANIFEST_TIMEOUT = 10


DOWNLOAD_TIMEOUT = 30


MISSING_STATUSES = (403, 404, 410)


class _AnonymousSession(requests.Session):
    # Catalogue requests carry no credentials. Requests would otherwise attach a
    # ~/.netrc login for the host, and for the host of a redirect.
    def __init__(self) -> None:
        super().__init__()
        self.auth = lambda request: request

    def rebuild_auth(self, prepared_request: Any, response: Any) -> None:
        prepared_request.headers.pop("Authorization", None)


def _get(url: str, *, timeout: float, verify: str | bool) -> requests.Response:
    try:
        with _AnonymousSession() as session:
            return session.get(url, timeout=timeout, verify=verify)
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
    # Percent-encoded and backslash forms count as the same climb
    parts = unquote(target.path).replace("\\", "/").split("/")
    if (
        (target.scheme, target.netloc) != (base.scheme, base.netloc)
        or not target.path.startswith(folder)
        or ".." in parts
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
    digest = check_checksum(data, expected, LATEST_NAME)
    return UpdateResult("updated", install_dataset(data, directory, sha256=digest))


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
    digest = check_checksum(data, entry["file_sha256"], entry["file"])
    info = install_dataset(
        data,
        directory,
        sha256=digest,
        expect_version=target,
        require_meta=True,
        unpack_size=entry.get("size_bytes"),
    )
    return UpdateResult("updated", info)


def ensure_catalogue(
    base_url: str, *, directory: Path | None = None, verify: str | bool = True
) -> EnsureResult:
    directory = directory or catalogue_dir()
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
        )

    installed = installed_catalogue(directory)
    return EnsureResult(
        installed.path,
        result.status,
        installed.version,
        installed.date,
        None,
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
