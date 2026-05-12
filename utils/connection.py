# utils/connection.py
from __future__ import annotations

import logging
import requests

logger = logging.getLogger("main.utils.connection")

try:
    import config
except ModuleNotFoundError:
    config = None

_AUTH_PATH = "/api/v1/auth/token/"


def _build_url(host: str) -> str:
    host = host.strip().rstrip("/")
    if host.startswith("http://"):
        host = "https://" + host[len("http://") :]
    elif not host.startswith("https://"):
        host = f"https://{host}"
    return f"{host}{_AUTH_PATH}"


def get_jwt_token(
    host: str | None = None, key: str | None = None, *, timeout: int = 10
) -> str | None:
    host = host or getattr(config, "HOST", "") if config else ""
    key = key or getattr(config, "KEY", "") if config else ""

    if not host:
        logger.debug("HOST empty – skipping ExitCloud authentication.")
        return None
    if not key:
        logger.debug("KEY empty – skipping ExitCloud authentication.")
        return None

    url = _build_url(host)
    headers = {"Authorization": f"Bearer {key}"}

    try:
        resp = requests.post(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        token = (
            data.get("access_token")
            or data.get("token")
            or data.get("access")
            or data.get("jwt")
        )
        if token:
            return token

        logger.error(
            "Authentication succeeded but token field missing in response: %s", data
        )
    except requests.RequestException as exc:
        logger.error("EscapeCloud authentication request failed: %s", exc)
    except ValueError:
        logger.error("EscapeCloud authentication response was not valid JSON.")

    return None


def resolve_mode() -> tuple[str, str | None]:
    host = getattr(config, "HOST", "") if config else ""
    key = getattr(config, "KEY", "") if config else ""

    if not host:
        logger.debug("HOST empty – running in offline mode.")
        return "offline", None
    if not key:
        logger.debug("KEY empty – running in offline mode.")
        return "offline", None

    token = get_jwt_token(host=host, key=key)
    if token:
        return "online", token

    logger.debug("ExitCloud auth failed – falling back to offline mode.")
    return "offline", None
