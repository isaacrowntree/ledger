"""Tests for the PayPal CSV parser."""
import csv
import tempfile
from pathlib import Path

from etl.parsers.paypal_csv import PayPalCSVParser


def _write_csv(rows: list[dict], headers: list[str]) -> Path:
    """Write a temporary CSV file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    f.close()
    return Path(f.name)


# Activity report format (real PayPal exports)
ACTIVITY_HEADERS = [
    "Date", "Time", "Time Zone", "Description", "Currency", "Gross", "Fee",
    "Net", "Balance", "Transaction ID", "From email", "Name",
    "Bank Name", "Bank Account", "Shipping and handling amount", "GST",
    "Invoice ID", "Reference Txn ID",
]


class TestPayPalBasicParsing:
    def test_simple_purchase(self):
        rows = [
            {"Date": "15/01/2025", "Time": "10:00:00", "Time Zone": "Australia/Sydney",
             "Description": "Pre-approved payment", "Currency": "AUD", "Gross": "-50.00",
             "Fee": "0.00", "Net": "-50.00", "Balance": "", "Transaction ID": "TXN001",
             "Name": "Netflix", "Reference Txn ID": "", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
        ]
        path = _write_csv(rows, ACTIVITY_HEADERS)
        parser = PayPalCSVParser()
        txns = parser.parse(path)
        assert len(txns) == 1
        assert txns[0].amount == -50.0
        assert txns[0].description == "Netflix"
        assert txns[0].date == "2025-01-15"
        assert txns[0].currency == "AUD"
        path.unlink()

    def test_payment_received(self):
        rows = [
            {"Date": "20/02/2025", "Time": "14:00:00", "Time Zone": "Australia/Sydney",
             "Description": "Payment received", "Currency": "AUD", "Gross": "200.00",
             "Fee": "-5.00", "Net": "195.00", "Balance": "", "Transaction ID": "TXN002",
             "Name": "Client Payment", "Reference Txn ID": "", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
        ]
        path = _write_csv(rows, ACTIVITY_HEADERS)
        txns = PayPalCSVParser().parse(path)
        assert len(txns) == 1
        assert txns[0].amount == 200.0
        assert txns[0].fee == 5.0
        path.unlink()


class TestPayPalCurrencyConversion:
    def test_foreign_purchase_with_conversion(self):
        """USD purchase with AUD conversion rows should collapse into one AUD transaction."""
        rows = [
            {"Date": "10/03/2025", "Time": "10:00:00", "Time Zone": "Australia/Sydney",
             "Description": "Pre-approved payment", "Currency": "USD", "Gross": "-50.00",
             "Fee": "0.00", "Net": "-50.00", "Balance": "", "Transaction ID": "TXN_MAIN",
             "Name": "Twilio", "Reference Txn ID": "B-REF1", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
            {"Date": "10/03/2025", "Time": "10:00:01", "Time Zone": "Australia/Sydney",
             "Description": "General currency conversion", "Currency": "USD", "Gross": "50.00",
             "Fee": "0.00", "Net": "50.00", "Balance": "", "Transaction ID": "TXN_CONV1",
             "Name": "", "Reference Txn ID": "TXN_MAIN", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
            {"Date": "10/03/2025", "Time": "10:00:02", "Time Zone": "Australia/Sydney",
             "Description": "General currency conversion", "Currency": "AUD", "Gross": "-75.50",
             "Fee": "0.00", "Net": "-75.50", "Balance": "", "Transaction ID": "TXN_CONV2",
             "Name": "", "Reference Txn ID": "TXN_MAIN", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
        ]
        path = _write_csv(rows, ACTIVITY_HEADERS)
        txns = PayPalCSVParser().parse(path)
        # Should collapse to 1 transaction
        assert len(txns) == 1
        assert txns[0].description == "Twilio"
        assert txns[0].amount == -75.5  # AUD amount
        assert txns[0].original_amount == -50.0  # USD amount
        assert txns[0].original_currency == "USD"
        path.unlink()

    def test_foreign_purchase_without_conversion(self):
        """USD purchase with no conversion rows stores USD amount as approximate."""
        rows = [
            {"Date": "18/03/2026", "Time": "10:00:00", "Time Zone": "Australia/Sydney",
             "Description": "Payment refund", "Currency": "USD", "Gross": "79.00",
             "Fee": "0.00", "Net": "79.00", "Balance": "", "Transaction ID": "TXN_REFUND",
             "Name": "Private Internet Access, Inc.", "Reference Txn ID": "TXN_ORIG",
             "From email": "", "Bank Name": "", "Bank Account": "",
             "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
        ]
        path = _write_csv(rows, ACTIVITY_HEADERS)
        txns = PayPalCSVParser().parse(path)
        assert len(txns) == 1
        assert txns[0].amount == 79.0  # Stored as-is (no AUD conversion available)
        assert txns[0].original_amount == 79.0
        assert txns[0].original_currency == "USD"
        path.unlink()


class TestPayPalConversionSkipping:
    def test_conversion_rows_not_emitted(self):
        """General currency conversion rows should be merged, not emitted separately."""
        rows = [
            {"Date": "10/03/2025", "Time": "10:00:00", "Time Zone": "Australia/Sydney",
             "Description": "Pre-approved payment", "Currency": "USD", "Gross": "-20.00",
             "Fee": "0.00", "Net": "-20.00", "Balance": "", "Transaction ID": "TXN_A",
             "Name": "Some Service", "Reference Txn ID": "", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
            {"Date": "10/03/2025", "Time": "10:00:01", "Time Zone": "Australia/Sydney",
             "Description": "General currency conversion", "Currency": "USD", "Gross": "20.00",
             "Fee": "0.00", "Net": "20.00", "Balance": "", "Transaction ID": "TXN_B",
             "Name": "", "Reference Txn ID": "TXN_A", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
            {"Date": "10/03/2025", "Time": "10:00:02", "Time Zone": "Australia/Sydney",
             "Description": "General currency conversion", "Currency": "AUD", "Gross": "-30.00",
             "Fee": "0.00", "Net": "-30.00", "Balance": "", "Transaction ID": "TXN_C",
             "Name": "", "Reference Txn ID": "TXN_A", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
        ]
        path = _write_csv(rows, ACTIVITY_HEADERS)
        txns = PayPalCSVParser().parse(path)
        assert len(txns) == 1  # Only the parent, not conversion rows
        assert txns[0].amount == -30.0  # AUD
        path.unlink()


class TestPayPalTransferDetection:
    def test_general_card_deposit_parsed(self):
        """Bank deposits to PayPal should be parsed (categorized as transfer by rules)."""
        rows = [
            {"Date": "01/01/2025", "Time": "10:00:00", "Time Zone": "Australia/Sydney",
             "Description": "General card deposit", "Currency": "AUD", "Gross": "500.00",
             "Fee": "0.00", "Net": "500.00", "Balance": "", "Transaction ID": "TXN_DEP",
             "Name": "General card deposit", "Reference Txn ID": "", "From email": "",
             "Bank Name": "", "Bank Account": "", "Shipping and handling amount": "",
             "GST": "", "Invoice ID": ""},
        ]
        path = _write_csv(rows, ACTIVITY_HEADERS)
        txns = PayPalCSVParser().parse(path)
        assert len(txns) == 1
        assert txns[0].amount == 500.0
        path.unlink()


class TestPayPalIdempotency:
    def test_dedup_by_transaction_id(self):
        """Same transaction ID in two files should only insert once."""
        rows = [
            {"Date": "15/01/2025", "Time": "10:00:00", "Time Zone": "Australia/Sydney",
             "Description": "Pre-approved payment", "Currency": "AUD", "Gross": "-50.00",
             "Fee": "0.00", "Net": "-50.00", "Balance": "", "Transaction ID": "UNIQUE_TXN",
             "Name": "Netflix", "Reference Txn ID": "", "From email": "", "Bank Name": "",
             "Bank Account": "", "Shipping and handling amount": "", "GST": "", "Invoice ID": ""},
        ]
        path1 = _write_csv(rows, ACTIVITY_HEADERS)
        path2 = _write_csv(rows, ACTIVITY_HEADERS)
        parser = PayPalCSVParser()
        txns1 = parser.parse(path1)
        txns2 = parser.parse(path2)
        # Both parse the same transaction
        assert txns1[0].reference_id == txns2[0].reference_id
        path1.unlink()
        path2.unlink()
