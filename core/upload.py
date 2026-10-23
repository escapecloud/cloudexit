from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils import codes
from utils.settings import Settings, load_settings

from .utils_sync import post_assessment

logger = logging.getLogger("core.engine.upload")

REASON_UNAVAILABLE = "upload_unavailable"
REASON_REJECTED = "upload_rejected"


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

    @property
    def failed(self) -> bool:
        return self.attempted and not self.success


def failure(reason: str, message: str, sync_id: str | None = None) -> UploadResult:
    exit_code = (
        codes.UPLOAD_REJECTED if reason == REASON_REJECTED else codes.UPLOAD_UNAVAILABLE
    )
    return UploadResult(True, False, exit_code, reason, message, sync_id)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _link(value: Any) -> str | None:
    url = _text(value)
    return url if url and url.startswith(("https://", "http://")) else None


def upload(
    document: dict[str, Any] | None,
    settings: Settings | None,
    *,
    token: str,
    name: str,
    report_path: str,
    started_at: int,
    meta: dict[str, int],
) -> UploadResult:
    # Transitional: the existing upload still builds its own request from the run
    # data. The sync document is only used for its id until the v2 client
    # replaces the inside of this function.
    sync_id = ((document or {}).get("sync") or {}).get("id")
    settings = settings or load_settings()

    try:
        result = post_assessment(
            name=name,
            started_at=started_at,
            report_path=report_path,
            meta=meta,
            token=token,
            host=settings.host.value,
            timeout=settings.timeout.value,
            verify=settings.ca_bundle.value or True,
        )
    except Exception as exc:
        # Whatever goes wrong in the upload must not cost the local report
        logger.debug("The upload raised %s", type(exc).__name__, exc_info=True)
        return failure(
            REASON_UNAVAILABLE,
            f"unexpected upload error ({type(exc).__name__})",
            sync_id,
        )

    if result.get("success"):
        data = (result.get("payload") or {}).get("data") or {}
        return UploadResult(
            True,
            True,
            message="uploaded",
            sync_id=sync_id,
            assessment_id=_text(data.get("assessment_id")),
            url=_link(data.get("url")),
        )

    status = result.get("status")
    rejected = isinstance(status, int) and 400 <= status < 500 and status != 429
    return failure(
        REASON_REJECTED if rejected else REASON_UNAVAILABLE,
        str(result.get("logs") or "the upload failed"),
        sync_id,
    )


def report_info(result: UploadResult | None) -> dict[str, str] | None:
    if result is None or not result.attempted:
        return None
    info = {
        "status": "uploaded" if result.success else "failed",
        "reason": result.reason,
        "sync_id": result.sync_id,
        "assessment_id": result.assessment_id,
        "url": result.url,
    }
    return {key: value for key, value in info.items() if value}


def write_upload_record(raw_data_path: str, result: UploadResult) -> Path | None:
    info = report_info(result)
    if info is None:
        return None
    info["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = Path(raw_data_path) / "upload.json"
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return path
