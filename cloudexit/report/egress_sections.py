from xml.sax.saxutils import escape, quoteattr

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from cloudexit.resources import find_asset

from .egress import (
    coverage_facts,
    cost_facts,
    introduction_facts,
    landscape_facts,
    pricing_facts,
)
from .evidence import link_markup
from .flow import section
from .palette import HAIRLINE, HOTSPOT_COUNT, INK, MUTED
from .placeholder import LabelWithPlaceholder
from .sections import GLOSSARY_TERM_WIDTH
from .table_style import data_table, header_cell, share_bar, summary_strip, text_style

BODY = text_style("EgressBody", 8.5, 11)
BODY_RIGHT = text_style("EgressBodyRight", 8.5, 11, alignment=TA_RIGHT)
BODY_BOLD = text_style("EgressBodyBold", 8.5, 11, bold=True)
BODY_BOLD_RIGHT = text_style(
    "EgressBodyBoldRight", 8.5, 11, bold=True, alignment=TA_RIGHT
)
CAPTION = text_style("EgressCaption", 7.5, 10, MUTED)


def _text(value, style=BODY):
    return Paragraph(escape(str(value)), style)


def _right_header(text, appearance):
    return header_cell(text, appearance, TA_RIGHT, split_long_words=False)


def _total_row_style(rows):
    return [
        ("LINEABOVE", (0, len(rows)), (-1, len(rows)), 0.8, HexColor(INK)),
        ("NOSPLIT", (0, len(rows) - 1), (-1, len(rows))),
    ]


def _type_cell(row, report_path):
    label = f"<b>{escape(row['label'])}</b>"
    icon = find_asset(report_path, row["icon"]) if row["icon"] else None
    if icon is None:
        # A missing icon must not cost the whole document
        return LabelWithPlaceholder(row["label"], label, BODY)
    return Paragraph(
        f'<img src={quoteattr(str(icon))} width="11" height="11" valign="middle"/> '
        f"{label}",
        BODY,
    )


def _heading(facts, styles, key="heading", style="Heading1"):
    return Paragraph(escape(facts[key]), styles[style])


def introduction(context, styles, b):
    facts = introduction_facts(context.scope, b)
    body = styles["ContentStyle"]
    content = [
        _heading(facts, styles),
        _heading(facts, styles, "purpose_heading", "Heading2"),
        Paragraph(escape(facts["purpose"]), body),
        _heading(facts, styles, "scope_heading", "Heading2"),
        Paragraph(escape(facts["scope"]), body),
        _heading(facts, styles, "limitations_heading", "Heading2"),
    ]
    for index, text in enumerate(facts["limitations"]):
        if index:
            content.append(Spacer(1, 6))
        content.append(Paragraph(escape(text), body))
    return content


def coverage(context, styles, b):
    facts = coverage_facts(context.cloud_service_provider, b)
    if facts is None:
        return []
    rows = [list(facts["headers"])] + [
        [_text(name, BODY_BOLD), _text(note)] for name, note in facts["rows"]
    ]
    return [
        _heading(facts, styles, style="Heading2"),
        Paragraph(escape(facts["intro"]), styles["ContentStyle"]),
        Spacer(1, 8),
        data_table(rows, [6 * cm, 10 * cm], context.appearance),
    ]


def _landscape_table(facts, context):
    appearance = context.appearance
    types, resources, size, share = facts["headers"]
    rows = [
        [
            types,
            _right_header(resources, appearance),
            _right_header(size, appearance),
            share,
        ]
    ]
    for row in facts["rows"]:
        rows.append(
            [
                _type_cell(row, context.report_path),
                _text(row["resources"], BODY_RIGHT),
                _text(row["size"], BODY_RIGHT),
                share_bar(row["share"], appearance),
            ]
        )
    extra = _total_row_style(rows)
    label, count, total = facts["total"]
    rows.append(
        [
            _text(label, BODY_BOLD),
            _text(count, BODY_BOLD_RIGHT),
            _text(total, BODY_BOLD_RIGHT),
            "",
        ]
    )
    return data_table(rows, [5.9 * cm, 2.0 * cm, 2.6 * cm, 5.5 * cm], appearance, extra)


def _stats(facts):
    return summary_strip(
        [
            (label, value, HOTSPOT_COUNT if attention else INK)
            for label, value, attention in facts["stats"]
        ]
    )


def data_landscape(context, estimate, styles, b):
    facts = landscape_facts(estimate, b)
    head = [
        _heading(facts, styles),
        Paragraph(escape(facts["lead"]), styles["ContentStyle"]),
        Spacer(1, 8),
    ]
    if facts["empty"]:
        return section(head, [])

    body = [_stats(facts), Spacer(1, 12)]
    if facts["rows"]:
        body += [_landscape_table(facts, context), Spacer(1, 6)]
    if facts["not_sized"]:
        body += [Paragraph(escape(facts["not_sized"]), CAPTION), Spacer(1, 2)]
    body += [
        Paragraph(escape(facts["caption"]), CAPTION),
        Spacer(1, 2),
        Paragraph(escape(facts["source_note"]), CAPTION),
    ]
    return section(head, body)


def _cost_table(facts, context):
    appearance = context.appearance
    types, size, share, fee = facts["headers"]
    rows = [
        [
            types,
            _right_header(size, appearance),
            share,
            _right_header(fee, appearance),
        ]
    ]
    for row in facts["rows"]:
        rows.append(
            [
                _type_cell(row, context.report_path),
                _text(row["size"], BODY_RIGHT),
                share_bar(row["share"], appearance),
                _text(row["fee"], BODY_RIGHT),
            ]
        )
    extra = _total_row_style(rows)
    label, size_total, fee_total = facts["total"]
    rows.append(
        [
            _text(label, BODY_BOLD),
            _text(size_total, BODY_BOLD_RIGHT),
            "",
            _text(fee_total, BODY_BOLD_RIGHT),
        ]
    )
    return data_table(rows, [5.4 * cm, 2.6 * cm, 5.5 * cm, 2.5 * cm], appearance, extra)


def estimated_costs(context, estimate, styles, b):
    facts = cost_facts(estimate, b)
    head = [
        _heading(facts, styles),
        Paragraph(escape(facts["lead"]), styles["ContentStyle"]),
        Spacer(1, 8),
    ]
    if facts["empty"]:
        return section(head, [])

    body = [_stats(facts), Spacer(1, 12)]
    if facts["rows"]:
        body += [_cost_table(facts, context), Spacer(1, 6)]
    body.append(Paragraph(escape(facts["caption"]), CAPTION))
    return section(head, body)


def _glossary_table(entries, appearance):
    table = Table(
        [[_text(term, BODY_BOLD), _text(definition)] for term, definition in entries],
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
                    HexColor(appearance.header_row_color),
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
    return table


def _prices(facts, context, styles):
    appearance = context.appearance
    headers = list(facts["headers"])
    # Price is the third column, right aligned like the other figures
    headers[2] = _right_header(headers[2], appearance)
    rows = [headers]
    for row in facts["rows"]:
        cells = [_text(cell) for cell in row]
        cells[2] = _text(row[2], BODY_RIGHT)
        rows.append(cells)
    widths = (
        [4.0 * cm, 5.4 * cm, 3.6 * cm, 3.0 * cm]
        if len(headers) == 4
        else [4.6 * cm, 6.8 * cm, 4.6 * cm]
    )
    content = [
        _heading(facts, styles),
        Paragraph(escape(facts["intro"]), styles["ContentStyle"]),
        Spacer(1, 8),
        data_table(rows, widths, appearance),
        Spacer(1, 6),
    ]
    color = appearance.heading2_color
    for text in (facts["units"], facts["no_archive"]):
        if text:
            content += [Paragraph(escape(text), CAPTION), Spacer(1, 2)]
    for template, url in facts["sources"]:
        content += [
            Paragraph(template.format(link=link_markup(url, color)), CAPTION),
            Spacer(1, 2),
        ]
    content.append(Paragraph(escape(facts["note"]), CAPTION))
    return content


def appendix(context, estimate, styles, b):
    facts = pricing_facts(estimate, b)
    content = _prices(facts, context, styles)
    content += [
        Spacer(1, 12),
        _heading(facts, styles, "glossary_heading", "Heading2"),
        Spacer(1, 4),
        _glossary_table(facts["glossary"], context.appearance),
    ]
    return content
