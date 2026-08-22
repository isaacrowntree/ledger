"""What every statement parser must provide.

There is one entry point, `parse_statement`, and it returns a ParsedStatement --
the parser's complete account of what the file says. There is no second, looser
path: a parser cannot hand back a bare list of transactions and leave the engine
to guess the period, the balances, or which way a balance points. Guessing those
is what allowed three years of mortgage interest to be stored with the wrong sign.

A parser MUST declare:

* `source_type` -- which account family the rows belong to.
* `balance_convention` -- SIGNED if the printed balance moves with the amount,
  OWING if it is a debt that grows as you spend, NONE if the source prints no
  reconcilable balance. Declaring NONE is a legitimate answer (Amex prints no
  balance); silently defaulting to it is not, which is why it must be stated.

A parser SHOULD populate `opening_balance` and `closing_balance` whenever the
source prints them. That is what lets the engine prove, at ingest time, that no
row was dropped -- see ParsedStatement.validate().
"""
import math
import re
from abc import ABC, abstractmethod
from pathlib import Path

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement


class BaseParser(ABC):
    """Abstract base for all statement parsers."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """The source type identifier (e.g. 'ing', 'paypal', 'hsbc')."""
        ...

    @property
    @abstractmethod
    def balance_convention(self) -> BalanceConvention:
        """Which way this source writes a balance. Must be stated explicitly."""
        ...

    @abstractmethod
    def parse_statement(self, file_path: Path) -> ParsedStatement:
        """Describe the file: its period, its printed balances, and its rows."""
        ...

    # -- construction helper -------------------------------------------------
    # Shared so every parser assembles a statement the same way. It does not
    # supply defaults for anything the contract requires a parser to decide.

    def build(
        self,
        file_path: Path,
        rows: list[ParsedRow],
        opening_balance: float | None = None,
        closing_balance: float | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> ParsedStatement:
        dates = [r.date for r in rows if r.date]
        return ParsedStatement(
            source_file=str(file_path),
            source_type=self.source_type,
            rows=rows,
            period_start=period_start or (min(dates) if dates else None),
            period_end=period_end or (max(dates) if dates else None),
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            balance_convention=self.balance_convention,
        )


def detect_convention(rows, opening: float | None, default=None):
    """Whether the running balance moves WITH the amounts or against them.

    Decided by counting how many rows each convention actually explains. A
    transaction account's balance follows the amount; a loan's balance is a debt
    that rises as it is charged.
    """
    signed = owing = 0
    prev = opening
    for row in rows:
        if row.balance is None:
            continue
        if prev is not None:
            delta = row.balance - prev
            if abs(delta - row.amount) < 0.005:
                signed += 1
            elif abs(-delta - row.amount) < 0.005:
                owing += 1
        prev = row.balance
    if signed == 0 and owing == 0:
        # No per-row balances to learn from -- a card statement prints only a
        # closing figure. Guessing SIGNED here inverted every card statement, so
        # the caller's declared default stands instead.
        return default if default is not None else BalanceConvention.SIGNED
    return BalanceConvention.OWING if owing > signed else BalanceConvention.SIGNED


def resign_unsigned_rows(rows, convention: BalanceConvention, opening: float | None) -> None:
    """Give an unsigned printed amount the direction its balance movement implies.

    ING's loan statements print every movement unsigned in one column, so a
    repayment and an interest charge look identical -- which is how years of
    mortgage interest came to be stored as though it reduced the debt.

    Only the SIGN is taken from the balance; the magnitude always stays the
    printed figure, and a row whose magnitude disagrees with the balance movement
    is left untouched. Rewriting magnitudes from balances would make the chain
    check tautological and hide exactly the dropped rows it exists to catch.
    """
    prev = opening
    for row in rows:
        if row.balance is None:
            continue
        if prev is not None:
            delta = row.balance - prev
            implied = -delta if convention is BalanceConvention.OWING else delta
            if abs(abs(implied) - abs(row.amount)) < 0.005 and implied != 0:
                row.amount = math.copysign(abs(row.amount), implied)
        prev = row.balance


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

    # One label per line, in order.
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


def labelled_balance(text: str, label: str) -> float | None:
    """Read a balance printed beside its label.

    Same line only. Some layouts (ING's quarterly statements) list every label in
    one block and every value in another, so a newline-spanning match pairs a
    label with an unrelated number.

    A trailing "CR" means the account is in credit and flips the sign: on a card
    statement, where the balance is what is OWED, "Opening balance $1.64 CR" is
    -1.64 owing. Missing that produced a discrepancy of exactly twice the balance.
    """
    m = re.search(
        rf"{label}[^\S\n]+(-?)\$?\s*([\d,]+\.\d{{2}})[^\S\n]*(CR|DR)?\b",
        text, re.I)
    if not m:
        return None
    sign, digits, marker = m.groups()
    try:
        value = float(digits.replace(",", ""))
    except ValueError:
        return None
    if sign == "-" or (marker and marker.upper() == "CR"):
        value = -value
    return value


def statement_from_transactions(
    parser: "BaseParser",
    file_path,
    transactions: list,
    opening_balance: float | None = None,
    closing_balance: float | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> ParsedStatement:
    """Build a ParsedStatement from a parser's own RawTransaction list.

    A per-row running balance is picked up from raw_data where the source
    prints one; sources that print only a statement total (a credit card) leave
    it None and are checked against opening/closing instead.
    """
    rows = [
        ParsedRow(
            index=i, date=t.date, description=t.description, amount=t.amount,
            balance=money((t.raw_data or {}).get("balance")),
            raw=t.raw_data or {}, currency=t.currency,
            original_amount=t.original_amount, original_currency=t.original_currency,
            fee=t.fee, reference_id=t.reference_id,
        )
        for i, t in enumerate(transactions)
    ]
    return parser.build(file_path, rows, opening_balance=opening_balance,
                        closing_balance=closing_balance,
                        period_start=period_start, period_end=period_end)


def chronological(transactions: list) -> list:
    """Put rows into the order the engine requires: oldest first.

    Three cases, and the distinction matters:

    * Already ascending -- nothing to do.
    * Fully descending (a newest-first export) -- reverse. Reversing rather than
      sorting keeps the relative order of same-day rows, which is exactly what
      makes a running balance resolvable.
    * Neither -- the statement groups rows by section (purchases, then payments)
      rather than by date. Sort by date, but ONLY when no row carries a running
      balance: sorting a balance chain would scramble it into nonsense. When
      balances are present the order is left alone, so validation reports the
      disorder instead of this function hiding it.
    """
    dates = [t.date for t in transactions if t.date]
    if len(dates) < 2 or dates == sorted(dates):
        return transactions

    if dates == sorted(dates, reverse=True):
        return list(reversed(transactions))

    has_balance = any((t.raw_data or {}).get("balance") not in (None, "", "None")
                      for t in transactions)
    if has_balance:
        return transactions

    return sorted(transactions, key=lambda t: t.date)   # stable: ties keep their order


def money(value) -> float | None:
    """Parse a printed money value: "$1,234.56", "-$12.80", "1234.56", "" -> None."""
    if value in (None, "", "None"):
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
