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
        if tag in ("style", "script", "head") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def _looks_quoted_printable(raw: str) -> bool:
    """Soft line breaks or an escaped '=' only appear in quoted-printable text."""
    return bool(re.search(r"=\r?\n|=3D", raw))


def to_text(raw: str) -> str:
    """Visible text, one non-empty line per block, quoted-printable undone."""
    if _looks_quoted_printable(raw):
        raw = re.sub(r"=\r?\n", "", raw)
        # Consecutive escapes are one UTF-8 sequence (=E2=80=99 is a single ’).
        raw = re.sub(r"(?:=[0-9A-F]{2})+",
                     lambda m: bytes.fromhex(m.group(0).replace("=", "")).decode("utf-8", "replace"), raw)
    p = _Text()
    p.feed(raw)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(p.out).split("\n")]
    return "\n".join(line for line in lines if line)
