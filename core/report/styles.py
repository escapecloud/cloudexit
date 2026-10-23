from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

from .fonts import FONT, FONT_BOLD
from .palette import DEFAULT_APPEARANCE


def load_styles(appearance=DEFAULT_APPEARANCE):
    styles = getSampleStyleSheet()

    for name in ("Normal", "BodyText", "Bullet"):
        styles[name].fontName = FONT
    for name in ("Title", "Heading1", "Heading2", "Heading3"):
        styles[name].fontName = FONT_BOLD

    # A heading never ends a page on its own.
    for name in ("Heading1", "Heading2", "Heading3"):
        styles[name].keepWithNext = 1

    styles["Heading1"].leading = 1.5 * styles["Heading1"].fontSize
    # Space above a chapter that follows another section on the same page;
    # ReportLab drops it at the top of a page.
    styles["Heading1"].spaceBefore = 18
    styles["Heading1"].textColor = HexColor(appearance.heading1_color)
    styles["Heading2"].leading = 1.5 * styles["Heading2"].fontSize
    styles["Heading2"].textColor = HexColor(appearance.heading2_color)
    styles["Heading3"].leading = 1.5 * styles["Heading3"].fontSize
    styles["Heading3"].textColor = HexColor(appearance.heading2_color)

    content_style = ParagraphStyle(
        "ContentStyle",
        parent=styles["BodyText"],
        fontName=FONT,
        fontSize=9.5,
        leading=14.5,
        # Left-aligned: justified text leaves uneven gaps in a 16 cm column.
        alignment=TA_LEFT,
        firstLineIndent=0,
        # No single line of a paragraph alone at the top or foot of a page.
        allowWidows=0,
        allowOrphans=0,
    )
    reference_style = ParagraphStyle(
        "ReferenceStyle",
        parent=styles["BodyText"],
        fontName=FONT,
        fontSize=8,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.grey,
        firstLineIndent=0,
    )
    tableheader_style = ParagraphStyle(
        "TableHeader",
        parent=styles["BodyText"],
        fontName=FONT_BOLD,
        fontSize=9.5,
        leading=14.5,
        alignment=TA_LEFT,
        firstLineIndent=0,
        textColor=colors.HexColor("#FFFFFF"),
    )
    table_style = ParagraphStyle(
        "TableStyle",
        parent=styles["BodyText"],
        fontName=FONT,
        fontSize=8,
        leading=12,
        alignment=TA_LEFT,
        firstLineIndent=0,
    )

    styles.add(content_style)
    styles.add(reference_style)
    styles.add(tableheader_style)
    styles.add(table_style)

    return styles
