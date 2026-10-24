from __future__ import annotations

from typing import Any

from utils import codes
from utils.data import compare_catalogue_versions

from .upload import UploadResult


def upload_status(result: UploadResult | None) -> str:
    if result is None or not result.attempted:
        return "skipped"
    if result.success:
        return "uploaded"
    return "spooled" if result.spooled else "failed"


def catalogue_outdated(
    local_version: str | None, result: UploadResult | None
) -> bool | None:
    # True when the platform says so, or its catalogue is newer than the one
    # used; None when nothing tells.
    if result is None or not result.success:
        return None
    codes_seen = {item.get("code") for item in result.warning_items}
    if "collector_catalogue_outdated" in codes_seen:
        return True
    host = ((result.catalogue or {}).get("host") or {}).get("version")
    standing = compare_catalogue_versions(local_version, host)
    if standing is None:
        return None
    return standing == "older"


def upload_json(
    result: UploadResult | None, skipped_reason: str | None
) -> dict[str, Any]:
    status = upload_status(result)
    attempted = result is not None and result.attempted
    error = None
    if attempted and not result.success:
        error = {
            "code": result.error_code or result.reason,
            "message": result.message,
            "request_id": result.request_id,
        }
    return {
        "status": status,
        "reason": skipped_reason if status == "skipped" else None,
        "assessment_id": result.assessment_id if attempted else None,
        "sync_id": result.sync_id if attempted else None,
        "platform_sync_id": result.platform_sync_id if attempted else None,
        "url": result.url if attempted else None,
        "warnings": [dict(item) for item in result.warning_items] if attempted else [],
        "error": error,
    }


def build_run_json(
    *,
    exit_code: int,
    report_dir: str | None,
    sync_json: str | None,
    upload: UploadResult | None,
    skipped_reason: str | None,
    catalogue_version: str | None,
) -> dict[str, Any]:
    delivered = upload if upload is not None and upload.success else None
    return {
        "exit_code": int(exit_code),
        "report_dir": report_dir,
        "sync_json": sync_json,
        "upload": upload_json(upload, skipped_reason),
        # What the platform returned, for CI checks; the report never shows it
        "results": delivered.results if delivered else None,
        "catalogue": {
            "version": catalogue_version,
            "outdated": catalogue_outdated(catalogue_version, upload),
        },
    }


def exit_code_of(value: Any) -> int:
    if value is None:
        return codes.SUCCESS
    if isinstance(value, int):
        return value
    return codes.UNEXPECTED
