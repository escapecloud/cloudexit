import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cloudexit.resources import find_asset
from core.utils_report_common import summarize_alternative_technologies
from cloudexit.catalogue.store import CatalogueError, catalogue_info
from cloudexit.version import __version__

from .palette import DEFAULT_APPEARANCE, SEVERITIES, Appearance

PROVIDER_NAMES = {1: "Microsoft Azure", 2: "Amazon Web Services"}
STRATEGY_CODES = {1: "repatriation", 2: "hybrid_cloud", 3: "alternate_cloud"}
COST_MONTHS = 6
DOCUMENT_CODE = "ES"


@dataclass(frozen=True)
class Provenance:
    collector_version: str
    collected_at: datetime
    generated_at: datetime
    run_id: str | None = None
    catalogue_version: str | None = None
    catalogue_sequence: int | None = None
    catalogue_sha256: str | None = None
    uploaded: bool = False
    platform_url: str | None = None
    assessment_id: str | None = None

    @property
    def run_short_id(self):
        # The end of a UUIDv7 is random; its start is the time.
        return self.run_id.replace("-", "")[-8:] if self.run_id else ""

    @property
    def collected_date(self):
        return self.collected_at.strftime("%Y-%m-%d")

    def as_dict(self):
        return {
            "collector_version": self.collector_version,
            "catalogue_version": self.catalogue_version,
            "catalogue_sequence": self.catalogue_sequence,
            "catalogue_sha256": self.catalogue_sha256,
            "sync_id": self.run_id,
            "uploaded": self.uploaded,
            "platform_url": self.platform_url,
        }


def collect_provenance(db_path, *, run_id=None, upload=None, collected_at=None):
    try:
        info = catalogue_info(db_path)
    except CatalogueError:
        info = None
    now = datetime.now(timezone.utc)
    upload = upload or {}
    return Provenance(
        collector_version=__version__,
        collected_at=collected_at or now,
        generated_at=now,
        run_id=run_id or upload.get("sync_id"),
        catalogue_version=info.version if info else None,
        catalogue_sequence=info.sequence if info else None,
        catalogue_sha256=info.sha256 if info else None,
        uploaded=upload.get("status") == "uploaded",
        platform_url=upload.get("url") if upload.get("status") == "uploaded" else None,
        assessment_id=upload.get("assessment_id"),
    )


@dataclass
class ResourceTypeEntry:
    resource_type_id: str
    name: str
    icon: str | None
    resources: int = 0
    locations: set = field(default_factory=set)


@dataclass(frozen=True)
class CostEntry:
    month: str
    amount: float
    currency: str
    final: bool


@dataclass
class RiskEntry:
    risk_id: int
    code: str
    name: str
    description: str
    severity: str
    resource_type_ids: set = field(default_factory=set)

    @property
    def scope_wide(self):
        return not self.resource_type_ids


@dataclass(frozen=True)
class AlternativeEntry:
    name: str
    vendor: str
    country: str | None
    open_source: bool
    support_plan: bool


@dataclass
class AlternativesEntry:
    resource_type_id: str
    name: str
    icon: str | None
    resources: int
    alternatives: list = field(default_factory=list)


@dataclass(frozen=True)
class ScopeFacts:
    kind: str
    provider: str
    rows: tuple = ()
    region: str = ""
    resource_group: str = ""
    file_name: str = ""
    locations: tuple = ()


@dataclass
class ReportContext:
    name: str
    cloud_service_provider: int
    provider: str
    exit_strategy: int
    strategy_code: str
    scope: ScopeFacts
    provenance: Provenance
    resources: list
    costs: list
    risks: list
    alternatives: list
    report_path: str
    appearance: Appearance = DEFAULT_APPEARANCE

    @property
    def document_id(self):
        return f"{DOCUMENT_CODE}-{self.provenance.run_short_id or 'LOCAL'}-EN"

    @property
    def total_resources(self):
        return sum(entry.resources for entry in self.resources)

    @property
    def regions(self):
        return {location for entry in self.resources for location in entry.locations}

    @property
    def completed(self):
        return self.provenance.collected_at.strftime("%Y-%m-%d %H:%M UTC")


def _icon_path(report_path, icon):
    # None when the resource type has no icon file; the report draws a
    # placeholder for it.
    found = find_asset(report_path, str(icon or ""))
    return str(found) if found is not None else None


def _resource_entries(resource_inventory, resource_type_mapping, report_path):
    entries = {}
    for row in resource_inventory:
        key = str(row["resource_type"])
        info = resource_type_mapping.get(key, {})
        entry = entries.setdefault(
            key,
            ResourceTypeEntry(
                key,
                (info.get("name") or "").strip() or "–",
                _icon_path(report_path, info.get("icon")),
            ),
        )
        entry.resources += int(row.get("count") or 0)
        if row.get("location"):
            entry.locations.add(row["location"])
    return sorted(
        entries.values(), key=lambda entry: (-entry.resources, entry.name.lower())
    )


def _cost_entries(cost_data, collected_at):
    current = collected_at.strftime("%Y-%m")
    rows = sorted(cost_data, key=lambda row: str(row["month"]))[-COST_MONTHS:]
    return [
        CostEntry(
            str(row["month"])[:7],
            float(row["cost"] or 0),
            str(row["currency"] or "").upper(),
            str(row["month"])[:7] < current,
        )
        for row in rows
    ]


def _severity(definition):
    for key in ("catalogue_severity", "severity"):
        value = str(definition.get(key) or "").lower()
        if value in SEVERITIES:
            return value
    return None


def _risk_entries(risk_data, risk_definitions):
    definitions = {definition["id"]: definition for definition in risk_definitions}
    entries = {}
    for row in risk_data:
        definition = definitions.get(row["risk"])
        severity = _severity(definition) if definition else None
        if severity is None:
            continue
        entry = entries.setdefault(
            row["risk"],
            RiskEntry(
                row["risk"],
                str(definition.get("code") or f"R{row['risk']}"),
                definition["name"],
                definition.get("description") or "",
                severity,
            ),
        )
        if row["resource_type"] not in (None, "null", ""):
            entry.resource_type_ids.add(str(row["resource_type"]))
    return sorted(
        entries.values(),
        key=lambda entry: (SEVERITIES.index(entry.severity), entry.risk_id),
    )


def _vendor(name):
    # The shared summary names a missing vendor "Unknown Organization"; the
    # report leaves it out.
    return "" if name == "Unknown Organization" else name


def _alternative_entries(
    resources, resource_inventory, alternatives, technologies, organizations, strategy
):
    grouped = summarize_alternative_technologies(
        resource_inventory,
        alternatives,
        technologies,
        strategy,
        alternative_technology_organizations=organizations,
    )
    entries = []
    for resource in resources:
        found = [
            AlternativeEntry(
                tech["product_name"],
                _vendor(tech["organization_name"]),
                (
                    tech["organization_country_code"]
                    if tech["organization_country_code"] != "N/A"
                    else None
                ),
                tech["open_source"],
                tech["support_plan"],
            )
            for tech in grouped.get(resource.resource_type_id, [])
        ]
        # The report does not rank them: alphabetical, then the vendor.
        found.sort(key=lambda alt: (alt.name.lower(), alt.vendor.lower()))
        entries.append(
            AlternativesEntry(
                resource.resource_type_id,
                resource.name,
                resource.icon,
                resource.resources,
                found,
            )
        )
    return entries


def _scope(provider_details, cloud_service_provider, tfstate_scope):
    provider = PROVIDER_NAMES.get(cloud_service_provider, "–")
    if provider_details.get("tfstatePath"):
        scope = tfstate_scope or {
            "file": os.path.basename(provider_details["tfstatePath"])
        }
        locations = tuple(scope.get("locations") or ())
        rows = [("file", scope.get("file") or "–")]
        if scope.get("sha256"):
            rows.append(("sha256", scope["sha256"]))
        if scope.get("lineage"):
            rows.append(("lineage", scope["lineage"]))
        if scope.get("serial") is not None:
            rows.append(("serial", str(scope["serial"])))
        if cloud_service_provider == 1:
            rows.append(("subscriptions", ", ".join(scope.get("subscriptions") or [])))
            rows.append(
                ("resource_groups", ", ".join(scope.get("resource_groups") or []))
            )
        else:
            rows.append(("regions", ", ".join(locations)))
        return ScopeFacts(
            "tfstate",
            provider,
            tuple(rows),
            file_name=scope.get("file") or "",
            locations=locations,
        )
    if cloud_service_provider == 1:
        rows = (
            ("tenant", provider_details.get("tenantId") or ""),
            ("subscription", provider_details.get("subscriptionId") or ""),
            ("resource_group", provider_details.get("resourceGroupName") or ""),
        )
        return ScopeFacts(
            "azure",
            provider,
            rows,
            resource_group=provider_details.get("resourceGroupName") or "",
        )
    rows = (
        ("account", provider_details.get("accountId") or ""),
        ("region", provider_details.get("region") or ""),
    )
    return ScopeFacts(
        "aws", provider, rows, region=provider_details.get("region") or ""
    )


def build_context(
    *,
    metadata: dict[str, Any],
    provider_details: dict[str, Any],
    resource_type_mapping: dict[str, dict[str, Any]],
    resource_inventory: list[dict[str, Any]],
    cost_data: list[dict[str, Any]],
    risk_data: list[dict[str, Any]],
    risk_definitions: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    alternative_technologies: list[dict[str, Any]],
    alternative_technology_organizations: list[dict[str, Any]],
    exit_strategy: int,
    tfstate_scope: dict[str, Any] | None,
    provenance: Provenance,
    report_path: str,
) -> ReportContext:
    csp = int(metadata["cloud_service_provider"])
    resources = _resource_entries(
        resource_inventory, resource_type_mapping, report_path
    )
    return ReportContext(
        name=metadata["name"],
        cloud_service_provider=csp,
        provider=PROVIDER_NAMES.get(csp, "–"),
        exit_strategy=exit_strategy,
        strategy_code=STRATEGY_CODES.get(int(exit_strategy), ""),
        scope=_scope(provider_details, csp, tfstate_scope),
        provenance=provenance,
        resources=resources,
        costs=_cost_entries(cost_data, provenance.collected_at),
        risks=_risk_entries(risk_data, risk_definitions),
        alternatives=_alternative_entries(
            resources,
            resource_inventory,
            alternatives,
            alternative_technologies,
            alternative_technology_organizations,
            exit_strategy,
        ),
        report_path=report_path,
    )
