import re
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Spacer

from .evidence import link_markup
from .fonts import FONT_BOLD
from .sections import (
    AWS_ICONS_URL,
    AWS_TRADEMARK_URL,
    AZURE_ICONS_URL,
    LICENCE_URL,
    REPOSITORY_URL,
)
from .wording import BLOCKS

NOTICE_BODY = "NoticeBody"
NOTICE_LABEL = "NoticeLabel"
FIELD = re.compile(r"({[a-z_]+})")


def text_parts(template, links, values=None):
    # A template split into plain text and (text, url) links, so the PDF and
    # the HTML are built from the same content
    parts = []
    for piece in FIELD.split(template):
        field = piece[1:-1]
        if field in links:
            link = links[field]
            # A (label, url) pair shows the label; a bare url shows itself
            parts.append(link if isinstance(link, tuple) else (link, link))
        elif values and field in values:
            parts.append(str(values[field]))
        elif piece:
            parts.append(piece)
    return parts


def icon_notice(provider, b):
    # The names are the links: the HTML shows the name, the PDF the name and, in
    # brackets, the address
    if provider == 2:
        return text_parts(
            b["notices_icons_aws"],
            {
                "icons": (b["notices_label_aws_icons"], AWS_ICONS_URL),
                "guidelines": (b["notices_label_aws_guidelines"], AWS_TRADEMARK_URL),
            },
        )
    if provider == 1:
        return text_parts(
            b["notices_icons_azure"],
            {"icons": (b["notices_label_azure_icons"], AZURE_ICONS_URL)},
        )
    return None


def notices_html(provider, generated_at, b=BLOCKS, egress=False):
    # The notices as plain data. A paragraph is a list of text and
    # (text, url) links. Only the notice for the report's own provider's icons.
    # The Egress & Data Landscape adds what its figures are and are not.
    disclaimer = [
        [b["notices_disclaimer"]],
        [b["notices_generated"]],
        [b["notices_purpose"]],
        [b["notices_figures"]],
        *([[b["notices_egress"]]] if egress else []),
    ]
    trademarks = [[b["notices_trademark_own"]], [b["notices_trademark_others"]]]
    icons = icon_notice(provider, b)
    if icons:
        trademarks.append(icons)
    return {
        "heading": b["notices_heading"],
        "blocks": [
            {
                "heading": b["notices_disclaimer_heading"],
                "paragraphs": disclaimer,
            },
            {"heading": b["notices_trademarks_heading"], "paragraphs": trademarks},
            {
                "heading": b["notices_software_heading"],
                "paragraphs": [
                    text_parts(
                        b["notices_software"],
                        {"repository_url": REPOSITORY_URL, "licence_url": LICENCE_URL},
                    )
                ],
            },
        ],
        "copyright": b["notices_copyright"].format(year=generated_at.year),
    }


def _link_markup(label, url, color):
    # A link that is not its own address prints the address too, for print
    if label == url:
        return link_markup(url, color)
    return f"{escape(label)} ({link_markup(url, color)})"


def parts_markup(parts, color):
    return "".join(
        (
            _link_markup(part[0], part[1], color)
            if isinstance(part, tuple)
            else escape(part)
        )
        for part in parts
    )


def _styles(styles):
    body = styles["ContentStyle"]
    return (
        ParagraphStyle(NOTICE_BODY, parent=body, fontSize=8.5, leading=12.5),
        ParagraphStyle(
            NOTICE_LABEL,
            parent=body,
            fontName=FONT_BOLD,
            fontSize=9,
            leading=13,
            spaceBefore=8,
            spaceAfter=2,
            textColor=styles["Heading2"].textColor,
        ),
    )


def legal_notices(context, styles, b, egress=False):
    notices = notices_html(
        context.cloud_service_provider, context.provenance.generated_at, b, egress
    )
    color = context.appearance.heading2_color
    body, label = _styles(styles)
    content = [Paragraph(notices["heading"], styles["Heading1"])]
    for block in notices["blocks"]:
        content.append(Paragraph(escape(block["heading"]), label))
        for parts in block["paragraphs"]:
            content.append(Paragraph(parts_markup(parts, color), body))
    content.append(Spacer(1, 10))
    content.append(Paragraph(escape(notices["copyright"]), body))
    return content
