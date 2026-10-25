from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.frames import Frame

from .cover import fit_logo, logo_reader
from .flow import KeepWithNext
from .fonts import FONT, FONT_BOLD, FONT_MONO
from .palette import MUTED, RULE, SUBTITLE
from .provenance import attach_metadata

MARGIN = 2.5 * cm
HEADER_TOP = 1.15 * cm
HEADER_LOGO_WIDTH = 3.4 * cm
HEADER_LOGO_HEIGHT = 0.75 * cm
HEADER_TEXT_WIDTH = 16 * cm - HEADER_LOGO_WIDTH - 0.5 * cm
FOOTER_BASELINE = 1.15 * cm
# The body spans the same 2.5–18.5 cm as the header and footer lines; the
# vertical padding keeps the first heading clear of the header rule.
BODY_SIDE_PADDING = 0
BODY_VERTICAL_PADDING = 6

OUTLINE_STYLES = {"Heading1": 0, "Heading2": 1}


class ReportDocTemplate(BaseDocTemplate):
    def __init__(self, filename, context, cover=None, **kw):
        self.context = context
        self.cover = cover
        self.allowSplitting = 0
        # Every page starts in this font; the default adds Helvetica to the PDF.
        kw.setdefault("initialFontName", FONT)
        kw.setdefault("keepTogetherClass", KeepWithNext)
        BaseDocTemplate.__init__(self, filename, **kw)
        self.normal_frame = Frame(
            2.5 * cm,
            2.5 * cm,
            16 * cm,
            25 * cm,
            leftPadding=BODY_SIDE_PADDING,
            rightPadding=BODY_SIDE_PADDING,
            topPadding=BODY_VERTICAL_PADDING,
            bottomPadding=BODY_VERTICAL_PADDING,
            id="F1",
        )
        self.cover_frame = Frame(0, 0, 21 * cm, 29.7 * cm, id="F1")
        template = PageTemplate(
            "normal", [self.normal_frame], onPage=self.header_and_footer
        )
        self.addPageTemplates(template)

    def beforeDocument(self):
        # Each multiBuild pass draws on a new canvas; the last one is saved.
        attach_metadata(self.canv, self.context.provenance)

    def handle_pageBegin(self):
        # The cover page uses the whole sheet.
        if self.page == 0:
            self.pageTemplates[0].frames[0] = self.cover_frame
        else:
            self.pageTemplates[0].frames[0] = self.normal_frame
        BaseDocTemplate.handle_pageBegin(self)

    def afterFlowable(self, flowable):
        # The outline is what readers navigate by and what the structure test
        # compares. It is added when the pages are written, see NumberedCanvas.
        if flowable.__class__.__name__ != "Paragraph":
            return
        level = OUTLINE_STYLES.get(flowable.style.name)
        if level is not None:
            self.canv.outline_entries.append(
                (self.page, flowable.getPlainText(), level)
            )

    def header_and_footer(self, canvas, doc):
        if canvas.getPageNumber() == 1:
            if self.cover:
                self.cover(canvas, doc)
        elif self.cover:
            draw_header(canvas, self.cover)
            draw_footer(canvas, self.cover)


def fit_text(canvas, text, font, size, width):
    text = " ".join((text or "").split())
    if canvas.stringWidth(text, font, size) <= width:
        return text
    while text and canvas.stringWidth(f"{text}…", font, size) > width:
        text = text[:-1].rstrip()
    return f"{text}…"


def draw_header(canvas, running):
    canvas.saveState()
    width, height = A4
    top = height - HEADER_TOP

    canvas.setFillColor(HexColor(SUBTITLE))
    canvas.setFont(FONT_BOLD, 7.5)
    canvas.drawString(
        MARGIN,
        top - 8,
        fit_text(canvas, running.title, FONT_BOLD, 7.5, HEADER_TEXT_WIDTH),
    )
    canvas.setFillColor(HexColor(MUTED))
    canvas.setFont(FONT, 7.5)
    canvas.drawString(
        MARGIN,
        top - 18,
        fit_text(canvas, running.project_name, FONT, 7.5, HEADER_TEXT_WIDTH),
    )

    logo = logo_reader(running.report_path)
    logo_width, logo_height = fit_logo(
        *logo.getSize(), HEADER_LOGO_WIDTH, HEADER_LOGO_HEIGHT
    )
    canvas.drawImage(
        logo,
        width - MARGIN - logo_width,
        top + 1 - logo_height,
        width=logo_width,
        height=logo_height,
        mask="auto",
    )

    canvas.setStrokeColor(HexColor(running.appearance.header_footer_color))
    canvas.setLineWidth(0.75)
    canvas.line(MARGIN, top - 25, width - MARGIN, top - 25)
    canvas.restoreState()


def draw_footer(canvas, running):
    canvas.saveState()
    width, _height = A4

    canvas.setStrokeColor(HexColor(RULE))
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, FOOTER_BASELINE + 11, width - MARGIN, FOOTER_BASELINE + 11)

    canvas.setFillColor(HexColor(MUTED))
    canvas.setFont(FONT_MONO, 6.8)
    canvas.drawString(MARGIN, FOOTER_BASELINE, running.document_id)

    edition = running.b["footer_edition"].upper()
    edition_width = canvas.stringWidth(edition, FONT, 6.8)
    canvas.setFont(FONT, 6.8)
    canvas.drawString((width - edition_width) / 2, FOOTER_BASELINE, edition)
    canvas.restoreState()


class NumberedCanvas(Canvas):
    # The page total is only known once the last page is laid out, so pages
    # are kept until save() and the counter is drawn then. Each multiBuild
    # pass uses a new canvas, so it is drawn once per page.
    def __init__(self, *args, page_of, **kwargs):
        super().__init__(*args, **kwargs)
        self._page_of = page_of
        self._page_states = []
        # (page, text, level), collected while the document is laid out
        self.outline_entries = []

    def showPage(self):
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._page_states)
        by_page = {}
        for page, text, level in self.outline_entries:
            by_page.setdefault(page, []).append((text, level))
        for state in self._page_states:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                self._draw_page_count(total)
            # Bookmarks name the page being written, so they are added here and
            # not while the document is laid out.
            for index, (text, level) in enumerate(by_page.get(self._pageNumber, [])):
                key = f"outline-{self._pageNumber}-{index}"
                self.bookmarkPage(key)
                self.addOutlineEntry(text, key, level=level)
            super().showPage()
        super().save()

    def _draw_page_count(self, total):
        width, _height = A4
        self.saveState()
        self.setFillColor(HexColor(MUTED))
        self.setFont(FONT, 7)
        self.drawRightString(
            width - MARGIN,
            FOOTER_BASELINE,
            self._page_of.format(page=self._pageNumber, total=total),
        )
        self.restoreState()


def set_pdf_metadata(doc, context, b):
    # What PDF viewers and document management systems show; it follows the
    # cover.
    provenance = context.provenance
    doc.title = f"{doc.cover.title} – {context.name}"
    doc.subject = b["pdf_subject"].format(
        document_id=doc.cover.document_id, version=provenance.collector_version
    )
    doc.author = "cloudexit"
    doc.creator = f"cloudexit {provenance.collector_version}"
    doc.keywords = [
        keyword
        for keyword in (
            doc.cover.document_id,
            provenance.run_id,
            (
                f"catalogue {provenance.catalogue_version}"
                if provenance.catalogue_version
                else None
            ),
        )
        if keyword
    ]
