"""Telling a Bankwest credit from a charge.

The statement puts credits in a Credit column and charges in a Debit column.
Flattening the PDF to plain text throws that away, and the parser was left
guessing from the description -- treating a row as a credit only if it mentioned
PAYMENT RECEIVED, CREDIT, REFUND or REVERSAL.

Every other credit became a charge. Across the archive that inflated spending and
understated payments by the same amount on 18 statements, each one showing a gap
of exactly twice the misread row.

Extraction now marks amounts found in the Credit column with a trailing "CR", so
the column decides and the description no longer has to.
"""
from etl.parsers.bankwest_pdf import BankwestPDFParser


def _entries(rows):
    return BankwestPDFParser()._parse_entries("Date Description Debit Credit\n" + rows)


class TestCreditColumnMarker:
    def test_marked_amount_is_a_credit(self):
        entries = _entries("20 Jan 21 SOME MERCHANT REBATE $36.04 CR")
        assert entries[0]["amount"] == 36.04

    def test_unmarked_amount_is_a_charge(self):
        entries = _entries("20 Jan 21 SOME MERCHANT $36.04")
        assert entries[0]["amount"] == -36.04

    def test_a_credit_needs_no_recognisable_wording(self):
        """The whole point: an ordinary-looking merchant name in the credit
        column is still a credit."""
        entries = _entries("20 Jan 21 HARDWARE STORE 370000 SUBURBIA $86.31 CR")
        assert entries[0]["amount"] == 86.31

    def test_description_keywords_still_work_without_a_marker(self):
        """Older extractions carry no column information; the keyword fallback
        must keep working for them."""
        entries = _entries("20 Jan 21 BILL PAYMENT RECEIVED FROM ING $1,500.00")
        assert entries[0]["amount"] == 1500.00

    def test_marker_is_not_left_in_the_description(self):
        entries = _entries("20 Jan 21 SOME MERCHANT REBATE $36.04 CR")
        assert "CR" not in entries[0]["description"].split()[-1:]
