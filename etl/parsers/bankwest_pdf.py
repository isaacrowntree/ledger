import re
from pathlib import Path

import pdfplumber

from etl.parsers.pdf_layout import ColumnRuler, Word, make_row

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.models import RawTransaction
from etl.parsers.base import BaseParser, chronological, labelled_balance, money

MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}

# Bankwest credit card eStatement layout:
#
#   Date        Description                               Debit      Credit
#   02 Nov 25   PAYPAL *baarsm 4029357733 AUS             $500.00
#   11 Nov 25   BILL PAYMENT RECEIVED FROM ING                       $352.00
#
# - Dates: DD Mon YY
# - Debit = money spent (positive number in debit column)
# - Credit = payment received (positive number in credit column)
# - Some lines have foreign currency info on next line (e.g. "12.99 PLN")
# - "Opening balance" line has a dollar amount but no date

DATE_RE = re.compile(r"^(\d{2}\s+\w{3}\s+\d{2})\s+(.+)")
AMOUNT_RE = re.compile(r"\$[\d,]+\.\d{2}")
FOREIGN_CURRENCY_RE = re.compile(r"^\d+[\.,]\d{2}\s+[A-Z]{3}$")

SKIP_PATTERNS = [
    re.compile(r"^Opening balance"),
    re.compile(r"^Closing balance"),
    re.compile(r"^Date\s+Description"),
    re.compile(r"^Bankwest Zero"),
    re.compile(r"^Account number"),
    re.compile(r"^Your transactions"),
    re.compile(r"^Summary"),
    re.compile(r"^Purchases\s+\$"),
    re.compile(r"^Cash advances"),
    re.compile(r"^Balance transfers"),
    re.compile(r"^Interest and other"),
    re.compile(r"^Payments and other"),
    re.compile(r"^Transaction details"),
    re.compile(r"^Standard interest"),
    re.compile(r"^These rates"),
    re.compile(r"^\d+ of \d+$"),
]

# Lines that signal the disclaimer/footer — exit transactions mode when seen,
# otherwise their text gets appended onto the last transaction's description.
# Case-insensitive: the PDFs use lowercase headings ("important information
# about your Bankwest..."), mixed-case legalese ("Misused, Lost or Stolen"), etc.
STOP_PATTERNS = [
    re.compile(r"^Ways to pay", re.IGNORECASE),
    re.compile(r"^Important information about your", re.IGNORECASE),
    re.compile(r"^Other information", re.IGNORECASE),
    re.compile(r"^Unauthorised or unknown transactions", re.IGNORECASE),
    re.compile(r"^Misused, lost or stolen", re.IGNORECASE),
    re.compile(r"® Registered to BPAY", re.IGNORECASE),
    re.compile(r"^continued on next page", re.IGNORECASE),
    re.compile(r"^Closing Balance\b", re.IGNORECASE),
    re.compile(r"^Page \d+ of \d+", re.IGNORECASE),
]


class BankwestPDFParser(BaseParser):
    """Parse Bankwest Australia credit card PDF eStatements."""

    source_type = "bankwest"

    # A credit card statement quotes what is OWED: spending makes it rise.
    balance_convention = BalanceConvention.OWING

    def parse_statement(self, file_path: Path) -> ParsedStatement:
        transactions = chronological(self._read(file_path))
        text = self._extract_text(file_path)
        rows = [
            ParsedRow(index=i, date=t.date, description=t.description,
                      amount=t.amount, raw=t.raw_data or {}, currency=t.currency,
                      original_amount=t.original_amount,
                      original_currency=t.original_currency, fee=t.fee,
                      reference_id=t.reference_id)
            for i, t in enumerate(transactions)
        ]
        return self.build(
            file_path, rows,
            opening_balance=labelled_balance(text, "Opening [Bb]alance"),
            closing_balance=labelled_balance(text, "Closing [Bb]alance"),
        )

    def _read(self, file_path: Path) -> list[RawTransaction]:
        text = self._extract_text(file_path)
        closing_balance = self._extract_closing_balance(text)
        entries = self._parse_entries(text)

        transactions = []
        for entry in entries:
            txn = self._build_transaction(entry, file_path, closing_balance)
            if txn:
                transactions.append(txn)
        return transactions

    def _extract_closing_balance(self, text: str) -> float | None:
        """Extract closing balance from statement."""
        m = re.search(r"Closing [Bb]alance\s+\$?([\d,]+\.\d{2})", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return None

    def _extract_text(self, file_path: Path) -> str:
        """Flatten the statement to text, keeping which COLUMN each amount is in.

        The Debit and Credit columns are the only reliable way to tell a charge
        from a credit -- descriptions do not say. Plain text extraction discards
        the x-positions that carry that, so amounts sitting under the Credit
        column header are marked with a trailing "CR" as the text is built.

        Falls back to plain extraction for any page without a recognisable
        column header, where the parser's description keywords still apply.
        """
        pages = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                marked = self._page_text_with_columns(page)
                if marked is None:
                    marked = page.extract_text()
                if marked:
                    pages.append(marked)
        return "\n".join(pages)

    def _page_text_with_columns(self, page) -> str | None:
        """Page text with credit-column amounts marked, or None if not laid out.

        Uses the shared ColumnRuler, which anchors on each column's RIGHT edge --
        these columns are right-aligned, so left edges drift with the width of
        the number while right edges stay put.
        """
        try:
            words = page.extract_words()
        except Exception:
            return None
        if not words:
            return None

        lines: dict[float, list[dict]] = {}
        for word in words:
            lines.setdefault(round(word["top"] / 3), []).append(word)

        ruler = None
        header_key = None
        for key in sorted(lines):
            row = make_row([Word(w["text"], w["x0"], w["x1"]) for w in lines[key]])
            ruler = ColumnRuler.from_header(row, {"debit": "Debit", "credit": "Credit"})
            if ruler:
                header_key = key
                break

        if ruler is None:
            return None

        rendered = []
        for key in sorted(lines):
            row = make_row([Word(w["text"], w["x0"], w["x1"]) for w in lines[key]])
            parts = []
            for word in row.words:
                text = word.text
                # Only inside the transaction list. The account summary above it
                # prints figures in the same columns, and marking those turned
                # "Opening balance $10.00" into a credit of -10.00.
                if (key > header_key and AMOUNT_RE.fullmatch(text)
                        and ruler.column_of(word) == "credit"):
                    text = f"{text} CR"
                parts.append(text)
            rendered.append(" ".join(parts))
        return "\n".join(rendered)

    def _parse_entries(self, text: str) -> list[dict]:
        lines = text.split("\n")
        entries = []
        current = None
        in_transactions = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Detect start of transaction section. The column layout has varied
            # over the years -- an older statement carries an extra "Card" column
            # -- and a header regex too narrow to match it meant the section never
            # opened and the whole statement parsed to nothing.
            if re.match(r"^Date\s+Description\b.*\b(Debit|Credit|Amount)\b", line):
                in_transactions = True
                continue

            if not in_transactions:
                continue

            # Exit transactions mode at footer/disclaimer
            if any(p.match(line) for p in STOP_PATTERNS):
                if current and current.get("amount") is not None:
                    entries.append(current)
                current = None
                in_transactions = False
                continue

            # Skip known non-transaction lines
            if any(p.match(line) for p in SKIP_PATTERNS):
                continue

            # Skip foreign currency continuation lines (e.g. "12.99 PLN")
            if FOREIGN_CURRENCY_RE.match(line):
                if current:
                    current["description"] += f" ({line})"
                continue

            # New transaction: starts with date
            date_match = DATE_RE.match(line)
            if date_match:
                if current:
                    entries.append(current)

                date_str = date_match.group(1)
                rest = date_match.group(2)

                # Extract amounts from end of line
                amounts = AMOUNT_RE.findall(rest)
                if amounts:
                    # Remove amounts from description
                    desc = rest
                    for amt in amounts:
                        desc = desc.replace(amt, "").strip()
                    # Clean trailing whitespace, location codes and the column marker.
                    desc = re.sub(r"\s+CR\b", " ", desc)
                    desc = re.sub(r"\s+", " ", desc).strip()

                    if len(amounts) == 1:
                        amt_val = self._parse_amount(amounts[0])
                        # Which COLUMN the amount sits in decides whether it is a
                        # charge or a credit. Extraction marks credit-column
                        # amounts with a trailing "CR"; guessing from the
                        # description instead turned every credit that did not
                        # happen to say "REFUND" into spending.
                        marked_credit = re.search(
                            re.escape(amounts[0]) + r"\s*CR\b", rest)
                        desc_upper = desc.upper()
                        keyword_credit = any(
                            w in desc_upper
                            for w in ["PAYMENT RECEIVED", "CREDIT", "REFUND", "REVERSAL"])
                        if marked_credit or keyword_credit:
                            amount = amt_val
                        else:
                            amount = -amt_val
                    elif len(amounts) == 2:
                        # Both debit and credit on same line (unusual)
                        amount = -self._parse_amount(amounts[0]) + self._parse_amount(amounts[1])
                    else:
                        amount = -self._parse_amount(amounts[0])

                    current = {
                        "date": self._normalize_date(date_str),
                        "description": desc,
                        "amount": amount,
                    }
                else:
                    # Date line with no amounts — description continues on next line
                    current = {
                        "date": self._normalize_date(date_str),
                        "description": rest.strip(),
                        "amount": None,
                    }
            elif current:
                # Continuation line
                amounts = AMOUNT_RE.findall(line)
                if amounts and current.get("amount") is None:
                    desc_part = line
                    for amt in amounts:
                        desc_part = desc_part.replace(amt, "").strip()
                    if desc_part:
                        current["description"] += " " + desc_part

                    amt_val = self._parse_amount(amounts[0])
                    desc_upper = current["description"].upper()
                    if any(w in desc_upper for w in ["PAYMENT RECEIVED", "CREDIT", "REFUND", "REVERSAL"]):
                        current["amount"] = amt_val
                    else:
                        current["amount"] = -amt_val
                else:
                    # Pure description continuation
                    current["description"] += " " + line

        if current and current.get("amount") is not None:
            entries.append(current)

        return entries

    def _build_transaction(self, entry: dict, file_path: Path, closing_balance: float | None = None) -> RawTransaction | None:
        if entry.get("amount") is None:
            return None
        date = entry.get("date", "")
        if not date:
            return None

        description = re.sub(r"\s+", " ", entry.get("description", "")).strip()
        if not description:
            return None

        return RawTransaction(
            date=date,
            description=description,
            amount=entry["amount"],
            currency="AUD",
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data={
                "date": entry.get("date", ""),
                "description": description,
                "amount": str(entry.get("amount", "")),
                "closing_balance": str(closing_balance) if closing_balance is not None else "",
            },
        )

    def _normalize_date(self, date_str: str) -> str:
        """Parse 'DD Mon YY' to 'YYYY-MM-DD'."""
        m = re.match(r"(\d{2})\s+(\w{3})\s+(\d{2})", date_str)
        if m:
            day, mon, year = m.group(1), m.group(2), m.group(3)
            month = MONTH_MAP.get(mon, "")
            if not month:
                return ""
            return f"20{year}-{month}-{day}"
        return ""

    def _parse_amount(self, s: str) -> float:
        return float(s.replace("$", "").replace(",", ""))
