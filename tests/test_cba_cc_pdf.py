"""Tests for the CBA credit card (Awards Mastercard) PDF parser."""
from pathlib import Path

import pytest

from etl.parsers import cba_cc_pdf
from etl.parsers.cba_cc_pdf import CBACreditPDFParser
from etl.parsers.pdf_layout import StatementParseError
from tests.statement_rows import CreditCard as CC
from tests.statement_rows import line, text_words

PERIOD = "Statement Period 26 Nov 2025 - 23 Dec 2025"


def statement(*entries, opening="500.00", charges="60.26", payments="500.00", closing="60.26"):
    """Wrap entry rows in the page-1 payment summary used to reconcile them."""
    return [
        line(text_words(PERIOD, 50)),
        line(text_words("Account 4111 1111 1111 1111", 50)),
        line(text_words(f"Opening balance at 26 Nov ${opening}", 50)),
        line(text_words(f"New transactions and charges ${charges} Total amount owing $0.00", 50)),
        line(text_words(f"Payments/refunds -${payments}", 50)),
        line(text_words(f"Closing balance at 23 Dec ${closing}", 50)),
        CC.header(),
        *entries,
    ]


@pytest.fixture
def parse(monkeypatch):
    def _parse(rows):
        monkeypatch.setattr(cba_cc_pdf, "extract_rows", lambda *a, **k: rows)
        return CBACreditPDFParser().parse_statement(Path("card.pdf")).rows
    return _parse


class TestSigns:
    def test_charge_is_negative_and_trailing_minus_is_a_credit(self, parse):
        txns = parse(statement(
            CC.row(date="26 Nov", desc="Corner Cafe Newtown Newtown", amount="10.26"),
            CC.row(date="27 Nov", desc="Supermarket 1234 Newtown", amount="50.00"),
            CC.row(date="01 Dec", desc="Payment Received, Thank You", amount="500.00-"),
        ))
        assert [t.amount for t in txns] == [-10.26, -50.00, 500.00]
        assert txns[2].description == "Payment Received, Thank You"


class TestContinuationRows:
    def test_note_below_an_entry_joins_its_description(self, parse):
        """Foreign-merchant notes are printed on their own line."""
        txns = parse(statement(
            CC.row(date="09 Dec", desc="Games Store Seattle", amount="10.26"),
            CC.row(desc="## DEU MERCHANT"),
            CC.row(date="10 Dec", desc="Supermarket 1234 Newtown", amount="50.00"),
            CC.row(date="11 Dec", desc="Payment Received, Thank You", amount="500.00-"),
        ))
        assert len(txns) == 3
        assert txns[0].description == "Games Store Seattle ## DEU MERCHANT"


class TestInterestLines:
    def test_zero_interest_lines_are_dropped(self, parse):
        """Printed every month whether or not interest was charged."""
        txns = parse(statement(
            CC.row(date="26 Nov", desc="Corner Cafe Newtown", amount="60.26"),
            CC.row(desc="Interest charged on purchases Purchase Rate 20.990%p.a.", amount="0.00"),
            CC.row(date="01 Dec", desc="Payment Received, Thank You", amount="500.00-"),
        ))
        assert len(txns) == 2
        assert all("Interest" not in t.description for t in txns)

    def test_charged_interest_is_dated_to_the_period_end(self, parse):
        txns = parse(statement(
            CC.row(date="26 Nov", desc="Corner Cafe Newtown", amount="50.26"),
            CC.row(desc="Interest charged on purchases Purchase Rate 20.990%p.a.", amount="10.00"),
            CC.row(date="01 Dec", desc="Payment Received, Thank You", amount="500.00-"),
        ))
        interest = [t for t in txns if "Interest" in t.description]
        assert len(interest) == 1
        assert interest[0].date == "2025-12-23"
        assert interest[0].amount == -10.00


class TestStopSections:
    def test_recurring_payments_table_is_not_imported(self, parse):
        """That table lists amounts and dates but they are not transactions."""
        txns = parse(statement(
            CC.row(date="26 Nov", desc="Corner Cafe Newtown", amount="60.26"),
            CC.row(date="01 Dec", desc="Payment Received, Thank You", amount="500.00-"),
            line(text_words("Helping you identify your regular payments", CC.DESC_X0)),
            CC.row(desc="Games Store Seattle Hh 31.48 10 Dec 2025"),
            CC.row(date="22 Nov", desc="Jb Hifi Mobile Southbank", amount="99.00"),
        ))
        assert len(txns) == 2


class TestYearInference:
    def test_period_straddling_new_year(self, parse):
        rows = statement(
            CC.row(date="28 Dec", desc="Woolworths 3604 Newtown", amount="60.26"),
            CC.row(date="05 Jan", desc="Payment Received, Thank You", amount="500.00-"),
        )
        rows[0] = line(text_words("Statement Period 24 Dec 2025 - 22 Jan 2026", 50))
        txns = parse(rows)
        assert [t.date for t in txns] == ["2025-12-28", "2026-01-05"]


class TestReconciliation:
    def test_charges_must_match_the_summary(self, parse):
        with pytest.raises(StatementParseError, match="charges total"):
            parse(statement(
                CC.row(date="26 Nov", desc="Corner Cafe", amount="99.99"),
                CC.row(date="01 Dec", desc="Payment Received", amount="500.00-"),
            ))

    def test_payments_must_match_the_summary(self, parse):
        with pytest.raises(StatementParseError, match="payments total"):
            parse(statement(
                CC.row(date="26 Nov", desc="Corner Cafe", amount="60.26"),
                CC.row(date="01 Dec", desc="Payment Received", amount="123.00-"),
            ))

    def test_summary_must_balance(self, parse):
        with pytest.raises(StatementParseError, match="closing balance says"):
            parse(statement(
                CC.row(date="26 Nov", desc="Corner Cafe", amount="60.26"),
                CC.row(date="01 Dec", desc="Payment Received", amount="500.00-"),
                closing="999.99",
            ))

    def test_missing_summary_is_rejected(self, parse):
        with pytest.raises(StatementParseError, match="payment summary missing"):
            parse([
                line(text_words(PERIOD, 50)),
                CC.header(),
                CC.row(date="26 Nov", desc="Corner Cafe", amount="10.00"),
            ])


class TestMetadata:
    def test_records_card_and_period(self, parse):
        txns = parse(statement(
            CC.row(date="26 Nov", desc="Corner Cafe Newtown", amount="60.26"),
            CC.row(date="01 Dec", desc="Payment Received, Thank You", amount="500.00-"),
        ))
        raw = txns[0].raw
        assert raw["card"] == "4111 1111 1111 1111"
        assert raw["statement_period"] == "2025-11-26..2025-12-23"
        assert raw["closing_balance"] == "60.26"
        assert CBACreditPDFParser.source_type == "cba-cc"
