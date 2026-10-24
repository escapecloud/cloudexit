from __future__ import annotations

import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

from cloudexit import codes
from utils.data import (
    CatalogueError,
    catalogue_dir,
    compare_catalogue_versions,
    installed_catalogue,
)
from utils.settings import describe

from .api_v2 import ApiClient, ApiError, check_key, client_policy, resolve_ca_bundle
from .spool import DEFAULT_MAX_AGE_DAYS, Spool, format_age, host_id
from .sync_document import SCHEMA_VERSION

STATUS_BUDGET = 15.0
KEY_EXPIRY_WARNING_DAYS = 14
LIMITS_SHOWN = (
    "max_body_bytes",
    "max_resource_items",
    "max_cost_items",
    "max_egress_items",
    "max_collection_age_days",
    "clock_skew_seconds",
)
# Which problem decides the exit code when there are several
EXIT_PRIORITY = (
    codes.UPLOAD_UNAVAILABLE,
    codes.UPLOAD_AUTH,
    codes.UPLOAD_UNSUPPORTED,
)
OID_NAMES = {
    b"\x55\x04\x03": "CN",
    b"\x55\x04\x0a": "O",
    b"\x55\x04\x0b": "OU",
}
STANDARD_NAMES = {"commonName": "CN", "organizationName": "O"}


@dataclass
class TlsResult:
    status: str
    bundle: str = ""
    message: str | None = None
    subject: str | None = None
    issuer: str | None = None
    expires: str | None = None


def status_budget(settings: Any) -> float:
    # A budget the user set (--upload-budget) applies here too
    budget = getattr(settings, "upload_budget", None)
    if budget is not None and budget.source != "default":
        return float(budget.value)
    return STATUS_BUDGET


def ca_bundle_choice(
    settings: Any, environ: Mapping[str, str] | None = None
) -> tuple[str, str]:
    # The bundle the platform requests verify with, and where it came from
    environ = os.environ if environ is None else environ
    configured = settings.ca_bundle.value
    if configured:
        return str(configured), f"{settings.ca_bundle.source}: {configured}"
    bundle = resolve_ca_bundle(None, environ)
    if isinstance(bundle, str):
        return bundle, f"the environment: {bundle}"
    return requests.certs.where(), "the default CA bundle (certifi)"


def _der(data: bytes, offset: int) -> tuple[int, int, int]:
    # One DER element: its tag, where its content starts and where it ends
    tag = data[offset]
    length = data[offset + 1]
    start = offset + 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    end = start + length
    if end > len(data):
        raise ValueError("truncated DER")
    return tag, start, end


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    found = []
    offset = start
    while offset < end:
        tag, begin, finish = _der(data, offset)
        found.append((tag, begin, finish))
        offset = finish
    return found


def _text(tag: int, raw: bytes) -> str:
    if tag == 0x1E:
        return raw.decode("utf-16-be", "replace")
    return raw.decode("utf-8" if tag == 0x0C else "latin-1", "replace")


def _name(data: bytes, start: int, end: int) -> str:
    parts = []
    for _, set_start, set_end in _children(data, start, end):
        for _, seq_start, seq_end in _children(data, set_start, set_end):
            pair = _children(data, seq_start, seq_end)
            if len(pair) != 2:
                continue
            oid = data[pair[0][1] : pair[0][2]]
            label = OID_NAMES.get(oid)
            if label:
                tag, begin, finish = pair[1]
                parts.append(f"{label}={_text(tag, data[begin:finish])}")
    return ", ".join(parts)


def certificate_names(der: bytes) -> dict[str, str]:
    # Subject and issuer of a certificate, read from its DER form. Best effort:
    # it only decorates the message about why verification failed.
    try:
        _, start, end = _der(der, 0)
        _, tbs_start, tbs_end = _children(der, start, end)[0]
        fields = _children(der, tbs_start, tbs_end)
        if fields and fields[0][0] == 0xA0:
            fields = fields[1:]
        # serial, signature, issuer, validity, subject
        issuer, subject = fields[2], fields[4]
        return {
            "issuer": _name(der, issuer[1], issuer[2]),
            "subject": _name(der, subject[1], subject[2]),
        }
    except (IndexError, ValueError):
        return {}


def _verified_names(cert: Mapping[str, Any]) -> dict[str, str]:
    def render(entries: Any) -> str:
        parts = []
        for group in entries or ():
            for key, value in group:
                label = STANDARD_NAMES.get(key)
                if label:
                    parts.append(f"{label}={value}")
        return ", ".join(parts)

    return {
        "subject": render(cert.get("subject")),
        "issuer": render(cert.get("issuer")),
    }


def _context(bundle: str) -> ssl.SSLContext:
    if os.path.isdir(bundle):
        return ssl.create_default_context(capath=bundle)
    return ssl.create_default_context(cafile=bundle)


def _presented(
    host: str, port: int, timeout: float, connect: Callable[..., Any]
) -> dict[str, str]:
    # What the server showed, looked at without verifying it
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with connect((host, port), timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                return certificate_names(tls.getpeercert(binary_form=True) or b"")
    except (OSError, ssl.SSLError):
        return {}


def check_tls(
    base_url: str,
    bundle: str,
    *,
    timeout: float = 5.0,
    connect: Callable[..., Any] = socket.create_connection,
    context_factory: Callable[[str], ssl.SSLContext] = _context,
) -> TlsResult:
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        return TlsResult("not_applicable", bundle, "the host does not use https")
    if requests.utils.get_environ_proxies(base_url):
        return TlsResult(
            "proxy",
            bundle,
            "a proxy is configured; TLS is checked by the platform request",
        )
    host, port = parts.hostname or "", parts.port or 443
    try:
        context = context_factory(bundle)
        with connect((host, port), timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
        names = _verified_names(cert)
        return TlsResult(
            "ok",
            bundle,
            subject=names["subject"] or None,
            issuer=names["issuer"] or None,
            expires=cert.get("notAfter"),
        )
    except ssl.SSLCertVerificationError as exc:
        names = _presented(host, port, timeout, connect)
        return TlsResult(
            "failed",
            bundle,
            exc.verify_message or str(exc),
            subject=names.get("subject"),
            issuer=names.get("issuer"),
        )
    except ssl.SSLError as exc:
        return TlsResult("failed", bundle, exc.reason or str(exc))
    except OSError as exc:
        return TlsResult(
            "unreachable",
            bundle,
            f"cannot connect to {host}:{port} ({type(exc).__name__})",
        )


@dataclass
class StatusReport:
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    problems: list[tuple[int, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def add(self, title: str, lines: list[str]) -> None:
        self.sections.append((title, lines))

    def problem(self, exit_code: int, message: str) -> None:
        self.problems.append((exit_code, message))

    @property
    def exit_code(self) -> int:
        for code in EXIT_PRIORITY:
            if any(found == code for found, _ in self.problems):
                return code
        return self.problems[0][0] if self.problems else codes.SUCCESS

    def lines(self) -> list[str]:
        out = []
        for title, lines in self.sections:
            out.append(title)
            out.extend(f"  {line}" for line in lines)
            out.append("")
        for message in self.warnings:
            out.append(f"warning: {message}")
        for _, message in self.problems:
            out.append(f"problem: {message}")
        if not self.problems:
            out.append("Everything checked is fine.")
        return out

    def as_json(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "problems": [{"exit_code": c, "message": m} for c, m in self.problems],
            "warnings": list(self.warnings),
            **self.data,
        }


def _local_catalogue_version() -> str | None:
    try:
        installed = installed_catalogue(catalogue_dir())
    except (CatalogueError, OSError):
        return None
    return installed.version if installed else None


def catalogue_standing(local: str | None, host: str | None) -> str:
    if not host:
        return "the platform reports none"
    if not local:
        return f"{host} (none is installed here)"
    standing = compare_catalogue_versions(local, host)
    if standing is None:
        return f"{host} (versions cannot be compared)"
    wording = {
        "older": "the installed one is older: run `cloudexit catalogue update`",
        "same": "the same as the installed one",
        "newer": "the installed one is newer",
    }[standing]
    return f"{host} ({wording})"


def host_catalogue_lines(
    settings: Any,
    local: str | None,
    make_client: Callable[..., ApiClient] | None = None,
) -> list[str]:
    # The platform's catalogue next to the installed one, for `catalogue status`
    if not (settings.host.value and settings.api_key.value):
        return []
    make_client = make_client or ApiClient.from_settings
    try:
        client = make_client(settings, budget=status_budget(settings))
        host = ((client.meta(fresh=True).get("catalogue") or {}).get("version")) or None
    except ApiError as error:
        return [f"platform:  unavailable ({error.message})"]
    return [f"platform:  {catalogue_standing(local, host)}"]


def _expiry_lines(key: Mapping[str, Any], now: float, report: StatusReport) -> str:
    expires = key.get("expires_at")
    if not isinstance(expires, (int, float)):
        return "unknown"
    when = datetime.fromtimestamp(expires, tz=timezone.utc).strftime("%Y-%m-%d")
    days = (expires - now) / 86400
    if days < KEY_EXPIRY_WARNING_DAYS:
        report.warnings.append(
            f"the API key expires in {max(0, int(days))} days ({when}); "
            f"keys are warned about from {KEY_EXPIRY_WARNING_DAYS} days"
        )
    return f"{when} (in {int(days)} days)"


def run_status(
    settings: Any,
    *,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], float] = time.time,
    tls: Callable[..., TlsResult] = check_tls,
    make_client: Callable[..., ApiClient] | None = None,
    spool: Spool | None = None,
) -> StatusReport:
    report = StatusReport()
    host, key = settings.host.value, settings.api_key.value
    report.add("Settings", describe(settings))
    report.data["configured"] = bool(host and key)
    local = _local_catalogue_version()

    def finish_spool(days: int) -> None:
        if not host:
            return
        report.add("Spool", _spool_lines(settings, spool, days, now(), report))

    if not host and not key:
        report.add("Platform", ["not configured: runs stay offline"])
        report.add("Catalogue", [f"installed: {local or 'none'}"])
        report.data["catalogue"] = {"local": local, "platform": None}
        return report
    if bool(host) != bool(key):
        report.problem(
            codes.UPLOAD_AUTH,
            (
                "a host is configured but no API key"
                if host
                else "an API key is configured but no host"
            ),
        )
        return report
    try:
        check_key(key)
    except ApiError as error:
        report.problem(error.exit_code, error.message)
        return report

    make_client = make_client or ApiClient.from_settings
    try:
        client = make_client(settings, budget=status_budget(settings), environ=environ)
    except ApiError as error:
        report.problem(error.exit_code, error.message)
        return report

    bundle, label = ca_bundle_choice(settings, environ)
    result = tls(client.base_url, bundle)
    report.data["tls"] = {
        "status": result.status,
        "ca_bundle": label,
        "message": result.message,
        "subject": result.subject,
        "issuer": result.issuer,
    }
    lines = [f"CA bundle: {label}"]
    if result.status == "ok":
        lines.append("certificate: verified")
        if result.subject:
            lines.append(f"  issued to {result.subject}")
        if result.issuer:
            lines.append(f"  issued by {result.issuer}")
    elif result.status in ("failed", "unreachable"):
        lines.append(f"certificate: NOT verified: {result.message}")
        if result.subject:
            lines.append(f"  the server presented a certificate for {result.subject}")
        if result.issuer:
            lines.append(f"  issued by {result.issuer} (not trusted by this bundle)")
        report.add("TLS", lines)
        report.problem(
            codes.UPLOAD_UNAVAILABLE,
            f"TLS check failed: {result.message}; use --ca-bundle for a private CA",
        )
        return report
    else:
        lines.append(f"certificate: not checked directly ({result.message})")
    report.add("TLS", lines)

    try:
        meta = client.meta(fresh=True)
    except ApiError as error:
        report.problem(error.exit_code, f"{error.code}: {error.message}")
        return report

    host_info = meta.get("host") or {}
    contract = meta.get("contract") or {}
    catalogue = meta.get("catalogue") or {}
    limits = meta.get("limits") or {}
    state, message = client_policy(meta)
    versions = contract.get("schema_versions") or []
    report.add(
        "Platform (GET /meta)",
        [
            f"host:      {host_info.get('product')}"
            + (f" {host_info.get('region')}" if host_info.get("region") else "")
            + (
                f" (version {host_info['app_version']})"
                if host_info.get("app_version")
                else ""
            ),
            f"schema:    {', '.join(map(str, versions)) or 'unknown'}"
            + (f" (current {contract['current']})" if contract.get("current") else ""),
            f"catalogue: {catalogue.get('version') or 'unknown'}",
            "limits:    "
            + ", ".join(f"{k}={limits[k]}" for k in LIMITS_SHOWN if k in limits),
            f"client:    {message}",
        ],
    )
    report.data["platform"] = {
        "product": host_info.get("product"),
        "region": host_info.get("region"),
        "app_version": host_info.get("app_version"),
        "schema_versions": versions,
        "catalogue_version": catalogue.get("version"),
        "limits": {k: limits[k] for k in LIMITS_SHOWN if k in limits},
        "client_policy": {"status": state, "message": message},
    }
    if state == "unsupported":
        report.problem(codes.UPLOAD_UNSUPPORTED, message)
    elif state == "deprecated":
        report.warnings.append(message)
    if versions and SCHEMA_VERSION not in versions:
        report.problem(
            codes.UPLOAD_UNSUPPORTED,
            f"the platform accepts schema versions {', '.join(map(str, versions))}, "
            f"not {SCHEMA_VERSION}",
        )

    days = limits.get("max_collection_age_days")
    days = days if isinstance(days, int) and days > 0 else DEFAULT_MAX_AGE_DAYS
    try:
        whoami = client.whoami()
    except ApiError as error:
        report.problem(error.exit_code, f"{error.code}: {error.message}")
        finish_spool(days)
        return report
    key_info = whoami.get("key") or {}
    tenant = whoami.get("tenant") or {}
    expiry = _expiry_lines(key_info, now(), report)
    report.add(
        "Key (GET /whoami)",
        [
            f"organisation: {tenant.get('name')} ({tenant.get('type')})",
            f"key:          {key_info.get('display')} \"{key_info.get('name')}\", "
            f"{key_info.get('kind')}",
            f"assessment:   {key_info.get('assessment_id') or 'any (not bound)'}",
            f"scopes:       {', '.join(key_info.get('scopes') or []) or 'none'}",
            f"expires:      {expiry}",
        ],
    )
    for item in whoami.get("warnings") or []:
        if isinstance(item, dict) and item.get("message"):
            report.warnings.append(str(item["message"]))
    report.data["key"] = {
        "organisation": tenant.get("name"),
        "kind": key_info.get("kind"),
        "assessment_id": key_info.get("assessment_id"),
        "scopes": key_info.get("scopes") or [],
        "expires_at": key_info.get("expires_at"),
    }

    finish_spool(days)
    report.add(
        "Catalogue",
        [
            f"installed: {local or 'none'}",
            f"platform:  {catalogue_standing(local, catalogue.get('version'))}",
        ],
    )
    report.data["catalogue"] = {
        "local": local,
        "platform": catalogue.get("version"),
        "standing": compare_catalogue_versions(local, catalogue.get("version")),
    }
    return report


def _spool_lines(
    settings: Any, spool: Spool | None, days: int, now: float, report: StatusReport
) -> list[str]:
    spool = spool or Spool.from_settings(settings)
    mine = spool.entries(host_id(settings.host.value))
    others = len(spool.entries()) - len(mine)
    stale = [e for e in mine if now - e.created > days * 86400]
    report.data["spool"] = {
        "pending": len(mine),
        "too_old": len(stale),
        "other_hosts": others,
    }
    if not mine and not others:
        return ["empty"]
    lines = []
    if mine:
        oldest = max(now - e.created for e in mine)
        lines.append(
            f"{len(mine)} sync{'' if len(mine) == 1 else 's'} waiting for this host "
            f"(oldest {format_age(oldest)}): `cloudexit spool flush`"
        )
    if stale:
        lines.append(f"{len(stale)} will be rejected: older than {days} days")
    if others:
        lines.append(f"{others} for other hosts")
    return lines
