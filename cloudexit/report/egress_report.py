import os
from functools import partial

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import Spacer

from .base import NumberedCanvas, ReportDocTemplate, set_pdf_metadata
from .context import EGRESS_DOCUMENT_CODE
from .cover import RecordCover
from .egress import load_egress_estimate
from .egress_sections import (
    appendix,
    coverage,
    data_landscape,
    estimated_costs,
    introduction,
    next_steps,
)
from .evidence import build_document_information_page
from .flow import chapter_break, section_break
from .notices import legal_notices
from .styles import load_styles
from .wording import BLOCKS


def build_egress_content(context, estimate, styles, b):
    content = [Spacer(1, 0), chapter_break()]
    content += build_document_information_page(
        context,
        b,
        document_id=context.document_id_for(EGRESS_DOCUMENT_CODE),
        document_type=b["ev_egress_report"],
        companion_id=context.document_id,
    )
    content += introduction(context, styles, b)
    content += coverage(context, styles, b)
    content.append(chapter_break())
    content += data_landscape(context, estimate, styles, b)
    content.append(section_break())
    content += estimated_costs(context, estimate, styles, b)
    content.append(chapter_break())
    content += appendix(context, estimate, styles, b)
    content.append(Spacer(1, 12))
    content += next_steps(context, styles, b)
    content.append(chapter_break())
    content += legal_notices(context, styles, b, egress=True)
    return content


def generate_egress_report(context, json_path, path=None, estimate=None):
    path = path or os.path.join(context.report_path, "egress.pdf")
    b = BLOCKS
    estimate = estimate or load_egress_estimate(context.report_path, json_path)
    cover = RecordCover(
        b["cover_title_egress"],
        context,
        b,
        document_id=context.document_id_for(EGRESS_DOCUMENT_CODE),
    )
    doc = ReportDocTemplate(
        path,
        context,
        cover=cover,
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )
    set_pdf_metadata(doc, context, b)
    doc.multiBuild(
        build_egress_content(context, estimate, load_styles(context.appearance), b),
        canvasmaker=partial(NumberedCanvas, page_of=b["footer_page_of"]),
    )
    return path
