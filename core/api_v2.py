from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

import requests

from utils import codes
from utils.settings import KEY_PREFIXES, Setting, SettingsError, normalize_host
from utils.version import __version__

from .sync_document import (
    MAX_BODY_BYTES,
    MAX_CATALOGUE_TYPE_IDS,
    MAX_COST_ITEMS,
    MAX_COUNT_PER_ITEM,
    MAX_EGRESS_ITEMS,
    MAX_FLAGS,
    MAX_LOCATION_CHARS,
    MAX_NAME_CHARS,
    MAX_NOTES,
    MAX_RESOURCE_ITEMS,
    MAX_TIERS,
)

logger = logging.getLogger("core.engine.api_v2")

API_PATH = "/api/v2"
CLIENT_NAME = "cloudexit"

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
PREFER_WAIT = 20
META_TTL_SECONDS = 300
POLL_DEFAULT_SECONDS = 2.0
POLL_MAX_SECONDS = 30.0
PENDING_STATUSES = ("accepted", "processing")

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
KEY_BODY = re.compile(r"[0-9A-Za-z]{54}")
CHECKSUM_CHARS = 6
# What a platform must say about itself in /meta for a key of each kind. An
# EscapeCloud key works on any host name, address or port and in any region.
KEY_PLATFORMS = {
    "esc_sk_": ("escapecloud", None),
    "exc_eu_sk_": ("exitcloud", "eu"),
    "exc_us_sk_": ("exitcloud", "us"),
}
PRODUCT_NAMES = {"escapecloud": "EscapeCloud", "exitcloud": "ExitCloud"}

EXIT_STRATEGIES = {1: "repatriation", 2: "hybrid_cloud", 3: "alternate_cloud"}

DEFAULT_LIMITS = {
    "max_body_bytes": MAX_BODY_BYTES,
    "max_resource_items": MAX_RESOURCE_ITEMS,
    "max_count_per_item": MAX_COUNT_PER_ITEM,
    "max_location_chars": MAX_LOCATION_CHARS,
    "max_cost_items": MAX_COST_ITEMS,
    "max_egress_items": MAX_EGRESS_ITEMS,
    "max_name_chars": MAX_NAME_CHARS,
    "max_egress_tiers_per_item": MAX_TIERS,
    "max_egress_flags_per_item": MAX_FLAGS,
    "max_egress_notes_per_item": MAX_NOTES,
    "max_catalogue_type_ids": MAX_CATALOGUE_TYPE_IDS,
    "clock_skew_seconds": 86400,
    "max_collection_age_days": 30,
}

# Written by hand from docs/api-v2/errors.yaml; a test compares the two.
ERROR_EXIT_CODES = {
    "unauthenticated": codes.UPLOAD_AUTH,
    "invalid_api_key": codes.UPLOAD_AUTH,
    "api_key_expired": codes.UPLOAD_AUTH,
    "api_key_revoked": codes.UPLOAD_AUTH,
    "wrong_region": codes.UPLOAD_AUTH,
    "principal_inactive": codes.UPLOAD_AUTH,
    "insufficient_scope": codes.UPLOAD_AUTH,
    "key_binding_violation": codes.UPLOAD_AUTH,
    "assessment_key_v1": codes.UPLOAD_AUTH,
    "idempotency_key_missing": codes.UPLOAD_REJECTED,
    "idempotency_key_mismatch": codes.UPLOAD_REJECTED,
    "idempotency_key_reused": codes.UPLOAD_REJECTED,
    "request_in_progress": codes.UPLOAD_UNAVAILABLE,
    "invalid_json": codes.UPLOAD_REJECTED,
    "unsupported_media_type": codes.UPLOAD_REJECTED,
    "payload_too_large": codes.UPLOAD_REJECTED,
    "limit_exceeded": codes.UPLOAD_REJECTED,
    "validation_failed": codes.UPLOAD_REJECTED,
    "catalogue_mismatch": codes.UPLOAD_REJECTED,
    "catalogue_sha256_mismatch": codes.UPLOAD_REJECTED,
    "unsupported_schema_version": codes.UPLOAD_UNSUPPORTED,
    "client_version_unsupported": codes.UPLOAD_UNSUPPORTED,
    "assessment_not_found": codes.UPLOAD_TARGET,
    "sync_not_found": codes.UPLOAD_TARGET,
    "assessment_deleted": codes.UPLOAD_TARGET,
    "assessment_archived": codes.UPLOAD_TARGET,
    "target_mismatch": codes.UPLOAD_TARGET,
    "insufficient_credits": codes.UPLOAD_QUOTA,
    "quota_exceeded": codes.UPLOAD_QUOTA,
    "rate_limited": codes.UPLOAD_UNAVAILABLE,
    "maintenance": codes.UPLOAD_UNAVAILABLE,
    "service_unavailable": codes.UPLOAD_UNAVAILABLE,
    "internal_error": codes.UPLOAD_UNAVAILABLE,
}


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: int,
        *,
        status: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.status = status
        self.request_id = request_id
        self.retryable = retryable


@dataclass(frozen=True)
class KeyInfo:
    prefix: str
    product: str
    region: str | None


@dataclass(frozen=True)
class PushResult:
    status: str
    sync_id: str | None
    assessment_id: str | None
    assessment_name: str | None
    assessment_created: bool
    url: str | None
    replayed: bool
    pending: bool
    warnings: tuple[str, ...]
    request_id: str | None
    billing: dict[str, int] | None = None


def exit_code_for(code: str | None, status: int | None, retryable: bool) -> int:
    if code in ERROR_EXIT_CODES:
        return ERROR_EXIT_CODES[code]
    # An unknown code is judged by the kind of HTTP status and the retryable flag
    if retryable or (status is not None and (status >= 500 or status < 400)):
        return codes.UPLOAD_UNAVAILABLE
    if status in (401, 403):
        return codes.UPLOAD_AUTH
    if status == 402:
        return codes.UPLOAD_QUOTA
    if status in (404, 410):
        return codes.UPLOAD_TARGET
    return codes.UPLOAD_REJECTED


def checksum(text: str) -> str:
    value = zlib.crc32(text.encode("ascii"))
    digits = []
    for _ in range(CHECKSUM_CHARS):
        value, digit = divmod(value, 62)
        digits.append(BASE62[digit])
    return "".join(reversed(digits))


def _base_url(host: str) -> str:
    try:
        return normalize_host(host)
    except SettingsError as exc:
        raise ApiError("invalid_host", str(exc), codes.UPLOAD_UNAVAILABLE) from None


def check_key(key: str | None) -> KeyInfo:
    # Local only: a typo is caught before anything is sent, and the key never
    # appears in a message.
    prefix = next((p for p in KEY_PREFIXES if key and key.startswith(p)), None)
    if not prefix:
        raise ApiError(
            "invalid_api_key",
            "the API key does not start with " + ", ".join(KEY_PREFIXES),
            codes.UPLOAD_AUTH,
        )
    body = key[len(prefix) :]
    if (
        not KEY_BODY.fullmatch(body)
        or checksum(key[:-CHECKSUM_CHARS]) != key[-CHECKSUM_CHARS:]
    ):
        raise ApiError(
            "invalid_api_key",
            "the API key is not valid (check it for typos)",
            codes.UPLOAD_AUTH,
        )
    product, region = KEY_PLATFORMS[prefix]
    return KeyInfo(prefix, product, region)


def _describe(product: str, region: str | None) -> str:
    name = PRODUCT_NAMES.get(product, product)
    return f"{name} {region.upper()}" if region else name


def check_platform(key: KeyInfo, meta: Mapping[str, Any]) -> None:
    # Catches a configuration mistake (a key for another product or region),
    # not a hostile host: the trust anchor is HTTPS to the host the user
    # configured. A platform that does not say what it is is left to decide.
    host = meta.get("host")
    if not isinstance(host, dict):
        return
    product = host.get("product")
    if product not in PRODUCT_NAMES:
        return
    region = host.get("region")
    region = region.lower() if isinstance(region, str) and region else None
    if product != key.product or (key.region and region and region != key.region):
        raise ApiError(
            "wrong_region",
            f"This is an {_describe(key.product, key.region)} key, but the "
            f"platform reports {_describe(product, region)}.",
            codes.UPLOAD_AUTH,
        )


def resolve_ca_bundle(
    configured: str | None, environ: Mapping[str, str] | None = None
) -> str | bool:
    environ = os.environ if environ is None else environ
    if configured:
        return str(configured)
    for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        value = environ.get(name, "").strip()
        if value:
            return value
    return True


def request_timeout(setting: Setting | None) -> tuple[float, float]:
    if setting is not None and setting.source != "default" and setting.value:
        return float(setting.value), float(setting.value)
    return CONNECT_TIMEOUT, READ_TIMEOUT


def detect_ci(environ: Mapping[str, str]) -> str:
    if environ.get("GITHUB_ACTIONS") == "true":
        return "github"
    if environ.get("GITLAB_CI"):
        return "gitlab"
    if environ.get("TF_BUILD"):
        return "azure"
    return "none"


def user_agent(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    return f"{CLIENT_NAME}/{__version__} (python/{python}; {system}; ci={detect_ci(environ)})"


def cache_dir(
    environ: Mapping[str, str] | None = None, *, platform: str = sys.platform
) -> Path:
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "cloudexit" / "cache"
    xdg = environ.get("XDG_CACHE_HOME", "")
    base = xdg if xdg and os.path.isabs(xdg) else str(Path.home() / ".cache")
    return Path(base) / "cloudexit"


def _version(text: Any) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", str(text).split("+")[0].split("-")[0])[:3]
    return (
        tuple(int(part) for part in parts) + (0,) * (3 - len(parts)) if parts else None
    )


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())


def _billing(value: Any) -> dict[str, int] | None:
    # What the platform charged for this sync, on hosts that charge
    if not isinstance(value, dict):
        return None
    found = {
        key: value[key]
        for key in ("charged_credits", "balance")
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
    }
    return found or None


def _warning_lines(items: Any) -> list[str]:
    lines = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("message"):
            code = item.get("code")
            line = _one_line(item["message"])
            lines.append(f"{line} ({code})" if code else line)
    return lines


def limit_problems(
    document: dict[str, Any], limits: Mapping[str, Any], *, size: int, now: float
) -> list[str]:
    def limit(name: str) -> int:
        value = limits.get(name)
        ok = isinstance(value, int) and not isinstance(value, bool)
        return value if ok else DEFAULT_LIMITS[name]

    problems: list[str] = []

    def over(name: str, actual: int, what: str) -> None:
        if actual > limit(name):
            problems.append(f"{what} is {actual}, above the limit of {limit(name)}")

    def longest(items: list[Any], field: str) -> int:
        return max((len(item.get(field) or []) for item in items), default=0)

    inventory = document.get("inventory") or {}
    resources = inventory.get("resources") or []
    egress_items = (inventory.get("egress") or {}).get("items") or []
    sync = document.get("sync") or {}
    claim = (sync.get("collector") or {}).get("catalogue") or {}
    create = (document.get("target") or {}).get("create") or {}

    over("max_body_bytes", size, "the request body (bytes)")
    over("max_resource_items", len(resources), "the number of resource items")
    over(
        "max_count_per_item",
        max((item.get("count") or 0 for item in resources), default=0),
        "the largest resource count",
    )
    over(
        "max_location_chars",
        max((len(str(item.get("location"))) for item in resources), default=0),
        "the longest resource location",
    )
    over("max_cost_items", len(inventory.get("costs") or []), "the number of costs")
    over("max_egress_items", len(egress_items), "the number of egress items")
    over("max_egress_flags_per_item", longest(egress_items, "flags"), "egress flags")
    over("max_egress_notes_per_item", longest(egress_items, "notes"), "egress notes")
    over("max_egress_tiers_per_item", longest(egress_items, "tiers"), "egress tiers")
    over(
        "max_catalogue_type_ids",
        len(claim.get("resource_type_ids") or []),
        "the number of catalogue type ids",
    )
    for field in ("name", "group"):
        over("max_name_chars", len(create.get(field) or ""), f"the assessment {field}")

    completed_at = sync.get("completed_at")
    if isinstance(completed_at, int):
        max_age = limit("max_collection_age_days") * 86400
        if now - completed_at > max_age:
            problems.append(
                f"the collection is older than {limit('max_collection_age_days')} days"
            )
        if completed_at - now > limit("clock_skew_seconds"):
            problems.append("the collection time is ahead of the clock")
    return problems


class ApiClient:
    def __init__(
        self,
        host: str,
        api_key: str,
        *,
        ca_bundle: str | None = None,
        timeout: tuple[float, float] | None = None,
        environ: Mapping[str, str] | None = None,
        cache_path: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        environ = os.environ if environ is None else environ
        self.base_url = _base_url(host)
        self._key_info = check_key(api_key)
        self.verify = resolve_ca_bundle(ca_bundle, environ)
        if isinstance(self.verify, str) and not os.path.exists(self.verify):
            raise ApiError(
                "tls_error",
                f"the CA bundle {self.verify} does not exist",
                codes.UPLOAD_UNAVAILABLE,
            )
        self.timeout = timeout or (CONNECT_TIMEOUT, READ_TIMEOUT)
        self._key = api_key
        self._cache_dir = cache_path or cache_dir(environ)
        self._sleep = sleep
        self._clock = clock
        self._monotonic = monotonic
        self._meta: dict[str, Any] | None = None
        self._platform_verified = False

        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent(environ)
        # A truthy auth stops requests from replacing the Bearer header with a
        # ~/.netrc login for the host.
        self.session.auth = lambda request: request

    @classmethod
    def from_settings(cls, settings: Any, **options: Any) -> ApiClient:
        host, key = settings.host.value, settings.api_key.value
        if not host or not key:
            raise ApiError(
                "not_configured",
                "no platform host or API key is configured",
                codes.UPLOAD_AUTH,
            )
        return cls(
            host,
            key,
            ca_bundle=settings.ca_bundle.value,
            timeout=request_timeout(settings.timeout),
            **options,
        )

    def _error(self, response: requests.Response, request_id: str) -> ApiError:
        status = response.status_code
        request_id = response.headers.get("X-Request-ID") or request_id
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = error["code"]
            message = _one_line(error.get("message") or code)
            retryable = bool(error.get("retryable"))
            request_id = error.get("request_id") or request_id
            for detail in error.get("details") or []:
                if isinstance(detail, dict) and detail.get("expected_host"):
                    message += f" (expected host: {detail['expected_host']})"
        else:
            code, message, retryable = f"http_{status}", f"HTTP status {status}", False
        message = message.replace(self._key, "[api key]")
        return ApiError(
            code,
            message,
            exit_code_for(code, status, retryable),
            status=status,
            request_id=request_id,
            retryable=retryable,
        )

    def _call(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...],
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        auth: bool = True,
    ) -> tuple[dict[str, Any], requests.Response]:
        if auth and not self._platform_verified:
            # The key is only sent to a platform that is what the key is for
            self.meta()
        request_id = uuid.uuid4().hex
        send = {"Accept": "application/json", "X-Request-ID": request_id}
        send.update(headers or {})
        if auth:
            send["Authorization"] = f"Bearer {self._key}"
        logger.debug(
            "%s %s request_id=%s body_bytes=%d",
            method,
            path,
            request_id,
            len(body or b""),
        )
        host = urlsplit(self.base_url).hostname
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{API_PATH}{path}",
                data=body,
                headers=send,
                timeout=self.timeout,
                verify=self.verify,
                allow_redirects=False,
            )
        except requests.exceptions.SSLError as exc:
            logger.debug("TLS failure: %s", type(exc).__name__)
            raise ApiError(
                "tls_error",
                f"could not verify the certificate of {host}; for a private CA "
                "use --ca-bundle or CLOUDEXIT_CA_BUNDLE",
                codes.UPLOAD_UNAVAILABLE,
                request_id=request_id,
            ) from None
        except requests.exceptions.Timeout as exc:
            logger.debug("Timeout: %s", type(exc).__name__)
            raise ApiError(
                "timeout",
                f"{host} did not answer in time",
                codes.UPLOAD_UNAVAILABLE,
                request_id=request_id,
                retryable=True,
            ) from None
        except (requests.exceptions.RequestException, OSError) as exc:
            logger.debug("Request failure: %s", type(exc).__name__)
            raise ApiError(
                "connection_failed",
                f"could not reach {host} ({type(exc).__name__})",
                codes.UPLOAD_UNAVAILABLE,
                request_id=request_id,
                retryable=True,
            ) from None

        logger.debug(
            "%s %s -> %d request_id=%s",
            method,
            path,
            response.status_code,
            response.headers.get("X-Request-ID") or request_id,
        )
        if response.status_code not in expected:
            raise self._error(response, request_id)
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ApiError(
                "invalid_response",
                "the platform answered with something that is not a JSON object",
                codes.UPLOAD_UNAVAILABLE,
                status=response.status_code,
                request_id=response.headers.get("X-Request-ID") or request_id,
            )
        return data, response

    def _read_meta_cache(self) -> dict[str, Any] | None:
        try:
            saved = json.loads(self._meta_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(saved, dict) or saved.get("base_url") != self.base_url:
            return None
        fetched_at = saved.get("fetched_at")
        meta = saved.get("meta")
        if not isinstance(fetched_at, (int, float)) or not isinstance(meta, dict):
            return None
        if not 0 <= self._clock() - fetched_at < META_TTL_SECONDS:
            return None
        return meta

    def _write_meta_cache(self, meta: dict[str, Any]) -> None:
        path = self._meta_file()
        saved = {
            "base_url": self.base_url,
            "fetched_at": int(self._clock()),
            "meta": meta,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            logger.debug("The meta cache could not be written", exc_info=True)

    def _meta_file(self) -> Path:
        digest = hashlib.sha256(self.base_url.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"meta-{digest}.json"

    def meta(self) -> dict[str, Any]:
        if self._meta is None:
            self._meta = self._read_meta_cache()
        if self._meta is None:
            data, _ = self._call("GET", "/meta", expected=(200,), auth=False)
            if data.get("object") != "meta":
                raise ApiError(
                    "invalid_response",
                    "the platform did not answer with its capabilities",
                    codes.UPLOAD_UNAVAILABLE,
                )
            self._meta = data
            self._write_meta_cache(data)
        if not self._platform_verified:
            check_platform(self._key_info, self._meta)
            self._platform_verified = True
        return self._meta

    def whoami(self) -> dict[str, Any]:
        data, _ = self._call("GET", "/whoami", expected=(200,))
        return data

    def _check_meta(
        self, meta: Mapping[str, Any], document: Mapping[str, Any]
    ) -> list[str]:
        warnings: list[str] = []
        versions = (meta.get("contract") or {}).get("schema_versions")
        schema_version = document.get("schema_version")
        if isinstance(versions, list) and schema_version not in versions:
            raise ApiError(
                "unsupported_schema_version",
                f"the platform accepts schema version {', '.join(map(str, versions))}"
                f", not {schema_version}",
                codes.UPLOAD_UNSUPPORTED,
            )
        policy = (meta.get("clients") or {}).get(CLIENT_NAME)
        current = _version(__version__)
        if isinstance(policy, dict) and current:
            minimum = _version(policy.get("min_supported"))
            blocked = [_version(v) for v in policy.get("blocked") or []]
            if current in blocked or (minimum and current < minimum):
                raise ApiError(
                    "client_version_unsupported",
                    f"cloudexit {__version__} is not accepted by the platform"
                    + (
                        f"; use {policy['recommended']} or newer"
                        if policy.get("recommended")
                        else ""
                    ),
                    codes.UPLOAD_UNSUPPORTED,
                )
            deprecated = _version(policy.get("deprecated_below"))
            if deprecated and current < deprecated:
                warnings.append(
                    f"cloudexit {__version__} is deprecated"
                    + (
                        f"; upgrade to {policy['recommended']}"
                        if policy.get("recommended")
                        else ""
                    )
                )
        return warnings

    def _target(
        self,
        whoami: Mapping[str, Any],
        name: str,
        exit_strategy: int,
        assessment_id: str | None,
    ) -> dict[str, Any] | None:
        if assessment_id:
            try:
                return {"assessment_id": str(uuid.UUID(str(assessment_id).strip()))}
            except ValueError:
                raise ApiError(
                    "invalid_assessment_id",
                    "the assessment id is not a valid id (expected a UUID)",
                    codes.UPLOAD_TARGET,
                ) from None
        if (whoami.get("key") or {}).get("kind") != "account":
            return None
        strategy = EXIT_STRATEGIES.get(exit_strategy)
        if not strategy:
            raise ApiError(
                "invalid_target",
                f"exit strategy {exit_strategy} cannot be sent to the platform",
                codes.UPLOAD_REJECTED,
            )
        return {"create": {"name": name, "exit_strategy": strategy}}

    def _settle(
        self, data: dict[str, Any], response: requests.Response
    ) -> tuple[dict[str, Any], bool]:
        deadline = self._monotonic() + self.timeout[1]
        while data.get("status") in PENDING_STATUSES:
            platform_id = data.get("id")
            remaining = deadline - self._monotonic()
            if not isinstance(platform_id, str) or remaining <= 0:
                return data, True
            delay = _retry_after(response)
            self._sleep(min(delay, remaining))
            data, response = self._call(
                "GET", f"/syncs/{quote(platform_id, safe='')}", expected=(200,)
            )
        if data.get("status") == "failed":
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            code = str(error.get("code") or "sync_failed")
            retryable = bool(error.get("retryable"))
            raise ApiError(
                code,
                _one_line(error.get("message") or "the platform could not process it"),
                exit_code_for(code, None, retryable),
                request_id=error.get("request_id") or data.get("request_id"),
                retryable=retryable,
            )
        return data, False

    def push(
        self,
        document: Mapping[str, Any],
        *,
        name: str,
        exit_strategy: int,
        assessment_id: str | None = None,
    ) -> PushResult:
        sync_id = (document.get("sync") or {}).get("id")
        if not isinstance(sync_id, str) or not sync_id:
            raise ApiError(
                "invalid_document",
                "the sync document has no sync id",
                codes.UPLOAD_REJECTED,
            )
        meta = self.meta()
        warnings = self._check_meta(meta, document)
        whoami = self.whoami()
        warnings += _warning_lines(whoami.get("warnings"))

        outgoing = {key: value for key, value in document.items() if key != "target"}
        target = self._target(whoami, name, exit_strategy, assessment_id)
        if target:
            outgoing["target"] = target
        body = json.dumps(outgoing, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        problems = limit_problems(
            outgoing, meta.get("limits") or {}, size=len(body), now=self._clock()
        )
        if problems:
            raise ApiError(
                "limit_exceeded", "; ".join(problems[:5]), codes.UPLOAD_REJECTED
            )

        data, response = self._call(
            "POST",
            "/syncs",
            expected=(200, 201, 202),
            body=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": sync_id,
                "Prefer": f"wait={PREFER_WAIT}",
            },
        )
        replayed = response.headers.get("Idempotent-Replayed", "").lower() == "true"
        data, pending = self._settle(data, response)

        assessment = data.get("assessment")
        assessment = assessment if isinstance(assessment, dict) else {}
        url = assessment.get("url")
        return PushResult(
            status=str(data.get("status") or ""),
            sync_id=data.get("id") if isinstance(data.get("id"), str) else None,
            assessment_id=assessment.get("id"),
            assessment_name=assessment.get("name"),
            assessment_created=assessment.get("created") is True,
            url=(
                url
                if isinstance(url, str) and url.startswith(("https://", "http://"))
                else None
            ),
            replayed=replayed,
            pending=pending,
            warnings=tuple(warnings + _warning_lines(data.get("warnings"))),
            request_id=response.headers.get("X-Request-ID") or data.get("request_id"),
            # A replay charges nothing, whatever the stored answer says
            billing=None if replayed else _billing(data.get("billing")),
        )


def _retry_after(response: requests.Response) -> float:
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return POLL_DEFAULT_SECONDS
    return max(0.0, min(seconds, POLL_MAX_SECONDS))
