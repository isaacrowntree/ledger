"""Shared helpers for statements laid out as right-aligned numeric columns.

Statements from CBA and Bankwest put debits and credits in separate columns
and print both as bare positive numbers. Flattening a page with
``extract_text()`` throws away the only thing that distinguishes them — a
$10.00 fee and a $10.00 deposit produce byte-identical text. These helpers
keep each word's horizontal extent so a parser can recover the column by
matching a word's *right* edge against the column heading's right edge
(the columns are right-aligned, so left edges drift with the number's width
while right edges stay put).
"""
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# A statement amount: optional $, thousands separators, exactly 2 decimals,
# optionally followed by a CR/DR balance marker or a trailing minus (CBA
# credit cards mark credits as "900.00-").
_AMOUNT_RE = re.compile(r"^\$?(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})(CR|DR|-)?$", re.IGNORECASE)

_SUFFIXES = ("CR", "DR", "-")


class StatementParseError(Exception):
    """Raised when a statement does not reconcile or cannot be parsed safely.

    Ingesting a silently-wrong statement corrupts the ledger, so parsers fail
    loudly rather than importing partial or unbalanced data.
    """


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float


@dataclass(frozen=True)
class Row:
    """One visual line of a page, words ordered left to right."""
    words: tuple[Word, ...]
    page: int

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def first_x0(self) -> float:
        return self.words[0].x0 if self.words else 0.0

    def drop_left_of(self, x: float) -> "Row":
        """Drop margin artefacts (print codes, fold marks) left of the table."""
        return Row(tuple(w for w in self.words if w.x0 >= x), self.page)


def parse_amount(text: str) -> float | None:
    """Return an amount's magnitude, or None if the word is not an amount.

    The sign is deliberately not inferred here: on these statements it comes
    from which column the word sits in, not from the text.
    """
    m = _AMOUNT_RE.match(text)
    if not m:
        return None
    return float(f"{m.group(1).replace(',', '')}.{m.group(2)}")


def parse_balance(text: str) -> float | None:
    """Return a running balance as a signed number.

    ``DR`` marks a debit balance — money owed — and becomes negative, so that
    a loan balance and a deposit balance can be compared on one number line.
    """
    value = parse_amount(text)
    if value is None:
        return None
    return -value if text.upper().endswith("DR") else value


def is_amount(text: str) -> bool:
    return _AMOUNT_RE.match(text) is not None


@dataclass
class ColumnRuler:
    """Assigns words to named columns by matching right edges."""
    anchors: dict[str, float]
    tolerance: float = 12.0
    # Banks disagree on what gets right-aligned: CBA aligns the number and its
    # CR marker as a group, Bankwest aligns the number alone and lets "DR" hang
    # past the column edge. A marked word may therefore end well right of its
    # anchor, but still may not fall short of it.
    suffix_overhang: float = 15.0

    @classmethod
    def from_header(cls, row: Row, labels: dict[str, str], tolerance: float = 12.0) -> "ColumnRuler | None":
        """Build a ruler from a header row, or None if a label is missing.

        Each label may span several words ("Amount (A$)"); the anchor is the
        right edge of its final word, which is where the column's numbers end.
        """
        texts = [w.text.upper() for w in row.words]
        anchors = {}
        for name, label in labels.items():
            tokens = label.upper().split()
            anchor = None
            for i in range(len(texts) - len(tokens) + 1):
                if texts[i:i + len(tokens)] == tokens:
                    anchor = row.words[i + len(tokens) - 1].x1
                    break
            if anchor is None:
                return None
            anchors[name] = anchor
        return cls(anchors, tolerance)

    def column_of(self, word: Word) -> str | None:
        """Return the column whose right edge this word aligns with."""
        overhang = self.suffix_overhang if word.text.upper().endswith(_SUFFIXES) else 0.0
        best, best_gap = None, None
        for name, anchor in self.anchors.items():
            delta = word.x1 - anchor
            if not -self.tolerance <= delta <= self.tolerance + overhang:
                continue
            if best_gap is None or abs(delta) < best_gap:
                best, best_gap = name, abs(delta)
        return best

    def is_column_amount(self, word: Word) -> bool:
        """True when a word is an amount sitting in one of the ruler's columns.

        Description text is left-aligned and can happen to end near a column's
        right edge, so alignment alone must not disqualify a word from being
        part of the description.
        """
        return parse_amount(word.text) is not None and self.column_of(word) is not None

    def amounts_in(self, row: Row) -> dict[str, tuple[float, str]]:
        """Map column name -> (magnitude, raw text) for amounts in known columns."""
        found = {}
        for w in row.words:
            value = parse_amount(w.text)
            if value is None:
                continue
            column = self.column_of(w)
            if column:
                found[column] = (value, w.text)
        return found


def extract_rows(file_path: Path, x_tolerance: float = 3.0, y_tolerance: float = 3.0) -> list[Row]:
    """Read a PDF into visual rows, preserving each word's horizontal extent.

    ``x_tolerance`` matters: CBA transaction-account statements contain no
    space glyphs at all, only positional gaps just under pdfplumber's default
    of 3, so the default silently welds whole phrases into one token.
    """
    rows: list[Row] = []
    with pdfplumber.open(file_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(x_tolerance=x_tolerance, keep_blank_chars=False)
            rows.extend(_group_into_rows(words, page_no, y_tolerance))
    return rows


def _group_into_rows(words: list[dict], page_no: int, y_tolerance: float) -> list[Row]:
    """Cluster words into rows by vertical position.

    A row's parts are not always at an identical ``top``: Bankwest prints the
    date a point higher than the description it belongs to.
    """
    rows = []
    current: list[dict] = []
    current_top = None

    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if current_top is None or abs(w["top"] - current_top) <= y_tolerance:
            if current_top is None:
                current_top = w["top"]
            current.append(w)
        else:
            rows.append(_build_row(current, page_no))
            current, current_top = [w], w["top"]

    if current:
        rows.append(_build_row(current, page_no))
    return rows


def make_row(words: list[Word], page: int = 1) -> Row:
    """Assemble words into a row, ordering them and reattaching CR/DR markers."""
    ordered = sorted(words, key=lambda w: w.x0)
    return Row(tuple(_merge_suffixes(ordered)), page)


def _build_row(raw_words: list[dict], page_no: int) -> Row:
    return make_row([Word(w["text"], w["x0"], w["x1"]) for w in raw_words], page_no)


def _merge_suffixes(words: list[Word]) -> list[Word]:
    """Reattach a CR/DR/- marker that was split off from its number.

    Whether "8,000.00CR" arrives as one token or two depends on the extraction
    tolerance, and the marker carries the right edge that identifies the
    balance column — so the two must be rejoined before any column lookup.
    """
    merged: list[Word] = []
    for w in words:
        prev = merged[-1] if merged else None
        if (prev is not None
                and w.text.upper() in _SUFFIXES
                and is_amount(prev.text)
                and not prev.text.upper().endswith(_SUFFIXES)
                and w.x0 - prev.x1 < 6):
            merged[-1] = Word(prev.text + w.text.upper(), prev.x0, w.x1)
        else:
            merged.append(w)
    return merged
