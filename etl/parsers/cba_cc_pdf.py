"""Parser for Commonwealth Bank credit card (Awards Mastercard) statements.

Layout:

    Date   Transaction details                        Amount (A$)
    26 Nov Corner Cafe Newtown Newtown                  10.26
    01 Dec Payment Received, Thank You                    500.00-
    09 Dec Games Store Seattle                           31.48
           ## DEU MERCHANT
    23 Dec Monthly Fee                                       8.00

Unlike the transaction-account statement there is one amount column, and a
credit is marked by a *trailing* minus rather than by position. Entries carry
no year, so it is taken from the statement period, which may straddle new
year. Interest lines are printed without a date and are dated to the end of
the period.

There is no running balance to walk, so the parse is instead reconciled
against the payment summary on page 1: charges and payments must each add up
to their printed totals, and opening + charges - payments must land on the
printed closing balance.
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
)

HEADER_LABELS = {"amount": "Amount (A$)"}
HEADER_REQUIRED = ("DATE", "TRANSACTION", "DETAILS", "AMOUNT")

DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})\b")
PERIOD_RE = re.compile(
    r"Statement Period\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})",
    re.IGNORECASE,
)
CARD_RE = re.compile(r"Account\s+((?:\d{4}\s+){3}\d{4})", re.IGNORECASE)
INTEREST_RE = re.compile(r"^Interest charged on", re.IGNORECASE)

OPENING_RE = re.compile(r"Opening balance at .{0,12}?\$([\d,]+\.\d{2})", re.IGNORECASE)
CLOSING_RE = re.compile(r"Closing balance at .{0,12}?\$([\d,]+\.\d{2})", re.IGNORECASE)
CHARGES_RE = re.compile(r"New transactions and charges\s+\$([\d,]+\.\d{2})", re.IGNORECASE)
PAYMENTS_RE = re.compile(r"Payments/refunds\s+-?\$([\d,]+\.\d{2})", re.IGNORECASE)

# Everything below the transaction table: the recurring-payments listing prints
# amounts too, and its rows must never be mistaken for transactions.
STOP_PATTERNS = [
    re.compile(r"^Please check your transactions", re.IGNORECASE),
    re.compile(r"^Helping you identify your regular payments", re.IGNORECASE),
    re.compile(r"^Mastercard is the registered trademark", re.IGNORECASE),
    re.compile(r"^Things you should know", re.IGNORECASE),
]

CENTS = 0.005


class CBACreditPDFParser(BaseParser):
    """Parse Commonwealth Bank credit card PDF statements."""

    source_type = "cba-cc"

    # A credit card statement quotes what is OWED. There is no per-entry
    # balance, so the engine checks it against the printed summary instead.
    balance_convention = BalanceConvention.OWING

    def parse_statement(self, file_path: Path) -> ParsedStatement:
        rows = extract_rows(file_path)
        period_start, period_end = self._extract_period(rows, file_path)
        card = self._extract_card(rows)
        summary = self._extract_summary(rows, file_path)

        entries = self._collect_entries(rows, period_start, period_end, file_path)
        self._verify(entries, summary, file_path)

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
                    "card": card,
                    "statement_period": f"{period_start.isoformat()}..{period_end.isoformat()}",
                    "closing_balance": f"{summary['closing']:.2f}",
                },
            )
            for entry in entries
        ]
        return statement_from_transactions(
            self, file_path, transactions,
            opening_balance=summary.get("opening"), closing_balance=summary.get("closing"),
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

    def _extract_card(self, rows: list[Row]) -> str:
        for row in rows:
            m = CARD_RE.search(row.text)
            if m:
                return re.sub(r"\s+", " ", m.group(1))
        return ""

    def _extract_summary(self, rows: list[Row], file_path: Path) -> dict:
        """Read the payment summary block used to reconcile the parse."""
        wanted = {
            "opening": OPENING_RE, "closing": CLOSING_RE,
            "charges": CHARGES_RE, "payments": PAYMENTS_RE,
        }
        summary: dict[str, float] = {}
        for row in rows:
            for name, pattern in wanted.items():
                if name in summary:
                    continue
                m = pattern.search(row.text)
                if m:
                    summary[name] = float(m.group(1).replace(",", ""))
        missing = [name for name in wanted if name not in summary]
        if missing:
            raise StatementParseError(
                f"{file_path.name}: payment summary missing {', '.join(missing)}"
            )
        return summary

    # -- transaction table --------------------------------------------------

    def _collect_entries(
        self, rows: list[Row], period_start: date, period_end: date, file_path: Path
    ) -> list[dict]:
        entries: list[dict] = []
        ruler: ColumnRuler | None = None
        date_x0 = 0.0
        current: dict | None = None
        current_page = None

        for row in rows:
            if row.page != current_page:
                current_page, ruler, current = row.page, None, None

            if ruler is None:
                built = self._build_ruler(row)
                if built:
                    ruler, date_x0 = built
                continue

            row = row.drop_left_of(date_x0 - 10)
            if not row.words:
                continue

            text = row.text
            if any(p.match(text) for p in STOP_PATTERNS):
                ruler, current = None, None
                continue

            amounts = ruler.amounts_in(row)
            m = DATE_RE.match(text)
            starts_entry = row.first_x0 <= date_x0 + 6 and m

            if starts_entry:
                current = {
                    "date": _resolve_date(
                        int(m.group(1)), m.group(2), period_start, period_end, file_path
                    ),
                    "parts": [],
                }
                entries.append(current)
                words = row.words[len(m.group(0).split()):]
            elif INTEREST_RE.match(text):
                # Printed without a date, at the very end of the period.
                current = {"date": period_end.isoformat(), "parts": []}
                entries.append(current)
                words = row.words
            elif current is not None and not amounts:
                # A note attached to the entry above, e.g. "## DEU MERCHANT".
                current["parts"].extend(w.text for w in row.words)
                continue
            else:
                continue

            current["parts"].extend(w.text for w in words if not ruler.is_column_amount(w))

            if "amount" not in amounts:
                raise StatementParseError(f"{file_path.name}: no amount in row: {text!r}")
            magnitude, raw = amounts["amount"]
            # A trailing minus marks a payment or refund; anything else is a
            # charge, which leaves the account as an expense.
            current["amount"] = magnitude if raw.endswith("-") else -magnitude

        for entry in entries:
            entry["description"] = " ".join(entry.pop("parts")).strip()
        # Zero-value interest lines are printed every month whether or not any
        # interest was charged; they are noise, not transactions.
        return [e for e in entries if e["amount"] != 0]

    def _build_ruler(self, row: Row) -> tuple[ColumnRuler, float] | None:
        texts = [w.text.upper() for w in row.words]
        if not all(label in texts for label in HEADER_REQUIRED):
            return None
        ruler = ColumnRuler.from_header(row, HEADER_LABELS)
        if ruler is None:
            return None
        return ruler, row.words[texts.index("DATE")].x0

    # -- reconciliation -----------------------------------------------------

    def _verify(self, entries: list[dict], summary: dict, file_path: Path) -> None:
        charges = -sum(e["amount"] for e in entries if e["amount"] < 0)
        payments = sum(e["amount"] for e in entries if e["amount"] > 0)

        if abs(charges - summary["charges"]) > CENTS:
            raise StatementParseError(
                f"{file_path.name}: charges total {charges:.2f} != "
                f"statement's {summary['charges']:.2f}"
            )
        if abs(payments - summary["payments"]) > CENTS:
            raise StatementParseError(
                f"{file_path.name}: payments total {payments:.2f} != "
                f"statement's {summary['payments']:.2f}"
            )

        expected = summary["opening"] + charges - payments
        if abs(expected - summary["closing"]) > CENTS:
            raise StatementParseError(
                f"{file_path.name}: opening {summary['opening']:.2f} + charges {charges:.2f} "
                f"- payments {payments:.2f} = {expected:.2f}, "
                f"closing balance says {summary['closing']:.2f}"
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

    A period such as 24 Dec 2025 - 22 Jan 2026 spans both years, so the year is
    whichever of the two lands the date inside the period.
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
