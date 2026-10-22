# utils/connection.py
from __future__ import annotations

import logging
import requests

from utils.settings import Settings, load_settings, normalize_host

logger = logging.getLogger("main.utils.connection")

_AUTH_PATH = "/api/v1/auth/token/"


def _build_url(host: str) -> str:
    return f"{normalize_host(host)}{_AUTH_PATH}"


def get_jwt_token(
    host: str | None = None,
    key: str | None = None,
    *,
    timeout: float = 10,
    verify: str | bool = True,
) -> str | None:
    if not host:
        logger.debug("Host empty – skipping ExitCloud authentication.")
        return None
    if not key:
        logger.debug("API key empty – skipping ExitCloud authentication.")
        return None

    url = _build_url(host)
    headers = {"Authorization": f"Bearer {key}"}

    try:
        resp = requests.post(url, headers=headers, timeout=timeout, verify=verify)
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
        logger.error(
            "EscapeCloud authentication request failed: %s", exc, exc_info=True
        )
    except ValueError:
        logger.error(
            "EscapeCloud authentication response was not valid JSON.", exc_info=True
        )

    return None


def resolve_mode(settings: Settings | None = None) -> tuple[str, str | None]:
    settings = settings or load_settings()
    host = settings.host.value
    key = settings.api_key.value

    if not host:
        logger.debug("Host empty – running in offline mode.")
        return "offline", None
    if not key:
        logger.debug("API key empty – running in offline mode.")
        return "offline", None

    token = get_jwt_token(
        host=host,
        key=key,
        timeout=settings.timeout.value,
        verify=settings.ca_bundle.value or True,
    )
    if token:
        return "online", token

    logger.debug("ExitCloud auth failed – falling back to offline mode.")
    return "offline", None
