"""HSBC's transaction-account statements, read by column position.

HSBC issues two products through one parser. A credit card statement prints a
single amount column and is read from flattened text (``hsbc_pdf``). An everyday
account -- "Financial Statement", or "Details of your Accounts" -- prints debits
and credits as separate columns of bare positive numbers:

    Date   Transaction Details   Debits/Withdrawals  Credits/Deposits   Balance
    28 Sep BALANCE BROUGHT FORWARD                                     1,000.00
    29 Sep EFTPOS WOOLWORTHS                  45.50                      954.50
    30 Sep SALARY                                          3,200.00    4,154.50

``extract_text()`` welds those columns into one string, so a $45.50 withdrawal
and a $45.50 deposit become identical text -- which is how every amount on these
statements came to be booked as spending. The columns are recovered here from
each word's right edge, as the CBA and Bankwest parsers already do
(``pdf_layout``), with the anchors read from the header row rather than
hardcoded: HSBC has used at least two sets of labels ("Debit"/"Credit" and
"Debits/Withdrawals"/"Credits/Deposits") at different positions on the page.

One file can describe several accounts, each with its own header, brought-forward
row and closing balance, and one account's table can run over a page break and
repeat its header. Sections are therefore keyed by account number: the statement's
opening and closing balances are the sums across accounts, and a per-row running
balance is only handed back when a single account carries rows, because a chain
that jumps from one account's balance to another's is not a chain.

Each account's own "Transaction Total" line is checked against the rows read for
it. That is what makes a misread column loud: a debit read as a credit leaves both
totals wrong, and the statement is refused rather than ingested backwards.

Layout reported by @colin-tso in issue #5.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from etl.parsers.dates import StatementPeriod, parse_period, resolve_year
from etl.parsers.pdf_layout import (ColumnRuler, Row, StatementParseError,
                                    extract_rows)

# HSBC's two label sets. Each is tried whole: "Debit" must not match a page that
# says "Debits/Withdrawals", whose column sits 3cm further left.
HEADER_LABEL_SETS = (
    {"debit": "Debits/Withdrawals", "credit": "Credits/Deposits", "balance": "Balance"},
    {"debit": "Debit", "credit": "Credit", "balance": "Balance"},
)
HEADER_REQUIRED = ("DATE", "TRANSACTION", "DETAILS")

DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})\b")
ACCOUNT_RE = re.compile(r"Account\s+(?:No\.?\s+)?(\d{6,})", re.I)
# The account header prints that account's closing balance beside "Balance",
# which is the only closing figure a dormant account gets.
ACCOUNT_BALANCE_RE = re.compile(r"Balance\s+\$?(-?[\d,]+\.\d{2})\s*$", re.I)

BROUGHT_FORWARD_RE = re.compile(r"BALANCE\s+BROUGHT\s+FORWARD", re.I)
# "24 Dec CLOSING BALANCE 0.00", or the older layout's "28 Mar BALANCE AUD 0.00".
CLOSING_RE = re.compile(r"CLOSING\s+BALANCE|\bBALANCE\s+(?:AUD|USD|EUR|GBP|NZD|HKD)\b", re.I)
TOTALS_RE = re.compile(r"^Transaction\s+Total\b", re.I)
COUNT_RE = re.compile(r"^Transaction\s+Number\b", re.I)
END_RE = re.compile(r"END\s+OF\s+STATEMENT", re.I)

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

CENTS = 0.005


@dataclass
class AccountEntry:
    date: str
    description: str
    amount: float               # ledger convention: money in positive, out negative
    balance: float | None       # running balance printed after this row


@dataclass
class AccountStatement:
    """What one everyday-account PDF says, across all the accounts it covers."""
    entries: list[AccountEntry]
    opening: float | None
    closing: float | None
    period: StatementPeriod | None
    # False when more than one account carries rows, in which case the per-row
    # balances do not form a single chain and are withheld.
    chained: bool = True


@dataclass
class _Account:
    number: str
    entries: list[AccountEntry] = field(default_factory=list)
    opening: float | None = None
    closing: float | None = None
    header_balance: float | None = None
    debit_total: float | None = None
    credit_total: float | None = None


def parse_account_statement(file_path: Path, rows: list[Row] | None = None
                            ) -> AccountStatement | None:
    """Read an everyday-account statement, or None if this is not that layout.

    Returning None rather than raising is what lets one parser serve both HSBC
    products: a credit card statement has no debit/credit header to anchor to and
    falls through to the text-based path.
    """
    rows = list(rows) if rows is not None else extract_rows(file_path)
    if not rows:
        return None

    period = parse_period("\n".join(row.text for row in rows))
    accounts = _read_accounts(rows, period, file_path)
    if accounts is None:
        return None

    for account in accounts:
        _verify_totals(account, file_path)

    entries = [entry for account in accounts for entry in account.entries]
    with_rows = [account for account in accounts if account.entries]
    chained = len(with_rows) <= 1
    if not chained:
        entries = [AccountEntry(e.date, e.description, e.amount, None) for e in entries]

    return AccountStatement(
        entries=entries,
        opening=_total([a.opening for a in accounts]),
        closing=_total([a.closing if a.closing is not None else a.header_balance
                        for a in accounts]),
        period=period,
        chained=chained,
    )


def _read_accounts(rows: list[Row], period: StatementPeriod | None,
                   file_path: Path) -> list[_Account] | None:
    """Walk the pages, collecting one _Account per account number."""
    accounts: dict[str, _Account] = {}
    order: list[str] = []
    account: _Account | None = None
    ruler: ColumnRuler | None = None
    date_x0 = 0.0
    pending: dict | None = None
    # The account header sits above the column header, so it is remembered until
    # the column header arrives and opens the table it belongs to.
    heading: tuple[str, float | None] | None = None

    for row in rows:
        text = row.text

        built = _build_ruler(row)
        if built:
            _require_complete(pending, file_path)
            pending = None
            ruler, date_x0 = built
            number = heading[0] if heading else f"#{len(order) + 1}"
            if number not in accounts:
                accounts[number] = _Account(number)
                order.append(number)
            account = accounts[number]
            if heading and heading[1] is not None:
                account.header_balance = heading[1]
            heading = None
            continue

        found = ACCOUNT_RE.search(text)
        if found and not DATE_RE.match(text):
            balance = ACCOUNT_BALANCE_RE.search(text)
            heading = (found.group(1),
                       float(balance.group(1).replace(",", "")) if balance else None)

        if ruler is None or account is None:
            continue

        if END_RE.search(text):
            _require_complete(pending, file_path)
            pending, ruler, account = None, None, None
            continue

        amounts = ruler.amounts_in(row)

        if BROUGHT_FORWARD_RE.search(text):
            _require_complete(pending, file_path)
            pending = None
            if account.opening is None and "balance" in amounts:
                account.opening = amounts["balance"][0]
            continue

        if CLOSING_RE.search(text):
            _require_complete(pending, file_path)
            pending = None
            if "balance" in amounts:
                account.closing = amounts["balance"][0]
            continue

        if TOTALS_RE.search(text):
            _require_complete(pending, file_path)
            pending = None
            if "debit" in amounts:
                account.debit_total = amounts["debit"][0]
            if "credit" in amounts:
                account.credit_total = amounts["credit"][0]
            # The table is over; anything below it (scheduled payments, marketing)
            # is not a transaction until the next header says otherwise.
            ruler = None
            continue

        if COUNT_RE.search(text):
            continue

        date_match = DATE_RE.match(text)
        starts_entry = bool(date_match) and row.first_x0 <= date_x0 + 6
        if starts_entry:
            _require_complete(pending, file_path)
            pending = {
                "date": _resolve_date(date_match, period, file_path, text),
                "parts": [],
            }
            words = row.words[len(date_match.group(0).split()):]
        elif pending is None:
            continue        # page furniture between the header and the first row
        else:
            words = row.words

        pending["parts"].extend(w.text for w in words if not ruler.is_column_amount(w))

        debit = amounts.get("debit")
        credit = amounts.get("credit")
        if debit is None and credit is None:
            continue        # a description that runs over several lines
        if debit is not None and credit is not None:
            raise StatementParseError(
                f"{file_path.name}: row is in both the debit and credit columns: {text!r}")

        entry = AccountEntry(
            date=pending["date"],
            description=" ".join(pending["parts"]).strip(),
            amount=-debit[0] if debit else credit[0],
            balance=amounts["balance"][0] if "balance" in amounts else None,
        )
        pending = None
        # A repayment HSBC has only SCHEDULED is printed with a date past the end
        # of the period. It has not happened, it is not in the closing balance,
        # and ingesting it invents money that never moved.
        if period is not None and entry.date > period.end:
            continue
        account.entries.append(entry)

    _require_complete(pending, file_path)
    if not order:
        return None
    return [accounts[number] for number in order]


def _build_ruler(row: Row) -> tuple[ColumnRuler, float] | None:
    texts = [w.text.upper() for w in row.words]
    if not all(label in texts for label in HEADER_REQUIRED):
        return None
    for labels in HEADER_LABEL_SETS:
        ruler = ColumnRuler.from_header(row, labels)
        if ruler is not None:
            return ruler, row.words[texts.index("DATE")].x0
    return None


def _verify_totals(account: _Account, file_path: Path) -> None:
    """Check the rows read for an account against the totals it prints.

    A credit read as a debit is invisible to a balance check that only looks at
    the closing figure -- both totals move, and by the same amount -- so the
    statement's own two totals are the thing worth checking.
    """
    for name, printed, actual in (
        ("debits", account.debit_total,
         sum(-e.amount for e in account.entries if e.amount < 0)),
        ("credits", account.credit_total,
         sum(e.amount for e in account.entries if e.amount > 0)),
    ):
        if printed is None:
            continue
        if abs(printed - actual) > CENTS:
            raise StatementParseError(
                f"{file_path.name}: account {account.number} {name} total "
                f"{actual:,.2f} but the statement says {printed:,.2f}")


def _resolve_date(match: re.Match, period: StatementPeriod | None,
                  file_path: Path, text: str) -> str:
    day, month_name = int(match.group(1)), match.group(2).upper()
    month = MONTHS.get(month_name)
    if month is None:
        raise StatementParseError(f"{file_path.name}: unrecognised month in {text!r}")
    year = resolve_year(day, month, period)
    if year is None:
        raise StatementParseError(
            f"{file_path.name}: no statement period to date {text!r} against")
    return f"{year:04d}-{month:02d}-{day:02d}"


def _require_complete(pending: dict | None, file_path: Path) -> None:
    """A started entry must reach its amount before anything else begins."""
    if pending is not None:
        description = " ".join(pending["parts"]).strip() or "(no description)"
        raise StatementParseError(
            f"{file_path.name}: entry on {pending['date']} '{description}' has no amount")


def _total(values: list[float | None]) -> float | None:
    """Sum across accounts, or None if any account did not print its figure.

    Summing what is known and ignoring the rest would produce a plausible number
    that no page of the statement actually claims.
    """
    if not values or any(v is None for v in values):
        return None
    return sum(values)


__all__ = ["AccountEntry", "AccountStatement", "parse_account_statement"]
