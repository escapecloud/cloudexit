from reportlab.lib.colors import HexColor
from reportlab.platypus import Flowable, Paragraph

from .fonts import FONT_BOLD
from .helpers import initials
from .palette import PLACEHOLDER_BACKGROUND, SUBTITLE

CORNER = 0.22


class IconPlaceholder(Flowable):
    # Stands in for a resource type's icon: a neutral rounded square with the
    # type's initials.
    def __init__(self, name, size=11):
        super().__init__()
        self.initials = initials(name)
        self.size = size
        self.width = self.height = size

    def wrap(self, available_width, available_height):
        return self.size, self.size

    def draw(self):
        canvas, size = self.canv, self.size
        canvas.setFillColor(HexColor(PLACEHOLDER_BACKGROUND))
        canvas.roundRect(0, 0, size, size, size * CORNER, stroke=0, fill=1)
        font_size = size * (0.5 if len(self.initials) > 1 else 0.6)
        canvas.setFillColor(HexColor(SUBTITLE))
        canvas.setFont(FONT_BOLD, font_size)
        canvas.drawCentredString(size / 2, (size - font_size * 0.72) / 2, self.initials)


class LabelWithPlaceholder(Flowable):
    # A text label with the placeholder in front of its first line, where a
    # table cell would otherwise show an inline icon.
    def __init__(self, name, markup, style, size=11, gap=2):
        super().__init__()
        self.placeholder = IconPlaceholder(name, size)
        self.paragraph = Paragraph(markup, style)
        self.style = style
        self.size = size
        self.gap = gap

    def wrap(self, available_width, available_height):
        offset = self.size + self.gap
        _, text_height = self.paragraph.wrap(available_width - offset, available_height)
        self.width = available_width
        self.height = max(text_height, self.size)
        return self.width, self.height

    def draw(self):
        offset = self.size + self.gap
        text_height = self.paragraph.height
        top = self.height
        self.paragraph.drawOn(self.canv, offset, top - text_height)
        middle = top - self.style.leading / 2
        self.placeholder.canv = self.canv
        self.canv.saveState()
        self.canv.translate(0, middle - self.size / 2)
        self.placeholder.draw()
        self.canv.restoreState()
