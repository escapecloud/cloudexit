from html.parser import HTMLParser

# A block of the text dump starts at a section heading; the small labels of
# the dashboard cards stay in the text of their block
SECTION_HEADINGS = ("h1", "h2", "h3")


class TextOfHtml(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
        # (heading, text) blocks: a block runs from one heading to the next
        self.blocks = [("", [])]
        self.heading = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in SECTION_HEADINGS and not self.skip:
            self.heading = []
            self.blocks.append(("", []))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in SECTION_HEADINGS and self.heading is not None:
            title = " ".join(" ".join(self.heading).split())
            self.blocks[-1] = (title, self.blocks[-1][1])
            self.heading = None

    def handle_data(self, data):
        if self.skip:
            return
        self.parts.append(data)
        if self.heading is not None:
            self.heading.append(data)
        else:
            self.blocks[-1][1].append(data)


def html_text(html):
    parser = TextOfHtml()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def html_blocks(html):
    # The visible text, one block per section heading, for diffing between
    # commits. Text before the first heading is the first block.
    parser = TextOfHtml()
    parser.feed(html)
    blocks = []
    for title, parts in parser.blocks:
        text = " ".join(" ".join(parts).split())
        if title or text:
            blocks.append(f"## {title}\n{text}" if title else text)
    return "\n\n".join(blocks) + "\n"
