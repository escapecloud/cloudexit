# cloudexit/report/egress.py
import json
import logging
import os
import sqlite3
from collections import Counter
from datetime import datetime
from typing import Any
from jinja2 import Environment

from cloudexit.report.helpers import format_gib, gib_number, initials, share_label
from cloudexit.resources import asset_path
from cloudexit.catalogue.db import load_data
from cloudexit.egress.model import (
    GIB,
    compute_totals,
    read_egress_inventory,
)
from cloudexit.providers.aws.egress import ARCHIVE_TIERS as AWS_ARCHIVE_TIERS
from cloudexit.providers.azure.egress import ARCHIVE_TIERS as AZURE_ARCHIVE_TIERS
from cloudexit.report.context import (
    DOCUMENT_CODE,
    EGRESS_DOCUMENT_CODE,
    PROVIDER_NAMES,
    format_document_id,
    scope_facts,
)
from cloudexit.report.notices import notices_html, text_parts
from cloudexit.report.sections import BOOK_DEMO_URL, PRODUCT_SITES
from cloudexit.report.wording import BLOCKS

logger = logging.getLogger(__name__)

DEFAULT_PRICING_ZONE = "zone1"
UNIT_DIVISORS = {"GB": 10**9, "GiB": 2**30}
COMPONENTS = ("internet_egress", "archive_retrieval")

ARCHIVE_TIERS_BY_CSP = {1: AZURE_ARCHIVE_TIERS, 2: AWS_ARCHIVE_TIERS}

CATEGORIES = {
    "object": {
        "label": "Object Storage",
        "badge": "Object",
        "color": "rgba(34, 197, 94, 1)",
        "in_allocation": True,
    },
    "block": {
        "label": "Block (allocated)",
        "badge": "Block",
        "color": "rgba(83, 155, 255, 1)",
        "in_allocation": True,
    },
    "file": {
        "label": "File Storage",
        "badge": "File",
        "color": "rgba(245, 158, 11, 1)",
        "in_allocation": True,
    },
    "database": {
        "label": "Databases",
        "badge": "Database",
        "color": "rgba(168, 85, 247, 1)",
        "in_allocation": True,
    },
    "backup": {
        "label": None,
        "badge": "Backup",
        "color": None,
        "in_allocation": False,
    },
}

PRICING_SOURCE_LINKS = {
    1: {
        "internet_egress": "https://azure.microsoft.com/pricing/details/bandwidth/",
        "archive_retrieval": (
            "https://azure.microsoft.com/pricing/details/storage/blobs/"
        ),
    },
    2: {
        "internet_egress": "https://aws.amazon.com/ec2/pricing/on-demand/",
        "archive_retrieval": "https://aws.amazon.com/s3/pricing/",
    },
}

COVERAGE_NOTES_KEYS = {1: "eg_coverage_notes_azure", 2: "eg_coverage_notes_aws"}

FALLBACK_ICONS = {
    "microsoft.compute/disks": "/icons/azure/compute/10032-icon-service-Disks.png",
    "microsoft.compute/snapshots": (
        "/icons/azure/compute/10026-icon-service-Disks-Snapshots.png"
    ),
    "aws.ec2.describe_volumes.volumes": "/icons/aws/Storage/Elastic-Block-Store.png",
    "aws.ec2.describe_snapshots.snapshots": (
        "/icons/aws/Storage/Elastic-Block-Store.png"
    ),
    "aws.backup.list_backup_vaults.backupvaultlist": "/icons/aws/Storage/Backup.png",
}


def _unit_divisor(unit: str) -> float:
    return UNIT_DIVISORS.get(unit, GIB)


def load_pricing(report_path: str) -> list[dict[str, Any]]:
    db_path = os.path.join(report_path, "data", "assessment.db")
    try:
        rows = load_data("egresspricing", db_path=db_path)
    except sqlite3.Error:
        # Runs created before the dataset shipped the egresspricing table;
        # fall back to the master dataset download.
        logger.debug("egresspricing missing from %s; using master dataset", db_path)
        rows = load_data("egresspricing")
    return [row for row in rows if not row.get("valid_to")]


def calculate_tiered_cost(total_units: float, tiers: list[dict[str, Any]]) -> float:
    cost = 0.0
    for tier in sorted(tiers, key=lambda tier: tier["tier_from"]):
        lower = tier["tier_from"]
        upper = tier["tier_to"]
        if total_units <= lower:
            break
        covered = total_units if upper is None else min(total_units, upper)
        cost += (covered - lower) * tier["price_per_unit"]
    return cost


def build_fee_estimate(
    rows: list[dict[str, Any]],
    totals: dict[str, Any],
    cloud_service_provider: int,
    prices: list[dict[str, Any]],
) -> tuple[float, dict[str, float | None]]:
    active = [
        price
        for price in prices
        if price["csp"] == cloud_service_provider
        and price["zone"] == DEFAULT_PRICING_ZONE
    ]
    internet_tiers = [
        price for price in active if price["component"] == "internet_egress"
    ]
    if not internet_tiers:
        raise ValueError(
            "No active internet egress pricing for cloud service provider "
            f"{cloud_service_provider} in the egresspricing dataset."
        )
    retrieval_prices = [
        price for price in active if price["component"] == "archive_retrieval"
    ]
    retrieval_rate_per_byte = 0.0
    if retrieval_prices:
        retrieval = retrieval_prices[0]
        retrieval_rate_per_byte = retrieval["price_per_unit"] / _unit_divisor(
            retrieval["unit"]
        )
    archive_tiers = ARCHIVE_TIERS_BY_CSP.get(cloud_service_provider, set())

    total_bytes = totals["known_size_bytes"]
    internet_divisor = _unit_divisor(internet_tiers[0]["unit"])
    internet_fee = calculate_tiered_cost(total_bytes / internet_divisor, internet_tiers)
    retrieval_fee = totals["archive_tier_bytes"] * retrieval_rate_per_byte
    total_fee = internet_fee + retrieval_fee

    fees_by_id: dict[str, float | None] = {}
    for row in rows:
        if row["size_bytes"] is None:
            fees_by_id[row["id"]] = None
            continue
        share = (row["size_bytes"] / total_bytes) if total_bytes else 0.0
        row_archive_bytes = sum(
            size
            for tier, size in (row["tier_bytes"] or {}).items()
            if tier in archive_tiers
        )
        fees_by_id[row["id"]] = (
            internet_fee * share + row_archive_bytes * retrieval_rate_per_byte
        )
    return total_fee, fees_by_id


def format_fee(fee: float | None) -> str:
    if fee is None:
        return "n/a"
    return f"${fee:,.2f}"


def _free_tier(
    prices: list[dict[str, Any]], cloud_service_provider: int
) -> dict[str, Any] | None:
    tiers = sorted(
        (
            price
            for price in prices
            if price["csp"] == cloud_service_provider
            and price["component"] == "internet_egress"
            and price["zone"] == DEFAULT_PRICING_ZONE
        ),
        key=lambda tier: tier["tier_from"],
    )
    if tiers and tiers[0]["price_per_unit"] == 0.0 and tiers[0]["tier_to"]:
        return tiers[0]
    return None


def free_tier_limit_display(
    prices: list[dict[str, Any]], cloud_service_provider: int
) -> str | None:
    tier = _free_tier(prices, cloud_service_provider)
    return f"{tier['tier_to']:g} {tier['unit']}" if tier else None


def free_allowance_bytes(
    prices: list[dict[str, Any]], cloud_service_provider: int
) -> float | None:
    tier = _free_tier(prices, cloud_service_provider)
    return tier["tier_to"] * _unit_divisor(tier["unit"]) if tier else None


def _summarize_totals(
    totals: dict[str, Any],
    total_fee: float,
    prices: list[dict[str, Any]],
    cloud_service_provider: int,
) -> dict[str, Any]:
    free_tier_limit = free_tier_limit_display(prices, cloud_service_provider)
    free_tier = (
        total_fee == 0
        and totals["known_size_bytes"] > 0
        and free_tier_limit is not None
    )
    unknown_count = totals["resources_with_unknown_size"]
    total_fee_display = format_fee(total_fee)
    total_data_display = format_gib(totals["known_size_bytes"])
    if unknown_count > 0:
        total_data_display = f"{total_data_display}+"
        if not free_tier:
            total_fee_display = f"{total_fee_display}+"
    return {
        "free_tier": free_tier,
        "free_tier_limit": free_tier_limit,
        "unknown_count": unknown_count,
        "total_fee_display": total_fee_display,
        "total_data_display": total_data_display,
    }


def _build_icon_lookup(report_path: str) -> dict[str, str]:
    db_path = os.path.join(report_path, "data", "assessment.db")
    try:
        return {
            item["code"].strip().lower(): item["icon"]
            for item in load_data("resourcetype", db_path=db_path)
            if item.get("icon")
        }
    except Exception as e:
        logger.debug("Resource type icon lookup unavailable: %s", str(e))
        return {}


def _resolve_icon(resource_type: str, icon_lookup: dict[str, str]) -> str | None:
    code = resource_type.strip().lower()
    candidate = code
    while candidate:
        if candidate in icon_lookup:
            return icon_lookup[candidate]
        if "/" not in candidate:
            break
        candidate = candidate.rsplit("/", 1)[0]
    return FALLBACK_ICONS.get(code)


def _build_allocation(rows: list[dict[str, Any]]) -> tuple[list, list, list]:
    allocation_categories = {
        key: info for key, info in CATEGORIES.items() if info["in_allocation"]
    }
    totals_by_category: dict[str, int] = {}
    for row in rows:
        if row["size_bytes"] is not None:
            category = row.get("category")
            if category in allocation_categories:
                totals_by_category[category] = (
                    totals_by_category.get(category, 0) + row["size_bytes"]
                )

    labels, values, colors = [], [], []
    for category, info in allocation_categories.items():
        size_bytes = totals_by_category.get(category, 0)
        if size_bytes > 0:
            labels.append(info["label"])
            values.append(size_bytes)
            colors.append(info["color"])
    return labels, values, colors


def _build_type_groups(
    rows: list[dict[str, Any]],
    fees_by_id: dict[str, float | None],
    icon_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault(
            row["label"],
            {
                "label": row["label"],
                "icon": _resolve_icon(row["type"], icon_lookup),
                "initials": initials(row["label"]),
                "category": row["category"],
                "category_label": (
                    CATEGORIES.get(row["category"], {}).get("badge") or row["category"]
                ),
                "size_bytes": None,
                "fee": None,
                "resources": [],
            },
        )
        if row["size_bytes"] is not None:
            group["size_bytes"] = (group["size_bytes"] or 0) + row["size_bytes"]
        fee = fees_by_id.get(row["id"])
        if fee is not None:
            group["fee"] = (group["fee"] or 0.0) + fee
        group["resources"].append(
            {
                "name": row["name"],
                "fee_display": format_fee(fee) if fee is not None else None,
                "detail": "; ".join(row["flags"] + row["notes"]),
            }
        )

    for group in groups.values():
        group["size_display"] = format_gib(group["size_bytes"])
        group["fee_display"] = format_fee(group["fee"])

    return sorted(
        groups.values(),
        key=lambda group: (
            group["fee"] is None,
            -(group["fee"] or 0.0),
            -(group["size_bytes"] or 0),
        ),
    )


def _load_estimate(
    report_path: str, json_path: str
) -> tuple[dict, list, dict, dict, float, list]:
    with open(json_path, "r", encoding="utf-8") as json_file:
        payload = json.load(json_file)

    # Run metadata stays in the raw artifact; the resources come from the
    # assessment database, which is the store of record for the estimate.
    meta = payload["meta"]
    db_path = os.path.join(report_path, "data", "assessment.db")
    rows, archive_tiers = read_egress_inventory(db_path)
    totals = compute_totals(rows, archive_tiers)

    pricing = load_pricing(report_path)
    total_fee, fees_by_id = build_fee_estimate(
        rows, totals, meta["cloud_service_provider"], pricing
    )

    icon_lookup = _build_icon_lookup(report_path)
    type_groups = _build_type_groups(rows, fees_by_id, icon_lookup)
    return meta, rows, totals, pricing, total_fee, type_groups


def load_egress_estimate(report_path: str, json_path: str) -> dict[str, Any]:
    meta, rows, totals, pricing, total_fee, type_groups = _load_estimate(
        report_path, json_path
    )
    return {
        "meta": meta,
        "rows": rows,
        "totals": totals,
        "pricing": pricing,
        "total_fee": total_fee,
        "type_groups": type_groups,
        "summary": _summarize_totals(
            totals, total_fee, pricing, meta["cloud_service_provider"]
        ),
    }


def _generated_at(timestamp):
    # The run's own time, as the estimate stored it; now when it cannot be read
    try:
        return datetime.strptime(str(timestamp), "%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return datetime.now()


def _share(part, whole):
    return part / whole * 100 if whole else 0


def _plus(text, estimate):
    # Resources without a size are left out, so the figure is a lower bound
    return f"{text}+" if estimate["summary"]["unknown_count"] > 0 else text


def introduction_facts(scope, b=BLOCKS):
    return {
        "heading": b["eg_heading_introduction"],
        "purpose_heading": b["eg_heading_purpose"],
        "purpose": b["eg_purpose_block"],
        "scope_heading": b["eg_heading_scope"],
        "scope": scope_sentence(scope, b),
        "limitations_heading": b["eg_heading_limitations"],
        "limitations": [
            b["eg_limitations_block"],
            b["eg_limitations_scope"],
            b["eg_limitations_data"],
        ],
    }


def scope_sentence(scope, b=BLOCKS):
    if scope.kind == "azure":
        return b["eg_scope_azure"].format(
            resource_group=scope.resource_group or b["ev_dash"]
        )
    if scope.kind == "aws":
        return b["eg_scope_aws"].format(region=scope.region or b["ev_dash"])
    return b["eg_scope_plain"]


def coverage_facts(cloud_service_provider, b=BLOCKS):
    key = COVERAGE_NOTES_KEYS.get(cloud_service_provider)
    if not key:
        return None
    return {
        "heading": b["eg_heading_coverage"],
        "intro": b["eg_coverage_intro"],
        "headers": (b["eg_th_resource_type"], b["eg_th_measured"]),
        "rows": [(name, note or b["ev_dash"]) for name, note in b[key]],
    }


def landscape_facts(estimate, b=BLOCKS):
    rows = estimate["rows"]
    summary = estimate["summary"]
    total = estimate["totals"]["known_size_bytes"]
    resources = len(rows)
    unsized = [row for row in rows if row["size_bytes"] is None]
    sized = resources - len(unsized)
    counts = Counter(row["label"] for row in rows)
    groups = sorted(
        (group for group in estimate["type_groups"] if group["size_bytes"] is not None),
        key=lambda group: -group["size_bytes"],
    )
    top = groups[0] if groups else None

    if not resources:
        lead = b["eg_no_data_notice"]
    elif not sized:
        key = "eg_land_none_sized_one" if resources == 1 else "eg_land_none_sized"
        lead = b[key].format(resources=resources)
    else:
        sentences = [
            b["eg_land_lead_one" if resources == 1 else "eg_land_lead"].format(
                total=format_gib(total), sized=sized, resources=resources
            )
        ]
        if len(groups) > 1 and total > 0 and top["size_bytes"]:
            sentences.append(
                b["eg_land_top"].format(
                    type=top["label"],
                    size=format_gib(top["size_bytes"]),
                    share=share_label(_share(top["size_bytes"], total)),
                )
            )
        if len(unsized) == 1:
            sentences.append(b["eg_land_unsized_one"])
        elif unsized:
            sentences.append(b["eg_land_unsized_many"].format(count=len(unsized)))
        lead = " ".join(sentences)

    unsized_counts = Counter(row["label"] for row in unsized)
    not_sized = [
        b["eg_land_not_sized_one" if count == 1 else "eg_land_not_sized_many"].format(
            type=label, count=count
        )
        for label, count in sorted(
            unsized_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "heading": b["eg_heading_landscape"],
        "empty": not resources,
        "lead": lead,
        "stats": [
            (b["eg_st_volume"], summary["total_data_display"], False),
            (
                b["eg_st_sized"],
                b["eg_st_sized_value"].format(sized=sized, resources=resources),
                False,
            ),
            (b["eg_st_not_sized"], str(len(unsized)), bool(unsized)),
            (b["eg_st_largest"], top["label"] if top else b["ev_dash"], False),
        ],
        "headers": (
            b["eg_th_resource_type"],
            b["eg_th_resources"],
            b["eg_th_size_gib"],
            b["eg_th_share"],
        ),
        "rows": [
            {
                "label": group["label"],
                "icon": group["icon"],
                "initials": group["initials"],
                "resources": counts[group["label"]],
                "size": gib_number(group["size_bytes"]),
                "share": _share(group["size_bytes"], total),
                "share_text": share_label(_share(group["size_bytes"], total)),
            }
            for group in groups
        ],
        "total": (b["eg_total"], resources, _plus(gib_number(total), estimate)),
        "not_sized": (
            b["eg_land_not_sized"].format(types=", ".join(not_sized))
            if not_sized
            else None
        ),
        "caption": b["eg_land_caption"],
        "source_note": b["eg_land_source_note"],
    }


def _allowance_text(prices, cloud_service_provider):
    # The provider's own figure, plus the GiB value when it is published in GB
    tier = _free_tier(prices, cloud_service_provider)
    if tier is None:
        return None
    limit = f"{tier['tier_to']:g} {tier['unit']}"
    if tier["unit"] == "GiB":
        return limit
    return (
        f"{limit} ({format_gib(free_allowance_bytes(prices, cloud_service_provider))})"
    )


def cost_facts(estimate, b=BLOCKS):
    rows = estimate["rows"]
    summary = estimate["summary"]
    csp = estimate["meta"]["cloud_service_provider"]
    total = estimate["totals"]["known_size_bytes"]
    total_fee = estimate["total_fee"]
    resources = len(rows)
    unsized = sum(1 for row in rows if row["size_bytes"] is None)
    sized = resources - unsized
    allowance = free_allowance_bytes(estimate["pricing"], csp)
    allowance_text = _allowance_text(estimate["pricing"], csp)
    groups = [
        group for group in estimate["type_groups"] if group["size_bytes"] is not None
    ]
    billable = total if allowance is None else max(0, total - allowance)

    if not resources:
        sentences = [b["eg_no_data_notice"]]
    elif not sized:
        sentences = [b["eg_cost_lead_none"]]
    elif summary["free_tier"]:
        sentences = [
            b["eg_cost_lead_free"].format(fee=format_fee(total_fee)),
            b["eg_cost_headroom"].format(
                used=format_gib(total), allowance=allowance_text
            ),
        ]
    else:
        sentences = [
            b["eg_cost_lead_paid"].format(
                total=format_gib(total), fee=format_fee(total_fee)
            )
        ]
        if allowance is not None:
            sentences.append(
                b["eg_cost_lead_allowance"].format(
                    allowance=allowance_text, billable=format_gib(billable)
                )
            )
        top = max(groups, key=lambda group: group["fee"] or 0)
        if total_fee > 0 and top["fee"] and len(groups) > 1:
            sentences.append(
                b["eg_cost_lead_top"].format(
                    type=top["label"],
                    share=share_label(_share(top["fee"], total_fee)),
                )
            )
    if sized and unsized == 1:
        sentences.append(b["eg_cost_unsized_one"])
    elif sized and unsized:
        sentences.append(b["eg_cost_unsized_many"].format(count=unsized))

    if summary["free_tier"]:
        volume_stat = (b["eg_st_allowance"], summary["free_tier_limit"], False)
    else:
        volume_stat = (b["eg_st_billable"], format_gib(billable), False)
    return {
        "heading": b["eg_heading_costs"],
        "empty": not resources,
        "lead": " ".join(sentences),
        "stats": [
            (b["eg_st_fee"], summary["total_fee_display"], False),
            (b["eg_st_volume"], summary["total_data_display"], False),
            volume_stat,
            (b["eg_st_not_sized"], str(unsized), bool(unsized)),
        ],
        "headers": (
            b["eg_th_resource_type"],
            b["eg_th_size_gib"],
            b["eg_th_share"],
            b["eg_th_fee"],
        ),
        # A fee of zero has no split to show
        "rows": (
            [
                {
                    "label": group["label"],
                    "icon": group["icon"],
                    "initials": group["initials"],
                    "size": gib_number(group["size_bytes"]),
                    "share": _share(group["size_bytes"], total),
                    "share_text": share_label(_share(group["size_bytes"], total)),
                    "fee": format_fee(group["fee"]),
                }
                for group in groups
            ]
            if total_fee > 0
            else []
        ),
        "total": (
            b["eg_total"],
            _plus(gib_number(total), estimate),
            summary["total_fee_display"],
        ),
        "caption": b["eg_cost_caption"],
    }


def _tier_display(row, b):
    unit = row["unit"]
    if row["tier_from"] == 0 and row["tier_to"] is None:
        return b["eg_tier_all_volumes"]
    if row["tier_from"] == 0 and row["price_per_unit"] == 0:
        return b["eg_tier_first"].format(to=f"{row['tier_to']:,.0f}", unit=unit)
    if row["tier_to"] is None:
        return b["eg_tier_over"].format(from_=f"{row['tier_from']:,.0f}", unit=unit)
    return b["eg_tier_range"].format(
        from_=f"{row['tier_from']:,.0f}", to=f"{row['tier_to']:,.0f}", unit=unit
    )


def _price_display(row, b):
    if row["price_per_unit"] == 0:
        return b["eg_price_free"]
    return f"{row['price_per_unit']:g} / {row['unit']}"


def pricing_facts(estimate, b=BLOCKS):
    csp = estimate["meta"]["cloud_service_provider"]
    rows = sorted(
        (
            price
            for price in estimate["pricing"]
            if price["csp"] == csp
            and price["zone"] == DEFAULT_PRICING_ZONE
            and price["component"] in COMPONENTS
        ),
        key=lambda price: (COMPONENTS.index(price["component"]), price["tier_from"]),
    )
    if not rows:
        return None

    provider = PROVIDER_NAMES.get(csp) or b["eg_provider_fallback"]
    valid_dates = {row["valid_from"] for row in rows}
    show_valid_from = len(valid_dates) > 1
    if show_valid_from:
        intro = b["eg_pricing_intro_mixed"].format(provider=provider)
    else:
        intro = b["eg_pricing_intro"].format(
            provider=provider, valid=next(iter(valid_dates))
        )
    headers = [b["eg_th_component"], b["eg_th_tier"], b["eg_th_price"]]
    if show_valid_from:
        headers.append(b["eg_th_valid_from"])

    table = []
    for row in rows:
        table_row = [
            b[f"eg_component_{row['component']}"],
            _tier_display(row, b),
            _price_display(row, b),
        ]
        if show_valid_from:
            table_row.append(row["valid_from"])
        table.append(table_row)

    unit = next(
        (row["unit"] for row in rows if row["component"] == "internet_egress"), None
    )
    units = {"GB": "eg_pricing_units", "GiB": "eg_pricing_units_gib"}.get(unit)
    shown = {row["component"] for row in rows}
    links = PRICING_SOURCE_LINKS.get(csp, {})
    sources = [
        (component, links[component])
        for component in COMPONENTS
        if component in shown and component in links
    ]
    if len(sources) == 1:
        source_lines = [(b["eg_pricing_source"], sources[0][1])]
    else:
        source_lines = [
            (
                b["eg_pricing_source_component"].replace(
                    "{component}", b[f"eg_component_{component}"]
                ),
                link,
            )
            for component, link in sources
        ]
    return {
        "heading": b["eg_heading_pricing"],
        "intro": intro,
        "headers": headers,
        "rows": table,
        "units": b[units].format(provider=provider) if units else None,
        "no_archive": (
            None if "archive_retrieval" in shown else b["eg_pricing_no_archive"]
        ),
        # (template with a {link} field, url)
        "sources": source_lines,
        "note": b["eg_pricing_note"],
        "glossary_heading": b["eg_heading_glossary"],
        "glossary": [
            (entry["term"], entry["definition"])
            for entry in sorted(
                b["eg_glossary"], key=lambda entry: entry["term"].lower()
            )
        ],
    }


def next_steps_facts(
    executive_id, uploaded=False, platform_url=None, assessment_id=None, b=BLOCKS
):
    # Paragraphs as text and (text, url) links, like the notices
    sites = dict(PRODUCT_SITES)
    paragraphs = [text_parts(b["eg_next_lead"], {}, {"executive_id": executive_id})]
    if uploaded:
        paragraphs.append([b["eg_next_uploaded"]])
        if platform_url:
            paragraphs.append(text_parts(b["fa_uploaded_link"], {"link": platform_url}))
        elif assessment_id:
            paragraphs.append(
                text_parts(b["fa_uploaded_id"], {}, {"assessment_id": assessment_id})
            )
    else:
        paragraphs.append(
            text_parts(
                b["eg_next_platforms"],
                {"exitcloud_url": sites["exitcloud"], "book_demo_url": BOOK_DEMO_URL},
            )
        )
    return {"heading": b["eg_heading_next_steps"], "paragraphs": paragraphs}


def generate_egress_html_report(
    report_path: str,
    json_path: str,
    provider_details: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    try:
        estimate = load_egress_estimate(report_path, json_path)
        meta, rows = estimate["meta"], estimate["rows"]
        csp = meta["cloud_service_provider"]
        summary = estimate["summary"]
        b = BLOCKS

        allocation_labels, allocation_values, allocation_colors = _build_allocation(
            rows
        )

        type_groups = estimate["type_groups"]
        fee_groups = [group for group in type_groups if (group["fee"] or 0.0) > 0]
        fee_labels = [group["label"] for group in fee_groups]
        fee_values = [round(group["fee"], 2) for group in fee_groups]

        template_content = asset_path("template", "egress.html").read_text(
            encoding="utf-8"
        )

        scope = scope_facts(provider_details or {}, csp, None)
        executive_id = format_document_id(DOCUMENT_CODE, run_id)
        env = Environment(autoescape=True)
        template = env.from_string(template_content)
        html_content = template.render(
            page_title=f"{b['cover_title_egress']} – {meta['name']}",
            document_id=format_document_id(EGRESS_DOCUMENT_CODE, run_id),
            name=meta["name"],
            cloud_service_provider=csp,
            assessment_type=meta["assessment_type"],
            timestamp=meta["timestamp"],
            total_data_display=summary["total_data_display"],
            total_fee_display=summary["total_fee_display"],
            unknown_count=summary["unknown_count"],
            has_allocation=bool(allocation_values),
            allocation_labels_json=json.dumps(allocation_labels),
            allocation_values_json=json.dumps(allocation_values),
            allocation_colors_json=json.dumps(allocation_colors),
            has_fees=bool(fee_values),
            fee_labels_json=json.dumps(fee_labels),
            fee_values_json=json.dumps(fee_values),
            free_tier=summary["free_tier"],
            free_tier_limit_display=summary["free_tier_limit"],
            known_data_display=format_gib(estimate["totals"]["known_size_bytes"]),
            type_groups=type_groups,
            introduction=introduction_facts(scope, b),
            coverage=coverage_facts(csp, b),
            landscape=landscape_facts(estimate, b),
            costs=cost_facts(estimate, b),
            pricing=pricing_facts(estimate, b),
            next_steps=next_steps_facts(executive_id, b=b),
            notices=notices_html(csp, _generated_at(meta["timestamp"]), b, egress=True),
        )

        html_path = os.path.join(report_path, "egress.html")
        with open(html_path, "w", encoding="utf-8") as report_file:
            report_file.write(html_content)

        return {
            "success": True,
            "logs": "Egress report generated successfully.",
            "html_path": html_path,
        }

    except Exception as e:
        logger.error(f"Error generating egress report: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}
