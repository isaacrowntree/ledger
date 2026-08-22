"""The parser/engine contract: what a statement must say about itself."""
import pytest

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement


def _stmt(rows, **kw):
    kw.setdefault("balance_convention", BalanceConvention.SIGNED)
    return ParsedStatement(source_file="s.pdf", source_type="test", rows=rows, **kw)


def _row(i, amount, balance=None, date="2026-01-0%d", description="TXN"):
    return ParsedRow(index=i, date=date % (i + 1) if "%" in date else date,
                     description=description, amount=amount, balance=balance)


class TestSelfConsistency:
    def test_statement_that_adds_up_is_clean(self):
        s = _stmt([_row(0, -10.0, 90.0), _row(1, -20.0, 70.0)],
                  opening_balance=100.0, closing_balance=70.0)
        assert s.validate() == []

    def test_dropped_row_is_caught(self):
        """The whole point: a parser that silently loses a line must not pass."""
        s = _stmt([_row(0, -10.0, 90.0)], opening_balance=100.0, closing_balance=70.0)
        codes = [i.code for i in s.validate()]
        assert "balance_mismatch" in codes

    def test_mismatch_reports_the_shortfall(self):
        s = _stmt([_row(0, -10.0)], opening_balance=100.0, closing_balance=70.0)
        issue = next(i for i in s.validate() if i.code == "balance_mismatch")
        assert issue.amount == 20.00

    def test_empty_statement_with_no_balances_is_an_error(self):
        """Nothing corroborates the emptiness, so it may be a failed parse."""
        assert [i.code for i in _stmt([]).validate()] == ["no_rows"]

    def test_a_genuinely_empty_statement_is_valid(self):
        """ING issues quarterly statements reading "There were no transactions
        on your Savings Maximiser account for this quarter". An empty statement
        whose own balances show no movement is correct, not a failed parse."""
        s = _stmt([], opening_balance=0.0, closing_balance=0.0)
        assert s.validate() == []

    def test_empty_statement_that_should_have_moved_is_an_error(self):
        """Balances moved but no rows were found -- the parser dropped them all."""
        s = _stmt([], opening_balance=100.0, closing_balance=70.0)
        assert [i.code for i in s.validate()] == ["no_rows"]


class TestOwingConvention:
    def test_spending_increases_what_is_owed(self):
        """On a card, a purchase makes the printed balance go UP."""
        s = _stmt([_row(0, -10.0, 110.0), _row(1, -20.0, 130.0)],
                  opening_balance=100.0, closing_balance=130.0,
                  balance_convention=BalanceConvention.OWING)
        assert s.validate() == []

    def test_owing_statement_read_as_signed_would_fail(self):
        """Guards the convention actually being applied, not ignored."""
        s = _stmt([_row(0, -10.0, 110.0)],
                  opening_balance=100.0, closing_balance=110.0,
                  balance_convention=BalanceConvention.SIGNED)
        assert [i.code for i in s.validate()] != []

    def test_inverted_interest_sign_is_caught(self):
        """A mortgage interest charge stored as though it reduced the debt."""
        s = _stmt([_row(0, 1849.17, 309742.36)],
                  opening_balance=307893.19, closing_balance=309742.36,
                  balance_convention=BalanceConvention.OWING)
        codes = [i.code for i in s.validate()]
        assert "chain_break" in codes and "balance_mismatch" in codes


class TestRowDiscipline:
    def test_rows_must_be_indexed_in_printed_order(self):
        rows = [_row(0, -10.0), _row(5, -20.0)]
        assert "bad_row_index" in [i.code for i in _stmt(rows).validate()]

    def test_dates_must_be_iso(self):
        rows = [ParsedRow(0, "01/02/2026", "TXN", -10.0)]
        assert "bad_date" in [i.code for i in _stmt(rows).validate()]

    def test_value_dated_row_out_of_date_order_is_accepted(self):
        """Banks print a value date but list rows in processing order: ING shows a
        Swift transfer dated 02/04 sitting after 06/04 rows. Its balance chains
        perfectly, and the chain -- not the dates -- is the authority on order."""
        rows = [_row(0, -10.0, 90.0, date="2026-04-06"),
                _row(1, 45.25, 135.25, date="2026-04-02"),
                _row(2, -5.0, 130.25, date="2026-04-06")]
        s = _stmt(rows, opening_balance=100.0, closing_balance=130.25)
        assert s.validate() == []

    def test_unordered_rows_without_balances_are_still_flagged(self):
        """With no chain to vouch for the order, dates are all there is."""
        rows = [_row(0, -10.0, date="2026-04-06"), _row(1, -5.0, date="2026-04-02")]
        assert "rows_out_of_order" in [i.code for i in _stmt(rows).validate()]

    def test_rows_must_fall_inside_the_declared_period(self):
        rows = [_row(0, -10.0, date="2026-03-01")]
        s = _stmt(rows, period_start="2026-01-01", period_end="2026-01-31")
        assert "row_outside_period" in [i.code for i in s.validate()]

    def test_no_balance_source_skips_balance_checks(self):
        """Amex prints no balance; such a statement cannot be balance-checked,
        and must not be failed for it."""
        s = _stmt([_row(0, -10.0), _row(1, -20.0)],
                  balance_convention=BalanceConvention.NONE)
        assert s.validate() == []
