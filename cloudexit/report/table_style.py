from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Table, TableStyle

from .fonts import FONT, FONT_BOLD, FONT_ITALIC, FONT_MONO
from .helpers import share_label
from .palette import HAIRLINE, INK, MUTED, SUBTITLE, tint

SECONDARY = SUBTITLE


def mix_with_white(color, amount):
    return HexColor(tint(color, amount))


def text_style(name, size, leading, color=INK, bold=False, alignment=TA_LEFT):
    return ParagraphStyle(
        name,
        fontName=FONT_BOLD if bold else FONT,
        fontSize=size,
        leading=leading,
        textColor=HexColor(color),
        alignment=alignment,
    )


def header_cell(text, appearance, alignment=TA_LEFT, split_long_words=True):
    style = text_style(
        "DataTableHeader",
        7.8,
        9.5,
        appearance.header_row_color,
        bold=True,
        alignment=alignment,
    )
    # In narrow columns a word slightly wider than the cell runs into the
    # padding instead of breaking mid-word.
    style.splitLongWords = int(split_long_words)
    return Paragraph(text, style)


def data_table(rows, col_widths, appearance, extra=()):
    header_color = HexColor(appearance.header_row_color)
    rows = [
        [
            header_cell(cell, appearance) if isinstance(cell, str) else cell
            for cell in rows[0]
        ],
        *rows[1:],
    ]
    table = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    mix_with_white(appearance.header_row_color, 0.9),
                ),
                ("LINEBELOW", (0, 0), (-1, 0), 1, header_color),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, HexColor(HAIRLINE)),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
                *extra,
            ]
        )
    )
    return table


KV_LABEL = text_style("KeyValueLabel", 8.2, 10.5, SECONDARY, bold=True)
KV_VALUE = text_style("KeyValueValue", 8.2, 10.5)
KV_MONO = ParagraphStyle(
    "KeyValueMono", parent=KV_VALUE, fontName=FONT_MONO, fontSize=7.8
)
FOOTNOTE = text_style("Footnote", 7.5, 9.5, MUTED).clone(
    "Footnote", fontName=FONT_ITALIC
)


def mono(text):
    # Inline monospace for identifiers inside a key-value value.
    return f'<font name="{FONT_MONO}" size="7.8">{text}</font>'


def key_value_table(rows, appearance, label_width=4.6 * cm):
    # rows: (label, value); a string value is escaped markup-free text, any
    # other value (a Paragraph) is used as it is.
    table = Table(
        [
            [
                Paragraph(escape(label), KV_LABEL),
                (
                    Paragraph(escape(value), KV_VALUE)
                    if isinstance(value, str)
                    else value
                ),
            ]
            for label, value in rows
        ],
        colWidths=[label_width, 16 * cm - label_width],
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


STAT_LABEL = text_style("AltStatLabel", 7.2, 9, MUTED, bold=True)


def summary_strip(stats, widths=None):
    # stats: (label, value, colour); values are plain text. Equal columns
    # unless widths are given.
    cells = [
        [
            Paragraph(escape(str(label).upper()), STAT_LABEL),
            Paragraph(
                escape(str(value)),
                text_style("AltStatValue", 15, 18, color, bold=True),
            ),
        ]
        for label, value, color in stats
    ]
    strip = Table(
        [cells],
        colWidths=widths or [16 * cm / len(stats)] * len(stats),
        hAlign="LEFT",
    )
    strip.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, HexColor(INK)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return strip


def share_bar(share, appearance, width=5.0 * cm):
    # A share of 0-100 as a bar with its label; the track is the full width
    track = width - 34
    drawing = Drawing(width, 9)
    drawing.add(Rect(0, 1, track, 7, fillColor=HexColor(HAIRLINE), strokeColor=None))
    if share:
        drawing.add(
            Rect(
                0,
                1,
                track * share / 100,
                7,
                fillColor=HexColor(appearance.header_row_color),
                strokeColor=None,
            )
        )
    drawing.add(
        String(
            track + 4,
            1.5,
            share_label(share),
            fontName=FONT,
            fontSize=7.8,
            fillColor=HexColor(INK),
        )
    )
    return drawing
