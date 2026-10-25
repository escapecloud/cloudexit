from .egress import (
    DEFAULT_PRICING_ZONE,
    coverage_facts,
    fee_number,
    free_tier_headroom,
    pricing_facts,
)
from .context import EGRESS_DOCUMENT_CODE, PROVIDER_NAMES
from .helpers import gib_number, percent, report_icon
from .wording import BLOCKS

SEGMENT_COLORS = (
    "var(--primary-800)",
    "var(--primary-600)",
    "#2dd4c8",
    "#5eeae1",
    "#99f6f0",
)
OTHER_COLOR = "var(--neutral-300)"


def _share(part, whole):
    return part / whole * 100 if whole else 0


def _internet_unit(prices, csp):
    return next(
        (
            price["unit"]
            for price in prices
            if price["csp"] == csp
            and price["component"] == "internet_egress"
            and price["zone"] == DEFAULT_PRICING_ZONE
        ),
        "GiB",
    )


def _kpis(estimate, sized, unsized, groups, b):
    summary = estimate["summary"]
    csp = estimate["meta"]["cloud_service_provider"]
    total = estimate["totals"]["known_size_bytes"]
    nothing_sized = summary["nothing_sized"]
    # Nothing was measured: no volume to show, and no headroom to claim against
    headroom = (
        None if nothing_sized else free_tier_headroom(estimate["pricing"], csp, total)
    )
    plus = "+" if summary["unknown_count"] > 0 else ""
    volume = b["ev_dash"] if nothing_sized else gib_number(total)

    kpis = [
        {
            "label": b["html_eg_kpi_fee"],
            "figure": summary["total_fee_display"],
            "unit": "",
            "caption": b[
                "html_eg_fee_free" if summary["free_tier"] else "html_eg_fee_paid"
            ],
            "primary": True,
        },
        {
            "label": b["eg_st_volume"],
            "figure": volume,
            "unit": "" if nothing_sized else f"GiB{plus}",
            "caption": (
                b["html_eg_types_sized"].format(sized=len(sized), types=len(groups))
                if groups
                else ""
            ),
        },
    ]
    if headroom:
        kpis.append(
            {
                "label": b["html_eg_kpi_headroom"],
                "figure": f"{headroom['left']:,.1f}",
                "unit": headroom["unit"],
                "caption": b["html_eg_headroom_left"].format(
                    limit=f"{headroom['limit']:g} {headroom['unit']}"
                ),
                "bar": round(headroom["used_share"]),
                "aria": b["html_eg_headroom_aria"].format(
                    share=percent(headroom["used_share"])
                ),
            }
        )
    else:
        kpis.append(
            {
                "label": b["eg_st_billable"],
                "figure": volume,
                "unit": "" if nothing_sized else "GiB",
                # With nothing sized the prices were never applied, so the
                # caption would claim something the run did not establish
                "caption": "" if nothing_sized else b["html_eg_billable_caption"],
            }
        )
    kpis.append(
        {
            "label": b["eg_st_not_sized"],
            "figure": str(len(unsized)),
            "unit": "",
            "caption": b["html_eg_not_sized_caption"],
        }
    )
    return kpis, headroom


def _volume(sized, unsized, total, b):
    if not sized or total <= 0:
        return None
    shown, rest = sized[: len(SEGMENT_COLORS)], sized[len(SEGMENT_COLORS) :]
    segments = [
        (
            group["label"],
            _share(group["size_bytes"], total),
            SEGMENT_COLORS[index],
        )
        for index, group in enumerate(shown)
    ]
    if rest:
        segments.append(
            (
                b["html_eg_vol_other"],
                _share(sum(group["size_bytes"] for group in rest), total),
                OTHER_COLOR,
            )
        )
    legend = [
        {"text": f"{label} {percent(share)}", "color": color}
        for label, share, color in segments
    ]
    return {
        "segments": [
            {"flex": round(share, 1), "color": color} for _, share, color in segments
        ],
        "legend": legend,
        "unsized": (
            b["html_eg_vol_unsized"].format(
                types=", ".join(group["label"] for group in unsized)
            )
            if unsized
            else None
        ),
        "aria": b["html_eg_vol_aria"].format(
            parts=", ".join(item["text"] for item in legend)
        ),
    }


def _table(context, estimate, sized, unsized, total, b):
    summary = estimate["summary"]
    plus = "+" if summary["unknown_count"] > 0 else ""

    def row(group):
        icon = group["icon"].lstrip("/") if group["icon"] else ""
        return {
            "label": group["label"],
            "initials": group["initials"],
            "icon": report_icon(context.report_path, icon),
        }

    rows = [
        {
            **row(group),
            "size": gib_number(group["size_bytes"], 2),
            "share": percent(_share(group["size_bytes"], total)),
            "share_value": round(_share(group["size_bytes"], total)),
            "fee": fee_number(group["fee"] or 0.0),
        }
        for group in sized
    ]
    rows += [
        {**row(group), "size": None, "share": None, "share_value": 0, "fee": None}
        for group in unsized
    ]
    return {
        "headers": (
            b["eg_th_resource_type"],
            b["eg_th_size_gib"],
            b["eg_th_share"],
            b["eg_th_fee"],
        ),
        "rows": rows,
        "not_sized": b["html_eg_not_sized"],
        "total": (
            b["eg_total"],
            (
                b["ev_dash"]
                if summary["nothing_sized"]
                else f"{gib_number(total, 2)}{plus}"
            ),
            (
                b["ev_dash"]
                if summary["nothing_sized"]
                else fee_number(estimate["total_fee"])
            ),
        ),
    }


def _notes(context, estimate, headroom, b):
    csp = estimate["meta"]["cloud_service_provider"]
    total = estimate["totals"]["known_size_bytes"]
    provider = PROVIDER_NAMES.get(csp) or b["eg_provider_fallback"]
    unit = _internet_unit(estimate["pricing"], csp)
    if unit == "GB" and headroom:
        used_gb = total / 10**9
        units = b["html_eg_units_gb"].format(
            provider=provider,
            gib=gib_number(total),
            gb=f"{used_gb:,.1f}",
            limit=f"{headroom['limit']:g} {headroom['unit']}",
        )
    elif unit == "GB":
        units = b["html_eg_units_gb_plain"].format(provider=provider)
    else:
        units = b["html_eg_units_gib"].format(provider=provider)

    currencies = {entry.currency for entry in context.costs}
    if len(currencies) == 1:
        amounts = b["html_eg_amounts_costs"].format(currency=next(iter(currencies)))
    else:
        amounts = b["html_eg_amounts"]
    return [f"{units} {amounts}", b["html_eg_scope_note"]]


def egress_page(context, estimate, b=BLOCKS):
    csp = estimate["meta"]["cloud_service_provider"]
    total = estimate["totals"]["known_size_bytes"]
    groups = estimate["type_groups"]
    sized = sorted(
        (group for group in groups if group["size_bytes"] is not None),
        key=lambda group: -group["size_bytes"],
    )
    unsized = [group for group in groups if group["size_bytes"] is None]
    kpis, headroom = _kpis(estimate, sized, unsized, groups, b)
    prices = pricing_facts(estimate, b)
    return {
        "heading": b["ev_egress_report"],
        "caption": b["html_eg_caption"].format(
            document_id=context.egress_document_id
            or context.document_id_for(EGRESS_DOCUMENT_CODE)
        ),
        "empty": b["eg_no_data_notice"] if not estimate["rows"] else None,
        "kpis": kpis,
        "volume": _volume(sized, unsized, total, b),
        "table": _table(context, estimate, sized, unsized, total, b),
        "notes": _notes(context, estimate, headroom, b),
        "sized": {
            "summary": b["html_eg_sized_summary"],
            "facts": coverage_facts(csp, b),
        },
        "prices": (
            {
                "summary": b["html_eg_prices_summary"].format(
                    provider=PROVIDER_NAMES.get(csp) or b["eg_provider_fallback"]
                ),
                "facts": prices,
            }
            if prices
            else None
        ),
    }
