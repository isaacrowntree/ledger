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
    def test_parses_settled_rows(self):
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert len(txns) == 2

    def test_dates_normalized_and_chronological(self):
        """HSBC exports newest-first; the engine requires chronological order."""
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert [t.date for t in txns] == ["2026-07-31", "2026-08-21"]

    def test_purchases_negative_payments_positive(self):
        """HSBC files purchases positive and payments negative -- the inverse of
        Ledger's convention, matching the credit card PDF layout."""
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert txns[1].amount == -20.28
        assert txns[0].amount == 4267.00

    def test_column_padding_collapsed(self):
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert txns[1].description == "Local Kebab Suburbia 036"

    def test_pending_rows_are_skipped(self):
        """A blank Posting Date is a pending charge. HSBC describes it differently
        once it settles (the pending "... 036" suffix goes), so importing it now
        and the settled copy later double-counts it; its amount can also change."""
        txns = HSBCCSVParser().parse_statement(_write(SAMPLE)).rows
        assert all(t.raw["Posting Date"] for t in txns)
        assert "SQ *CORNER CAFE Suburbia 036" not in [t.description for t in txns]

    def test_export_without_posting_column_is_taken_as_settled(self):
        text = ('Transaction Date,Description,Amount\n'
                '21 Aug 2026,Local Kebab,"20.28"\n')
        assert len(HSBCCSVParser().parse_statement(_write(text)).rows) == 1

    def test_rows_land_in_the_hsbc_account(self):
        """source_type matches the PDF parser so both formats share one account."""
        assert HSBCCSVParser().source_type == "hsbc"

    def test_malformed_rows_skipped(self):
        bad = ('﻿ Transaction Date ,Posting Date,Description,Amount,\n'
               ' ,,,\n'
               ' not a date,,SOMETHING,"1.00"\n'
               ' 22 Aug 2026,,NO AMOUNT,\n')
        assert HSBCCSVParser().parse_statement(_write(bad)).rows == []
