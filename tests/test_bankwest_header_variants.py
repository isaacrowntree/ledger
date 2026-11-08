"""Bankwest's transaction-section header has changed over the years.

The parser only starts reading rows once it sees the column header. When the
header regex was too narrow, an older statement whose header carries an extra
"Card" column parsed to ZERO transactions -- while its own summary showed the
balance moving by thousands. Silent and total data loss for that
statement.
"""
from etl.parsers.bankwest_pdf import BankwestPDFParser

HEADERS = [
    "Date Description Debit Credit",           # current layout
    "Date Description Card Debit Credit",      # older, with a card column
    "Date Description Amount",                 # single amount column
    "Date Description Card Debit Credit Balance",
]


def _entries(header, rows="06 Jan 21 TRANSIT TOPUP CITYNAME $10.00"):
    return BankwestPDFParser()._parse_entries(f"{header}\n{rows}")


class TestHeaderVariants:
    def test_every_known_header_opens_the_section(self):
        for header in HEADERS:
            assert _entries(header), f"header not recognised: {header!r}"

    def test_row_is_read_correctly(self):
        entries = _entries("Date Description Card Debit Credit")
        assert entries[0]["date"] == "2021-01-06"
        assert entries[0]["description"].startswith("TRANSIT TOPUP")
        assert abs(entries[0]["amount"]) == 10.00

    def test_rows_before_the_header_are_ignored(self):
        """Summary figures above the transaction list are not transactions."""
        text = ("Opening balance $10.00\n"
                "Date Description Card Debit Credit\n"
                "06 Jan 21 TRANSIT TOPUP CITYNAME $10.00")
        assert len(BankwestPDFParser()._parse_entries(text)) == 1

    def test_unrelated_header_does_not_open_the_section(self):
        assert BankwestPDFParser()._parse_entries(
            "Your interest rates Your account summary\n"
            "06 Jan 21 TRANSIT TOPUP CITYNAME $10.00") == []


class TestRepaymentWarningEndsTheSection:
    """The 2020-21 layout prints its minimum-repayment warning right after the
    last row on a page. Read as a continuation it was glued onto that purchase,
    and the word "Repayment" then categorised it as Mortgage."""

    def test_warning_is_not_part_of_the_last_description(self):
        entries = _entries(
            "Date Description Card Debit Credit",
            "03 Feb 21 SOME STORE PTY LTD SUBURB NSW $1,965.50\n"
            "Minimum Repayment Warning: If you make only the minimum payment each month\n"
            "it will take you longer to pay off your balance. For example:\n"
            "Having trouble making repayments?: If you are having difficulty",
        )
        assert len(entries) == 1
        assert entries[0]["description"] == "SOME STORE PTY LTD SUBURB NSW"
        assert entries[0]["amount"] == -1965.50
