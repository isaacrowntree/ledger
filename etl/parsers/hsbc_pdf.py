import re
from pathlib import Path

import pdfplumber

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.models import RawTransaction
from etl.parsers.dates import StatementPeriod, parse_period, resolve_year
from etl.parsers.base import (BaseParser, chronological, detect_convention,
                              labelled_balance, money, resign_unsigned_rows)

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

# Credit card statements mark credits (payments, refunds) with a minus BEFORE the
# dollar sign -- "7201 BPAY PAYMENT -$4,267.00" -- or with a trailing minus.
# AMOUNTS_1_RE cannot see a minus across the "$", so credits are detected here.
CREDIT_MARKER_RE = re.compile(
    r"(?:-\s*\$\s*[\d,]+\.\d{2}|[\d,]+\.\d{2}\s*-)\s*$"
)


# Charges HSBC prints in the transaction list with NO date against them, e.g.
# "OVERSEAS TRANSACTION FEE $6.86". They are real money and must be captured, or
# the rows fall short of the statement's own closing balance. Matched by an
# explicit whitelist of charge names, because the page carries plenty of other
# dollar figures ("Minimum Payment $20.00") that are not transactions.
UNDATED_CHARGE_RE = re.compile(
    r"^((?:OVERSEAS TRANSACTION FEE|OVERSEAS ATM FEE|INTEREST ON PURCHASE(?:S)?|"
    r"INTEREST ON CASH ADVANCE(?:S)?|INTEREST CHARGED|ANNUAL FEE|CARD FEE|"
    r"LATE PAYMENT FEE|CASH ADVANCE FEE|OVERLIMIT FEE|OVER LIMIT FEE))"
    r"\s+\$?([\d,]+\.\d{2})\s*$", re.I)


# Promotional offers are printed as date-prefixed lines inside the statement --
# "28 Sep 20 BALANCE TRANSFER 15 MONTHS 7.99% $4,000.00" -- and read as
# transactions they become phantom rows that stop the statement adding up. A real
# transaction line never quotes an interest rate.
PROMOTIONAL_RE = re.compile(r"\d+\.\d{1,2}\s*%")


class HSBCPDFParser(BaseParser):
    """
    Parse HSBC Australia PDF bank/credit card statements.

    Handles both everyday account statements (Debit/Credit/Balance columns)
    and credit card statements (single Amount column). Auto-detects the layout
    by checking whether a "balance" column is present.
    """

    source_type = "hsbc"

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
        opening = labelled_balance(text, "Opening [Bb]alance")
        closing = labelled_balance(text, "Closing [Bb]alance")
        if opening is None or closing is None:
            # The transaction-account layout prints its balances elsewhere.
            account_opening, account_closing = account_statement_balances(text)
            opening = opening if opening is not None else account_opening
            closing = closing if closing is not None else account_closing

        statement = self.build(file_path, rows,
                               opening_balance=opening, closing_balance=closing)

        # HSBC issues both credit cards (balance = what is owed) and day to day
        # accounts (balance = what is there). Assuming one would invert every
        # amount on the other, so the balances decide.
        # A "Financial Statement" is a transaction account: its balance falls
        # when money leaves. Anything else from HSBC is a credit card, whose
        # balance is what is owed. Per-row balances, where printed, override.
        # "BALANCE BROUGHT FORWARD" is what distinguishes a transaction account.
        # Keying on a closing balance instead misread a CARD statement that
        # prints "CLOSING BALANCE" inside its transaction list, handing it the
        # wrong convention and inverting the whole statement.
        is_transaction_account = _BROUGHT_FORWARD_RE.search(text) is not None
        default = (BalanceConvention.SIGNED if is_transaction_account
                   else BalanceConvention.OWING)
        statement.balance_convention = detect_convention(rows, opening, default=default)
        resign_unsigned_rows(rows, statement.balance_convention, opening)
        return statement

    def _read(self, file_path: Path) -> list[RawTransaction]:
        text = self._extract_text(file_path)
        statement_year, _ = self._detect_year_and_end_month(text)
        statement_period = parse_period(text)
        has_balance_col = self._detect_balance_column(text)
        closing_balance = self._extract_closing_balance(text)
        entries = self._parse_entries(text, statement_year, has_balance_col, statement_period)

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

    def _detect_year_and_end_month(self, text: str) -> tuple[str, int | None]:
        """Find the statement period END year and month, e.g. 'Statement Period: 15 Dec 2024 to 14 Jan 2025'.

        Anchoring on the period end (not start) plus the end month lets
        _normalize_date handle Dec-Jan statements: a December transaction on a
        statement ending in January belongs to the previous year.
        """
        m = re.search(r"\d{1,2}\s+\w{3}\s+\d{4}\s+to\s+\d{1,2}\s+(\w{3})\s+(\d{4})", text)
        if m:
            return m.group(2), int(MONTH_MAP.get(m.group(1), 0)) or None
        # Fallback: find any 4-digit year near top of document
        for line in text.split("\n")[:30]:
            ym = re.search(r"\b(20\d{2})\b", line)
            if ym:
                return ym.group(1), None
        return "2025", None

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

    def _parse_entries(self, text: str, year: str, has_balance_col: bool, period: StatementPeriod | None = None) -> list[dict]:
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

            if PROMOTIONAL_RE.search(line):
                continue

            undated = UNDATED_CHARGE_RE.match(line)
            if undated and not DATE_RE.match(line):
                if current and current.get("amount") is not None:
                    entries.append(current)
                    current = None
                # Attribute to the last dated transaction, or to the end of the
                # statement period when the fee is printed before any.
                when = entries[-1]["date"] if entries else (
                    period.end if period else self._normalize_date("", year, period))
                entries.append({
                    "date": when,
                    "description": undated.group(1).strip(),
                    # A fee is a charge: negative, like any other purchase.
                    "amount": -float(undated.group(2).replace(",", "")),
                    "balance": None,
                })
                continue

            date_match = DATE_RE.match(line)
            if date_match:
                if current and current.get("amount") is not None:
                    entries.append(current)

                date_str = date_match.group(1).strip()
                rest = line[date_match.end():].strip()

                amount, balance, desc = self._extract_amounts(rest, has_balance_col)
                current = {
                    "date": self._normalize_date(date_str, year, period),
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

        # Try 1 amount (credit card format). Purchases print unsigned and become
        # negative (expense) in our convention; credits are flagged by a minus
        # before the "$" or a trailing minus, and stay positive.
        m = AMOUNTS_1_RE.search(text)
        if m:
            desc = text[:m.start()].strip()
            amount = self._parse_amount(m.group(1))
            if amount is None:
                return None, None, text
            is_credit = bool(CREDIT_MARKER_RE.search(text))
            return (abs(amount) if is_credit else -abs(amount)), None, desc

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

    def _normalize_date(self, date_str: str, default_year: str, period: StatementPeriod | None = None) -> str:
        def infer_year(day: str, month: str) -> str:
            """The year that places this day/month inside the statement period.

            The previous rule compared the month against the statement's end
            month, which mis-assigned any period spanning a month boundary
            mid-year and put transactions in the wrong FINANCIAL year.
            """
            resolved = resolve_year(int(day), int(month), period)
            return str(resolved) if resolved else default_year

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
            return f"{infer_year(day, month)}-{month}-{day.zfill(2)}"

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
            return f"{infer_year(day, month)}-{month}-{day.zfill(2)}"

        return ""

    def _parse_amount(self, s: str) -> float:
        return float(s.replace(",", "").replace(" ", ""))


# HSBC's transaction-account statements ("Financial Statement") print their
# balances differently from the credit card ones: an opening "Balance Brought
# Forward" row and a per-account "Balance" in the account header. Without these a
# dormant account parses to no rows AND no balances, which cannot be told apart
# from a failed parse.
_BROUGHT_FORWARD_RE = re.compile(r"Balance Brought Forward\s+\$?(-?[\d,]+\.\d{2})", re.I)
_ACCOUNT_BALANCE_RE = re.compile(
    r"Account(?:\s+No\.?)?\s+\S+\s+(?:Currency\s+\w+\s+)?Balance\s+\$?(-?[\d,]+\.\d{2})", re.I)


_CLOSING_BALANCE_RE = re.compile(r"CLOSING BALANCE\s+\$?(-?[\d,]+\.\d{2})", re.I)


def account_statement_balances(text: str) -> tuple[float | None, float | None]:
    """(opening, closing) for HSBC's transaction-account layout, else (None, None)."""
    # Read each independently: requiring both meant one unmatched pattern threw
    # away the other, leaving a statement with no balances at all and therefore
    # indistinguishable from a failed parse.
    opening = _BROUGHT_FORWARD_RE.search(text)
    closing = _ACCOUNT_BALANCE_RE.search(text) or _CLOSING_BALANCE_RE.search(text)
    return (float(opening.group(1).replace(",", "")) if opening else None,
            float(closing.group(1).replace(",", "")) if closing else None)
