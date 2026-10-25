from markupsafe import Markup

from .evidence import scope_rows
from .helpers import (
    format_change,
    initials,
    format_money,
    percent,
    report_icon,
    severity_label,
    strategy_name,
)
from .html_egress import egress_page
from .notices import text_parts
from .palette import SEVERITIES
from .sections import (
    AWS_ICONS_URL,
    AWS_TRADEMARK_URL,
    AZURE_ICONS_URL,
    PRODUCT_SITES,
    REPOSITORY_URL,
)
from .wording import BLOCKS

ALT_VISIBLE = 8
ALT_OPEN = 2
OPEN_RISKS_UP_TO = 3
AFFECTED_SHOWN = 3
REGIONS_SHOWN = 5
PROVIDER_LOGOS = {1: "azure", 2: "aws"}
ABOUT_URL = "#about"


def _count(b, key, n):
    return b[f"{key}_one"] if n == 1 else b[f"{key}_many"].format(n=n)


def _share(part, whole):
    return part / whole * 100 if whole else 0


def _month(month, b):
    return b["html_months"][int(month[5:7]) - 1]


def _mono(text):
    return Markup('<span class="mono">{}</span>').format(text)


def _regions_line(context, b):
    if not context.total_resources:
        return b["html_scope_no_resources"]
    names = sorted(context.regions)
    return f"{_count(b, 'scope_regions', len(names))} · {', '.join(names)}"


def _title(context, has_egress, b):
    provenance = context.provenance
    platform_url = provenance.platform_url if provenance.uploaded else None
    logo = PROVIDER_LOGOS.get(context.cloud_service_provider)
    downloads = [("report.pdf", b["html_download_summary"])]
    if has_egress:
        downloads.append(("egress.pdf", b["html_download_egress"]))
    return {
        "logo": f"assets/img/csp/{logo}.svg" if logo else None,
        "provider": context.provider,
        "name": context.name,
        "uploaded": provenance.uploaded,
        "chip": b["html_chip_uploaded" if provenance.uploaded else "html_chip_self"],
        "chip_title": b[
            (
                "html_chip_uploaded_title"
                if provenance.uploaded
                else "html_chip_self_title"
            )
        ],
        "platform_url": platform_url,
        "downloads": downloads,
    }


def _scope_card(context, b):
    scope = context.scope
    values = dict(scope.rows)
    regions = _regions_line(context, b)
    rows = [{"label": b["ev_cloud_provider"], "value": scope.provider or b["ev_dash"]}]
    if scope.kind == "azure":
        rows.append(
            {
                "label": b["ev_resource_group"],
                "value": scope.resource_group or b["ev_dash"],
                "small": regions,
            }
        )
    elif scope.kind == "aws":
        rows.append(
            {
                "label": b["ev_region"],
                "value": scope.region or b["ev_dash"],
                "small": regions,
            }
        )
    else:
        serial = values.get("serial")
        small = " · ".join(
            part
            for part in (
                b["html_scope_serial"].format(serial=serial) if serial else "",
                regions,
            )
            if part
        )
        rows.append(
            {
                "label": b["ev_file"],
                "value": scope.file_name or b["ev_dash"],
                "small": small,
            }
        )
    rows.append(
        {
            "label": b["cover_exit_strategy"],
            "value": strategy_name(context.strategy_code, b),
        }
    )
    provenance = context.provenance
    source = b["html_run_tfstate" if scope.kind == "tfstate" else "html_run_live"]
    rows.append(
        {
            "label": b["html_run_label"],
            "value": context.completed,
            "small": Markup(b["html_run_small"]).format(
                run=_mono(provenance.run_short_id or b["ev_dash"]), source=source
            ),
        }
    )
    sha = provenance.catalogue_sha256
    rows.append(
        {
            "label": b["ev_catalogue_label"],
            "value": provenance.catalogue_version or b["ev_catalogue_unknown"],
            "small": b["html_catalogue_sha"].format(sha=sha[:12]) if sha else None,
            "small_mono": True,
        }
    )
    return {"label": b["html_scope_label"], "rows": rows}


def _risks_card(context, b):
    risks = context.risks
    counts = {
        severity: sum(1 for risk in risks if risk.severity == severity)
        for severity in SEVERITIES
    }
    return {
        "label": b["html_risks_label"],
        "total": len(risks),
        "none": b["bs_find_risks_none"] if not risks else None,
        "aria": b["html_sev_aria"].format(
            counts=", ".join(
                f"{counts[severity]} {severity_label(severity, b).lower()}"
                for severity in SEVERITIES
            )
        ),
        "rows": [
            {
                "severity": severity,
                "label": severity_label(severity, b),
                "count": counts[severity],
                "width": round(_share(counts[severity], len(risks))),
            }
            for severity in SEVERITIES
        ],
    }


def _costs_card(context, b):
    if context.scope.kind == "tfstate":
        return {
            "kind": "message",
            "label": b["html_costs_label_short"],
            "figure": b["ev_dash"],
            "message": b["cost_tfstate"],
            "hint": b["html_costs_tfstate_hint"],
        }
    entries = context.costs
    if not entries or len({entry.currency for entry in entries}) > 1:
        return {
            "kind": "message",
            "label": b["html_costs_label"],
            "figure": b["ev_dash"],
            "message": (b["cost_mixed_currency"] if entries else b["tbl_cost_no_data"]),
            "hint": None,
        }

    currency = entries[-1].currency
    total = sum(entry.amount for entry in entries)
    latest = entries[-1]
    before = entries[-2] if len(entries) > 1 else None
    biggest = max(entry.amount for entry in entries)
    provisional = [entry for entry in entries if not entry.final]

    def label(entry):
        return _month(entry.month, b) + ("" if entry.final else "*")

    parts = [
        b[
            "html_costs_aria_part" if entry.final else "html_costs_aria_provisional"
        ].format(
            month=_month(entry.month, b), amount=format_money(entry.amount, currency)
        )
        for entry in entries
    ]
    return {
        "kind": "figures",
        "label": b["html_costs_label"],
        "figure": format_money(total, currency),
        "caption": (
            b["html_costs_none"]
            if total == 0
            else b["html_costs_average"].format(
                amount=format_money(total / len(entries), currency)
            )
        ),
        "change": format_change(
            before.amount if before else None, latest.amount
        ).replace(" %", " %"),
        "change_caption": (
            b["html_costs_change"].format(
                latest=_month(latest.month, b), previous=_month(before.month, b)
            )
            if before
            else ""
        ),
        "bars": [
            {
                "month": label(entry),
                "height": round(_share(entry.amount, biggest)) if biggest else 0,
                "zero": entry.amount <= 0,
                "provisional": not entry.final,
            }
            for entry in entries
        ],
        "aria": b["html_costs_aria"].format(parts=", ".join(parts)),
        "footnote": (
            b["html_costs_provisional"].format(month=_month(provisional[0].month, b))
            if provisional
            else b["html_costs_context"]
        ),
    }


def _hint(b):
    return {
        "title": b["html_hint_title"],
        "text": b["html_hint_text"],
        "sites": [
            (b["html_site_exitcloud"], dict(PRODUCT_SITES)["exitcloud"]),
            (b["html_site_escapecloud"], dict(PRODUCT_SITES)["escapecloud"]),
        ],
    }


def _scores(alternatives_total, platform_url, b):
    on_platform = f" {b['html_scores_on_platform']}" if platform_url else ""
    lock_text = (
        b["html_scores_lock_text"].format(
            alternatives=_count(b, "html_alt_n", alternatives_total)
        )
        if alternatives_total
        else b["html_scores_lock_text_plain"]
    )
    return {
        "platform_url": platform_url,
        "open_platform": b["html_open_platform"],
        "pill": b["html_scores_pill"],
        "hint": _hint(b),
        "grade": {
            "title": b["html_scores_grade"],
            "placeholder": b["ev_dash"],
            "scale": b["html_scores_scale"],
            "bands": b["html_scores_bands"],
            "text": b["html_scores_grade_text"] + on_platform,
        },
        "lock": {
            "title": b["html_scores_lock"],
            "dims": [
                (name, b["html_scores_dim_value"].format(dash=b["ev_dash"]))
                for name in b["html_scores_dims"]
            ],
            "text": lock_text + on_platform,
        },
    }


def _risks(context, b):
    risks = context.risks
    names = {entry.resource_type_id: entry.name for entry in context.resources}
    wide = sum(1 for risk in risks if risk.scope_wide)
    if not risks:
        caption = b["html_risks_none_caption"]
    else:
        count = _count(b, "html_risks_count", len(risks))
        if not wide:
            caption = b["html_risks_caption_only_count"].format(count=count)
        else:
            if wide == len(risks):
                key = "html_risks_wide_sole" if wide == 1 else "html_risks_wide_all"
            else:
                key = "html_risks_wide_one" if wide == 1 else "html_risks_wide_many"
            caption = b["html_risks_caption"].format(
                count=count, scope=b[key].format(n=wide)
            )

    def affected(risk):
        if risk.scope_wide:
            return None
        listed = sorted(
            names.get(type_id, b["ev_dash"]) for type_id in risk.resource_type_ids
        )
        text = ", ".join(listed[:AFFECTED_SHOWN])
        if len(listed) > AFFECTED_SHOWN:
            text += " " + b["html_risks_more"].format(n=len(listed) - AFFECTED_SHOWN)
        return text

    groups, position = [], 0
    for severity in SEVERITIES:
        members = [risk for risk in risks if risk.severity == severity]
        rows = []
        for risk in members:
            position += 1
            rows.append(
                {
                    "id": f"risk-{position}",
                    "code": risk.code,
                    "name": risk.name,
                    "description": risk.description,
                    "severity": severity,
                    "label": severity_label(severity, b),
                    "affected": affected(risk),
                }
            )
        if rows:
            groups.append(
                {
                    "heading": b["risk_group"].format(
                        severity=severity_label(severity, b), n=len(rows)
                    ),
                    "rows": rows,
                }
            )
    return {
        "heading": b["html_nav_risks"],
        "caption": caption,
        "headers": (
            b["risk_th_code"],
            b["risk_th_risk"],
            b["risk_th_severity"],
            b["html_risks_th_affected"],
        ),
        "scope_wide": b["risk_scope_wide"],
        "groups": groups,
        "open": len(risks) <= OPEN_RISKS_UP_TO,
        "footnote": text_parts(
            b["html_risks_footnote"], {"link": (b["html_nav_about"], ABOUT_URL)}
        ),
        "empty": (
            {
                "title": b["bs_find_risks_none"],
                "text": b[
                    (
                        "html_risks_empty_clean"
                        if context.total_resources
                        else "html_risks_empty_scope"
                    )
                ],
            }
            if not risks
            else None
        ),
    }


def _inventory(context, b):
    total = context.total_resources
    scope = context.scope
    regions = {}
    for entry in context.resources:
        for location, count in entry.location_counts.items():
            regions[location] = regions.get(location, 0) + count
    ranked = sorted(regions.items(), key=lambda item: (-item[1], item[0]))
    shown = [
        f"{name} {percent(_share(count, total))}"
        for name, count in ranked[:REGIONS_SHOWN]
    ]
    if len(ranked) > REGIONS_SHOWN:
        shown.append(b["html_risks_more"].format(n=len(ranked) - REGIONS_SHOWN))
    caption_parts = [_count(b, "scope_resources", total)]
    if total:
        caption_parts += [
            _count(b, "scope_types", len(context.resources)),
            b["html_inv_regions"].format(
                regions=_count(b, "scope_regions", len(regions)),
                shares=", ".join(shown),
            ),
        ]
        if scope.kind == "tfstate":
            caption_parts.append(b["html_inv_not_managed"])

    if scope.kind == "azure":
        empty = b["html_inv_empty_resource_group"].format(
            scope=scope.resource_group or b["ev_dash"]
        )
    elif scope.kind == "aws":
        empty = b["html_inv_empty_region"].format(scope=scope.region or b["ev_dash"])
    else:
        empty = b["html_inv_empty_tfstate"].format(
            scope=scope.file_name or b["ev_dash"]
        )

    return {
        "heading": b["es_heading_resource_inventory"],
        "caption": " · ".join(caption_parts),
        "headers": (
            b["res_th_resource_type"],
            b["html_inv_th_locations"],
            b["res_th_share"],
            b["res_th_resources"],
        ),
        "rows": [
            {
                "name": entry.name,
                "initials": initials(entry.name),
                "icon": report_icon(context.report_path, entry.icon_rel),
                "locations": ", ".join(
                    f"{name} · {count}"
                    for name, count in sorted(
                        entry.location_counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
                "share": percent(_share(entry.resources, total)),
                "share_value": round(_share(entry.resources, total)),
                "count": entry.resources,
            }
            for entry in context.resources
        ],
        "total": (b["res_total"], total),
        "footnote": b["es_res_caption"],
        "empty": (
            {"title": b["html_inv_empty_title"], "text": empty} if not total else None
        ),
    }


def _alternatives(context, b):
    entries = sorted(
        context.alternatives,
        key=lambda entry: (
            not entry.alternatives,
            -len(entry.alternatives),
            -entry.resources,
            entry.name.lower(),
        ),
    )
    total = sum(len(entry.alternatives) for entry in entries)
    with_alternatives = sum(1 for entry in entries if entry.alternatives)
    provenance = context.provenance

    groups = []
    for position, entry in enumerate(entries):
        rows = [
            {
                "name": alt.name,
                "vendor": alt.vendor or b["ev_dash"],
                "cc": alt.country or "",
                "os": alt.open_source,
                "sp": alt.support_plan,
                "extra": index >= ALT_VISIBLE,
            }
            for index, alt in enumerate(entry.alternatives)
        ]
        resources = _count(b, "scope_resources", entry.resources)
        hidden = max(0, len(rows) - ALT_VISIBLE)
        groups.append(
            {
                "name": entry.name,
                "initials": initials(entry.name),
                "icon": report_icon(context.report_path, entry.icon_rel),
                "meta": (
                    b["html_alt_meta"].format(
                        resources=resources,
                        alternatives=_count(b, "html_alt_n", len(rows)),
                    )
                    if rows
                    else b["html_alt_meta_none"].format(resources=resources)
                ),
                "rows": rows,
                "open": position < ALT_OPEN and bool(rows),
                "hidden": hidden,
                "more": (_count_more(b, hidden) if hidden else None),
            }
        )

    if not entries:
        caption = _count(b, "html_alt_n", 0)
    else:
        key = "html_alt_caption" if len(entries) != 1 else "html_alt_caption_one_type"
        caption = b[key].format(
            alternatives=_count(b, "html_alt_n", total),
            with_alternatives=with_alternatives,
            types=len(entries),
        )
    return {
        "heading": b["es_heading_alternative_technologies"],
        "caption": caption,
        "total": total,
        "search": b["html_alt_search"],
        "filters": [
            ("os", b["html_filter_os"]),
            ("sp", b["html_filter_sp"]),
            ("eu", b["html_filter_eu"]),
        ],
        "headers": (b["html_alt_th_name"], b["html_filter_os"], b["html_filter_sp"]),
        "yes": b["html_yes"],
        "no": b["html_no"],
        "no_match": b["html_alt_no_match"],
        "groups": groups,
        "footnote": b["html_alt_footnote"].format(
            version=provenance.catalogue_version or b["ev_catalogue_unknown"]
        ),
        "empty": (
            {"title": b["html_alt_empty_title"], "text": b["html_alt_empty_text"]}
            if not entries
            else None
        ),
    }


def _count_more(b, n):
    return b["html_alt_more_one"] if n == 1 else b["html_alt_more_many"].format(n=n)


def _platform_cell(context, b):
    provenance = context.provenance
    if not provenance.uploaded:
        if provenance.upload_reason:
            return [
                b["html_platform_not_uploaded"].format(reason=provenance.upload_reason)
            ]
        return [b["ev_platform_none"]]
    parts = [b["ev_platform_uploaded"]]
    if provenance.platform_url:
        label = provenance.platform_url.split("://", 1)[-1]
        if len(label) > 40:
            label = label[:40] + "…"
        return [f"{parts[0]}: ", (label, provenance.platform_url)]
    if provenance.assessment_id:
        parts.append(b["fa_uploaded_id"].format(assessment_id=provenance.assessment_id))
    return [" · ".join(parts)]


def _scope_table(context, b):
    rows = scope_rows(context, b)
    if context.scope.kind != "tfstate":
        return rows
    # A state file names no tenant, subscription or resource group of a run: its
    # own file and state, and the regions of what it holds
    dropped = {b["ev_subscriptions"], b["ev_resource_groups"]}
    rows = [row for row in rows if row[0] not in dropped]
    if not any(row[0] == b["ev_regions"] for row in rows):
        regions = ", ".join(sorted(context.regions)) or b["ev_not_determinable"]
        rows.insert(-1, (b["ev_regions"], regions, False))
    return rows


def _about(context, egress, b):
    provenance = context.provenance
    tfstate = context.scope.kind == "tfstate"
    sha = provenance.catalogue_sha256
    glossary = [
        entry
        for entry in b["html_glossary"]
        if (entry["id"] != "aws" or context.cloud_service_provider == 2)
        and (entry["id"] not in ("egress", "free_tier", "gib_gb") or egress is not None)
    ]
    phases = [
        (
            name,
            b["html_method_discovery_tfstate"] if tfstate and index == 0 else text,
        )
        for index, (name, text) in enumerate(b["es_method_phases"])
    ]
    return {
        "heading": b["html_nav_about"],
        "caption": b["html_about_caption"],
        "document": {
            "heading": b["html_about_document"],
            "rows": [
                (b["ev_document_id"], context.document_id, True),
                (b["ev_document_type"], b["ev_executive_report"], False),
                (b["ev_language"], b["language_name"], False),
                (
                    b["ev_collected_by"],
                    b["ev_collected_by_value"].format(
                        version=provenance.collector_version
                    ),
                    False,
                ),
                (
                    b["ev_generated_at"],
                    provenance.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
                    False,
                ),
            ],
        },
        "scope": {
            "heading": b["ev_assessment_scope_heading"],
            "rows": _scope_table(context, b),
        },
        "provenance": {
            "heading": b["ev_provenance_heading"],
            "run_id": (
                (b["ev_run_id"], provenance.run_id) if provenance.run_id else None
            ),
            "catalogue": (
                b["ev_catalogue_label"],
                provenance.catalogue_version or b["ev_catalogue_unknown"],
                b["html_catalogue_sha"].format(sha=sha[:12]) if sha else None,
            ),
            "platform": (b["ev_platform_label"], _platform_cell(context, b)),
        },
        "stored": b["ev_stored_footnote"].format(
            run=provenance.run_short_id or b["ev_dash"]
        ),
        "purpose": {
            "summary": b["html_about_purpose"],
            "meta": b["html_about_phases_meta"].format(n=len(phases)),
            "paragraphs": [b["es_purpose_block"], b["html_method_lead"]],
            "phases": phases,
            "closing": b["html_method_catalogue"],
        },
        "limitations": {
            "summary": b["es_heading_limitations"],
            "paragraphs": [b["err_limitations"], b["html_limitations_data"]],
        },
        "severity": {
            "summary": b["es_heading_severity"],
            "meta": b["html_about_levels_meta"].format(n=len(SEVERITIES)),
            "headers": (b["risk_th_severity"], b["es_severity_th_meaning"]),
            "rows": [
                (level, severity_label(level, b), b[f"es_severity_{level}"])
                for level in SEVERITIES
            ],
            "note": b["es_severity_note"],
        },
        "glossary": {
            "summary": b["es_heading_glossary"],
            "meta": b["html_about_terms_meta"].format(n=len(glossary)),
            "entries": [
                (entry["term"], entry["definition"])
                for entry in sorted(glossary, key=lambda e: e["term"].lower())
            ],
        },
    }


def _notices(context, egress, b):
    provenance = context.provenance
    icons = None
    if context.cloud_service_provider == 2:
        icons = text_parts(
            b["notices_icons_aws"],
            {
                "icons_url": (b["html_label_aws_icons"], AWS_ICONS_URL),
                "guidelines_url": (b["html_label_aws_guidelines"], AWS_TRADEMARK_URL),
            },
        )
    elif context.cloud_service_provider == 1:
        icons = text_parts(
            b["notices_icons_azure"],
            {"icons_url": (b["html_label_azure_icons"], AZURE_ICONS_URL)},
        )
    if context.scope.kind == "tfstate":
        figures = b["html_figures_tfstate"]
    else:
        figures = b["html_figures_egress" if egress is not None else "html_figures"]
    return {
        "disclaimer": {
            "heading": b["notices_disclaimer_heading"],
            "paragraphs": [
                [b["notices_disclaimer"]],
                [f"{b['notices_generated']} {b['html_notice_advice']}"],
                *([icons] if icons else []),
            ],
        },
        "blocks": [
            {
                "heading": b["notices_trademarks_heading"],
                "parts": [
                    f"{b['notices_trademark_own']} {b['notices_trademark_others']}"
                ],
            },
            {"heading": b["html_figures_heading"], "parts": [figures]},
            {
                "heading": b["notices_software_heading"],
                "parts": text_parts(
                    b["html_software"],
                    {"repository_link": (b["html_label_repository"], REPOSITORY_URL)},
                ),
            },
        ],
        "copyright": b["notices_copyright"].format(year=provenance.generated_at.year),
        "ids": b["html_footer_ids"].format(
            document_id=context.document_id, version=provenance.collector_version
        ),
    }


def _tour(context, egress, linked, b):
    tfstate = context.scope.kind == "tfstate"
    sections = [b["html_tour_sections"]]
    if not linked:
        sections.append(b["html_tour_sections_lock"])
    report = [b["html_tour_report"]]
    if linked:
        report.append(b["html_tour_report_platform"])
    if egress is not None:
        report.append(
            b[
                (
                    "html_tour_download_both_platform"
                    if linked
                    else "html_tour_download_both"
                )
            ]
        )
    else:
        report.append(b["html_tour_download_one"])
    scores = (
        b["html_tour_scores_platform"]
        if linked
        else f"{b['html_tour_scores_available']} {b['html_hint_text']}"
    )
    steps = [
        (".secnav", b["html_tour_sections_title"], " ".join(sections), "right"),
        (".titlebar", b["html_tour_report_title"], " ".join(report), "bottom"),
        (
            "#overview .overview",
            b["html_tour_overview_title"],
            b["html_tour_overview_tfstate" if tfstate else "html_tour_overview"],
            "bottom",
        ),
        ("#scores .scores", b["html_tour_scores_title"], scores, "bottom"),
        (
            "#risks .card-head",
            b["html_tour_risks_title"],
            b["html_tour_risks"],
            "bottom",
        ),
        (
            "#inventory .card-head",
            b["html_tour_inventory_title"],
            b["html_tour_inventory"],
            "bottom",
        ),
        (
            "#alternatives .card-head",
            b["html_tour_alternatives_title"],
            b["html_tour_alternatives"],
            "bottom",
        ),
    ]
    if egress is not None:
        steps.append(
            (
                "#egress .kpi-row",
                b["html_tour_egress_title"],
                b["html_tour_egress"],
                "bottom",
            )
        )
    steps.append(
        ("#about .kv-grid", b["html_tour_about_title"], b["html_tour_about"], "top")
    )
    return [
        {"element": element, "title": title, "description": text, "side": side}
        for element, title, text, side in steps
    ]


def html_sections(context, egress=None, b=BLOCKS):
    # Everything the page says, as plain data; the template only lays it out
    provenance = context.provenance
    platform_url = provenance.platform_url if provenance.uploaded else None
    alternatives = _alternatives(context, b)
    nav = [
        {"id": "overview", "label": b["html_nav_overview"]},
        {
            "id": "scores",
            "label": b["html_nav_scores"],
            "lock": b["html_lock_label"] if not platform_url else None,
        },
        {"id": "risks", "label": b["html_nav_risks"], "count": len(context.risks)},
        {
            "id": "inventory",
            "label": b["es_heading_resource_inventory"],
            "count": context.total_resources,
        },
        {
            "id": "alternatives",
            "label": b["es_heading_alternative_technologies"],
            "count": alternatives["total"],
        },
    ]
    if egress is not None:
        nav.append({"id": "egress", "label": b["ev_egress_report"]})
    nav.append({"id": "about", "label": b["html_nav_about"]})

    return {
        "title": f"{b['ev_executive_report']} – {context.name}",
        "language": "en",
        "dash": b["ev_dash"],
        "header": {
            "tag": b["cover_edition_value"],
            "logo_link": b["html_logo_link"],
            "help": b["html_help"],
            "help_aria": b["html_help_aria"],
            "tour": {
                "next": b["html_tour_next"],
                "back": b["html_tour_back"],
                "done": b["html_tour_done"],
                "progress": b["html_tour_progress"],
            },
        },
        "titlebar": _title(context, egress is not None, b),
        "download": b["html_download"],
        "open_platform": b["html_open_platform"],
        "nav": nav,
        "nav_label": b["html_nav_label"],
        "overview": {
            "scope": _scope_card(context, b),
            "risks": _risks_card(context, b),
            "costs": _costs_card(context, b),
        },
        "scores": _scores(alternatives["total"], platform_url, b),
        "risks": _risks(context, b),
        "inventory": _inventory(context, b),
        "alternatives": alternatives,
        "egress": egress_page(context, egress, b) if egress is not None else None,
        "about": _about(context, egress, b),
        "notices": _notices(context, egress, b),
        "tour": _tour(context, egress, bool(platform_url), b),
    }
