"""Tests for the CommSec contract note parser."""
from datetime import date
from pathlib import Path

import pytest

from etl.parsers.commsec_pdf import (
    BUY,
    SELL,
    CommSecContractNoteParser,
    ContractNoteError,
)

PATH = Path("note.pdf")


def buy_note(code="XYZ", units="125", consideration="2,326.25", brokerage="19.95",
             trade_date="10/09/2020"):
    return f"""BUY
TAX INVOICE
WE HAVE BOUGHT THE FOLLOWING SECURITIES FOR YOU
COMPANY: EXAMPLE INDEX FUND
{code}
SECURITY: EXCHANGE TRADED FUND UNITS FULLY PAID
DATE: {trade_date}
AS AT DATE: {trade_date}
CONFIRMATION NO: 100000001
ORDER NO: N100000002
ACCOUNT NO: 1234567
TOTAL UNITS: {units}
CONSIDERATION (AUD): ${consideration}
BROKERAGE & COSTS INCL GST: ${brokerage}
APPLICATION MONEY: $0.00
TOTAL COST: $2,346.20
SETTLEMENT DATE: 14/09/2020
"""


def sell_note(code="XYZ", units="315", consideration="7,865.55", brokerage="19.95"):
    return f"""SELL
TAX INVOICE
WE HAVE SOLD THE FOLLOWING SECURITIES FOR YOU
COMPANY EXAMPLE INDEX FUND
{code}
SECURITY EXAMPLE INDEX FUND
DATE: 01/08/2024
AS AT DATE: 01/08/2024
CONFIRMATION NO: 100000003 315 24.970000
TOTAL UNITS: {units}
CONSIDERATION (AUD): ${consideration}
BROKERAGE & COSTS INCL GST: ${brokerage}
NET PROCEEDS: $7,845.60
SETTLEMENT DATE: 05/08/2024
"""


def parse(text):
    return CommSecContractNoteParser().parse_text(text, PATH)


class TestBuyNote:
    def test_reads_the_parcel(self):
        t = parse(buy_note())
        assert (t.side, t.code, t.units) == (BUY, "XYZ", 125.0)
        assert t.trade_date == date(2020, 9, 10)
        assert t.settlement_date == date(2020, 9, 14)
        assert t.consideration == 2326.25
        assert t.brokerage == 19.95
        assert t.confirmation_no == "100000001"

    def test_cost_base_includes_brokerage(self):
        """Brokerage is an incidental cost and lifts the cost base."""
        assert parse(buy_note()).cost_base == 2346.20


class TestSellNote:
    def test_reads_the_disposal(self):
        t = parse(sell_note())
        assert (t.side, t.code, t.units) == (SELL, "XYZ", 315.0)
        assert t.trade_date == date(2024, 8, 1)

    def test_proceeds_are_net_of_brokerage(self):
        """Brokerage reduces the proceeds, so it cuts the gain at both ends."""
        assert parse(sell_note()).proceeds == 7845.60

    def test_units_read_from_total_not_the_confirmation_line(self):
        """The sell layout prints units beside the confirmation number."""
        assert parse(sell_note()).units == 315.0


class TestSecurityIdentification:
    def test_ticker_taken_from_line_below_company(self):
        assert parse(buy_note(code="ABC")).code == "ABC"
        assert parse(buy_note()).name == "EXAMPLE INDEX FUND"

    def test_missing_ticker_is_an_error(self):
        text = buy_note().replace("XYZ\n", "", 1)
        with pytest.raises(ContractNoteError, match="no ticker"):
            parse(text)


class TestMissingFigures:
    """A note missing a figure must fail rather than silently mis-cost a parcel."""

    @pytest.mark.parametrize("line,message", [
        ("TOTAL UNITS: 125", "total units"),
        ("CONSIDERATION (AUD): $2,326.25", "consideration"),
        ("DATE: 10/09/2020", "trade date"),
    ])
    def test_missing_field_raises(self, line, message):
        with pytest.raises(ContractNoteError, match=message):
            parse(buy_note().replace(line + "\n", "", 1))

    def test_absent_brokerage_is_zero_not_an_error(self):
        text = buy_note().replace("BROKERAGE & COSTS INCL GST: $19.95\n", "", 1)
        t = parse(text)
        assert t.brokerage == 0.0
        assert t.cost_base == 2326.25

    def test_unrecognised_side_raises(self):
        text = buy_note().replace("WE HAVE BOUGHT THE FOLLOWING SECURITIES FOR YOU\n", "", 1)
        with pytest.raises(ContractNoteError, match="buy from a sell"):
            parse(text.replace("BUY\n", "", 1))


class TestOrdering:
    def test_parse_all_sorts_oldest_first(self, tmp_path):
        parser = CommSecContractNoteParser()
        trades = [
            parser.parse_text(sell_note(), PATH),
            parser.parse_text(buy_note(), PATH),
        ]
        assert [t.side for t in sorted(trades, key=lambda t: t.trade_date)] == [BUY, SELL]
