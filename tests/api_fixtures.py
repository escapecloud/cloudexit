import copy
import time
import zlib

import yaml
from jsonschema import Draft202012Validator

from tests.flow_fixtures import SERVER_SCORES
from tests.http_fixtures import make_response
from tests.sync_fixtures import DEFINITION

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

HOST = "https://platform.test"
ASSESSMENT_ID = "0193a000-0000-7000-8000-000000000001"
PLATFORM_SYNC_ID = "0193a000-0000-7000-8000-0000000000aa"
REQUEST_ID = "req-fake-0001"
URL = "https://platform.test/assessments/a-1"


def make_key(prefix="esc_sk_", fill="A"):
    head = prefix + (fill * 48)[:48]
    value = zlib.crc32(head.encode("ascii"))
    digits = []
    for _ in range(6):
        value, digit = divmod(value, 62)
        digits.append(ALPHABET[digit])
    return head + "".join(reversed(digits))


KEY = make_key()
EU_KEY = make_key("exc_eu_sk_", "B")
US_KEY = make_key("exc_us_sk_", "C")


def minimal_document(sync_id, completed_at=None):
    return {
        "schema_version": "2.0",
        "sync": {"id": sync_id, "completed_at": completed_at or int(time.time())},
        "assessment_type": 2,
        "inventory": {"resources": []},
    }


def corrupt(key):
    last = key[-1]
    return key[:-1] + ("a" if last != "a" else "b")


def component_errors(name, value):
    spec = yaml.safe_load((DEFINITION / "openapi.yaml").read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": spec["components"]},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return sorted(
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in validator.iter_errors(value)
    )


def merged(base, changes):
    result = copy.deepcopy(base)
    for key, value in (changes or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


META = {
    "object": "meta",
    "api_version": "2",
    "host": {
        "product": "escapecloud",
        "region": None,
        "app_version": "9.9.9",
        "base_url": HOST,
    },
    "contract": {"schema_versions": ["2.0"], "current": "2.0"},
    "clients": {
        "cloudexit": {
            "min_supported": "1.0.0",
            "deprecated_below": None,
            "recommended": "2.0.0",
            "blocked": [],
        }
    },
    "catalogue": {
        "version": "2026-11-02",
        "sha256": "9" * 64,
        "collector_min_version": None,
    },
    "limits": {
        "max_body_bytes": 1000000,
        "max_resource_items": 1000,
        "max_count_per_item": 10000,
        "max_location_chars": 255,
        "max_cost_items": 12,
        "max_egress_items": 1000,
        "max_name_chars": 255,
        "clock_skew_seconds": 86400,
        "max_collection_age_days": 30,
        "idempotency_replay_hours": 168,
    },
    "rate_limits": {"per_key": {"requests_per_minute": 60}},
    "processing": {"modes": ["inline"]},
    "features": {"upsert_by_scope_key": False, "content_dedupe": False},
    "assessment_types": [
        {"id": 1, "code": "basic", "name": "Basic"},
        {"id": 2, "code": "standard", "name": "Standard"},
        {"id": 3, "code": "premium", "name": "Premium"},
    ],
}


def meta_body(**changes):
    return merged(META, changes)


def whoami_body(kind="assessment", scopes=None, assessment_id=ASSESSMENT_ID, **extra):
    if scopes is None:
        scopes = ["syncs:write"] if kind == "assessment" else ["assessments:create"]
    body = {
        "object": "whoami",
        "api_version": "2",
        "request_id": REQUEST_ID,
        "tenant": {"type": "organization", "id": "org-1", "name": "Org"},
        "key": {
            "id": "0193a000-0000-7000-8000-0000000000bb",
            "name": "key",
            "display": "esc_sk_…a1b2",
            "kind": kind,
            "scopes": scopes,
            "assessment_id": assessment_id if kind == "assessment" else None,
            "expires_at": 4102444800,
        },
        "warnings": [],
    }
    return merged(body, extra)


def sync_body(**changes):
    body = {
        "object": "sync",
        "api_version": "2",
        "id": PLATFORM_SYNC_ID,
        "client_sync_id": None,
        "request_id": REQUEST_ID,
        "status": "completed",
        "assessment": {
            "id": ASSESSMENT_ID,
            "name": "Flow Test",
            "created": False,
            "scope_key": "aws:123456789012:eu-central-1",
            "url": URL,
        },
        "timestamps": {"collected_at": 1, "received_at": 2, "processed_at": 3},
        "ordering": {"latest": True, "late": False},
        "content": {"payload_sha256": "1" * 64, "content_sha256": "2" * 64},
        "assessment_type": {"requested": 2, "granted": 2},
        "catalogue": {
            "client": {"version": "2026-11-02", "sequence": 41},
            "host": {"version": "2026-11-02", "sha256": "9" * 64},
            "unknown_resource_types": [],
            "inactive_resource_types": [],
        },
        "warnings": [],
        "results": {
            "scoring_version": "1",
            "scores": {
                "exit": SERVER_SCORES[0],
                "human": SERVER_SCORES[1],
                "technology": SERVER_SCORES[2],
                "operational": SERVER_SCORES[3],
            },
            "risks": [
                {"code": "R1", "severity": "high", "impacted_resource_type_ids": [1]}
            ],
        },
        "links": {"self": "/api/v2/syncs/x", "assessment": "/api/v2/assessments/y"},
    }
    return merged(body, changes)


def error_body(code, message="The platform said no.", retryable=False, **extra):
    error = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": REQUEST_ID,
        "details": [],
    }
    error.update(extra)
    return {"object": "error", "api_version": "2", "error": error}


def reply(status, body, **headers):
    headers.setdefault("X-Request-ID", REQUEST_ID)
    return make_response(status, json_body=body, headers=headers)


def meta_reply(**changes):
    return reply(200, meta_body(**changes))


def whoami_reply(**kwargs):
    return reply(200, whoami_body(**kwargs))


def created_reply(**changes):
    return reply(201, sync_body(**changes), Location="/api/v2/syncs/x")


def success_replies(**changes):
    return [meta_reply(), whoami_reply(), created_reply(**changes)]
