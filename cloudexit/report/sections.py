from xml.sax.saxutils import escape, quoteattr

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .brief_summary import cost_finding
from .evidence import link_markup
from .flow import section
from .fonts import FONT, FONT_BOLD, FONT_MONO
from .placeholder import LabelWithPlaceholder
from .helpers import (
    cost_problem,
    format_amount,
    format_change,
    format_money,
    severity_label,
    strategy_definition,
    strategy_name,
)
from .palette import (
    HAIRLINE,
    HOTSPOT,
    HOTSPOT_BACKGROUND,
    HOTSPOT_COUNT,
    INK,
    MUTED,
    RULE,
    SEVERITIES,
    SEVERITY_COLORS,
    SEVERITY_PIPS,
)
from .table_style import SECONDARY, data_table, header_cell, summary_strip, text_style

ALT_BODY = text_style("AltBody", 8.5, 11)
ALT_BODY_BOLD = text_style("AltBodyBold", 8.5, 11, bold=True)
ALT_BODY_RIGHT = text_style("AltBodyRight", 8.5, 11, alignment=TA_RIGHT)
ALT_MUTED = text_style("AltMuted", 8.5, 11, MUTED)
ALT_SMALL = text_style("AltSmall", 7.5, 9.5, MUTED)
ALT_CAPTION = text_style("AltCaption", 7.5, 10, MUTED)
RES_TOTAL_RIGHT = text_style("ResTotalRight", 8.5, 11, bold=True, alignment=TA_RIGHT)
RES_GROUP = text_style("ResGroup", 7.6, 9.5, SECONDARY, bold=True)
RISK_CODE = text_style("RiskCode", 8, 11, SECONDARY).clone(
    "RiskCode", fontName=FONT_MONO
)
RISK_MUTED_RIGHT = text_style("RiskMutedRight", 8.5, 11, MUTED, alignment=TA_RIGHT)
COMMAND = text_style("CommandLine", 8.5, 12, INK).clone(
    "CommandLine", fontName=FONT_MONO, leftIndent=8, spaceBefore=4, spaceAfter=8
)
GLOSSARY_TERM_WIDTH = 4.2 * cm
RISK_BAR_MIN_SEGMENT = 62
ALT_EXAMPLES = 3
PRODUCT_SITES = (
    ("escapecloud", "https://escapecloud.io"),
    ("exitcloud", "https://exitcloud.io"),
)
BOOK_DEMO_URL = "https://escapecloud.io/book-demo/"
AWS_ICONS_URL = "https://aws.amazon.com/architecture/icons/"
AWS_TRADEMARK_URL = "https://aws.amazon.com/trademark-guidelines/"
AZURE_ICONS_URL = "https://learn.microsoft.com/en-us/azure/architecture/icons/"
REPOSITORY_URL = "https://github.com/escapecloud/cloudexit/"
LICENCE_URL = "https://github.com/escapecloud/cloudexit/blob/main/LICENSE"


def _text(value, style=ALT_BODY):
    return Paragraph(escape(str(value)), style)


def _percent(part, total):
    return f"{(part / total * 100) if total else 0:.0f} %"


def _count_phrase(b, key, n):
    return b[f"{key}_one"] if n == 1 else b[f"{key}_many"].format(n=n)


def _no_rows_table(header, col_widths, appearance, message):
    message = [_text(message, ALT_MUTED)] + [""] * (len(header) - 1)
    return data_table(
        [header, message], col_widths, appearance, [("SPAN", (0, 1), (-1, 1))]
    )


def _group_row(rows, extra, label, columns):
    # A spanned label row that never ends up alone at the bottom of a page.
    index = len(rows)
    rows.append([_text(label, RES_GROUP)] + [""] * (columns - 1))
    extra.extend(
        [
            ("SPAN", (0, index), (-1, index)),
            ("TOPPADDING", (0, index), (-1, index), 9),
            ("BOTTOMPADDING", (0, index), (-1, index), 2),
            ("LINEBELOW", (0, index), (-1, index), 0.6, HexColor(RULE)),
            ("NOSPLIT", (0, index), (-1, index + 1)),
        ]
    )


def _type_name_cell(entry):
    if not entry.icon:
        return LabelWithPlaceholder(
            entry.name, f"<b>{escape(entry.name)}</b>", ALT_BODY
        )
    return Paragraph(
        f'<img src={quoteattr(entry.icon)} width="11" height="11" '
        f'valign="middle"/> <b>{escape(entry.name)}</b>',
        ALT_BODY,
    )


# Introduction
def scope_boundary(context, b):
    scope = context.scope
    counts = b["scope_counts"].format(
        resources=_count_phrase(b, "scope_resources", context.total_resources),
        types=_count_phrase(b, "scope_types", len(context.resources)),
    )
    values = {
        "counts": f"<b>{counts}</b>",
        "regions": _count_phrase(b, "scope_regions", len(context.regions)),
    }
    if scope.kind == "azure":
        return b["scope_boundary_azure"].format(
            resource_group=f"<b>{escape(scope.resource_group or b['ev_dash'])}</b>",
            **values,
        )
    if scope.kind == "aws":
        return b["scope_boundary_aws"].format(
            region=f"<b>{escape(scope.region or b['ev_dash'])}</b>", **values
        )
    return b["scope_boundary_tfstate"].format(
        file=f"<b>{escape(scope.file_name or b['ev_dash'])}</b>",
        provider=escape(context.provider),
        **values,
    )


def scope_strategy(context, b):
    strategy = f"<b>{escape(strategy_name(context.strategy_code, b))}</b>"
    definition = strategy_definition(context.strategy_code, b)
    if not definition:
        return b["scope_strategy_plain"].format(strategy=strategy)
    return b["scope_strategy"].format(strategy=strategy, definition=escape(definition))


def _method_list(b, styles):
    items = [
        ListItem(
            Paragraph(f"<b>{escape(name)}</b> {escape(text)}", styles["ContentStyle"]),
            leftIndent=14,
        )
        for name, text in b["es_method_phases"]
    ]
    return ListFlowable(
        items,
        bulletType="bullet",
        start="•",
        bulletFontName=FONT,
        bulletFontSize=9,
        leftIndent=14,
    )


def introduction(context, styles, b):
    body = styles["ContentStyle"]
    return [
        Paragraph(b["es_heading_introduction"], styles["Heading1"]),
        Paragraph(b["es_heading_purpose"], styles["Heading2"]),
        Paragraph(b["es_purpose_block"], body),
        Paragraph(b["es_heading_scope"], styles["Heading2"]),
        Paragraph(scope_boundary(context, b), body),
        Spacer(1, 6),
        Paragraph(scope_strategy(context, b), body),
        Paragraph(b["es_heading_method"], styles["Heading2"]),
        Paragraph(b["es_method_lead"], body),
        Paragraph(b["es_method_phases_lead"], body),
        _method_list(b, styles),
        Spacer(1, 6),
        Paragraph(b["es_method_catalogue"], body),
        Paragraph(b["es_heading_limitations"], styles["Heading2"]),
        Paragraph(b["err_limitations"], body),
        Spacer(1, 6),
        Paragraph(b["err_limitations_data"], body),
    ]


# Costs
def _cost_table(context, b):
    currency = context.costs[0].currency
    appearance = context.appearance
    rows = [
        [
            b["cost_th_month"],
            header_cell(
                b["cost_th_cost"].format(currency=escape(currency)),
                appearance,
                TA_RIGHT,
            ),
            header_cell(b["cost_th_change"], appearance, TA_RIGHT),
        ]
    ]
    previous = None
    for entry in context.costs:
        month = entry.month
        if not entry.final:
            month += f' <font color="{MUTED}">· {escape(b["cost_provisional"])}</font>'
        rows.append(
            [
                Paragraph(month, ALT_BODY),
                _text(format_amount(entry.amount), ALT_BODY_RIGHT),
                _text(format_change(previous, entry.amount), ALT_BODY_RIGHT),
            ]
        )
        previous = entry.amount
    caption = b["cost_caption"].format(currency=escape(currency))
    if not all(entry.final for entry in context.costs):
        caption += " " + b["cost_caption_provisional"]
    total_row = len(rows)
    rows.append(
        [
            _text(b["cost_total"], ALT_BODY_BOLD),
            _text(
                format_amount(sum(entry.amount for entry in context.costs)),
                RES_TOTAL_RIGHT,
            ),
            "",
        ]
    )
    return [
        data_table(
            rows,
            [5.0 * cm, 5.5 * cm, 5.5 * cm],
            appearance,
            [
                ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, HexColor(INK)),
                ("NOSPLIT", (0, total_row - 1), (-1, total_row)),
            ],
        ),
        Spacer(1, 6),
        Paragraph(caption, ALT_CAPTION),
    ]


def egress_note(context, b):
    # The companion document, when the run has an egress estimate
    if not context.egress_document_id:
        return None
    return b["es_costs_egress_note"].format(document_id=context.egress_document_id)


def costs_overview(context, facts, styles, b):
    head = [
        Paragraph(b["es_heading_costs_overview"], styles["Heading1"]),
        Paragraph(cost_finding(facts, b), styles["ContentStyle"]),
        Spacer(1, 8),
    ]
    note = egress_note(context, b)
    closing = [Paragraph(escape(note), styles["ContentStyle"])] if note else []
    problem = cost_problem(context, b)
    if problem is not None:
        return section(head, closing)
    entries = context.costs
    total = sum(entry.amount for entry in entries)
    latest = entries[-1]
    before = entries[-2] if len(entries) > 1 else None
    currency = latest.currency
    items = [
        summary_strip(
            [
                (b["cost_stat_total"], format_money(total, currency), INK),
                (
                    b["cost_stat_average"],
                    format_money(total / len(entries), currency),
                    INK,
                ),
                (
                    b["cost_stat_latest"].format(month=latest.month),
                    format_money(latest.amount, currency),
                    INK,
                ),
                (
                    b["cost_stat_change"].format(
                        month=before.month if before else b["ev_dash"]
                    ),
                    format_change(before.amount if before else None, latest.amount),
                    INK,
                ),
            ]
        ),
        Spacer(1, 12),
        *_cost_table(context, b),
        Spacer(1, 12),
        Paragraph(b["es_cost_note"], styles["ContentStyle"]),
        *closing,
    ]
    return section(head, items)


# Resource inventory
def resource_inventory(context, styles, b):
    appearance = context.appearance
    head = [
        Paragraph(b["es_heading_resource_inventory"], styles["Heading1"]),
        Paragraph(b["es_res_lead"], styles["ContentStyle"]),
        Spacer(1, 12),
    ]
    header = [
        b["res_th_resource_type"],
        header_cell(b["res_th_regions"], appearance, TA_RIGHT),
        header_cell(b["res_th_resources"], appearance, TA_RIGHT),
        header_cell(b["res_th_share"], appearance, TA_RIGHT),
    ]
    widths = [9.4 * cm, 2.1 * cm, 2.3 * cm, 2.2 * cm]
    if not context.resources:
        return section(
            head,
            [_no_rows_table(header, widths, appearance, b["tbl_res_no_resources"])],
        )

    total = context.total_resources
    rows = [header]
    for entry in context.resources:
        rows.append(
            [
                _type_name_cell(entry),
                _text(len(entry.locations), ALT_BODY_RIGHT),
                _text(entry.resources, ALT_BODY_RIGHT),
                _text(_percent(entry.resources, total), ALT_BODY_RIGHT),
            ]
        )
    total_row = len(rows)
    rows.append(
        [
            _text(b["res_total"], ALT_BODY_BOLD),
            _text(len(context.regions), RES_TOTAL_RIGHT),
            _text(total, RES_TOTAL_RIGHT),
            _text(_percent(total, total), RES_TOTAL_RIGHT),
        ]
    )
    table = data_table(
        rows,
        widths,
        appearance,
        [
            ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, HexColor(INK)),
            ("NOSPLIT", (0, total_row - 1), (-1, total_row)),
        ],
    )
    return section(
        head,
        [
            summary_strip(
                [
                    (b["res_stat_resources"], str(total), INK),
                    (b["res_stat_types"], str(len(context.resources)), INK),
                    (b["res_stat_regions"], str(len(context.regions)), INK),
                ]
            ),
            Spacer(1, 12),
            table,
            Spacer(1, 6),
            Paragraph(b["es_res_caption"], ALT_CAPTION),
        ],
    )


# Risk inventory
class SeverityBadge(Flowable):
    def __init__(self, severity, label):
        Flowable.__init__(self)
        self.severity = severity
        self.label = label
        self.width = 2.3 * cm
        self.height = 10

    def wrap(self, availWidth, availHeight):
        return min(self.width, availWidth), self.height

    def draw(self):
        canvas = self.canv
        size, gap = 4.6, 1.4
        color = HexColor(SEVERITY_COLORS.get(self.severity, MUTED))
        pips = SEVERITY_PIPS.get(self.severity, 0)
        y = (self.height - size) / 2
        for index in range(4):
            x = index * (size + gap)
            if index < pips:
                canvas.setFillColor(color)
                canvas.rect(x, y, size, size, stroke=0, fill=1)
            else:
                canvas.setStrokeColor(HexColor(RULE))
                canvas.setLineWidth(0.6)
                canvas.rect(x, y, size, size, stroke=1, fill=0)
        canvas.setFillColor(HexColor(INK))
        canvas.setFont(FONT, 8.2)
        canvas.drawString(4 * size + 3 * gap + 4, 2, self.label)


def _segment_widths(counts, available, minimum):
    # Proportional, but no non-empty segment narrower than the minimum.
    fixed = {}
    while True:
        free = [key for key in counts if key not in fixed]
        if not free:
            return fixed
        rest = available - sum(fixed.values())
        total = sum(counts[key] for key in free)
        widths = {key: rest * counts[key] / total for key in free}
        small = [key for key, width in widths.items() if width < minimum]
        if not small:
            return {**fixed, **widths}
        for key in small:
            fixed[key] = minimum


def _severity_bar(risks, b):
    width, bar_height, gap = 16 * cm, 15, 1.5
    counts = {
        severity: sum(1 for risk in risks if risk.severity == severity)
        for severity in SEVERITIES
    }
    counts = {severity: count for severity, count in counts.items() if count}
    drawing = Drawing(width, 36)
    if counts:
        widths = _segment_widths(
            counts, width - gap * (len(counts) - 1), RISK_BAR_MIN_SEGMENT
        )
        x = 0
        for severity, count in counts.items():
            segment = widths[severity]
            drawing.add(
                Rect(
                    x,
                    21,
                    segment,
                    bar_height,
                    fillColor=HexColor(SEVERITY_COLORS[severity]),
                    strokeColor=None,
                )
            )
            label = f"{count} {severity_label(severity, b)}"
            if stringWidth(label, FONT_BOLD, 7.8) + 8 > segment:
                label = str(count)
            drawing.add(
                String(
                    x + segment / 2,
                    25.7,
                    label,
                    fontName=FONT_BOLD,
                    fontSize=7.8,
                    fillColor=HexColor(INK) if severity == "medium" else white,
                    textAnchor="middle",
                )
            )
            x += segment + gap
    drawing.add(
        String(
            0,
            8,
            b["risk_bar_caption"],
            fontName=FONT,
            fontSize=7,
            fillColor=HexColor(MUTED),
        )
    )
    return drawing


def _risk_overview(context, b):
    risks = context.risks
    scope = {entry.resource_type_id for entry in context.resources}
    affected = {type_id for risk in risks for type_id in risk.resource_type_ids}
    return [
        summary_strip(
            [
                (b["risk_stat_unique"], str(len(risks)), INK),
                (
                    b["risk_stat_critical_high"],
                    str(
                        sum(
                            1 for risk in risks if risk.severity in ("critical", "high")
                        )
                    ),
                    SEVERITY_COLORS["high"],
                ),
                (
                    b["risk_stat_affected"],
                    b["risk_stat_affected_value"].format(
                        a=len(affected & scope), n=len(scope)
                    ),
                    INK,
                ),
                (
                    b["risk_stat_scope_wide"],
                    str(sum(1 for risk in risks if risk.scope_wide)),
                    INK,
                ),
            ]
        ),
        Spacer(1, 12),
        _severity_bar(risks, b),
        Spacer(1, 12),
    ]


def risk_inventory(context, styles, b):
    appearance = context.appearance
    head = [
        Paragraph(b["es_heading_risk_inventory"], styles["Heading1"]),
        Paragraph(b["es_risk_lead"], styles["ContentStyle"]),
        Spacer(1, 12),
    ]
    header = [
        b["risk_th_code"],
        b["risk_th_risk"],
        b["risk_th_severity"],
        header_cell(b["risk_th_affected"], appearance, TA_RIGHT),
    ]
    widths = [1.6 * cm, 9.5 * cm, 2.6 * cm, 2.3 * cm]
    if not context.risks:
        return section(
            head, [_no_rows_table(header, widths, appearance, b["tbl_risk_no_risks"])]
        )

    rows = [header]
    extra = []
    for severity in SEVERITIES:
        members = [risk for risk in context.risks if risk.severity == severity]
        if not members:
            continue
        _group_row(
            rows,
            extra,
            b["risk_group"].format(
                severity=severity_label(severity, b), n=len(members)
            ),
            len(header),
        )
        for risk in members:
            rows.append(
                [
                    _text(risk.code, RISK_CODE),
                    _text(risk.name, ALT_BODY_BOLD),
                    SeverityBadge(risk.severity, severity_label(risk.severity, b)),
                    (
                        _text(b["risk_scope_wide"], RISK_MUTED_RIGHT)
                        if risk.scope_wide
                        else _text(len(risk.resource_type_ids), ALT_BODY_RIGHT)
                    ),
                ]
            )
    return section(
        head,
        [
            *_risk_overview(context, b),
            data_table(rows, widths, appearance, extra),
            Spacer(1, 6),
            Paragraph(b["es_risk_caption"], ALT_CAPTION),
        ],
    )


# Alternative technologies
def _examples_cell(entry, b):
    cell = []
    for alt in entry.alternatives[:ALT_EXAMPLES]:
        vendor = (
            f' <font color="{MUTED}">· {escape(alt.vendor)}</font>'
            if alt.vendor
            else ""
        )
        cell.append(Paragraph(f"{escape(alt.name)}{vendor}", ALT_BODY))
        flags = [
            b[key]
            for key, present in (
                ("alt_flag_open_source", alt.open_source),
                ("alt_flag_support_plan", alt.support_plan),
            )
            if present
        ]
        if flags:
            cell.append(Paragraph(" · ".join(flags), ALT_SMALL))
    if len(entry.alternatives) > ALT_EXAMPLES:
        cell.append(
            Paragraph(
                b["alt_more"].format(k=len(entry.alternatives) - ALT_EXAMPLES),
                ALT_SMALL,
            )
        )
    return cell


def alternative_technologies(context, styles, b):
    appearance = context.appearance
    head = [
        Paragraph(b["es_heading_alternative_technologies"], styles["Heading1"]),
        Paragraph(
            b["es_alttech_lead"].format(
                strategy=escape(strategy_name(context.strategy_code, b))
            ),
            styles["ContentStyle"],
        ),
        Spacer(1, 8),
    ]
    header = [
        b["alt_th_resource_type"],
        header_cell(b["alt_th_resources"], appearance, TA_RIGHT),
        header_cell(b["alt_th_alternatives"], appearance, TA_RIGHT),
        b["alt_th_examples"],
    ]
    widths = [4.6 * cm, 2.05 * cm, 2.15 * cm, 7.2 * cm]
    if not context.alternatives:
        return section(
            head, [_no_rows_table(header, widths, appearance, b["alt_no_resources"])]
        )

    # Lock-in hot-spots first, then by resource count and name.
    entries = sorted(
        context.alternatives,
        key=lambda entry: (
            bool(entry.alternatives),
            -entry.resources,
            entry.name.lower(),
        ),
    )
    hotspots = sum(1 for entry in entries if not entry.alternatives)
    items = []
    if not any(entry.alternatives for entry in entries):
        items += [Paragraph(b["alt_empty_set"], styles["ContentStyle"]), Spacer(1, 6)]
    items += [
        summary_strip(
            [
                (
                    b["alt_stat_types"],
                    b["alt_stat_types_value"].format(
                        a=len(entries) - hotspots, n=len(entries)
                    ),
                    INK,
                ),
                (
                    b["alt_stat_alternatives"],
                    str(sum(len(entry.alternatives) for entry in entries)),
                    INK,
                ),
                (b["alt_stat_hotspots"], str(hotspots), HOTSPOT_COUNT),
            ]
        ),
        Spacer(1, 12),
    ]

    rows = [header]
    extra = []
    for entry in entries:
        index = len(rows)
        row = [
            _type_name_cell(entry),
            _text(entry.resources, ALT_BODY_RIGHT),
            _text(len(entry.alternatives), ALT_BODY_RIGHT),
        ]
        if entry.alternatives:
            row.append(_examples_cell(entry, b))
        else:
            row.append(Paragraph(b["alt_hotspot_short"], ALT_BODY))
            extra.extend(
                [
                    (
                        "BACKGROUND",
                        (0, index),
                        (-1, index),
                        HexColor(HOTSPOT_BACKGROUND),
                    ),
                    ("LINEBEFORE", (0, index), (0, index), 3, HexColor(HOTSPOT)),
                ]
            )
        rows.append(row)
    items += [
        data_table(rows, widths, appearance, extra),
        Spacer(1, 6),
        Paragraph(b["es_alttech_caption"], ALT_CAPTION),
    ]
    return section(head, items)


# Appendices
def severity_levels(context, styles, b):
    rows = [[b["risk_th_severity"], b["es_severity_th_meaning"]]]
    for severity in SEVERITIES:
        rows.append(
            [
                SeverityBadge(severity, severity_label(severity, b)),
                _text(b[f"es_severity_{severity}"], ALT_BODY),
            ]
        )
    return [
        Paragraph(b["es_heading_severity"], styles["Heading1"]),
        Paragraph(b["es_severity_lead"], styles["ContentStyle"]),
        Paragraph(b["es_severity_note"], styles["ContentStyle"]),
        Spacer(1, 10),
        data_table(rows, [3.6 * cm, 12.4 * cm], context.appearance),
    ]


def glossary_entries(context, b):
    # The AWS entry is only listed for AWS runs.
    return [
        entry
        for entry in b["glossary"]
        if entry["id"] != "aws" or context.cloud_service_provider == 2
    ]


def glossary(context, styles, b):
    entries = sorted(
        glossary_entries(context, b), key=lambda entry: entry["term"].lower()
    )
    table = Table(
        [
            [_text(entry["term"], ALT_BODY_BOLD), _text(entry["definition"])]
            for entry in entries
        ],
        colWidths=[GLOSSARY_TERM_WIDTH, 16 * cm - GLOSSARY_TERM_WIDTH],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                (
                    "LINEABOVE",
                    (0, 0),
                    (-1, 0),
                    1,
                    HexColor(context.appearance.header_row_color),
                ),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, HexColor(HAIRLINE)),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [
        Paragraph(b["es_heading_glossary"], styles["Heading1"]),
        Paragraph(b["es_glossary_block"], styles["ContentStyle"]),
        Spacer(1, 12),
        table,
    ]


# Closing page
def full_assessment(context, styles, b):
    body = styles["ContentStyle"]
    provenance = context.provenance
    if provenance.uploaded:
        content = [
            Paragraph(b["fa_heading_uploaded"], styles["Heading1"]),
            Paragraph(escape(b["fa_uploaded"]), body),
        ]
        if provenance.platform_url:
            link = link_markup(
                provenance.platform_url, context.appearance.heading2_color
            )
            content.append(Paragraph(b["fa_uploaded_link"].format(link=link), body))
        elif provenance.assessment_id:
            content.append(
                Paragraph(
                    escape(
                        b["fa_uploaded_id"].format(
                            assessment_id=provenance.assessment_id
                        )
                    ),
                    body,
                )
            )
        return content

    rows = [[_text(term, ALT_BODY_BOLD), _text(text)] for term, text in b["fa_items"]]
    table = Table(
        rows,
        colWidths=[GLOSSARY_TERM_WIDTH, 16 * cm - GLOSSARY_TERM_WIDTH],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                (
                    "LINEABOVE",
                    (0, 0),
                    (-1, 0),
                    1,
                    HexColor(context.appearance.header_row_color),
                ),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, HexColor(HAIRLINE)),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    color = context.appearance.heading2_color
    sites = b["fa_sites"].format(
        **{name: link_markup(url, color) for name, url in PRODUCT_SITES}
    )
    return [
        Paragraph(b["fa_heading"], styles["Heading1"]),
        Paragraph(escape(b["fa_lead"]), body),
        Spacer(1, 6),
        table,
        Spacer(1, 6),
        KeepTogether(
            [
                Paragraph(b["fa_how_heading"], styles["Heading2"]),
                Paragraph(escape(b["fa_how"]), body),
                Paragraph(escape(b["fa_command"]), COMMAND),
                Paragraph(sites, body),
            ]
        ),
    ]
