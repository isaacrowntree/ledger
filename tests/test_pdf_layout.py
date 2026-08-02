"""Tests for the shared column-aware PDF layout helpers."""
import pytest

from etl.parsers.pdf_layout import (
    ColumnRuler,
    Word,
    make_row,
    parse_amount,
    parse_balance,
)
from tests.statement_rows import Bankwest, CreditCard, SmartAccess, money, text_words


class TestAmountParsing:
    @pytest.mark.parametrize("text,expected", [
        ("10.26", 10.26),
        ("$1,500.00", 1500.00),
        ("10,000.00CR", 10000.00),
        ("$300,000.00DR", 300000.00),
        ("900.00-", 900.00),
    ])
    def test_magnitude_ignores_markers(self, text, expected):
        assert parse_amount(text) == expected

    @pytest.mark.parametrize("text", ["20.990%p.a.", "9900000-0", "123976", "RENT", "3,367"])
    def test_rejects_non_amounts(self, text):
        assert parse_amount(text) is None

    def test_debit_balance_is_negative(self):
        # A loan balance is money owed, so it sits below zero.
        assert parse_balance("$300,000.00DR") == -300000.00

    def test_credit_balance_is_positive(self):
        assert parse_balance("10,000.00CR") == 10000.00
        assert parse_balance("$5,000.00") == 5000.00


class TestSuffixMerging:
    def test_rejoins_split_marker(self):
        # Whether "8,000.00CR" arrives as one token or two depends on the
        # extraction tolerance; the column lookup needs the marker's edge.
        row = make_row([Word("8,000.00", 484, 524), Word("CR", 527, 540)])
        assert len(row.words) == 1
        assert row.words[0].text == "8,000.00CR"
        assert row.words[0].x1 == 540

    def test_leaves_distant_marker_alone(self):
        row = make_row([Word("8,000.00", 484, 524), Word("CR", 560, 574)])
        assert len(row.words) == 2

    def test_ignores_marker_after_non_amount(self):
        row = make_row([Word("BALANCE", 100, 150), Word("CR", 152, 165)])
        assert len(row.words) == 2


class TestColumnRuler:
    def test_separates_equal_amounts_by_column(self):
        """The whole point: identical text, different meaning."""
        ruler = ColumnRuler.from_header(
            SmartAccess.header(), {"debit": "Debit", "credit": "Credit", "balance": "Balance"}
        )
        debit = make_row([money("50.00", SmartAccess.DEBIT_X1)])
        credit = make_row([money("50.00", SmartAccess.CREDIT_X1)])
        assert ruler.amounts_in(debit) == {"debit": (50.0, "50.00")}
        assert ruler.amounts_in(credit) == {"credit": (50.0, "50.00")}

    def test_multi_word_label_anchors_on_last_word(self):
        ruler = ColumnRuler.from_header(CreditCard.header(), {"amount": "Amount (A$)"})
        # Anchoring on "Amount" instead of "(A$)" would miss by 19pt.
        assert ruler.anchors["amount"] == 563

    def test_missing_label_yields_no_ruler(self):
        assert ColumnRuler.from_header(SmartAccess.header(), {"nope": "Nope"}) is None

    def test_allows_dr_marker_to_overhang_column(self):
        """Bankwest right-aligns the number and lets "DR" hang past the edge."""
        ruler = ColumnRuler.from_header(
            Bankwest.header(), {"debit": "Debit", "credit": "Credit", "balance": "Balance"}
        )
        row = make_row([Bankwest.balance("$300,000.00DR")])
        assert ruler.amounts_in(row) == {"balance": (300000.00, "$300,000.00DR")}

    def test_unmarked_word_may_not_overhang(self):
        ruler = ColumnRuler.from_header(
            Bankwest.header(), {"debit": "Debit", "credit": "Credit", "balance": "Balance"}
        )
        row = make_row([money("$1,234.56", Bankwest.BALANCE_X1 + 12.6)])
        assert ruler.amounts_in(row) == {}

    def test_description_text_is_not_an_amount(self):
        """Alignment alone must not pull a word out of the description."""
        ruler = ColumnRuler.from_header(
            SmartAccess.header(), {"debit": "Debit", "credit": "Credit", "balance": "Balance"}
        )
        word = Word("PASSPORT", SmartAccess.DEBIT_X1 - 40, SmartAccess.DEBIT_X1)
        assert ruler.column_of(word) == "debit"
        assert ruler.is_column_amount(word) is False


class TestTextWords:
    def test_description_words_stay_clear_of_columns(self):
        words = text_words("Direct Debit 222222 GYM MEMBERSHIP", SmartAccess.DESC_X0)
        assert max(w.x1 for w in words) < SmartAccess.DEBIT_X1 - 12
