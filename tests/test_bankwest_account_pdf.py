"""Tests for the Bankwest home loan / offset account PDF parser."""
from pathlib import Path

import pytest

from etl.parsers import bankwest_account_pdf
from etl.parsers.bankwest_account_pdf import (
    BankwestLoanPDFParser,
    BankwestOffsetPDFParser,
)
from etl.parsers.pdf_layout import StatementParseError
from tests.statement_rows import Bankwest as BW
from tests.statement_rows import line, money, text_words

LOAN_TITLE = "BANKWEST SIMPLE HOME LOAN STATEMENT"
OFFSET_TITLE = "BANKWEST OFFSET TRAN ACCT STATEMENT"


def statement(*entries, title, opening, closing, debits=None, credits=None,
              account="123456-1", period="Period 15 Feb 24 - 14 Aug 24"):
    rows = [
        line(text_words(title, 50)),
        line(text_words(f"Account Number {account}", 355)),
        line(text_words(period, 355)),
        BW.header(),
        BW.row(date="15 FEB 24", desc="OPENING BALANCE", balance=opening),
        *entries,
        BW.row(date="14 AUG 24", desc="CLOSING BALANCE", balance=closing),
    ]
    if debits:
        rows.append(line(text_words("TOTAL DEBITS", 240), money(debits, BW.DEBIT_X1)))
    if credits:
        rows.append(line(text_words("TOTAL CREDITS", 233), money(credits, BW.CREDIT_X1)))
    return rows


@pytest.fixture
def parse_loan(monkeypatch):
    def _parse(rows):
        monkeypatch.setattr(bankwest_account_pdf, "extract_rows", lambda *a, **k: rows)
        return BankwestLoanPDFParser().parse(Path("homeloan.pdf"))
    return _parse


@pytest.fixture
def parse_offset(monkeypatch):
    def _parse(rows):
        monkeypatch.setattr(bankwest_account_pdf, "extract_rows", lambda *a, **k: rows)
        return BankwestOffsetPDFParser().parse(Path("offset.pdf"))
    return _parse


class TestLoanSigns:
    def test_debit_balances_are_negative_money_owed(self, parse_loan):
        txns = parse_loan(statement(
            BW.row(date="11 MAR 24", desc="DEBIT INTEREST",
                   debit="$1,500.00", balance="$301,500.00DR"),
            BW.row(date="11 MAR 24", desc="CREDIT TRANSFER FROM J BENNETT",
                   credit="$1,200.00", balance="$300,300.00DR"),
            title=LOAN_TITLE, opening="$300,000.00DR", closing="$300,300.00DR",
        ))
        # Interest charged is an expense; a repayment reduces what is owed.
        assert [t.amount for t in txns] == [-1500.00, 1200.00]
        assert txns[0].raw_data["balance"] == "-301500.00"

    def test_wrapped_description_amount_is_not_a_second_amount(self, parse_loan):
        """The offset saving wraps below the amount, inside Particulars."""
        txns = parse_loan(statement(
            BW.row(date="11 MAR 24", desc="DEBIT INTEREST AFTER OFFSET SAVING OF",
                   debit="$1,500.00"),
            BW.row(desc="$54.86", balance="$301,500.00DR"),
            title=LOAN_TITLE, opening="$300,000.00DR", closing="$301,500.00DR",
        ))
        assert len(txns) == 1
        assert txns[0].description == "DEBIT INTEREST AFTER OFFSET SAVING OF $54.86"
        assert txns[0].amount == -1500.00


class TestOffsetSigns:
    def test_fee_is_negative_and_deposit_positive(self, parse_offset):
        txns = parse_offset(statement(
            BW.row(date="03 JUN 24", desc="OFFSET FEE",
                   debit="$10.00", balance="$4,990.00"),
            BW.row(date="12 JUN 24", desc="J BENNETT morgtage",
                   credit="$5,000.00", balance="$9,990.00"),
            title=OFFSET_TITLE, opening="$5,000.00", closing="$9,990.00",
            account="123456-9",
        ))
        assert [t.amount for t in txns] == [-10.00, 5000.00]
        assert txns[0].source_type == "bankwest-offset"


class TestWrongAccountGuard:
    def test_loan_statement_rejected_by_offset_parser(self, parse_offset):
        """Both products share a layout, so only the title catches mis-filing."""
        rows = statement(
            BW.row(date="03 JUN 24", desc="OFFSET FEE",
                   debit="$10.00", balance="$4,990.00"),
            title=LOAN_TITLE, opening="$5,000.00", closing="$4,990.00",
        )
        with pytest.raises(StatementParseError, match="not a 'OFFSET TRAN ACCT' statement"):
            parse_offset(rows)

    def test_offset_statement_rejected_by_loan_parser(self, parse_loan):
        rows = statement(
            BW.row(date="03 JUN 24", desc="OFFSET FEE",
                   debit="$10.00", balance="$4,990.00"),
            title=OFFSET_TITLE, opening="$5,000.00", closing="$4,990.00",
        )
        with pytest.raises(StatementParseError, match="not a 'SIMPLE HOME LOAN' statement"):
            parse_loan(rows)


class TestReconciliation:
    def test_broken_balance_chain_is_rejected(self, parse_offset):
        with pytest.raises(StatementParseError, match="balance mismatch"):
            parse_offset(statement(
                BW.row(date="03 JUN 24", desc="OFFSET FEE",
                       debit="$10.00", balance="$99,999.99"),
                title=OFFSET_TITLE, opening="$5,000.00", closing="$99,999.99",
            ))

    def test_column_totals_are_cross_checked(self, parse_offset):
        """An independent check on top of the balance walk."""
        with pytest.raises(StatementParseError, match="debits total"):
            parse_offset(statement(
                BW.row(date="03 JUN 24", desc="OFFSET FEE",
                       debit="$10.00", balance="$4,990.00"),
                title=OFFSET_TITLE, opening="$5,000.00", closing="$4,990.00",
                debits="$99.00",
            ))

    def test_matching_totals_pass(self, parse_offset):
        txns = parse_offset(statement(
            BW.row(date="03 JUN 24", desc="OFFSET FEE",
                   debit="$10.00", balance="$4,990.00"),
            BW.row(date="12 JUN 24", desc="J BENNETT morgtage",
                   credit="$5,000.00", balance="$9,990.00"),
            title=OFFSET_TITLE, opening="$5,000.00", closing="$9,990.00",
            debits="$10.00", credits="$5,000.00",
        ))
        assert len(txns) == 2


class TestTrailingSections:
    def test_dated_interest_rate_block_is_not_a_transaction(self, parse_loan):
        """"14 FEB 24 Debit Interest Rates" trails the table and opens with a date."""
        rows = statement(
            BW.row(date="11 MAR 24", desc="DEBIT INTEREST",
                   debit="$1,500.00", balance="$301,500.00DR"),
            title=LOAN_TITLE, opening="$300,000.00DR", closing="$301,500.00DR",
            debits="$1,500.00",
        )
        rows.append(line(text_words("OFFSET TO LOAN ACCOUNT", BW.PART_X0)))
        rows.append(BW.row(date="14 FEB 24", desc="Debit Interest Rates"))
        rows.append(line(text_words("01 OWNER OCCUPIED LIMIT", BW.PART_X0),
                         BW.balance("$437,669.00")))
        txns = parse_loan(rows)
        assert len(txns) == 1


class TestMetadata:
    def test_records_account_and_period(self, parse_loan):
        txns = parse_loan(statement(
            BW.row(date="11 MAR 24", desc="DEBIT INTEREST",
                   debit="$1,500.00", balance="$301,500.00DR"),
            title=LOAN_TITLE, opening="$300,000.00DR", closing="$301,500.00DR",
        ))
        raw = txns[0].raw_data
        assert raw["account_number"] == "123456-1"
        assert raw["statement_period"] == "2024-02-15..2024-08-14"
        assert txns[0].date == "2024-03-11"
        assert txns[0].source_type == "bankwest-loan"
