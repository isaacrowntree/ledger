"""Charges HSBC prints without a date.

A statement lists fees like "OVERSEAS TRANSACTION FEE $6.86" in the transaction
list but with no date against them. A date-anchored line matcher skips them, so
the statement's rows fall short of its own closing balance -- which is exactly how
a real $6.86 charge went missing and left the statement unreconcilable.

They are attributed to the last dated transaction seen, or to the end of the
statement period when the fee is printed before any transaction.
"""
from etl.parsers.hsbc_pdf import HSBCPDFParser
from etl.parsers.dates import StatementPeriod

PERIOD = StatementPeriod("2026-04-08", "2026-05-05")


def _entries(text):
    return HSBCPDFParser()._parse_entries(text, "2026", False, PERIOD)


class TestUndatedCharges:
    def test_fee_without_a_date_is_captured(self):
        text = ("25/04/26 9010 LEISURE CENTRE $228.76\n"
                "OVERSEAS TRANSACTION FEE $6.86")
        entries = _entries(text)
        assert len(entries) == 2
        assert entries[1]["description"].startswith("OVERSEAS TRANSACTION FEE")

    def test_fee_is_a_charge_not_a_credit(self):
        """A fee increases what is owed, so it is negative like any purchase.
        Entries leave _parse_entries already in ledger convention."""
        text = "OVERSEAS TRANSACTION FEE $6.86"
        assert _entries(text)[0]["amount"] == -6.86

    def test_fee_takes_the_date_of_the_preceding_transaction(self):
        text = ("25/04/26 9010 LEISURE CENTRE $228.76\n"
                "OVERSEAS TRANSACTION FEE $6.86")
        entries = _entries(text)
        assert entries[1]["date"] == entries[0]["date"] == "2026-04-25"

    def test_fee_before_any_transaction_uses_the_period_end(self):
        entries = _entries("OVERSEAS TRANSACTION FEE $6.86")
        assert entries[0]["date"] == "2026-05-05"

    def test_interest_charges_are_captured(self):
        for label in ("INTEREST ON PURCHASES $12.34", "INTEREST CHARGED $12.34",
                      "ANNUAL FEE $99.00", "CASH ADVANCE FEE $5.00",
                      "LATE PAYMENT FEE $30.00"):
            assert _entries(label), f"missed: {label}"

    def test_marketing_lines_with_amounts_are_not_transactions(self):
        """The page is full of dollar figures that are not charges."""
        noise = ("Minimum Payment $20.00\n"
                 "Credit Limit $15,000.00\n"
                 "estimated total of interest charges of $91.89\n"
                 "Only the minimum payment 1 Year and 1 Month $23.78")
        assert _entries(noise) == []

    def test_a_dated_fee_is_not_double_counted(self):
        """When the fee does carry a date it is an ordinary transaction."""
        entries = _entries("27/04/26 OVERSEAS TRANSACTION FEE $6.86")
        assert len(entries) == 1
        assert entries[0]["date"] == "2026-04-27"
