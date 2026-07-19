# core/utils_report_egress.py
import json
import logging
import os
import sqlite3
from typing import Any
from jinja2 import Environment
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.utils_db import load_data
from core.utils_egress import GIB, format_bytes
from core.utils_egress_aws import ARCHIVE_TIERS as AWS_ARCHIVE_TIERS
from core.utils_egress_azure import ARCHIVE_TIERS as AZURE_ARCHIVE_TIERS
from core.utils_report import (
    _build_scope_section,
    _build_summary_section,
    _default_table_style,
)
from core.utils_report_pdf import draw_header_footer

PDF_HEADER_TITLE = "EscapeCloud Community Edition - Data & Egress"

logger = logging.getLogger("core.engine.report_egress")
logger.setLevel(logging.INFO)

DEFAULT_PRICING_ZONE = "zone1"
UNIT_DIVISORS = {"GB": 10**9, "GiB": 2**30}

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
EGRESS_FEE_CHART_COLORS = [
    HexColor("#115e59"),
    HexColor("#ffae1f"),
    HexColor("#539bff"),
    HexColor("#a855f7"),
    HexColor("#22c55e"),
    HexColor("#991b1b"),
]

PROVIDER_NAMES = {1: "Microsoft Azure", 2: "Amazon Web Services"}
COMPONENT_LABELS = {
    "internet_egress": "Internet Egress",
    "archive_retrieval": "Archive Retrieval",
}
COVERAGE_NOTES = {
    1: [
        ("Storage Accounts", "Capacity metrics can lag by up to roughly 48 hours"),
        ("Managed Disks, Snapshots", "Provisioned size; counted as an upper bound"),
        ("SQL Databases", "Used space from database metrics"),
        ("Cosmos DB Accounts", "Used space from database metrics"),
        ("PostgreSQL / MySQL Flexible Servers", "Used space from database metrics"),
        ("Recovery Services Vaults", "Not sized; recovery points cannot be exported"),
    ],
    2: [
        ("S3 Buckets", "Metrics can lag by up to roughly 48 hours"),
        (
            "EBS Volumes, Snapshots",
            "Upper bound; snapshots can over-count shared blocks",
        ),
        ("RDS Instances", "Aurora is not sized"),
        ("DynamoDB Tables", ""),
        ("AWS Backup Vaults", "Not sized; recovery points cannot be exported"),
    ],
}

ALPHA_NOTE = (
    "NOTE: This Data Landscape & Egress Estimation feature is currently in "
    "alpha version. Estimated values may be incomplete or inaccurate in some "
    "environments."
)

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
DEFAULT_ICON = "/icons/misc/no_image.png"


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


def free_tier_limit_display(
    prices: list[dict[str, Any]], cloud_service_provider: int
) -> str | None:
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
        return f"{tiers[0]['tier_to']:g} {tiers[0]['unit']}"
    return None


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
    total_fee_display = "$0" if free_tier else format_fee(total_fee)
    total_data_display = format_bytes(totals["known_size_bytes"])
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


def _resolve_icon(resource_type: str, icon_lookup: dict[str, str]) -> str:
    code = resource_type.strip().lower()
    candidate = code
    while candidate:
        if candidate in icon_lookup:
            return icon_lookup[candidate]
        if "/" not in candidate:
            break
        candidate = candidate.rsplit("/", 1)[0]
    return FALLBACK_ICONS.get(code, DEFAULT_ICON)


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
        group["size_display"] = format_bytes(group["size_bytes"])
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

    meta = payload["meta"]
    rows = payload["data"]["resources"]
    totals = payload["data"]["totals"]

    pricing = load_pricing(report_path)
    total_fee, fees_by_id = build_fee_estimate(
        rows, totals, meta["cloud_service_provider"], pricing
    )

    icon_lookup = _build_icon_lookup(report_path)
    type_groups = _build_type_groups(rows, fees_by_id, icon_lookup)
    return meta, rows, totals, pricing, total_fee, type_groups


def generate_egress_html_report(report_path: str, json_path: str) -> dict[str, Any]:
    try:
        meta, rows, totals, pricing, total_fee, type_groups = _load_estimate(
            report_path, json_path
        )

        summary = _summarize_totals(
            totals, total_fee, pricing, meta["cloud_service_provider"]
        )

        allocation_labels, allocation_values, allocation_colors = _build_allocation(
            rows
        )

        fee_groups = [group for group in type_groups if (group["fee"] or 0.0) > 0]
        fee_labels = [group["label"] for group in fee_groups]
        fee_values = [round(group["fee"], 2) for group in fee_groups]

        template_path = os.path.join("assets", "template", "egress.html")
        with open(template_path, "r") as file:
            template_content = file.read()

        env = Environment(autoescape=True)
        template = env.from_string(template_content)
        html_content = template.render(
            name=meta["name"],
            cloud_service_provider=meta["cloud_service_provider"],
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
            known_data_display=format_bytes(totals["known_size_bytes"]),
            type_groups=type_groups,
        )

        html_path = os.path.join(report_path, "egress.html")
        with open(html_path, "w") as report_file:
            report_file.write(html_content)

        return {
            "success": True,
            "logs": "Egress report generated successfully.",
            "html_path": html_path,
        }

    except Exception as e:
        logger.error(f"Error generating egress report: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}


def _icon_flowable(report_path: str, icon: str) -> Any:
    icon_path = os.path.join(report_path, "assets") + icon
    if not os.path.exists(icon_path):
        icon_path = os.path.join(report_path, "assets") + DEFAULT_ICON
    if not os.path.exists(icon_path):
        return ""
    return Image(icon_path, width=20, height=20)


def _fee_breakdown_display(group: dict[str, Any]) -> str:
    if group["fee"] is None:
        return "n/a (not sized)"
    return group["fee_display"]


def _egress_fee_chart_groups(
    type_groups: list[dict[str, Any]], max_segments: int = 5
) -> list[tuple[str, float]]:
    fee_groups = [
        (group["label"], group["fee"])
        for group in type_groups
        if group.get("fee") is not None and group["fee"] > 0
    ]
    if len(fee_groups) <= max_segments:
        return fee_groups
    visible = fee_groups[: max_segments - 1]
    other_total = sum(fee for _, fee in fee_groups[max_segments - 1 :])
    if other_total > 0:
        visible.append(("Other Components", other_total))
    return visible


def _draw_egress_fee_chart(type_groups: list[dict[str, Any]]) -> Drawing | None:
    chart_groups = _egress_fee_chart_groups(type_groups)
    if not chart_groups:
        return None

    total_fee = sum(fee for _, fee in chart_groups)
    drawing = Drawing(360, 170)

    pie = Pie()
    pie.x = 35
    pie.y = 20
    pie.width = 125
    pie.height = 125
    pie.data = [fee for _, fee in chart_groups]
    pie.innerRadiusFraction = 0.55
    for index, _ in enumerate(chart_groups):
        color = EGRESS_FEE_CHART_COLORS[index % len(EGRESS_FEE_CHART_COLORS)]
        pie.slices[index].fillColor = color
        pie.slices[index].strokeColor = colors.white
        pie.slices[index].strokeWidth = 1
    drawing.add(pie)

    legend_x = 190
    legend_y = 130
    for index, (label, fee) in enumerate(chart_groups):
        y = legend_y - index * 22
        color = EGRESS_FEE_CHART_COLORS[index % len(EGRESS_FEE_CHART_COLORS)]
        share = (fee / total_fee * 100) if total_fee else 0
        drawing.add(Rect(legend_x, y, 8, 8, fillColor=color, strokeColor=color))
        drawing.add(
            String(
                legend_x + 14,
                y - 1,
                f"{label}: {share:.1f}%",
                fontName="Helvetica",
                fontSize=8,
                fillColor=HexColor("#112726"),
            )
        )
    return drawing


def _build_coverage_section(cloud_service_provider, styles, content_style):
    rows = COVERAGE_NOTES.get(cloud_service_provider)
    if not rows:
        return []

    content = []
    content.append(
        Paragraph(
            "The estimation covers the following data-related resource types "
            "within the assessed scope:",
            content_style,
        )
    )
    table_data = [["Name", "Notes"]] + [[name, notes] for name, notes in rows]
    coverage_table = Table(table_data, colWidths=[7 * cm, 8.5 * cm])
    coverage_table.setStyle(_default_table_style())
    content.append(coverage_table)
    content.append(Spacer(1, 6))
    note_style = ParagraphStyle(
        "CoverageNote",
        fontSize=8,
        leading=10,
        textColor=HexColor("#6c757d"),
    )
    content.append(Paragraph(ALPHA_NOTE, note_style))
    content.append(PageBreak())
    return content


def _build_estimated_costs_section(
    total_fee, totals, free_tier, free_tier_limit, type_groups, styles, content_style
):
    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("Estimated Costs", styles["Heading2"]))

    unknown_count = totals["resources_with_unknown_size"]
    if free_tier:
        fee_display = "$0"
        intro = (
            f"The scanned footprint ({format_bytes(totals['known_size_bytes'])}) "
            "falls entirely within the cloud provider's complimentary monthly "
            f"internet egress allowance ({free_tier_limit} free tier)."
        )
    else:
        fee_display = format_fee(total_fee)
        if unknown_count > 0:
            fee_display = f"{fee_display}+"
        intro = (
            "Estimated one-time internet egress fee for transferring the data "
            "identified on the previous page out of the cloud provider."
        )
    content.append(Paragraph(intro, content_style))

    card_title_style = ParagraphStyle(
        "EgressCostCardTitle",
        parent=content_style,
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=HexColor("#115e59"),
    )
    card_amount_style = ParagraphStyle(
        "EgressCostCardAmount",
        parent=content_style,
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=HexColor("#112726"),
    )
    warning_style = ParagraphStyle(
        "EgressCostWarning",
        parent=content_style,
        fontSize=8,
        leading=10,
        textColor=HexColor("#664d03"),
    )
    breakdown_header_style = ParagraphStyle(
        "EgressCostBreakdownHeader",
        parent=card_title_style,
        textColor=colors.white,
    )
    total_card_data = [
        [
            Paragraph("TOTAL ESTIMATED NETWORK TRANSIT FEE", card_title_style),
        ],
        [Paragraph(fee_display, card_amount_style)],
    ]
    if unknown_count > 0:
        total_card_data.append(
            [
                Paragraph(
                    f"Excludes {unknown_count} "
                    f"resource{'s' if unknown_count != 1 else ''} with "
                    "unavailable size calculations.",
                    warning_style,
                )
            ]
        )
    total_card = Table(total_card_data, colWidths=[15.5 * cm])
    total_card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, HexColor("#d7e3e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    content.append(total_card)
    content.append(Spacer(1, 12))

    chart = _draw_egress_fee_chart(type_groups)
    if chart:
        chart_table = Table([[chart]], colWidths=[15.5 * cm])
        chart_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1, HexColor("#d7e3e1")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        content.append(chart_table)
        content.append(Spacer(1, 12))

    breakdown_data = [
        [
            Paragraph("Resource Type", breakdown_header_style),
            Paragraph("Estimated Cost (List Rate)", breakdown_header_style),
        ]
    ]
    for group in type_groups:
        breakdown_data.append([group["label"], _fee_breakdown_display(group)])
    breakdown_data.append(
        [
            Paragraph("Estimated Total Egress Fee", breakdown_header_style),
            Paragraph(fee_display, breakdown_header_style),
        ]
    )
    breakdown_table = Table(breakdown_data, colWidths=[10 * cm, 5.5 * cm])
    breakdown_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, -1), (-1, -1), HexColor("#115e59")),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#d7e3e1")),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    content.append(Paragraph("Cost Breakdown", styles["Heading2"]))
    content.append(breakdown_table)
    content.append(PageBreak())
    return content


def _build_data_landscape_section(
    type_groups,
    total_size_display,
    report_path,
    styles,
    content_style,
):
    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("Data Landscape", styles["Heading1"]))
    content.append(
        Paragraph(
            "The Data Landscape summarizes the data-bearing cloud resources "
            "identified within the defined scope, with the amount of data per "
            "resource type:",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    table_data = [["#", "Resource Type", "", "Size"]]
    for index, group in enumerate(type_groups, start=1):
        table_data.append(
            [
                str(index),
                group["label"],
                _icon_flowable(report_path, group["icon"]),
                group["size_display"],
            ]
        )
    table_data.append(["Total", "", "", total_size_display])

    landscape_table = Table(table_data, colWidths=[1 * cm, 9 * cm, 1.5 * cm, 4 * cm])
    landscape_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, -1), (-1, -1), HexColor("#115e59")),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, 0), 12),
                ("ALIGN", (0, 1), (1, -2), "LEFT"),
                ("ALIGN", (2, 1), (2, -2), "CENTER"),
                ("ALIGN", (3, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
            ]
        )
    )
    content.append(landscape_table)
    content.append(PageBreak())
    return content


def _tier_display(row: dict[str, Any]) -> str:
    unit = row["unit"]
    if row["tier_from"] == 0 and row["tier_to"] is None:
        return "all volumes"
    if row["tier_from"] == 0 and row["price_per_unit"] == 0:
        return f"first {row['tier_to']:,.0f} {unit} / month"
    if row["tier_to"] is None:
        return f"over {row['tier_from']:,.0f} {unit}"
    return f"{row['tier_from']:,.0f} {unit} – {row['tier_to']:,.0f} {unit}"


def _price_display(row: dict[str, Any]) -> str:
    if row["price_per_unit"] == 0:
        return "Free"
    return f"${row['price_per_unit']:g} / {row['unit']}"


def _build_pricing_basis_section(prices, cloud_service_provider, styles, content_style):
    rows = sorted(
        (
            price
            for price in prices
            if price["csp"] == cloud_service_provider
            and price["zone"] == DEFAULT_PRICING_ZONE
            and price["component"] in COMPONENT_LABELS
        ),
        key=lambda price: (
            list(COMPONENT_LABELS).index(price["component"]),
            price["tier_from"],
        ),
    )
    if not rows:
        return []

    content = []
    content.append(Spacer(1, 12))
    content.append(Paragraph("Appendix – Egress Prices", styles["Heading1"]))

    provider_name = PROVIDER_NAMES.get(cloud_service_provider, "Unknown Provider")
    content.append(
        Paragraph(
            "Our calculations and estimations are based on the following "
            f"{provider_name} list prices (Zone 1 – US/Europe, public "
            "internet routing):",
            content_style,
        )
    )
    content.append(Spacer(1, 12))

    show_valid_from = len({row["valid_from"] for row in rows}) > 1
    header = ["#", "Component", "Tier", "Price"]
    if show_valid_from:
        header.append("Valid from")
    table_data = [header]
    for index, row in enumerate(rows, start=1):
        table_row = [
            str(index),
            COMPONENT_LABELS[row["component"]],
            _tier_display(row),
            _price_display(row),
        ]
        if show_valid_from:
            table_row.append(row["valid_from"])
        table_data.append(table_row)

    col_widths = (
        [1 * cm, 4 * cm, 5.5 * cm, 2.5 * cm, 2.5 * cm]
        if show_valid_from
        else [1 * cm, 4.5 * cm, 6 * cm, 4 * cm]
    )
    pricing_table = Table(table_data, colWidths=col_widths)
    pricing_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#115e59")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BOX", (0, 0), (-1, -1), 1, HexColor("#112726")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, 0), 12),
                ("ALIGN", (0, 1), (2, -1), "LEFT"),
                ("ALIGN", (3, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
            ]
        )
    )
    content.append(pricing_table)
    content.append(Spacer(1, 12))

    footnote_style = ParagraphStyle(
        "PricingFootnote",
        fontSize=8,
        leading=10,
        textColor=HexColor("#6c757d"),
        spaceAfter=4,
    )
    if not any(row["component"] == "archive_retrieval" for row in rows):
        content.append(
            Paragraph(
                "Archive retrieval pricing was not available for this provider "
                "at assessment time; rehydration fees are not included in the "
                "estimate.",
                footnote_style,
            )
        )
    links = PRICING_SOURCE_LINKS.get(cloud_service_provider, {})
    components_shown = {row["component"] for row in rows}
    source_lines = [
        (COMPONENT_LABELS[component], links[component])
        for component in COMPONENT_LABELS
        if component in components_shown and component in links
    ]
    if len(source_lines) == 1:
        content.append(Paragraph(f"Source: {source_lines[0][1]}", footnote_style))
    else:
        for label, link in source_lines:
            content.append(Paragraph(f"Source ({label}): {link}", footnote_style))
    content.append(
        Paragraph(
            "List prices at the time of the assessment; excludes "
            "request/transaction costs and cross-region traffic.",
            footnote_style,
        )
    )
    return content


def generate_egress_pdf_report(
    report_path: str, json_path: str, provider_details: dict[str, Any]
) -> dict[str, Any]:
    try:
        meta, rows, totals, pricing, total_fee, type_groups = _load_estimate(
            report_path, json_path
        )

        summary = _summarize_totals(
            totals, total_fee, pricing, meta["cloud_service_provider"]
        )

        pdf_path = os.path.join(report_path, "egress.pdf")

        def header_footer(canvas, doc):
            draw_header_footer(report_path, canvas, doc, title=PDF_HEADER_TITLE)

        doc = SimpleDocTemplate(
            pdf_path, pagesize=A4, title="EscapeCloud_-_Data_and_Egress_Report"
        )
        styles = getSampleStyleSheet()
        content_style = ParagraphStyle(
            "ContentStyle", fontSize=10, leading=12, spaceAfter=10
        )
        styles["Heading1"].leading = 1.5 * styles["Heading1"].fontSize
        styles["Heading1"].textColor = HexColor("#112726")
        styles["Heading2"].leading = 1.5 * styles["Heading2"].fontSize
        styles["Heading2"].textColor = HexColor("#112726")

        content = []
        content += _build_summary_section(meta, styles, content_style)
        content += _build_scope_section(meta, provider_details, styles, content_style)
        content += _build_coverage_section(
            meta["cloud_service_provider"], styles, content_style
        )
        content += _build_data_landscape_section(
            type_groups,
            summary["total_data_display"],
            report_path,
            styles,
            content_style,
        )
        content += _build_estimated_costs_section(
            total_fee,
            totals,
            summary["free_tier"],
            summary["free_tier_limit"],
            type_groups,
            styles,
            content_style,
        )
        content += _build_pricing_basis_section(
            pricing, meta["cloud_service_provider"], styles, content_style
        )

        doc.build(content, onFirstPage=header_footer, onLaterPages=header_footer)

        return {
            "success": True,
            "logs": "Egress PDF report generated successfully.",
            "pdf_path": pdf_path,
        }

    except Exception as e:
        logger.error(f"Error generating egress PDF report: {str(e)}", exc_info=True)
        return {"success": False, "logs": str(e)}
