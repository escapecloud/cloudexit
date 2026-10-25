import os
from functools import partial

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import Spacer

from .base import NumberedCanvas, ReportDocTemplate, set_pdf_metadata
from .brief_summary import build_brief_summary, collect_brief_facts
from .cover import RecordCover
from .evidence import build_document_information_page
from .flow import chapter_break
from .notices import legal_notices
from .sections import (
    alternative_technologies,
    costs_overview,
    full_assessment,
    glossary,
    introduction,
    resource_inventory,
    risk_inventory,
    severity_levels,
)
from .styles import load_styles
from .wording import BLOCKS


def build_content(context, styles, b):
    facts = collect_brief_facts(context, b)
    content = [Spacer(1, 0), chapter_break()]
    content += build_document_information_page(context, b)
    content += build_brief_summary(facts, styles, context.appearance, b)
    content.append(chapter_break())
    content += introduction(context, styles, b)
    content.append(chapter_break())
    content += costs_overview(context, facts, styles, b)
    content.append(chapter_break())
    content += resource_inventory(context, styles, b)
    content.append(chapter_break())
    content += risk_inventory(context, styles, b)
    content.append(chapter_break())
    content += alternative_technologies(context, styles, b)
    content.append(chapter_break())
    content += severity_levels(context, styles, b)
    content.append(Spacer(1, 18))
    content += glossary(context, styles, b)
    content.append(chapter_break())
    content += full_assessment(context, styles, b)
    content.append(chapter_break())
    content += legal_notices(context, styles, b)
    return content


def generate_executive_summary(context, path=None):
    path = path or os.path.join(context.report_path, "report.pdf")
    b = BLOCKS
    cover = RecordCover(b["cover_title_executive_summary"], context, b)
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
        build_content(context, load_styles(context.appearance), b),
        canvasmaker=partial(NumberedCanvas, page_of=b["footer_page_of"]),
    )
    return path
