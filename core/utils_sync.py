# core/utils_sync.py
from __future__ import annotations

import json
import logging
import os
import time
import config
import requests
from typing import Any

from core.utils_db import load_data

# Configure logger
logger = logging.getLogger("core.engine.sync")
logger.setLevel(logging.INFO)

_ASSESS_PATH = "/api/v1/assessments/"


def _assess_url(host: str) -> str:
    host = host.strip().rstrip("/")
    if host.startswith("http://"):
        host = "https://" + host[len("http://") :]
    elif not host.startswith("https://"):
        host = f"https://{host}"
    return f"{host}{_ASSESS_PATH}"


def _build_payload(
    *,
    report_path: str,
    name: str,
    started_at: int,
    exit_strategy: int,
    cloud_service_provider: int,
    assessment_type: int,
) -> dict[str, Any]:
    db_path = os.path.join(report_path, "data", "assessment.db")

    resource_rows: list[dict[str, Any]] = load_data(
        "resource_inventory", db_path=db_path
    )
    cost_rows: list[dict[str, Any]] = load_data("cost_inventory", db_path=db_path)

    res_payload = [
        {
            "id": int(r["resource_type"]),
            "location": r.get("location") or "unknown",
            "count": int(r.get("count", 0)),
        }
        for r in resource_rows
    ]
    cost_payload = [
        {
            "month": c["month"],
            "cost": float(c["cost"]),
            "currency": c["currency"],
        }
        for c in cost_rows
    ]

    engine_version = getattr(config, "CLI_VERSION", "v1.0.0").strip()
    now = int(time.time())

    payload: dict[str, Any] = {
        "id": os.urandom(16).hex(),
        "object": "event",
        "cli_version": engine_version,
        "created": now,
        "type": "local.assessment.succeeded",
        "data": {
            "name": name,
            "exit_strategy": exit_strategy,
            "cloud_service_provider": cloud_service_provider,
            "assessmentType": assessment_type,
            "started_at": started_at,
            "completed_at": now,
            "success": True,
            "resource_inventory": res_payload,
            "cost_inventory": cost_payload,
        },
    }

    logger.debug("Outgoing payload:\n%s", json.dumps(payload, indent=2))
    return payload


def post_assessment(
    *,
    name: str,
    started_at: int,
    report_path: str,
    meta: dict[str, int],
    token: str,
    timeout: int = 10,
) -> dict[str, Any]:
    host = getattr(config, "HOST", "").strip()
    if not host:
        return {"success": False, "payload": None, "logs": "HOST missing in config.py"}

    url = _assess_url(host)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = _build_payload(
        report_path=report_path,
        started_at=started_at,
        name=name,
        exit_strategy=meta["exit_strategy"],
        cloud_service_provider=meta["cloud_service_provider"],
        assessment_type=meta["assessment_type"],
    )

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        ok = resp.ok
        return {
            "success": ok,
            "payload": resp.json() if ok else None,
            "logs": f"server responded {resp.status_code}",
        }
    except requests.RequestException as exc:
        return {"success": False, "payload": None, "logs": f"POST failed: {exc}"}
