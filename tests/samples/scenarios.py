import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tests.support.paths import ROOT

SAMPLES_DIR = ROOT / "tests" / "fixtures" / "samples"
TFSTATE_DIR = SAMPLES_DIR / "tfstate"

PROVIDER_IDS = {"azure": 1, "aws": 2}
FIELDS = (
    "name",
    "purpose",
    "provider",
    "exit_strategy",
    "scope",
    "resources",
    "costs",
    "egress",
    "upload",
    "clock",
    "run_id",
)
SCOPE_FIELDS = {
    "aws": ("accountId", "region"),
    "azure": ("tenantId", "subscriptionId", "resourceGroupName"),
}
UPLOAD_FIELDS = ("status", "url", "assessment_id", "sync_id", "reason")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
UUID7 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Scenario:
    case: str
    path: Path
    name: str
    purpose: str
    provider: str
    exit_strategy: int
    scope: dict
    resources: tuple
    costs: dict | None
    egress: dict | None
    upload: dict | None
    clock: datetime
    run_id: str

    @property
    def csp(self):
        return PROVIDER_IDS[self.provider]

    @property
    def tfstate(self):
        name = self.scope.get("tfstate")
        return str(TFSTATE_DIR / name) if name else None

    def provider_details(self):
        # What the collectors would have been given, without any credential
        if self.tfstate:
            return {"tfstatePath": self.tfstate}
        return dict(self.scope)

    def type_codes(self):
        codes = {resource["type"] for resource in self.resources}
        if self.egress:
            codes |= {resource["type"] for resource in self.egress["resources"]}
        return codes


def _fail(path, field, message):
    raise ScenarioError(f"{Path(path).name}: {field}: {message}")


def _text(path, field, value):
    if not isinstance(value, str) or not value.strip():
        _fail(path, field, "expected a non-empty string")
    return value


def _number(path, field, value, *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        _fail(path, field, "expected a number that is not negative")
    return value


def _count(path, field, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(path, field, "expected a whole number from 1")
    return value


def _only(path, field, data, allowed):
    for key in data:
        if key not in allowed:
            _fail(path, f"{field}.{key}", "unknown field")


def _scope(path, provider, scope):
    if not isinstance(scope, dict):
        _fail(path, "scope", "expected an object")
    if "tfstate" in scope:
        _only(path, "scope", scope, ("tfstate",))
        name = _text(path, "scope.tfstate", scope["tfstate"])
        if Path(name).name != name or not (TFSTATE_DIR / name).is_file():
            _fail(path, "scope.tfstate", f"no such file in {TFSTATE_DIR.name}/: {name}")
        return scope
    fields = SCOPE_FIELDS[provider]
    _only(path, "scope", scope, fields)
    for field in fields:
        if field not in scope:
            _fail(path, f"scope.{field}", "missing")
        _text(path, f"scope.{field}", scope[field])
    return scope


def _resources(path, resources):
    if not isinstance(resources, list):
        _fail(path, "resources", "expected a list")
    seen = set()
    for index, resource in enumerate(resources):
        field = f"resources[{index}]"
        if not isinstance(resource, dict):
            _fail(path, field, "expected an object")
        _only(path, field, resource, ("type", "region", "count"))
        for key in ("type", "region", "count"):
            if key not in resource:
                _fail(path, f"{field}.{key}", "missing")
        _text(path, f"{field}.type", resource["type"])
        _text(path, f"{field}.region", resource["region"])
        _count(path, f"{field}.count", resource["count"])
        key = (resource["type"], resource["region"])
        if key in seen:
            _fail(path, field, f"{key[0]} in {key[1]} is listed twice")
        seen.add(key)
    return tuple(resources)


def _costs(path, costs):
    if costs is None:
        return None
    if not isinstance(costs, dict):
        _fail(path, "costs", "expected an object or null")
    _only(path, "costs", costs, ("currency", "months", "currencies"))
    for key in ("currency", "months"):
        if key not in costs:
            _fail(path, f"costs.{key}", "missing")
    if not CURRENCY.match(str(costs["currency"])):
        _fail(path, "costs.currency", "expected a three-letter code such as USD")
    if not isinstance(costs["months"], dict):
        _fail(path, "costs.months", "expected an object of YYYY-MM: amount")
    for month, amount in costs["months"].items():
        if not MONTH.match(month):
            _fail(path, f"costs.months.{month}", "expected a month as YYYY-MM")
        _number(path, f"costs.months.{month}", amount)
    for month, currency in (costs.get("currencies") or {}).items():
        if month not in costs["months"]:
            _fail(path, f"costs.currencies.{month}", "not a month of costs.months")
        if not CURRENCY.match(str(currency)):
            _fail(path, f"costs.currencies.{month}", "expected a three-letter code")
    return costs


def _egress(path, egress):
    if egress is None:
        return None
    if not isinstance(egress, dict):
        _fail(path, "egress", "expected an object or null")
    _only(path, "egress", egress, ("resources", "archive_tiers"))
    tiers = egress.get("archive_tiers", [])
    if not isinstance(tiers, list) or not all(isinstance(t, str) for t in tiers):
        _fail(path, "egress.archive_tiers", "expected a list of tier names")
    resources = egress.get("resources")
    if not isinstance(resources, list):
        _fail(path, "egress.resources", "expected a list")
    for index, resource in enumerate(resources):
        field = f"egress.resources[{index}]"
        if not isinstance(resource, dict):
            _fail(path, field, "expected an object")
        _only(
            path,
            field,
            resource,
            ("type", "name", "size_gib", "unknown", "tiers", "flags", "notes"),
        )
        for key in ("type", "name", "size_gib"):
            if key not in resource:
                _fail(path, f"{field}.{key}", "missing")
        _text(path, f"{field}.type", resource["type"])
        _text(path, f"{field}.name", resource["name"])
        _number(path, f"{field}.size_gib", resource["size_gib"], allow_none=True)
        for key in ("tiers",):
            for tier, gib in (resource.get(key) or {}).items():
                _number(path, f"{field}.{key}.{tier}", gib)
        for key in ("flags", "notes"):
            values = resource.get(key, [])
            if not isinstance(values, list) or not all(
                isinstance(v, str) for v in values
            ):
                _fail(path, f"{field}.{key}", "expected a list of strings")
    return egress


def _upload(path, upload):
    if upload is None:
        return None
    if not isinstance(upload, dict):
        _fail(path, "upload", "expected an object or null")
    _only(path, "upload", upload, UPLOAD_FIELDS)
    _text(path, "upload.status", upload.get("status"))
    return upload


def _clock(path, value):
    text = _text(path, "clock", value)
    if not text.endswith("Z"):
        _fail(path, "clock", "expected an ISO time in UTC ending in Z")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        _fail(path, "clock", f"not an ISO time: {text}")


def load_scenario(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"{path.name}: cannot read the file: {exc}") from exc
    if not isinstance(data, dict):
        _fail(path, "(file)", "expected an object")
    for field in FIELDS:
        if field not in data:
            _fail(path, field, "missing")
    _only(path, "(file)", data, FIELDS)

    provider = data["provider"]
    if provider not in PROVIDER_IDS:
        _fail(
            path,
            "provider",
            f"expected one of {sorted(PROVIDER_IDS)}, got {provider!r}",
        )
    strategy = data["exit_strategy"]
    if strategy not in (1, 2, 3) or isinstance(strategy, bool):
        _fail(path, "exit_strategy", "expected 1, 2 or 3")
    run_id = _text(path, "run_id", data["run_id"])
    if not UUID7.match(run_id):
        _fail(path, "run_id", "expected a UUIDv7-shaped string")

    scope = _scope(path, provider, data["scope"])
    resources = _resources(path, data["resources"])
    costs = _costs(path, data["costs"])
    egress = _egress(path, data["egress"])
    if "tfstate" in scope and (resources or costs or egress):
        _fail(path, "scope.tfstate", "a state file has no resources, costs or egress")

    return Scenario(
        case=path.stem,
        path=path,
        name=_text(path, "name", data["name"]),
        purpose=_text(path, "purpose", data["purpose"]),
        provider=provider,
        exit_strategy=strategy,
        scope=scope,
        resources=resources,
        costs=costs,
        egress=egress,
        upload=_upload(path, data["upload"]),
        clock=_clock(path, data["clock"]),
        run_id=run_id,
    )


def load_scenarios(directory=SAMPLES_DIR):
    return {
        path.stem: load_scenario(path)
        for path in sorted(Path(directory).glob("*.json"))
    }
