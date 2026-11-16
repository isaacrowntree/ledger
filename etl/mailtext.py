"""Plain text from an email as `himalaya message read` prints it (often HTML-only)."""
import re
from html.parser import HTMLParser

_BLOCK = {"br", "p", "div", "tr", "td", "li", "h1", "h2", "h3", "table"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def to_text(raw: str) -> str:
    """Visible text, one non-empty line per block, quoted-printable undone."""
    raw = re.sub(r"=\r?\n", "", raw)
    raw = re.sub(r"=([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), raw)
    p = _Text()
    p.feed(raw)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(p.out).split("\n")]
    return "\n".join(line for line in lines if line)
