"""Promotional offers that look like transactions.

HSBC statements advertise balance-transfer offers in date-prefixed lines:

    28 Sep 20 BALANCE TRANSFER 15 MONTHS 7.99% $4,000.00

Read as transactions these become phantom rows and the statement stops adding up.
A real transaction line never quotes an interest rate, so the rate is the tell.
"""
from etl.parsers.hsbc_pdf import HSBCPDFParser
from etl.parsers.dates import StatementPeriod

PERIOD = StatementPeriod("2020-06-06", "2020-07-06")


def _entries(text):
    return HSBCPDFParser()._parse_entries(text, "2020", False, PERIOD)


class TestPromotionalLines:
    def test_balance_transfer_offer_is_not_a_transaction(self):
        assert _entries("28 Sep 20 BALANCE TRANSFER 15 MONTHS 7.99% $4,000.00") == []

    def test_several_offers_are_all_excluded(self):
        text = ("28 Sep 20 BALANCE TRANSFER 15 MONTHS 7.99% $4,000.00\n"
                "5 Oct 20 BALANCE TRANSFER 15 MONTHS 7.99% $5,000.00\n"
                "5 Oct 20 BALANCE TRANSFER 15 MONTHS 7.99% $3,600.00")
        assert _entries(text) == []

    def test_real_interest_charges_are_kept(self):
        """An actual interest posting carries no rate and is real money."""
        entries = _entries("6/07/2020 INTEREST ON SPECIAL $8.02")
        assert len(entries) == 1
        assert abs(entries[0]["amount"]) == 8.02

    def test_ordinary_purchases_are_kept(self):
        entries = _entries("19/06/2020 9010 SOME MERCHANT $42.10")
        assert len(entries) == 1

    def test_offers_mixed_with_real_rows(self):
        text = ("28 Sep 20 BALANCE TRANSFER 15 MONTHS 7.99% $4,000.00\n"
                "19/06/2020 9010 BPAY PAYMENT -$2,836.64\n"
                "6/07/2020 INTEREST ON SPECIAL $0.05")
        assert len(_entries(text)) == 2
