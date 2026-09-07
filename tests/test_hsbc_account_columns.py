"""HSBC everyday-account statements are read by column, not by flattened text.

Debits/Withdrawals and Credits/Deposits are both printed as bare positive
numbers, so ``extract_text()`` renders a $45.50 withdrawal and a $45.50 deposit
identically -- and every amount was booked as spending. The geometry in
``tests.statement_rows`` is measured from real statements; only the text is
invented, since a genuine one cannot be committed.

Layout reported by @colin-tso in issue #5.
"""
from pathlib import Path

import pytest

from etl.contract import BalanceConvention
from etl.parsers import hsbc_pdf
from etl.parsers.hsbc_account_pdf import parse_account_statement
from etl.parsers.hsbc_pdf import HSBCPDFParser
from etl.parsers.pdf_layout import StatementParseError
from tests.statement_rows import HSBCAccount as HA
from tests.statement_rows import HSBCFinancial as HF
from tests.statement_rows import line, text_words

PERIOD = "STATEMENT PERIOD FROM 28 Sep 2021 TO 24 Dec 2021"


def account(*entries, geometry=HA, number="519410412", opening="1,000.00",
            closing=None, debits="0.00", credits="0.00", page=1):
    """One account section: heading, column header, rows, totals."""
    return [
        geometry.heading(number=number,
                         balance=closing if closing is not None else opening, page=page),
        geometry.header(page=page),
        geometry.row(date="28 Sep", desc="BALANCE BROUGHT FORWARD",
                     balance=opening, page=page),
        *entries,
        geometry.row(date="24 Dec", desc="CLOSING BALANCE",
                     balance=closing if closing is not None else opening, page=page),
        geometry.totals(debits=debits, credits=credits, page=page),
    ]


def statement(*sections, period=PERIOD):
    return [line(text_words(period, 340)), *sections]


def parse(rows):
    return parse_account_statement(Path("hsbc_financial_statement.pdf"), rows)


class TestColumnSigns:
    def test_a_withdrawal_is_negative_and_a_deposit_positive(self):
        """The whole point: identical text, opposite directions."""
        parsed = parse(statement(*account(
            HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS",
                   debit="45.50", balance="954.50"),
            HA.row(date="30 Sep", desc="TRANSFER FROM SAM RIVERS",
                   credit="45.50", balance="1,000.00"),
            debits="45.50", credits="45.50",
        )))
        assert [e.amount for e in parsed.entries] == [-45.50, 45.50]

    def test_the_older_debit_credit_layout_reads_the_same(self):
        """HSBC has used two label sets, in different places on the page."""
        parsed = parse(statement(*account(
            HF.row(date="29 Sep", desc="SALARY", credit="3,200.00", balance="4,200.00"),
            geometry=HF, closing="4,200.00", credits="3,200.00",
        )))
        assert [e.amount for e in parsed.entries] == [3200.00]

    def test_a_row_in_both_columns_is_refused(self):
        with pytest.raises(StatementParseError, match="both the debit and credit"):
            parse(statement(*account(
                HA.row(date="29 Sep", desc="ODD", debit="10.00", credit="10.00",
                       balance="1,000.00"),
            )))


class TestPrintedTotals:
    """Each account prints its own debit and credit totals: check against them."""

    def test_a_column_misread_as_the_other_breaks_the_totals(self):
        """A withdrawal read as a deposit leaves both printed totals wrong."""
        with pytest.raises(StatementParseError, match="debits total 45.50"):
            parse(statement(*account(
                HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS",
                       debit="45.50", balance="954.50"),
                debits="0.00", credits="45.50",
            )))

    def test_a_dropped_row_breaks_the_totals(self):
        with pytest.raises(StatementParseError, match="debits total"):
            parse(statement(*account(debits="45.50", credits="0.00")))


class TestDescriptions:
    def test_a_description_running_over_lines_is_joined(self):
        parsed = parse(statement(*account(
            HA.row(date="29 Sep", desc="VISA DEBIT PURCHASE"),
            HA.row(desc="MELBOURNE AU", debit="45.50", balance="954.50"),
            debits="45.50",
        )))
        assert parsed.entries[0].description == "VISA DEBIT PURCHASE MELBOURNE AU"

    def test_an_entry_that_never_reaches_an_amount_is_refused(self):
        with pytest.raises(StatementParseError, match="has no amount"):
            parse(statement(*account(
                HA.row(date="29 Sep", desc="VISA DEBIT PURCHASE"),
            )))


class TestScheduledPayments:
    def test_a_payment_dated_after_the_period_is_not_a_transaction(self):
        """HSBC prints scheduled repayments that have not happened yet.

        They are absent from the closing balance, so ingesting one invents money
        that never moved.
        """
        parsed = parse(statement(*account(
            HA.row(date="26 Dec", desc="HOME LOAN INV P&I Payment",
                   debit="1,200.00", balance="1,000.00"),
        )))
        assert parsed.entries == []


class TestBalances:
    def test_the_running_balance_reaches_the_engine(self):
        """Without it the chain check and the sign repair are both dead."""
        parsed = parse(statement(*account(
            HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS",
                   debit="45.50", balance="954.50"),
            closing="954.50", debits="45.50",
        )))
        assert [e.balance for e in parsed.entries] == [954.50]
        assert (parsed.opening, parsed.closing) == (1000.00, 954.50)

    def test_a_dormant_account_still_reports_its_balances(self):
        """No rows and no balances is indistinguishable from a failed parse."""
        parsed = parse(statement(*account(opening="0.00")))
        assert parsed.entries == []
        assert (parsed.opening, parsed.closing) == (0.00, 0.00)


class TestSeveralAccounts:
    def test_balances_are_summed_across_accounts(self):
        parsed = parse(statement(
            *account(number="519410412", opening="1,000.00"),
            *account(number="519410087", opening="250.00"),
        ))
        assert (parsed.opening, parsed.closing) == (1250.00, 1250.00)

    def test_a_table_continued_on_the_next_page_is_one_account(self):
        """A continued account repeats its heading; its balances must not double."""
        parsed = parse(statement(
            HA.heading(number="519410412", balance="1,000.00"),
            HA.header(),
            HA.row(date="28 Sep", desc="BALANCE BROUGHT FORWARD", balance="1,000.00"),
            HA.heading(number="519410412", balance="1,000.00", page=2),
            HA.header(page=2),
            HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS", debit="45.50",
                   credit=None, balance="954.50", page=2),
            HA.row(date="24 Dec", desc="CLOSING BALANCE", balance="954.50", page=2),
            HA.totals(debits="45.50", page=2),
        ))
        assert (parsed.opening, parsed.closing) == (1000.00, 954.50)
        assert [e.amount for e in parsed.entries] == [-45.50]

    def test_per_row_balances_are_withheld_when_two_accounts_have_rows(self):
        """Two chains interleaved are not a chain, and would read as a break."""
        parsed = parse(statement(
            *account(HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS",
                            debit="45.50", balance="954.50"),
                     number="519410412", closing="954.50", debits="45.50"),
            *account(HA.row(date="30 Sep", desc="INTEREST", credit="0.50",
                            balance="250.50"),
                     number="519410087", opening="250.00", closing="250.50",
                     credits="0.50"),
        ))
        assert [e.balance for e in parsed.entries] == [None, None]
        assert [e.amount for e in parsed.entries] == [-45.50, 0.50]


class TestNotThisLayout:
    def test_a_credit_card_statement_is_left_to_the_text_parser(self):
        """One amount column, no debit/credit header to anchor to."""
        assert parse([line(text_words("7201 BPAY PAYMENT -$4,267.00", 50))]) is None


class TestThroughTheParser:
    """The statement the engine actually receives."""

    @pytest.fixture
    def statement_of(self, monkeypatch):
        def _parse(rows):
            # The parser reads the flattened text first, and only pays for the
            # page geometry when the text says this is an everyday account.
            monkeypatch.setattr(HSBCPDFParser, "_extract_text",
                                lambda self, path: "\n".join(r.text for r in rows))
            monkeypatch.setattr(hsbc_pdf, "parse_account_statement",
                                lambda *a, **k: parse_account_statement(
                                    Path("hsbc_financial_statement.pdf"), rows))
            return HSBCPDFParser().parse_statement(Path("hsbc_financial_statement.pdf"))
        return _parse

    def test_an_everyday_account_is_signed_not_owing(self, statement_of):
        """The class default is a card's OWING, which would invert every row."""
        parsed = statement_of(statement(*account(
            HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS",
                   debit="45.50", balance="954.50"),
            closing="954.50", debits="45.50",
        )))
        assert parsed.balance_convention is BalanceConvention.SIGNED
        assert [r.amount for r in parsed.rows] == [-45.50]
        assert [r.balance for r in parsed.rows] == [954.50]

    def test_the_statement_accounts_for_its_own_balances(self, statement_of):
        parsed = statement_of(statement(*account(
            HA.row(date="29 Sep", desc="EFTPOS WOOLWORTHS",
                   debit="45.50", balance="954.50"),
            HA.row(date="30 Sep", desc="SALARY", credit="200.00", balance="1,154.50"),
            closing="1,154.50", debits="45.50", credits="200.00",
        )))
        assert parsed.validate() == []
        assert (parsed.period_start, parsed.period_end) == ("2021-09-28", "2021-12-24")
