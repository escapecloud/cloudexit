import logging
import os
from dataclasses import dataclass
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph

from cloudexit.resources import find_asset

from .fonts import FONT, FONT_BOLD, FONT_MONO
from .helpers import strategy_name
from .palette import INK, MUTED, RULE, SUBTITLE

logger = logging.getLogger(__name__)

BAND_WIDTH = 0.9 * cm
MARGIN = 2.5 * cm
CONTENT_WIDTH = A4[0] - 2 * MARGIN
COLUMN_WIDTH = 8 * cm
CELL_WIDTH = COLUMN_WIDTH - 12
LOGO_MAX_WIDTH = 5.5 * cm
LOGO_MAX_HEIGHT = 1.6 * cm
LOGO_TOP = 2.2 * cm
TITLE_TOP = 8.1 * cm
FOOTER_Y = 1.5 * cm
ROW_SPACING = 14
LOGO_FILE = os.path.join("img", "logo", "report_logo.png")

TITLE_STYLE = ParagraphStyle(
    "CoverTitle",
    fontName=FONT_BOLD,
    fontSize=30,
    leading=34,
    textColor=HexColor(INK),
)
PROJECT_STYLE = ParagraphStyle(
    "CoverProject",
    fontName=FONT,
    fontSize=15,
    leading=19,
    textColor=HexColor(SUBTITLE),
)
LABEL_STYLE = ParagraphStyle(
    "CoverLabel",
    fontName=FONT_BOLD,
    fontSize=6.8,
    leading=8.5,
    textColor=HexColor(MUTED),
)
VALUE_STYLE = ParagraphStyle(
    "CoverValue",
    fontName=FONT,
    fontSize=9.5,
    leading=12.5,
    textColor=HexColor(INK),
)
MONO_VALUE_STYLE = ParagraphStyle(
    "CoverMonoValue", parent=VALUE_STYLE, fontName=FONT_MONO
)


@dataclass(frozen=True)
class CoverCell:
    label: str
    value: str
    style: ParagraphStyle = VALUE_STYLE


def fit_logo(width, height, max_width=LOGO_MAX_WIDTH, max_height=LOGO_MAX_HEIGHT):
    scale = min(max_width / width, max_height / height)
    return width * scale, height * scale


def logo_reader(report_path):
    path = find_asset(report_path, LOGO_FILE)
    if path is None:
        raise FileNotFoundError(f"report logo not found under {report_path}/assets")
    return ImageReader(str(path))


def _paragraph(value, style):
    return Paragraph(escape(str(value)), style)


class RecordCover:
    def __init__(self, title, context, b):
        self.title = title
        self.project_name = context.name
        self.document_id = context.document_id
        self.appearance = context.appearance
        self.report_path = context.report_path
        self.provenance = context.provenance
        self.b = b
        self.rows = self._rows(context, b)

    def _rows(self, context, b):
        provenance = context.provenance
        run = b["cover_run_value"].format(
            run=provenance.run_short_id or b["ev_dash"], completed=context.completed
        )
        left = [
            CoverCell(b["cover_doc_id"], self.document_id, MONO_VALUE_STYLE),
            CoverCell(b["cover_classification"], b["cover_classification_value"]),
            CoverCell(
                b["cover_exit_strategy"], strategy_name(context.strategy_code, b)
            ),
        ]
        right = [
            CoverCell(b["cover_run"], run),
            CoverCell(
                b["cover_generated"], provenance.generated_at.strftime("%Y-%m-%d")
            ),
            CoverCell(b["ev_language"], b["language_name"]),
        ]
        return list(zip(left, right))

    def __call__(self, canvas, doc):
        canvas.saveState()
        width, height = A4

        canvas.setFillColor(HexColor(self.appearance.header_footer_color))
        canvas.rect(0, 0, BAND_WIDTH, height, stroke=0, fill=1)

        self._draw_logo(canvas, width, height)
        y = self._draw_title_block(canvas, height - TITLE_TOP)
        self._draw_details(canvas, y - 34)
        self._draw_footer(canvas, width)

        canvas.restoreState()

    def _draw_logo(self, canvas, width, height):
        logo = logo_reader(self.report_path)
        logo_width, logo_height = fit_logo(*logo.getSize())
        canvas.drawImage(
            logo,
            width - MARGIN - logo_width,
            height - LOGO_TOP - logo_height,
            width=logo_width,
            height=logo_height,
            mask="auto",
        )

    def _draw_title_block(self, canvas, top):
        y = self._draw_paragraph(canvas, self.title, TITLE_STYLE, MARGIN, top)
        return self._draw_paragraph(
            canvas, self.project_name, PROJECT_STYLE, MARGIN, y - 8
        )

    def _draw_details(self, canvas, top):
        canvas.setStrokeColor(HexColor(RULE))
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, top, MARGIN + CONTENT_WIDTH, top)

        y = top
        for row in self.rows:
            y -= ROW_SPACING
            bottoms = [
                self._draw_cell(canvas, cell, MARGIN + index * COLUMN_WIDTH, y)
                for index, cell in enumerate(row)
                if cell is not None
            ]
            y = min(bottoms)
        y -= ROW_SPACING

        canvas.setStrokeColor(HexColor(RULE))
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, y, MARGIN + CONTENT_WIDTH, y)

    def _draw_cell(self, canvas, cell, x, top):
        y = self._draw_paragraph(
            canvas, cell.label.upper(), LABEL_STYLE, x, top, CELL_WIDTH
        )
        return self._draw_paragraph(
            canvas, cell.value, cell.style, x, y - 3, CELL_WIDTH
        )

    def _draw_paragraph(self, canvas, text, style, x, top, width=CONTENT_WIDTH):
        paragraph = _paragraph(text, style)
        _w, h = paragraph.wrap(width, A4[1])
        paragraph.drawOn(canvas, x, top - h)
        return top - h

    def _draw_footer(self, canvas, width):
        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont(FONT, 7)
        canvas.drawString(
            MARGIN,
            FOOTER_Y,
            self.b["cover_collected_with"].format(
                version=self.provenance.collector_version,
                date=self.provenance.collected_date,
            ),
        )
        canvas.setFont(FONT_MONO, 7)
        canvas.drawRightString(width - MARGIN, FOOTER_Y, self.document_id)
