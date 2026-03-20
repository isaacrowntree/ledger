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

# HSBC AU statements typically use:
#   DD Mon  or  DD Mon YY  or  DD/MM/YYYY
# with separate Debit/Credit columns, or a single signed amount column.
#
# Common layouts:
#   Date       Description                     Debit      Credit     Balance
#   01 Jan     EFTPOS PURCHASE WOOLWORTHS       45.50                 1,234.56
#   02 Jan     SALARY DEPOSIT                              3,200.00  4,434.56
#
# Or for credit cards:
#   Date       Description                     Amount
#   01 Jan     EFTPOS PURCHASE WOOLWORTHS       45.50
#   02 Jan     PAYMENT RECEIVED                -200.00

# Date at start of line: "DD Mon" or "DD Mon YY" or "DD/MM" or "DD/MM/YYYY"
DATE_RE = re.compile(
    r"^(\d{1,2}\s+\w{3}(?:\s+\d{2,4})?|\d{1,2}/\d{2}(?:/\d{2,4})?)\s+"
)

# One, two or three dollar amounts at end of line (debit, credit, balance)
AMOUNTS_3_RE = re.compile(
    r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
)
AMOUNTS_2_RE = re.compile(
    r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
)
AMOUNTS_1_RE = re.compile(
    r"(-?[\d,]+\.\d{2})\s*$"
)


class HSBCPDFParser(BaseParser):
    """
    Parse HSBC Australia PDF bank/credit card statements.

    Handles both everyday account statements (Debit/Credit/Balance columns)
    and credit card statements (single Amount column). Auto-detects the layout
    by checking whether a "balance" column is present.
    """

    source_type = "hsbc"

    def parse(self, file_path: Path) -> list[RawTransaction]:
        text = self._extract_text(file_path)
        statement_year = self._detect_year(text)
        has_balance_col = self._detect_balance_column(text)
        closing_balance = self._extract_closing_balance(text)
        entries = self._parse_entries(text, statement_year, has_balance_col)

        transactions = []
        for entry in entries:
            txn = self._build_transaction(entry, file_path, closing_balance)
            if txn:
                transactions.append(txn)
        return transactions

    def _extract_text(self, file_path: Path) -> str:
        pages = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n".join(pages)

    def _detect_year(self, text: str) -> str:
        """Try to find the statement year from header text like 'Statement Period: 01 Jan 2025 to 31 Jan 2025'."""
        m = re.search(r"(\d{1,2}\s+\w{3}\s+(\d{4}))\s+to\s+\d{1,2}\s+\w{3}\s+\d{4}", text)
        if m:
            return m.group(2)
        # Fallback: find any 4-digit year near top of document
        for line in text.split("\n")[:30]:
            ym = re.search(r"\b(20\d{2})\b", line)
            if ym:
                return ym.group(1)
        return "2025"

    def _detect_balance_column(self, text: str) -> bool:
        """Check if this looks like a 3-column layout (debit/credit/balance)."""
        header_line = text.lower()
        return "balance" in header_line and ("debit" in header_line or "credit" in header_line)

    def _extract_closing_balance(self, text: str) -> float | None:
        """Extract the closing balance from the statement header."""
        m = re.search(r"Closing Balance\s+\$?([\d,]+\.\d{2})", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return None

    def _parse_entries(self, text: str, year: str, has_balance_col: bool) -> list[dict]:
        lines = text.split("\n")
        entries = []
        current = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip non-transaction lines
            upper = line.upper()
            if any(skip in upper for skip in [
                "OPENING BALANCE", "CLOSING BALANCE", "BALANCE BROUGHT FORWARD",
                "BALANCE AUD", "BALANCE USD", "BALANCE EUR",
            ]):
                continue

            date_match = DATE_RE.match(line)
            if date_match:
                if current and current.get("amount") is not None:
                    entries.append(current)

                date_str = date_match.group(1).strip()
                rest = line[date_match.end():].strip()

                amount, balance, desc = self._extract_amounts(rest, has_balance_col)
                current = {
                    "date": self._normalize_date(date_str, year),
                    "description": desc,
                    "amount": amount,
                    "balance": balance,
                }
                if amount is not None:
                    entries.append(current)
                    current = None

            elif current and current.get("amount") is None:
                # Continuation line
                amount, balance, desc_part = self._extract_amounts(line, has_balance_col)
                if amount is not None:
                    if desc_part:
                        current["description"] += " " + desc_part
                    current["amount"] = amount
                    current["balance"] = balance
                    entries.append(current)
                    current = None
                else:
                    current["description"] += " " + line

        if current and current.get("amount") is not None:
            entries.append(current)

        return entries

    def _extract_amounts(self, text: str, has_balance_col: bool) -> tuple:
        """
        Extract amounts from end of line. Returns (amount, balance, remaining_description).
        For 3-col layout: debit, credit, balance — amount = -debit or +credit.
        For 2-col layout: amount, balance.
        For 1-col layout: signed amount.
        """
        # Try 3 amounts (debit, credit, balance)
        m = AMOUNTS_3_RE.search(text)
        if m:
            desc = text[:m.start()].strip()
            a, b, bal = m.group(1), m.group(2), m.group(3)
            a_val = self._parse_amount(a)
            b_val = self._parse_amount(b)
            bal_val = self._parse_amount(bal)
            # Convention: first non-zero of the pair is the transaction amount
            # Debits are negative (expenses)
            if a_val > 0 and b_val == 0:
                return -a_val, bal_val, desc
            elif b_val > 0:
                return b_val, bal_val, desc
            else:
                return -a_val, bal_val, desc

        # Try 2 amounts (amount, balance) or (debit, credit)
        m = AMOUNTS_2_RE.search(text)
        if m:
            desc = text[:m.start()].strip()
            a_val = self._parse_amount(m.group(1))
            b_val = self._parse_amount(m.group(2))
            if has_balance_col:
                # (amount, balance) — need to infer sign from context
                # Assume expense (negative) unless description suggests income
                return -a_val, b_val, desc
            else:
                # (debit, credit) — whichever is non-zero
                if a_val > 0:
                    return -a_val, None, desc
                return b_val, None, desc

        # Try 1 amount (credit card format: purchases positive, payments negative)
        # Negate so purchases become negative (expense) in our DB convention
        m = AMOUNTS_1_RE.search(text)
        if m:
            desc = text[:m.start()].strip()
            amount = self._parse_amount(m.group(1))
            return -amount, None, desc

        return None, None, text

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
                "description": entry.get("description", ""),
                "amount": str(entry.get("amount", "")),
                "balance": str(entry.get("balance", "")),
                "closing_balance": str(closing_balance) if closing_balance is not None else "",
            },
        )

    def _normalize_date(self, date_str: str, default_year: str) -> str:
        """Convert various HSBC date formats to YYYY-MM-DD."""
        # DD Mon YYYY or DD Mon YY
        m = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{2,4})", date_str)
        if m:
            day, mon, year = m.group(1), m.group(2), m.group(3)
            month = MONTH_MAP.get(mon, "")
            if not month:
                return ""
            if len(year) == 2:
                year = "20" + year
            return f"{year}-{month}-{day.zfill(2)}"

        # DD Mon (no year — use statement year)
        m = re.match(r"(\d{1,2})\s+(\w{3})", date_str)
        if m:
            day, mon = m.group(1), m.group(2)
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

        # DD/MM (no year)
        m = re.match(r"(\d{1,2})/(\d{2})", date_str)
        if m:
            day, month = m.group(1), m.group(2)
            return f"{default_year}-{month}-{day.zfill(2)}"

        return ""

    def _parse_amount(self, s: str) -> float:
        return float(s.replace(",", "").replace(" ", ""))
