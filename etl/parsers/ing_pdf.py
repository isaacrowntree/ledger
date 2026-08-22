import math
import re
from pathlib import Path

import pdfplumber

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.models import RawTransaction
from etl.parsers.base import (BaseParser, chronological, detect_convention,
                              labelled_balance, money, resign_unsigned_rows)

# ING AU statement format:
# Date        Details                          Money out $  Money in $  Balance $
# DD/MM/YYYY  Description line 1               -100.00                  1,234.56
#             Description continuation line
#
# Amounts: plain numbers with optional commas, negative = money out
# Descriptions can span multiple lines (continuation lines lack a date)

TXN_DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+)")

# Match lines ending with 1-3 amount columns (money_out, money_in, balance)
# Amounts are like: 100.00, 1,234.56, -100.00
# The line ends with the balance, optionally preceded by money out/in values
AMOUNT_TAIL_RE = re.compile(
    r"(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})$"
    r"|(-?[\d,]+\.\d{2})$"
)

# Skip non-transaction lines
SKIP_PATTERNS = [
    re.compile(r"^Page \d+ of \d+"),
    re.compile(r"^Transactions(\s+\(continued\))?$"),
    re.compile(r"^Transactions for \d+$"),
    re.compile(r"^Date\s+Details\s+(Money|Debit)"),
    re.compile(r"^Opening balance"),
    re.compile(r"^Closing balance"),
    re.compile(r"^1-[ISE]$"),
    re.compile(r"^0-[ISE]$"),
    re.compile(r"^\d+-[ISE]$"),
]

# Notices that sit in the transaction list but move no money. Matched anywhere in
# the line because they begin with a date, exactly like a real transaction:
#   "10/02/2026 RATE CHANGED FM 5.34% TO 5.59%"
# Left in, the interest RATE (5.34) is read as an amount and becomes a phantom
# transaction that breaks the statement's balance chain.
NON_TRANSACTION_PATTERNS = [
    re.compile(r"RATE CHANGED FM\s+[\d.]+%", re.I),
]

# Header pattern for loan statements: "Date Details Debit Credit Balance $"
LOAN_HEADER_RE = re.compile(r"^Date\s+Details\s+Debit\s+Credit\s+Balance")


class INGPDFParser(BaseParser):
    """Parse ING Australia PDF bank statements."""

    source_type = "ing"

    # An everyday account's balance moves the way the money moves. A mortgage
    # statement may quote the SAME debt as "-$307,416.41" or "307,153.72"
    # depending on layout, so the convention is settled per file below.
    balance_convention = BalanceConvention.SIGNED

    def parse_statement(self, file_path: Path) -> ParsedStatement:
        transactions = chronological(self._read(file_path))
        text = self._extract_text(file_path)

        rows = [
            ParsedRow(index=i, date=t.date, description=t.description, amount=t.amount,
                      balance=money((t.raw_data or {}).get("balance")),
                      raw=t.raw_data or {}, currency=t.currency,
                      original_amount=t.original_amount,
                      original_currency=t.original_currency, fee=t.fee,
                      reference_id=t.reference_id)
            for i, t in enumerate(transactions)
        ]

        # The summary block is authoritative and covers the whole statement,
        # including one with no transactions at all.
        opening, closing = summary_balances(text)
        if opening is None:
            opening = labelled_balance(text, "Opening balance")
        if closing is None:
            closing = labelled_balance(text, "Closing balance")

        # Interim statements print only a closing figure, but the running balance
        # on the first row implies the opening -- enough to check the arithmetic.
        if opening is None and rows and rows[0].balance is not None:
            opening = round(rows[0].balance - rows[0].amount, 2)
        if closing is None and rows and rows[-1].balance is not None:
            closing = rows[-1].balance

        statement = self.build(file_path, rows,
                               opening_balance=opening, closing_balance=closing)

        # Which way the balances point is decided by the balances, never by
        # prose: an everyday statement mentions "Orange Advantage" in a product
        # footer, and sniffing for that classified a transaction account as a
        # mortgage and inverted every amount on it.
        statement.balance_convention = detect_convention(rows, opening)
        resign_unsigned_rows(rows, statement.balance_convention, opening)

        return statement

    def _read(self, file_path: Path) -> list[RawTransaction]:
        text = self._extract_text(file_path)
        if self._is_interim(text):
            raw_entries = self._parse_interim_entries(text)
        else:
            raw_entries = self._parse_entries(text)
        transactions = []

        for entry in raw_entries:
            txn = self._build_transaction(entry, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _is_interim(self, text: str) -> bool:
        return "Interim statement" in text and "Deposit($)" in text

    def _parse_interim_entries(self, text: str) -> list[dict]:
        # Interim statements use: "DD Mon YYYY  Description  [-]$amount  [-]$balance"
        # Sign on the amount column tells direction (negative = withdrawal).
        txn_re = re.compile(
            r"^(\d{2}\s+[A-Za-z]{3}\s+\d{4})\s+(.+?)\s+(-?\$[\d,]+\.\d{2})\s+(-?\$[\d,]+\.\d{2})$"
        )
        entries: list[dict] = []
        current: dict | None = None
        in_transactions = False

        for raw in text.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("Date Description"):
                in_transactions = True
                continue
            if not in_transactions:
                continue
            if line.startswith("Brought Forward") or line.startswith("Closing Balance"):
                continue
            if line.startswith("Interim statements may") or line.startswith("transactions carefully") \
               or line.startswith("us a secure message") or line.startswith("ING is a business name") \
               or line.startswith("ABN ") or line.startswith("GPO Box") or line.startswith("www.ing.com.au"):
                if current:
                    entries.append(current)
                    current = None
                continue

            m = txn_re.match(line)
            if m:
                if current:
                    entries.append(current)
                date_str, desc, amount_str, balance_str = m.groups()
                amount = self._parse_signed_dollar(amount_str)
                current = {
                    "date": self._parse_interim_date(date_str),
                    "description": desc.strip(),
                    "money_out": amount_str if amount and amount < 0 else "",
                    "money_in": amount_str if amount and amount > 0 else "",
                    "amount": amount,
                    "balance": balance_str,
                }
            elif current is not None:
                current["description"] += " " + line

        if current:
            entries.append(current)
        return entries

    def _parse_signed_dollar(self, s: str) -> float | None:
        if not s:
            return None
        cleaned = s.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    _MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05",
               "Jun": "06", "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10",
               "Nov": "11", "Dec": "12"}

    def _parse_interim_date(self, s: str) -> str:
        # "01 Apr 2026" → "01/04/2026" (kept in DD/MM/YYYY for _build_transaction)
        parts = s.split()
        if len(parts) != 3:
            return ""
        day, mon, year = parts
        mm = self._MONTHS.get(mon)
        if not mm:
            return ""
        return f"{day}/{mm}/{year}"

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
            if re.match(r"^Date\s+Details\s+(Money|Debit)", line):
                in_transactions = True
                continue

            if not in_transactions:
                continue

            # Skip known non-transaction lines
            if any(p.search(line) for p in NON_TRANSACTION_PATTERNS):
                continue
            if any(p.match(line) for p in SKIP_PATTERNS):
                continue

            # Stop at footer / skip non-transaction loan lines
            if line.startswith("Statement continued"):
                continue

            # New transaction starts with a date
            date_match = TXN_DATE_RE.match(line)
            if date_match:
                # Save previous entry
                if current and current["amount"] is not None:
                    entries.append(current)
                elif current:
                    entries.append(current)

                date_str = date_match.group(1)
                rest = date_match.group(2)

                # Try to extract amounts from the end of this line
                amount_info = self._extract_amounts(rest)
                if amount_info:
                    desc_part, money_out, money_in, balance = amount_info
                    current = {
                        "date": date_str,
                        "description": desc_part,
                        "money_out": money_out,
                        "money_in": money_in,
                        "amount": self._compute_amount(money_out, money_in),
                        "balance": balance,
                    }
                else:
                    # Date line with description only, amounts on continuation
                    current = {
                        "date": date_str,
                        "description": rest,
                        "money_out": None,
                        "money_in": None,
                        "amount": None,
                        "balance": None,
                    }
            elif current:
                # Continuation line - stop if we hit footer/boilerplate
                if self._is_footer(line):
                    if current["amount"] is not None:
                        entries.append(current)
                    current = None
                    in_transactions = False
                    continue

                if current["amount"] is None:
                    # Still looking for amounts
                    amount_info = self._extract_amounts(line)
                    if amount_info:
                        desc_part, money_out, money_in, balance = amount_info
                        if desc_part:
                            current["description"] += " " + desc_part
                        current["money_out"] = money_out
                        current["money_in"] = money_in
                        current["amount"] = self._compute_amount(money_out, money_in)
                        current["balance"] = balance
                    else:
                        # More description text
                        current["description"] += " " + line
                else:
                    # Already have amounts, this is extra description for context
                    # (e.g. card details, foreign currency info)
                    current["description"] += " " + line

        # Don't forget the last entry
        if current:
            entries.append(current)

        return entries

    _FOOTER_MARKERS = [
        "Total Cashback Financial Year",
        "Total Interest Financial Year",
        "Total Interest for this statement",
        "Interest rate at end of statement",
        "Total Fees Financial Year",
        "Total Rebates Financial Year",
        "Total Rounded Up",
        "Please check all transactions carefully",
        "We recommend you retain",
        "Keeping you safe and secure",
        "ING takes the security",
        "There were no transactions",
        "Important Reminder",
        "Always here to help",
        "ING is a business name",
        "ING, a business name",
        "Client 1, Tax File",
        "Client 2, Tax File",
        "Any advice in this statement",
    ]

    def _is_footer(self, line: str) -> bool:
        return any(line.startswith(m) for m in self._FOOTER_MARKERS)

    def _extract_amounts(self, text: str) -> tuple[str, str, str, str] | None:
        """Extract amount columns from end of a line.

        ING uses 3 columns: Money out, Money in, Balance.
        A transaction has either money_out or money_in (not both), plus balance.
        So we see either 2 or 3 numbers at the end.

        Returns (description_part, money_out, money_in, balance) or None.
        """
        # Try to find 2+ numbers at the end of the line
        # Pattern: numbers separated by whitespace at end of string
        num_pattern = re.compile(r"(-?[\d,]+\.\d{2})")
        nums = list(num_pattern.finditer(text))

        if len(nums) < 2:
            return None

        # Take the last 2 or 3 numbers as amount columns
        # The rightmost is always balance
        # With 3 nums: money_out, money_in, balance (one of first two is usually empty/absent)
        # With 2 nums: either (money_out, balance) or (money_in, balance)

        # Find where amounts start
        first_amount_pos = nums[-2].start()
        # Check if there's a 3rd amount
        if len(nums) >= 3:
            candidate = nums[-3]
            # Only count as 3rd amount if it's close to the other amounts
            # (not part of description like "Receipt 123456")
            gap = nums[-2].start() - candidate.end()
            if gap < 15:
                first_amount_pos = candidate.start()

        desc_part = text[:first_amount_pos].strip()
        amount_text = text[first_amount_pos:].strip()

        # Parse the amount columns
        amount_nums = num_pattern.findall(amount_text)

        if len(amount_nums) == 3:
            return (desc_part, amount_nums[0], amount_nums[1], amount_nums[2])
        elif len(amount_nums) == 2:
            # Determine if it's (money_out, balance) or (money_in, balance)
            # Look at the raw text to see column positioning
            # If the first number is negative or preceded by -, it's money out
            val = self._parse_number(amount_nums[0])
            if val is not None and val < 0:
                return (desc_part, amount_nums[0], "", amount_nums[1])
            else:
                return (desc_part, "", amount_nums[0], amount_nums[1])

        return None

    def _compute_amount(self, money_out: str, money_in: str) -> float | None:
        """Compute transaction amount from money out/in columns."""
        out_val = self._parse_number(money_out) if money_out else None
        in_val = self._parse_number(money_in) if money_in else None

        if out_val is not None and out_val != 0:
            # Money out is shown as negative or positive; ensure it's negative
            return -abs(out_val)
        elif in_val is not None and in_val != 0:
            return abs(in_val)
        return None

    def _parse_number(self, s: str) -> float | None:
        if not s:
            return None
        cleaned = s.strip().replace(",", "").replace("$", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _build_transaction(self, entry: dict, file_path: Path) -> RawTransaction | None:
        date = self._parse_date(entry["date"])
        if not date:
            return None

        amount = entry.get("amount")
        if amount is None:
            return None

        description = self._clean_description(entry["description"])

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency="AUD",
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data={
                "date": entry["date"],
                "description": entry["description"],
                "money_out": entry.get("money_out", ""),
                "money_in": entry.get("money_in", ""),
                "balance": entry.get("balance", ""),
            },
        )

    def _parse_date(self, date_str: str) -> str:
        """Parse 'DD/MM/YYYY' to 'YYYY-MM-DD'."""
        if not date_str:
            return ""
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str)
        if not m:
            return ""
        day, month, year = m.groups()
        return f"{year}-{month}-{day}"

    def _clean_description(self, desc: str) -> str:
        desc = re.sub(r"\s+", " ", desc).strip()
        return desc


SUMMARY_LABELS = ("Opening balance", "Total money in", "Total money out", "Closing balance")
_MONEY_RE = re.compile(r"-?\$-?[\d,]+\.\d{2}")


def summary_balances(text: str) -> tuple[float | None, float | None]:
    """Opening and closing balance from ING's summary block.

    ING prints the four labels and their four values in separate blocks -- either
    all labels on one line or one per line -- so the values are read positionally
    rather than by pairing each label with the next number on the page, which
    would marry unrelated figures.

    Returns (None, None) unless the whole block is present and complete: a
    partial block is not worth guessing at, and a wrong opening balance would
    make every statement fail validation for the wrong reason.
    """
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if all(label in line for label in SUMMARY_LABELS):
            values = _following_values(lines, i + 1)
            return (values[0], values[3]) if values else (None, None)

    for i in range(len(lines) - 3):
        window = [lines[i + n].strip() for n in range(4)]
        if all(window[n].startswith(SUMMARY_LABELS[n]) for n in range(4)):
            values = _following_values(lines, i + 4)
            return (values[0], values[3]) if values else (None, None)

    return (None, None)


def _following_values(lines: list[str], start: int) -> list[float] | None:
    """The next four money figures at or after `start`, in printed order."""
    found: list[float] = []
    for line in lines[start:start + 8]:
        for match in _MONEY_RE.findall(line):
            found.append(float(match.replace("$", "").replace(",", "")))
            if len(found) == 4:
                return found
    return None
