from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import cm
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
)

# Room a section needs at the foot of a page before it starts there: its
# heading, lead and opening block (summary strip, bar) or first table rows.
SECTION_MIN = 6 * cm
# A Heading2 subsection that opens with text needs less.
SUBSECTION_MIN = 4 * cm
# A table with fewer rows moves with its caption; longer tables split.
SHORT_TABLE_ROWS = 8

CAPTION_STYLES = ("AltCaption", "EgressCaption")
OPENING_CAPTION_STYLES = ("ResBarCaption",)


class KeepWithNext(KeepTogether):
    # The group keepWithNext builds from a heading and what follows it. When it
    # does not fit but there is room for the heading and a few lines, it starts
    # here and splits, instead of moving a long paragraph or table whole.
    def split(self, aW, aH):
        if getattr(self, "_wrapInfo", None) != (aW, aH):
            self.wrap(aW, aH)
        if self._H > aH >= SUBSECTION_MIN:
            return self._content[:]
        return super().split(aW, aH)


def chapter_break():
    # The only hard page break in the reports: cover, page 2, chapters and
    # appendices.
    return PageBreak()


def section_break(min_height=SECTION_MIN):
    return CondPageBreak(min_height)


def section_head(*flowables):
    return KeepTogether(list(flowables))


def _opens(item):
    # Summary strips (one-row tables), bars and their captions open a section.
    if isinstance(item, Table):
        return len(item._cellvalues) == 1
    if isinstance(item, Paragraph):
        return item.style.name in OPENING_CAPTION_STYLES
    return isinstance(item, (Drawing, Spacer))


def split_opening(items):
    index = 0
    while index < len(items) and _opens(items[index]):
        index += 1
    return items[:index], items[index:]


def _is_caption(item):
    return isinstance(item, Paragraph) and item.style.name in CAPTION_STYLES


def keep_captions(items):
    # A short table and the caption under it move as one; a long table may
    # split, with its header repeated.
    result = []
    index = 0
    while index < len(items):
        item = items[index]
        if isinstance(item, Table) and len(item._cellvalues) < SHORT_TABLE_ROWS:
            end = index + 1
            while end < len(items) and isinstance(items[end], Spacer):
                end += 1
            if end < len(items) and _is_caption(items[end]):
                while end + 1 < len(items) and (
                    _is_caption(items[end + 1])
                    or isinstance(items[end + 1], Spacer)
                    and end + 2 < len(items)
                    and _is_caption(items[end + 2])
                ):
                    end += 1
                result.append(KeepTogether(items[index : end + 1]))
                index = end + 1
                continue
        result.append(item)
        index += 1
    return result


def _is_short_table(item):
    if isinstance(item, KeepTogether):
        return any(_is_short_table(child) for child in item._content)
    return isinstance(item, Table) and len(item._cellvalues) < SHORT_TABLE_ROWS


def section(head, items):
    # The heading, lead and opening block move as one unit; the rest flows.
    # Without a strip or bar, a short first table joins the lead, so the
    # heading never waits alone at the foot of a page for it.
    opening, rest = split_opening(list(items))
    rest = keep_captions(rest)
    if not opening and rest and _is_short_table(rest[0]):
        first = rest[0]
        # Flattened: a KeepTogether inside another always reports itself as
        # too tall and would push the whole section to the next page.
        opening = first._content if isinstance(first, KeepTogether) else [first]
        rest = rest[1:]
    return [section_head(*head, *opening), *rest]
