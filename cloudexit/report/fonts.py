from reportlab.graphics.shapes import STATE_DEFAULTS
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.tables import CellStyle

from cloudexit.resources import asset_path

FONT_DIR = asset_path("fonts")


class MissingFontError(FileNotFoundError):
    pass


FONT = "IBMPlexSans"
# SemiBold is the report's bold: it reads cleaner than Bold at small sizes.
FONT_BOLD = "IBMPlexSans-SemiBold"
FONT_ITALIC = "IBMPlexSans-Italic"
FONT_BOLD_ITALIC = "IBMPlexSans-SemiBoldItalic"
FONT_MONO = "IBMPlexMono"
FONT_MONO_BOLD = "IBMPlexMono-SemiBold"

FONT_FILES = {
    FONT: "IBMPlexSans-Regular.ttf",
    FONT_BOLD: "IBMPlexSans-SemiBold.ttf",
    FONT_ITALIC: "IBMPlexSans-Italic.ttf",
    FONT_BOLD_ITALIC: "IBMPlexSans-SemiBoldItalic.ttf",
    FONT_MONO: "IBMPlexMono-Regular.ttf",
    FONT_MONO_BOLD: "IBMPlexMono-SemiBold.ttf",
}


def register_fonts():
    if set(FONT_FILES) <= set(pdfmetrics.getRegisteredFontNames()):
        return
    # A missing file raises: a report must never switch typeface without
    # anyone noticing.
    for name, filename in FONT_FILES.items():
        path = FONT_DIR / filename
        if not path.is_file():
            raise MissingFontError(
                f"the font file {filename} is missing from {FONT_DIR}; "
                "the cloudexit installation is incomplete"
            )
        pdfmetrics.registerFont(TTFont(name, str(path)))
    # Families let <b> and <i> in Paragraph markup find the right face.
    pdfmetrics.registerFontFamily(
        FONT,
        normal=FONT,
        bold=FONT_BOLD,
        italic=FONT_ITALIC,
        boldItalic=FONT_BOLD_ITALIC,
    )
    pdfmetrics.registerFontFamily(
        FONT_MONO,
        normal=FONT_MONO,
        bold=FONT_MONO_BOLD,
        italic=FONT_MONO,
        boldItalic=FONT_MONO_BOLD,
    )
    # Mono has no italic, so the upright faces fill the italic slots. Map them
    # back as upright, or markup inside mono text would count as italic.
    addMapping(FONT_MONO, 0, 0, FONT_MONO)
    addMapping(FONT_MONO, 1, 0, FONT_MONO_BOLD)
    # Table cells and drawings select ReportLab's base fonts (Helvetica,
    # Times-Roman) even when they draw no text with them, which still lists
    # those fonts in every PDF. This package is the only user of them here.
    CellStyle.fontname = FONT
    STATE_DEFAULTS["fontName"] = FONT


register_fonts()
