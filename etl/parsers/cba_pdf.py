"""Parser for Commonwealth Bank transaction-account (Smart Access) statements.

Layout:

    Date  Transaction                       Debit    Credit     Balance
    01 Aug 2025 OPENING BALANCE                              10,000.00 CR
    01 Aug Transfer To Jane Bennett
           CommBank App rent               1,200.00          8,800.00 CR
    01 Aug Direct Credit 111111 Harbour Realty
           RENT PAYMENT                              1,500.00 11,500.00 CR

Three things make this format hostile to naive text extraction:

* Debits and credits are both printed as bare positive numbers, so only the
  column tells them apart — see ``pdf_layout``.
* The PDF contains no space characters at all. Word gaps are positional and
  fall just under pdfplumber's default tolerance, so the default flattens
  "Transfer To Jane Bennett" into "TransferToJaneBennett".
* An entry runs over as many lines as its description needs, and the amount
  lands on the entry's *last* line.

Every entry's running balance is checked against the previous one, which
means a dropped or misread row cannot pass silently.
"""
import re
from datetime import date
from pathlib import Path

from etl.models import RawTransaction
from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.parsers.base import BaseParser, statement_from_transactions
from etl.parsers.pdf_layout import (
    MONTH_MAP,
    ColumnRuler,
    Row,
    StatementParseError,
    extract_rows,
    parse_amount,
    parse_balance,
)

# The PDF has no space glyphs; anything at or above pdfplumber's default of 3
# welds adjacent words together.
X_TOLERANCE = 1.5

HEADER_LABELS = {"debit": "Debit", "credit": "Credit", "balance": "Balance"}
HEADER_REQUIRED = ("DATE", "TRANSACTION", "DEBIT", "CREDIT", "BALANCE")

DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})\b")
PERIOD_RE = re.compile(
    r"Period\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})",
    re.IGNORECASE,
)
ACCOUNT_RE = re.compile(r"Account Number\s+([\d\s]+\d)", re.IGNORECASE)
OPENING_RE = re.compile(r"OPENING BALANCE", re.IGNORECASE)
CLOSING_RE = re.compile(r"CLOSING BALANCE", re.IGNORECASE)
# The reconciliation table printed after the last entry.
TOTALS_RE = re.compile(r"Opening balance\s+-\s+Total debits", re.IGNORECASE)

CENTS = 0.005


class CBAPDFParser(BaseParser):
    """Parse Commonwealth Bank transaction-account PDF statements."""

    source_type = "cba"

    # A transaction account: the balance follows the money.
    balance_convention = BalanceConvention.SIGNED

    def parse_statement(self, file_path: Path) -> ParsedStatement:
        rows = extract_rows(file_path, x_tolerance=X_TOLERANCE)
        period_start, period_end = self._extract_period(rows, file_path)
        account_number = self._extract_account_number(rows)

        entries, opening, closing = self._collect_entries(rows, period_start, period_end, file_path)
        self._verify(entries, opening, closing, file_path)

        transactions = [
            RawTransaction(
                date=entry["date"],
                description=entry["description"],
                amount=entry["amount"],
                currency="AUD",
                source_type=self.source_type,
                source_file=str(file_path),
                raw_data={
                    "date": entry["date"],
                    "description": entry["description"],
                    "amount": f"{entry['amount']:.2f}",
                    "balance": f"{entry['balance']:.2f}",
                    "account_number": account_number,
                    "statement_period": f"{period_start.isoformat()}..{period_end.isoformat()}",
                },
            )
            for entry in entries
        ]
        return statement_from_transactions(
            self, file_path, transactions,
            opening_balance=opening, closing_balance=closing,
            period_start=period_start.isoformat(), period_end=period_end.isoformat())

    # -- statement metadata -------------------------------------------------

    def _extract_period(self, rows: list[Row], file_path: Path) -> tuple[date, date]:
        for row in rows:
            m = PERIOD_RE.search(row.text)
            if m:
                return (
                    _make_date(int(m.group(1)), m.group(2), int(m.group(3))),
                    _make_date(int(m.group(4)), m.group(5), int(m.group(6))),
                )
        raise StatementParseError(f"{file_path.name}: no statement period found")

    def _extract_account_number(self, rows: list[Row]) -> str:
        for row in rows:
            m = ACCOUNT_RE.search(row.text)
            if m:
                return re.sub(r"\s+", "", m.group(1))
        return ""

    # -- transaction table --------------------------------------------------

    def _collect_entries(
        self, rows: list[Row], period_start: date, period_end: date, file_path: Path
    ) -> tuple[list[dict], float | None, float | None]:
        entries: list[dict] = []
        opening = closing = None
        ruler: ColumnRuler | None = None
        date_x0 = 0.0
        pending: dict | None = None
        current_page = None

        for row in rows:
            # Each page repeats the column header. Dropping the ruler at a page
            # break stops the masthead of the next page being swallowed as a
            # continuation of the last entry.
            if row.page != current_page:
                _require_complete(pending, file_path)
                current_page, ruler = row.page, None

            if ruler is None:
                built = self._build_ruler(row)
                if built:
                    ruler, date_x0 = built
                continue

            row = row.drop_left_of(date_x0 - 10)
            if not row.words:
                continue

            text = row.text
            if TOTALS_RE.search(text):
                ruler = None
                continue

            amounts = ruler.amounts_in(row)

            if OPENING_RE.search(text):
                opening = self._balance_of(row, ruler, file_path, text)
                continue
            if CLOSING_RE.search(text):
                closing = self._balance_of(row, ruler, file_path, text)
                continue

            starts_entry = row.first_x0 <= date_x0 + 6 and DATE_RE.match(text)
            if starts_entry:
                _require_complete(pending, file_path)
                m = DATE_RE.match(text)
                pending = {
                    "date": _resolve_date(
                        int(m.group(1)), m.group(2), period_start, period_end, file_path
                    ),
                    "parts": [],
                }
                consumed = len(m.group(0).split())
                description_words = row.words[consumed:]
            elif pending is None:
                continue  # page furniture between the header and the first entry
            else:
                description_words = row.words

            pending["parts"].extend(
                w.text for w in description_words if not ruler.is_column_amount(w)
            )

            if "balance" not in amounts:
                continue

            balance = parse_balance(amounts["balance"][1])
            debit = amounts.get("debit")
            credit = amounts.get("credit")
            if (debit is None) == (credit is None):
                raise StatementParseError(
                    f"{file_path.name}: expected exactly one of debit/credit in row: {text!r}"
                )

            pending["amount"] = -debit[0] if debit else credit[0]
            pending["balance"] = balance
            pending["description"] = " ".join(pending["parts"]).strip()
            del pending["parts"]
            entries.append(pending)
            pending = None

        _require_complete(pending, file_path)
        return entries, opening, closing

    def _build_ruler(self, row: Row) -> tuple[ColumnRuler, float] | None:
        texts = [w.text.upper() for w in row.words]
        if not all(label in texts for label in HEADER_REQUIRED):
            return None
        ruler = ColumnRuler.from_header(row, HEADER_LABELS)
        if ruler is None:
            return None
        return ruler, row.words[texts.index("DATE")].x0

    def _balance_of(self, row: Row, ruler: ColumnRuler, file_path: Path, text: str) -> float:
        amounts = ruler.amounts_in(row)
        if "balance" not in amounts:
            raise StatementParseError(f"{file_path.name}: no balance in row: {text!r}")
        return parse_balance(amounts["balance"][1])

    # -- reconciliation -----------------------------------------------------

    def _verify(
        self, entries: list[dict], opening: float | None, closing: float | None, file_path: Path
    ) -> None:
        """Walk the running balance from opening to closing.

        Each entry's printed balance must equal the previous balance plus the
        signed amount. A misread column, a dropped row or a stray amount all
        break the chain, so a clean walk is strong evidence the parse is right.
        """
        if opening is None or closing is None:
            raise StatementParseError(f"{file_path.name}: missing opening or closing balance")

        balance = opening
        for entry in entries:
            expected = balance + entry["amount"]
            if abs(expected - entry["balance"]) > CENTS:
                raise StatementParseError(
                    f"{file_path.name}: balance mismatch on {entry['date']} "
                    f"'{entry['description']}': {balance:.2f} + {entry['amount']:.2f} "
                    f"= {expected:.2f}, statement says {entry['balance']:.2f}"
                )
            balance = entry["balance"]

        if abs(balance - closing) > CENTS:
            raise StatementParseError(
                f"{file_path.name}: final balance {balance:.2f} != closing balance {closing:.2f}"
            )


def _require_complete(pending: dict | None, file_path: Path) -> None:
    """An entry must reach a row carrying its balance before anything else starts."""
    if pending is not None:
        description = " ".join(pending["parts"]).strip() or "(no description)"
        raise StatementParseError(
            f"{file_path.name}: entry on {pending['date']} '{description}' has no amount"
        )


def _make_date(day: int, month_name: str, year: int) -> date:
    month = MONTH_MAP.get(month_name.upper())
    if not month:
        raise StatementParseError(f"unrecognised month {month_name!r}")
    return date(year, int(month), day)


def _resolve_date(
    day: int, month_name: str, period_start: date, period_end: date, file_path: Path
) -> str:
    """Restore the year, which entry rows omit.

    A statement period may straddle new year (1 Aug 2025 - 31 Jan 2026), so the
    year is whichever of the two lands the date inside the period.
    """
    for year in (period_start.year, period_end.year):
        try:
            candidate = _make_date(day, month_name, year)
        except ValueError:
            continue
        if period_start <= candidate <= period_end:
            return candidate.isoformat()
    raise StatementParseError(
        f"{file_path.name}: date {day} {month_name} falls outside "
        f"{period_start.isoformat()}..{period_end.isoformat()}"
    )
