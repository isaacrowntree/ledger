"""Tests for the Coles Mastercard CSV parser."""
import csv
import tempfile
from pathlib import Path

from etl.parsers.coles_csv import ColesCSVParser

HEADERS = [
    "Date", "Amount", "Account Number", "", "Transaction Type",
    "Transaction Details", "Category", "Merchant Name", "Processed On",
]


def _write_csv(rows: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(f, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    f.close()
    return Path(f.name)


def _row(date, amount, details, ttype="CREDIT CARD PURCHASE"):
    return {
        "Date": date, "Amount": amount, "Account Number": "Card ending 0021", "": "",
        "Transaction Type": ttype, "Transaction Details": details,
        "Category": "", "Merchant Name": "", "Processed On": date,
    }


class TestColesCSVParsing:
    def test_purchase_is_negative(self):
        path = _write_csv([_row("08 July 26", "-27.98", "AMAZON AU MARKETPLACE SYDNEY AUS")])
        txns = ColesCSVParser().parse(path)
        assert len(txns) == 1
        t = txns[0]
        assert t.amount == -27.98               # already signed; no flip
        assert t.date == "2026-07-08"           # "DD Month YY" -> ISO
        assert t.description == "AMAZON AU MARKETPLACE SYDNEY AUS"
        assert t.currency == "AUD"
        assert t.source_type == "coles"         # same account as the PDF parser
        path.unlink()

    def test_payment_is_positive(self):
        path = _write_csv([_row("26 June 26", "200.00", "BPAY PAYMENT - THANK YOU",
                                ttype="CREDIT CARD PAYMENT")])
        txns = ColesCSVParser().parse(path)
        assert txns[0].amount == 200.0
        assert txns[0].date == "2026-06-26"
        path.unlink()

    def test_falls_back_to_merchant_name_when_details_blank(self):
        row = _row("10 June 26", "-15.39", "")
        row["Merchant Name"] = "Amazon"
        path = _write_csv([row])
        txns = ColesCSVParser().parse(path)
        assert txns[0].description == "Amazon"
        path.unlink()

    def test_bad_rows_skipped(self):
        path = _write_csv([
            _row("", "-5.00", "NO DATE"),
            _row("01 June 26", "", "NO AMOUNT"),
            _row("02 June 26", "-9.00", "GOOD"),
        ])
        txns = ColesCSVParser().parse(path)
        assert len(txns) == 1
        assert txns[0].description == "GOOD"
        path.unlink()
