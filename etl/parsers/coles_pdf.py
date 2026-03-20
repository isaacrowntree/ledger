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

# Coles Mastercard (formerly Citi) credit card statements.
#
# Typical layout:
#   Transaction Date  Post Date  Description                   Amount
#   01 Jan            03 Jan     WOOLWORTHS 1234 SYDNEY         $45.50
#   02 Jan            02 Jan     PAYMENT RECEIVED              -$200.00 CR
#
# Or:
#   Date       Description                     Amount
#   01/01      WOOLWORTHS                       45.50
#   02/01      PAYMENT THANK YOU               -200.00
#
# Credits (payments) shown with CR suffix or negative sign.
# All amounts are in AUD.

# Date patterns — transaction date at start of line
# Handles: "01 Jan", "01 Jan 25", "01/01", "01/01/2025", "Jan 01" (old Coles format)
DATE_RE = re.compile(
    r"^(\d{1,2}\s+\w{3}(?:\s+\d{2,4})?|\d{1,2}/\d{2}(?:/\d{2,4})?|\w{3}\s+\d{1,2})\s+"
)

# Amount at end of line, optionally with $ sign and CR/DR suffix
AMOUNT_RE = re.compile(
    r"(-?\$?[\d,]+\.\d{2})\s*(CR|DR)?\s*$", re.IGNORECASE
)

# Old Coles format: amount at end after a long reference number
# e.g. "Zippay P665399540 Sydney Au 85424784348449865990798 80.00"
OLD_AMOUNT_RE = re.compile(
    r"(\d{10,})\s+(-?[\d,]+\.\d{2})\s*$"
)

# Post date pattern (to skip/consume it)
# Handles: "01 Jan", "01/01", "01/01/25", "Jan 01"
POST_DATE_RE = re.compile(
    r"^(\d{1,2}\s+\w{3}|\d{1,2}/\d{2}(?:/\d{2,4})?|\w{3}\s+\d{1,2})\s+"
)


class ColesCreditPDFParser(BaseParser):
    """
    Parse Coles Mastercard credit card PDF statements.

    Convention: purchases are negative (expenses), payments/credits are positive (income).
    This matches the rest of Ledger where negative = money out.
    Credit card statements show purchases as positive and payments as credits,
    so we flip the sign: purchases → negative, payments → positive.
    """

    source_type = "coles"

    def parse(self, file_path: Path) -> list[RawTransaction]:
        text = self._extract_text(file_path)
        statement_year = self._detect_year(text)
        closing_balance = self._extract_closing_balance(text)
        entries = self._parse_entries(text, statement_year)

        transactions = []
        for entry in entries:
            txn = self._build_transaction(entry, file_path, closing_balance)
            if txn:
                transactions.append(txn)
        return transactions

    def _extract_closing_balance(self, text: str) -> float | None:
        """Extract closing balance from statement header."""
        m = re.search(r"Closing Balance\s+\$?([\d,]+\.\d{2})", text)
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

    def _detect_year(self, text: str) -> str:
        """Find statement year from header."""
        # Look for "Statement Period DD/MM/YY - DD/MM/YY" (new NAB format)
        m = re.search(r"Statement Period\s+\d{1,2}/\d{2}/(\d{2})\s+-\s+\d{1,2}/\d{2}/(\d{2})", text)
        if m:
            return "20" + m.group(2)
        # Look for "Statement Period: ... YYYY" or "Statement Ends DD Month YYYY"
        m = re.search(r"Statement\s+(?:Ends|Period)[:\s]+.*?(\d{4})", text, re.IGNORECASE)
        if m:
            return m.group(1)
        # Look for month-year headers like "Page 1 of 2, January 2025" or "July 2019"
        m = re.search(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})", text)
        if m:
            return m.group(1)
        # Fallback: find "20XX" year in first 30 lines, but skip account/card numbers
        for line in text.split("\n")[:30]:
            # Skip lines with card/account numbers
            if re.search(r"\d{4}\s+\d{4}\s+\d{4}", line):
                continue
            ym = re.search(r"\b(20\d{2})\b", line)
            if ym:
                return ym.group(1)
        return "2025"

    def _parse_entries(self, text: str, year: str) -> list[dict]:
        lines = text.split("\n")
        entries = []
        current = None
        in_transactions = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Detect start of transaction section
            lower = line.lower()
            if any(marker in lower for marker in ["transaction", "date", "description"]):
                if "amount" in lower:
                    in_transactions = True
                    continue

            # Detect end markers
            if any(marker in lower for marker in [
                "closing balance", "minimum payment",
                "opening balance", "statement summary", "credit limit",
                "payment due", "important information",
            ]):
                if current and current.get("amount") is not None:
                    entries.append(current)
                    current = None
                continue

            # Skip non-transaction lines
            if any(marker in lower for marker in [
                "card number", "primary card", "page ", "customer support",
                "account number", "account name", "statement begins",
                "statement ends",
            ]):
                continue

            if not in_transactions:
                continue

            date_match = DATE_RE.match(line)
            if date_match:
                if current and current.get("amount") is not None:
                    entries.append(current)

                date_str = date_match.group(1).strip()
                rest = line[date_match.end():].strip()

                # Check if there's a post date at the start of the rest
                rest = self._consume_post_date(rest)

                amount, is_credit, desc = self._extract_amount(rest)
                current = {
                    "date": self._normalize_date(date_str, year),
                    "description": desc,
                    "amount": amount,
                    "is_credit": is_credit,
                }
                if amount is not None:
                    entries.append(current)
                    current = None

            elif current and current.get("amount") is None:
                amount, is_credit, desc_part = self._extract_amount(line)
                if amount is not None:
                    if desc_part:
                        current["description"] += " " + desc_part
                    current["amount"] = amount
                    current["is_credit"] = is_credit
                    entries.append(current)
                    current = None
                else:
                    current["description"] += " " + line

        if current and current.get("amount") is not None:
            entries.append(current)

        return entries

    def _consume_post_date(self, text: str) -> str:
        """If text starts with another date (post date), skip it."""
        m = POST_DATE_RE.match(text)
        if m:
            return text[m.end():].strip()
        return text

    def _extract_amount(self, text: str) -> tuple:
        """Returns (amount_float, is_credit, remaining_description)."""
        # Try standard format first: $45.50 DR/CR
        m = AMOUNT_RE.search(text)
        if m:
            desc = text[:m.start()].strip()
            raw = m.group(1).replace("$", "").replace(",", "").replace(" ", "")
            amount = float(raw)
            suffix = (m.group(2) or "").upper()
            is_credit = suffix == "CR" or amount < 0
            return abs(amount), is_credit, desc

        # Try old format: description REFERENCE_NUMBER 80.00
        m = OLD_AMOUNT_RE.search(text)
        if m:
            desc = text[:m.start()].strip()
            amount = float(m.group(2).replace(",", ""))
            is_credit = amount < 0
            return abs(amount), is_credit, desc

        return None, False, text

    def _build_transaction(self, entry: dict, file_path: Path, closing_balance: float | None = None) -> RawTransaction | None:
        if entry.get("amount") is None:
            return None
        date = entry.get("date", "")
        if not date:
            return None

        description = re.sub(r"\s+", " ", entry.get("description", "")).strip()
        if not description:
            return None

        # Credit card convention flip:
        # Purchases on statement are positive → we store as negative (expense)
        # Payments/credits on statement → we store as positive (income/payment)
        amount = entry["amount"]
        if entry.get("is_credit"):
            amount = abs(amount)   # Payment received = positive
        else:
            amount = -abs(amount)  # Purchase = negative

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency="AUD",
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data={
                "date": entry.get("date", ""),
                "description": entry.get("description", ""),
                "amount": str(entry.get("amount", "")),
                "is_credit": str(entry.get("is_credit", False)),
                "closing_balance": str(closing_balance) if closing_balance is not None else "",
            },
        )

    def _normalize_date(self, date_str: str, default_year: str) -> str:
        # DD Mon YYYY
        m = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{2,4})", date_str)
        if m:
            day, mon, year = m.group(1), m.group(2), m.group(3)
            month = MONTH_MAP.get(mon, "")
            if not month:
                return ""
            if len(year) == 2:
                year = "20" + year
            return f"{year}-{month}-{day.zfill(2)}"

        # DD Mon (no year)
        m = re.match(r"(\d{1,2})\s+(\w{3})$", date_str)
        if m:
            day, mon = m.group(1), m.group(2)
            month = MONTH_MAP.get(mon, "")
            if not month:
                return ""
            return f"{default_year}-{month}-{day.zfill(2)}"

        # Mon DD (old Coles format, e.g. "Dec 13")
        m = re.match(r"(\w{3})\s+(\d{1,2})", date_str)
        if m:
            mon, day = m.group(1), m.group(2)
            month = MONTH_MAP.get(mon, "")
            if not month:
                return ""
            return f"{default_year}-{month}-{day.zfill(2)}"

        # DD/MM/YYYY
        m = re.match(r"(\d{1,2})/(\d{2})/(\d{2,4})", date_str)
        if m:
            day, month, year = m.group(1), m.group(2), m.group(3)
            if len(year) == 2:
                year = "20" + year
            return f"{year}-{month}-{day.zfill(2)}"

        # DD/MM
        m = re.match(r"(\d{1,2})/(\d{2})", date_str)
        if m:
            day, month = m.group(1), m.group(2)
            return f"{default_year}-{month}-{day.zfill(2)}"

        return ""
