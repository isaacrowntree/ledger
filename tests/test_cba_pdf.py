"""Tests for the CBA transaction-account (Smart Access) PDF parser."""
from pathlib import Path

import pytest

from etl.parsers import cba_pdf
from etl.parsers.cba_pdf import CBAPDFParser
from etl.parsers.pdf_layout import StatementParseError
from tests.statement_rows import SmartAccess as SA
from tests.statement_rows import line, text_words

PERIOD = "Period 1 Aug 2025 - 31 Jan 2026"


def statement(*entries, opening="10,000.00CR", closing="8,000.00CR", period=PERIOD):
    """Wrap entry rows in the surrounding statement furniture."""
    return [
        line(text_words(period, 50)),
        line(text_words("Account Number 06 1234 12345678", 50)),
        SA.header(),
        SA.row(date="01 Aug", desc="2025 OPENING BALANCE", balance=opening),
        *entries,
        SA.row(date="31 Jan", desc="2026 CLOSING BALANCE", balance=closing),
    ]


@pytest.fixture
def parse(monkeypatch):
    def _parse(rows):
        monkeypatch.setattr(cba_pdf, "extract_rows", lambda *a, **k: rows)
        return CBAPDFParser().parse_statement(Path("smart_access.pdf")).rows
    return _parse


class TestColumnSigns:
    def test_debit_is_negative_and_credit_positive(self, parse):
        """Both print as bare positive numbers; only the column differs."""
        txns = parse(statement(
            SA.row(date="01 Aug", desc="Transfer To Sam Rivers",
                   debit="50.00", balance="9,950.00CR"),
            SA.row(date="02 Aug", desc="Fast Transfer From Lee Parker",
                   credit="50.00", balance="10,000.00CR"),
            closing="10,000.00CR",
        ))
        assert [t.amount for t in txns] == [-50.00, 50.00]

    def test_rejects_row_with_both_columns(self, parse):
        with pytest.raises(StatementParseError, match="exactly one of debit/credit"):
            parse(statement(
                SA.row(date="01 Aug", desc="Odd", debit="10.00", credit="10.00",
                       balance="10,000.00CR"),
            ))


class TestMultiRowEntries:
    def test_description_spans_rows_until_the_amount(self, parse):
        txns = parse(statement(
            SA.row(date="01 Aug", desc="Transfer To Jane Bennett"),
            SA.row(desc="CommBank App rent", debit="1,200.00", balance="8,800.00CR"),
            closing="8,800.00CR",
        ))
        assert len(txns) == 1
        assert txns[0].description == "Transfer To Jane Bennett CommBank App rent"
        assert txns[0].amount == -1200.00

    def test_three_row_entry(self, parse):
        txns = parse(statement(
            SA.row(date="13 Aug", desc="Transfer To Example Airways Pty Ltd- 2"),
            SA.row(desc="PayID Email from CommBank App"),
            SA.row(desc="flight to melbourne", debit="204.00", balance="9,796.00CR"),
            closing="9,796.00CR",
        ))
        assert txns[0].description == (
            "Transfer To Example Airways Pty Ltd- 2 "
            "PayID Email from CommBank App flight to melbourne"
        )

    def test_entry_without_an_amount_is_an_error(self, parse):
        with pytest.raises(StatementParseError, match="has no amount"):
            parse(statement(
                SA.row(date="01 Aug", desc="Transfer To Jane Bennett"),
                SA.row(date="02 Aug", desc="Something else", debit="1.00",
                       balance="9,999.00CR"),
            ))


class TestYearInference:
    def test_year_rolls_over_within_a_period(self, parse):
        """Entry rows omit the year and this period straddles new year."""
        txns = parse(statement(
            SA.row(date="30 Dec", desc="Salary Northern Beaches",
                   credit="1,000.00", balance="11,000.00CR"),
            SA.row(date="02 Jan", desc="Direct Debit 222222 GYM MEMBERSHIP",
                   debit="32.70", balance="10,967.30CR"),
            closing="10,967.30CR",
        ))
        assert [t.date for t in txns] == ["2025-12-30", "2026-01-02"]

    def test_date_outside_the_period_is_rejected(self, parse):
        with pytest.raises(StatementParseError, match="falls outside"):
            parse(statement(
                SA.row(date="15 Mar", desc="Impossible", debit="1.00",
                       balance="9,999.00CR"),
            ))


class TestBalanceReconciliation:
    def test_broken_chain_is_rejected(self, parse):
        """A misread column or dropped row must not import silently."""
        with pytest.raises(StatementParseError, match="balance mismatch"):
            parse(statement(
                SA.row(date="01 Aug", desc="Transfer To Sam Rivers",
                       debit="50.00", balance="12,000.00CR"),
            ))

    def test_closing_balance_must_match(self, parse):
        with pytest.raises(StatementParseError, match="!= closing balance"):
            parse(statement(
                SA.row(date="01 Aug", desc="Transfer To Sam Rivers",
                       debit="50.00", balance="9,950.00CR"),
                closing="8,000.00CR",
            ))

    def test_missing_opening_balance_is_rejected(self, parse):
        rows = [
            line(text_words(PERIOD, 50)),
            SA.header(),
            SA.row(date="01 Aug", desc="Transfer", debit="50.00", balance="9,950.00CR"),
        ]
        with pytest.raises(StatementParseError, match="missing opening or closing"):
            parse(rows)


class TestMetadata:
    def test_records_balance_and_account(self, parse):
        txns = parse(statement(
            SA.row(date="01 Aug", desc="Transfer To Sam Rivers",
                   debit="50.00", balance="9,950.00CR"),
            closing="9,950.00CR",
        ))
        raw = txns[0].raw
        assert raw["balance"] == "9950.00"
        assert raw["account_number"] == "06123412345678"
        assert raw["statement_period"] == "2025-08-01..2026-01-31"
        assert CBAPDFParser.source_type == "cba"
        assert txns[0].currency == "AUD"

    def test_missing_period_is_rejected(self, parse):
        with pytest.raises(StatementParseError, match="no statement period"):
            parse([SA.header(), SA.row(date="01 Aug", desc="x", debit="1.00",
                                       balance="1.00CR")])


class TestPageHandling:
    def test_masthead_of_next_page_is_not_a_continuation(self, parse):
        """Each page repeats the header; text above it must be ignored."""
        rows = [
            line(text_words(PERIOD, 50)),
            SA.header(page=1),
            SA.row(date="01 Aug", desc="2025 OPENING BALANCE", balance="10,000.00CR"),
            SA.row(date="01 Aug", desc="Transfer To Sam Rivers",
                   debit="50.00", balance="9,950.00CR"),
            line(text_words("Statement 70 Page 2 of 2)", 50), page=2),
            line(text_words("Account Number 06 1234 12345678", 50), page=2),
            SA.header(page=2),
            SA.row(date="02 Aug", desc="Fast Transfer From Lee Parker",
                   credit="472.00", balance="10,422.00CR", page=2),
            SA.row(date="31 Jan", desc="2026 CLOSING BALANCE",
                   balance="10,422.00CR", page=2),
        ]
        txns = parse(rows)
        assert [t.description for t in txns] == [
            "Transfer To Sam Rivers", "Fast Transfer From Lee Parker",
        ]
