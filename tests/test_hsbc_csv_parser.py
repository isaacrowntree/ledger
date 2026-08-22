"""Tests for the HSBC credit card CSV parser (TransHist.csv exports)."""
import tempfile
from pathlib import Path

from etl.parsers.hsbc_csv import HSBCCSVParser

# HSBC exports a BOM, pads every field with spaces, quotes amounts, and leaves a
# trailing empty column. Reproduced verbatim so the parser is tested against the
# real shape rather than a tidied one.
SAMPLE = (
    '﻿ Transaction Date ,Posting Date,Description,Amount,\n'
    ' 22 Aug 2026,,SQ *CORNER CAFE        Suburbia 036 ,"35.18"\n'
    ' 21 Aug 2026, 22 Aug 2026,Local Kebab              Suburbia     036 ,"20.28"\n'
    ' 31 Jul 2026, 31 Jul 2026,BPAY PAYMENT             - ,"-4,267.00"\n'
)


def _write(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                    newline="", encoding="utf-8")
    f.write(text)
    f.close()
    return Path(f.name)


class TestHSBCCSVParser:
    def test_parses_all_rows(self):
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert len(txns) == 3

    def test_dates_normalized_and_chronological(self):
        """HSBC exports newest-first; the engine requires chronological order."""
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert [t.date for t in txns] == ["2026-07-31", "2026-08-21", "2026-08-22"]

    def test_purchases_negative_payments_positive(self):
        """HSBC files purchases positive and payments negative -- the inverse of
        Ledger's convention, matching the credit card PDF layout."""
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert txns[2].amount == -35.18
        assert txns[0].amount == 4267.00

    def test_column_padding_collapsed(self):
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert txns[2].description == "SQ *CORNER CAFE Suburbia 036"

    def test_blank_posting_date_tolerated(self):
        """Unsettled transactions have no posting date; they must still import."""
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert txns[2].raw["Posting Date"] == ""

    def test_rows_land_in_the_hsbc_account(self):
        """source_type matches the PDF parser so both formats share one account."""
        assert HSBCCSVParser().source_type == "hsbc"

    def test_malformed_rows_skipped(self):
        bad = ('﻿ Transaction Date ,Posting Date,Description,Amount,\n'
               ' ,,,\n'
               ' not a date,,SOMETHING,"1.00"\n'
               ' 22 Aug 2026,,NO AMOUNT,\n')
        assert HSBCCSVParser().parse_statement(_write(bad)).rows == []
