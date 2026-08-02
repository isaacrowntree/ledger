"""Parser for Bankwest home loan and offset account statements.

Both products print the same table, so one parser serves them via a subclass
per account:

    Date      Particulars                         Debit     Credit    Balance
    29 MAY 24 OPENING BALANCE                                    $5,000.00
    03 JUN 24 OFFSET FEE                         $10.00          $4,990.00
    12 JUN 24 J BENNETT morgtage                    $5,000.00 $9,990.00

Loan balances carry a ``DR`` marker and are treated as negative, so interest
charged and repayments received move the balance the same way they move a
deposit account's. As on the transaction-account statement, debits and credits
are both bare positive numbers distinguished only by column.

A description may wrap onto the line below the amount:

    11 MAR 24 DEBIT INTEREST AFTER OFFSET SAVING OF       $1,500.00
              $54.86                                            $301,500.00DR

so the ``$54.86`` there is part of the description, not a second amount — it
sits in the Particulars column. An entry is closed by the row bearing its
balance, wherever the amount appeared.
"""
import re
from datetime import date
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser
from etl.parsers.pdf_layout import (
    MONTH_MAP,
    ColumnRuler,
    Row,
    StatementParseError,
    extract_rows,
    parse_balance,
)

HEADER_LABELS = {"debit": "Debit", "credit": "Credit", "balance": "Balance"}
HEADER_REQUIRED = ("DATE", "PARTICULARS", "DEBIT", "CREDIT", "BALANCE")

DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2})\b")
PERIOD_RE = re.compile(
    r"Period\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2})\s*-\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2})",
    re.IGNORECASE,
)
ACCOUNT_RE = re.compile(r"Account Number\s+(\d+-\d)", re.IGNORECASE)
OPENING_RE = re.compile(r"OPENING BALANCE", re.IGNORECASE)
CLOSING_RE = re.compile(r"CLOSING BALANCE", re.IGNORECASE)
TOTAL_DEBITS_RE = re.compile(r"^TOTAL DEBITS", re.IGNORECASE)
TOTAL_CREDITS_RE = re.compile(r"^TOTAL CREDITS", re.IGNORECASE)

# "14 FEB 24 Debit Interest Rates" trails the table and opens with a date, so
# the table must be closed off before it is reached.
STOP_PATTERNS = [
    TOTAL_DEBITS_RE,
    re.compile(r"^OFFSET TO LOAN ACCOUNT", re.IGNORECASE),
    re.compile(r"^Make sure you check the entries", re.IGNORECASE),
    re.compile(r"^Important Passcode Safety Notice", re.IGNORECASE),
    re.compile(r"^It is a condition of the lending", re.IGNORECASE),
]

CENTS = 0.005


class BankwestAccountPDFParser(BaseParser):
    """Shared parser for Bankwest statements laid out as Debit/Credit/Balance."""

    source_type = "bankwest-account"
    # Distinguishing words in the statement title, used to reject a statement
    # filed under the wrong account.
    statement_title = ""

    def parse(self, file_path: Path) -> list[RawTransaction]:
        rows = extract_rows(file_path)
        self._check_statement_type(rows, file_path)
        account_number = self._extract_account_number(rows)
        period = self._extract_period(rows)

        entries, opening, closing, totals = self._collect_entries(rows, file_path)
        self._verify(entries, opening, closing, totals, file_path)

        return [
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
                    "statement_period": period,
                },
            )
            for entry in entries
        ]

    # -- statement metadata -------------------------------------------------

    def _check_statement_type(self, rows: list[Row], file_path: Path) -> None:
        """Reject a statement staged under the wrong account.

        The loan and the offset share a layout and a filename convention, so
        without this a home loan statement dropped into the offset folder would
        import cleanly against the wrong account.
        """
        if not self.statement_title:
            return
        for row in rows[:20]:
            if self.statement_title.upper() in row.text.upper():
                return
        raise StatementParseError(
            f"{file_path.name}: not a {self.statement_title!r} statement — "
            f"check it is staged under the right account"
        )

    def _extract_account_number(self, rows: list[Row]) -> str:
        for row in rows:
            m = ACCOUNT_RE.search(row.text)
            if m:
                return m.group(1)
        return ""

    def _extract_period(self, rows: list[Row]) -> str:
        for row in rows:
            m = PERIOD_RE.search(row.text)
            if m:
                start = _make_date(int(m.group(1)), m.group(2), 2000 + int(m.group(3)))
                end = _make_date(int(m.group(4)), m.group(5), 2000 + int(m.group(6)))
                return f"{start.isoformat()}..{end.isoformat()}"
        return ""

    # -- transaction table --------------------------------------------------

    def _collect_entries(
        self, rows: list[Row], file_path: Path
    ) -> tuple[list[dict], float | None, float | None, dict]:
        entries: list[dict] = []
        opening = closing = None
        totals: dict[str, float] = {}
        ruler: ColumnRuler | None = None
        date_x0 = 0.0
        pending: dict | None = None
        current_page = None

        for row in rows:
            if row.page != current_page:
                _require_complete(pending, file_path)
                current_page, ruler, pending = row.page, None, None

            if ruler is None:
                built = self._build_ruler(row)
                if built:
                    ruler, date_x0 = built
                continue

            text = row.text
            # The totals sit in the Debit and Credit columns below the table.
            if TOTAL_DEBITS_RE.match(text) or TOTAL_CREDITS_RE.match(text):
                found = ruler.amounts_in(row)
                key = "debits" if TOTAL_DEBITS_RE.match(text) else "credits"
                column = "debit" if key == "debits" else "credit"
                if column in found:
                    totals[key] = found[column][0]

            if any(p.match(text) for p in STOP_PATTERNS):
                _require_complete(pending, file_path)
                ruler, pending = None, None
                continue

            row = row.drop_left_of(date_x0 - 10)
            if not row.words:
                continue
            text = row.text

            amounts = ruler.amounts_in(row)

            if OPENING_RE.search(text):
                opening = self._balance_of(amounts, file_path, text)
                continue
            if CLOSING_RE.search(text):
                closing = self._balance_of(amounts, file_path, text)
                continue

            m = DATE_RE.match(text)
            if row.first_x0 <= date_x0 + 6 and m:
                _require_complete(pending, file_path)
                pending = {
                    "date": _make_date(
                        int(m.group(1)), m.group(2), 2000 + int(m.group(3))
                    ).isoformat(),
                    "parts": [],
                    "debit": None,
                    "credit": None,
                }
                words = row.words[len(m.group(0).split()):]
            elif pending is None:
                continue
            else:
                words = row.words

            pending["parts"].extend(w.text for w in words if not ruler.is_column_amount(w))
            for column in ("debit", "credit"):
                if column in amounts:
                    if pending[column] is not None:
                        raise StatementParseError(
                            f"{file_path.name}: two {column} amounts in one entry: {text!r}"
                        )
                    pending[column] = amounts[column][0]

            if "balance" not in amounts:
                continue

            debit, credit = pending["debit"], pending["credit"]
            if (debit is None) == (credit is None):
                raise StatementParseError(
                    f"{file_path.name}: expected exactly one of debit/credit in entry: {text!r}"
                )
            pending["amount"] = -debit if debit is not None else credit
            pending["balance"] = parse_balance(amounts["balance"][1])
            pending["description"] = " ".join(pending["parts"]).strip()
            for key in ("parts", "debit", "credit"):
                del pending[key]
            entries.append(pending)
            pending = None

        _require_complete(pending, file_path)
        return entries, opening, closing, totals

    def _build_ruler(self, row: Row) -> tuple[ColumnRuler, float] | None:
        texts = [w.text.upper() for w in row.words]
        if not all(label in texts for label in HEADER_REQUIRED):
            return None
        ruler = ColumnRuler.from_header(row, HEADER_LABELS)
        if ruler is None:
            return None
        return ruler, row.words[texts.index("DATE")].x0

    def _balance_of(self, amounts: dict, file_path: Path, text: str) -> float:
        if "balance" not in amounts:
            raise StatementParseError(f"{file_path.name}: no balance in row: {text!r}")
        return parse_balance(amounts["balance"][1])

    # -- reconciliation -----------------------------------------------------

    def _verify(
        self,
        entries: list[dict],
        opening: float | None,
        closing: float | None,
        totals: dict,
        file_path: Path,
    ) -> None:
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

        # Independent of the balance walk: the statement's own column totals.
        checks = [
            ("debits", -sum(e["amount"] for e in entries if e["amount"] < 0)),
            ("credits", sum(e["amount"] for e in entries if e["amount"] > 0)),
        ]
        for key, parsed in checks:
            if key in totals and abs(parsed - totals[key]) > CENTS:
                raise StatementParseError(
                    f"{file_path.name}: {key} total {parsed:.2f} != statement's {totals[key]:.2f}"
                )


class BankwestLoanPDFParser(BankwestAccountPDFParser):
    """Parse Bankwest home loan statements."""

    source_type = "bankwest-loan"
    statement_title = "SIMPLE HOME LOAN"


class BankwestOffsetPDFParser(BankwestAccountPDFParser):
    """Parse Bankwest offset transaction account statements."""

    source_type = "bankwest-offset"
    statement_title = "OFFSET TRAN ACCT"


def _require_complete(pending: dict | None, file_path: Path) -> None:
    """An entry must reach a row carrying its balance before anything else starts."""
    if pending is not None:
        description = " ".join(pending["parts"]).strip() or "(no description)"
        raise StatementParseError(
            f"{file_path.name}: entry on {pending['date']} '{description}' has no balance"
        )


def _make_date(day: int, month_name: str, year: int) -> date:
    month = MONTH_MAP.get(month_name.upper())
    if not month:
        raise StatementParseError(f"unrecognised month {month_name!r}")
    return date(year, int(month), day)
