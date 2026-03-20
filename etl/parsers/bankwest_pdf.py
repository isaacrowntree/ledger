import re
from pathlib import Path

import pdfplumber

from etl.models import RawTransaction
from etl.parsers.base import BaseParser

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


class BankwestPDFParser(BaseParser):
    """Parse Bankwest Australia credit card PDF eStatements."""

    source_type = "bankwest"

    def parse(self, file_path: Path) -> list[RawTransaction]:
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
        pages = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n".join(pages)

    def _parse_entries(self, text: str) -> list[dict]:
        lines = text.split("\n")
        entries = []
        current = None
        in_transactions = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Detect start of transaction section
            if re.match(r"^Date\s+Description\s+(Debit|Amount)", line):
                in_transactions = True
                continue

            if not in_transactions:
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
                    # Clean trailing whitespace and location codes
                    desc = re.sub(r"\s+", " ", desc).strip()

                    if len(amounts) == 1:
                        amt_val = self._parse_amount(amounts[0])
                        # Check column position to determine debit vs credit
                        # Credits appear later in the line (further right)
                        amt_pos = rest.rfind(amounts[0])
                        # If the amount is near the end AND there's significant space before it,
                        # it could be in either column. Use context: "PAYMENT" / "RECEIVED" = credit
                        desc_upper = desc.upper()
                        if any(w in desc_upper for w in ["PAYMENT RECEIVED", "CREDIT", "REFUND", "REVERSAL"]):
                            amount = amt_val  # credit (positive)
                        else:
                            amount = -amt_val  # debit (negative = expense)
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
