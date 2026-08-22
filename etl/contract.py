"""The contract between a statement parser and the ingestion engine.

The engine owns correctness; a parser's only job is to describe faithfully what a
statement says. Everything that used to be re-invented per parser -- what makes a
transaction unique, which way a balance points, whether a file was fully captured
-- lives here and is enforced once for every source.

A parser answers three questions and nothing more:

1. **What period does this statement cover** (`period_start`, `period_end`).
2. **What balances does it print** (`opening_balance`, `closing_balance`, and a
   per-row `balance` where the source prints a running one), in the source's own
   convention, declared via `balance_convention`.
3. **What rows does it list**, in the order the BALANCE evolves, indexed from 0
   (`ParsedRow.index`). Order is part of the contract because a running balance
   only means anything read forwards: several sources export newest-first, and a
   parser must reverse them rather than hand the engine a chain that runs
   backwards. That order is usually chronological but need not be -- a row
   carrying a value date is printed where it was processed, not where its date
   would put it.

The engine then guarantees:

- **Identity / idempotency.** The dedup key is derived here, from intrinsic fields
  plus the row's position among identical rows in its own file. Never from the
  filename -- the same statement re-downloaded under another name must not
  double-count -- and never from a statement-level figure, which identifies the
  file rather than the row.
- **Validation.** A statement whose rows do not add up to its own printed balances
  is not silently accepted. This catches a parser that drops rows (a multi-line
  description, an unusual layout) at ingest time, instead of months later.
- **Sign.** `balance_convention` tells the engine which way the source writes a
  balance, so a debt that grows is stored consistently no matter which layout the
  bank used. This is what the ING mortgage got wrong in two different ways.
"""
from dataclasses import dataclass, field
from enum import Enum


class BalanceConvention(str, Enum):
    """Which way this source writes a balance.

    SIGNED  -- balance moves WITH the transaction amount (an everyday account:
               spend $10, balance falls $10).
    OWING   -- balance is what is OWED, so it moves AGAINST the amount (a credit
               card or loan: spend $10, the balance owed rises $10).
    NONE    -- the source prints no balance that can be reconciled against.

    ING prints the same mortgage as "-$307,416.41" on one layout and "307,153.72"
    on another. Declaring the convention removes the guesswork that made interest
    charges land with the wrong sign for three years.
    """
    SIGNED = "signed"
    OWING = "owing"
    NONE = "none"


@dataclass
class ParsedRow:
    """One line of a statement, exactly as printed."""
    index: int                      # position within the statement, from 0, chronological
    date: str                       # YYYY-MM-DD
    description: str
    amount: float                   # ledger convention: money in positive, out negative
    balance: float | None = None    # running balance after this row, if printed
    raw: dict = field(default_factory=dict)

    # Everything below is intrinsic to the row and must survive ingestion.
    currency: str = "AUD"
    original_amount: float | None = None    # pre-conversion amount, if foreign
    original_currency: str | None = None
    fee: float = 0.0
    reference_id: str | None = None         # the source's own unique id, if it has one


@dataclass
class ValidationIssue:
    code: str
    detail: str
    amount: float | None = None

    def __str__(self) -> str:
        money = f" ({self.amount:,.2f})" if self.amount is not None else ""
        return f"{self.code}: {self.detail}{money}"


@dataclass
class ParsedStatement:
    """What a parser hands the engine."""
    source_file: str
    source_type: str
    rows: list[ParsedRow]
    period_start: str | None = None
    period_end: str | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    balance_convention: BalanceConvention = BalanceConvention.NONE

    TOLERANCE = 0.005

    def signed_delta(self, opening: float, closing: float) -> float:
        """Movement the printed balances imply, in transaction-amount terms."""
        delta = closing - opening
        return -delta if self.balance_convention is BalanceConvention.OWING else delta

    def validate(self) -> list[ValidationIssue]:
        """Check the statement against itself. Empty list means it adds up."""
        issues: list[ValidationIssue] = []

        if not self.rows:
            # A statement can legitimately list nothing -- ING issues quarterly
            # statements for dormant accounts. It is only a failed parse if the
            # statement's own balances say money moved.
            if (self.opening_balance is not None and self.closing_balance is not None
                    and abs(self.signed_delta(self.opening_balance,
                                              self.closing_balance)) <= self.TOLERANCE):
                return issues
            issues.append(ValidationIssue(
                "no_rows", "parser returned no transactions, but the statement's "
                           "balances do not corroborate an empty statement"))
            return issues

        if [r.index for r in self.rows] != list(range(len(self.rows))):
            issues.append(ValidationIssue(
                "bad_row_index", "rows are not indexed 0..n-1"))

        # Order is only checkable by date when no running balance is printed.
        # Where there is one, the chain below vouches for the order directly --
        # and banks legitimately list a row out of date order when it carries a
        # value date (ING prints a Swift transfer dated 02/04 among the 06/04
        # rows, its balance continuing the chain exactly).
        dates = [r.date for r in self.rows if r.date]
        has_balances = any(r.balance is not None for r in self.rows)
        if not has_balances and dates != sorted(dates):
            issues.append(ValidationIssue(
                "rows_out_of_order",
                "rows are not in chronological order (a newest-first export must "
                "be reversed before the running balance can be read)"))

        for r in self.rows:
            if not r.date or len(r.date) != 10 or r.date[4] != "-":
                issues.append(ValidationIssue("bad_date", f"row {r.index}: {r.date!r}"))
                break

        if self.period_start and self.period_end:
            outside = [r for r in self.rows
                       if not (self.period_start <= r.date <= self.period_end)]
            if outside:
                issues.append(ValidationIssue(
                    "row_outside_period",
                    f"{len(outside)} row(s) dated outside "
                    f"{self.period_start}..{self.period_end}"))

        # The heart of it: do the rows account for the printed movement?
        if self.opening_balance is not None and self.closing_balance is not None:
            expected = self.signed_delta(self.opening_balance, self.closing_balance)
            actual = sum(r.amount for r in self.rows)
            if abs(actual - expected) > self.TOLERANCE:
                issues.append(ValidationIssue(
                    "balance_mismatch",
                    f"rows sum to {actual:,.2f} but opening {self.opening_balance:,.2f} "
                    f"-> closing {self.closing_balance:,.2f} implies {expected:,.2f}",
                    amount=round(actual - expected, 2)))

        # A running balance, where printed, must agree row by row.
        if self.balance_convention is not BalanceConvention.NONE:
            prev = self.opening_balance
            for r in self.rows:
                if r.balance is None:
                    continue
                if prev is not None:
                    moved = self.signed_delta(prev, r.balance)
                    if abs(moved - r.amount) > self.TOLERANCE:
                        issues.append(ValidationIssue(
                            "chain_break",
                            f"row {r.index} ({r.date} {r.description[:32]!r}) "
                            f"amount {r.amount:,.2f} but balance moved {moved:,.2f}",
                            amount=round(r.amount - moved, 2)))
                prev = r.balance

        return issues
