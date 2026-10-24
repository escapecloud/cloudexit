from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils import codes
from utils.settings import Settings, load_settings

from .api_v2 import ApiClient, ApiError

logger = logging.getLogger("core.engine.upload")

REASON_UNAVAILABLE = "upload_unavailable"
REASON_REJECTED = "upload_rejected"
REASONS = {
    codes.UPLOAD_REJECTED: REASON_REJECTED,
    codes.UPLOAD_AUTH: "upload_auth",
    codes.UPLOAD_UNAVAILABLE: REASON_UNAVAILABLE,
    codes.UPLOAD_QUOTA: "upload_quota",
    codes.UPLOAD_TARGET: "upload_target",
    codes.UPLOAD_UNSUPPORTED: "upload_unsupported",
}


@dataclass(frozen=True)
class UploadResult:
    attempted: bool
    success: bool
    exit_code: int | None = None
    reason: str | None = None
    message: str = ""
    sync_id: str | None = None
    assessment_id: str | None = None
    url: str | None = None
    platform_sync_id: str | None = None
    error_code: str | None = None
    request_id: str | None = None
    warnings: tuple[str, ...] = ()
    replayed: bool = False
    pending: bool = False

    @property
    def failed(self) -> bool:
        return self.attempted and not self.success


def failure(
    reason: str,
    message: str,
    sync_id: str | None = None,
    *,
    exit_code: int | None = None,
    error_code: str | None = None,
    request_id: str | None = None,
) -> UploadResult:
    if exit_code is None:
        exit_code = (
            codes.UPLOAD_REJECTED
            if reason == REASON_REJECTED
            else codes.UPLOAD_UNAVAILABLE
        )
    return UploadResult(
        True,
        False,
        exit_code,
        reason,
        message,
        sync_id,
        error_code=error_code,
        request_id=request_id,
    )


def error_failure(error: ApiError, sync_id: str | None = None) -> UploadResult:
    return failure(
        REASONS.get(error.exit_code, REASON_UNAVAILABLE),
        error.message,
        sync_id,
        exit_code=error.exit_code,
        error_code=error.code,
        request_id=error.request_id,
    )


def upload(
    document: dict[str, Any] | None,
    settings: Settings | None,
    *,
    name: str,
    meta: dict[str, int],
) -> UploadResult:
    sync_id = ((document or {}).get("sync") or {}).get("id")
    if document is None:
        return failure(
            REASON_REJECTED,
            "the sync document could not be built, so there is nothing to upload",
            sync_id,
        )
    settings = settings or load_settings()

    try:
        client = ApiClient.from_settings(settings)
        pushed = client.push(
            document,
            name=name,
            exit_strategy=meta["exit_strategy"],
            assessment_id=settings.assessment_id.value,
        )
    except ApiError as error:
        logger.debug("The upload failed: %s", error.code)
        return error_failure(error, sync_id)
    except Exception as exc:
        # Whatever goes wrong in the upload must not cost the local report
        logger.debug("The upload raised %s", type(exc).__name__, exc_info=True)
        return failure(
            REASON_UNAVAILABLE,
            f"unexpected upload error ({type(exc).__name__})",
            sync_id,
        )

    if pushed.pending:
        message = "accepted, still processing"
    elif pushed.replayed:
        message = "already uploaded"
    else:
        message = "uploaded"
    return UploadResult(
        True,
        True,
        message=message,
        sync_id=sync_id,
        assessment_id=pushed.assessment_id,
        url=pushed.url,
        platform_sync_id=pushed.sync_id,
        request_id=pushed.request_id,
        warnings=pushed.warnings,
        replayed=pushed.replayed,
        pending=pushed.pending,
    )


REPORT_KEYS = ("status", "reason", "sync_id", "assessment_id", "url")


def _record(result: UploadResult | None) -> dict[str, Any] | None:
    if result is None or not result.attempted:
        return None
    info = {
        "status": "uploaded" if result.success else "failed",
        "reason": result.reason,
        "error_code": result.error_code,
        "sync_id": result.sync_id,
        "platform_sync_id": result.platform_sync_id,
        "assessment_id": result.assessment_id,
        "url": result.url,
        "request_id": result.request_id,
        "pending": result.pending,
        "warnings": list(result.warnings),
    }
    return {key: value for key, value in info.items() if value}


def report_info(result: UploadResult | None) -> dict[str, str] | None:
    # What the local report may show: the status and the link, nothing the
    # platform computed.
    record = _record(result)
    if record is None:
        return None
    return {key: record[key] for key in REPORT_KEYS if key in record}


def write_upload_record(raw_data_path: str, result: UploadResult) -> Path | None:
    info = _record(result)
    if info is None:
        return None
    info["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = Path(raw_data_path) / "upload.json"
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return path
