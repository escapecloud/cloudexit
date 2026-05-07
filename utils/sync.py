# utils/sync.py
from __future__ import annotations

import logging
import requests
import config
from typing import Optional, Dict, Any
from utils.auth import get_jwt_token

logger = logging.getLogger("main.utils.sync")

_ASSESS_PATH = "/api/v1/assessments/"


def _build_url(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith("http"):
        host = f"https://{host}"
    return f"{host}{_ASSESS_PATH}"


def submit_assessment(
    payload: Dict[str, Any],
    *,
    host: str | None = None,
    key: str | None = None,
    timeout: int = 10,
) -> Optional[requests.Response]:
    host = host or getattr(config, "HOST", "") if config else ""
    if not host:
        logger.warning("HOST not configured – skipping assessment sync.")
        return None

    token = get_jwt_token(host=host, key=key) if key else get_jwt_token(host=host)
    if not token:
        logger.warning("Could not obtain JWT – skipping assessment sync.")
        return None

    url = _build_url(host)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        logger.info("POST %s – status %s", url, resp.status_code)
        return resp
    except requests.RequestException as exc:
        logger.error("Assessment POST failed: %s", exc)
        return None
