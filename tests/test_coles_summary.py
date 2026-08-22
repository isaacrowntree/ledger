"""Reading Coles' Account Summary table.

Coles prints the summary as a two-line header followed by a row of values:

    Credit Limit Opening Instalment Closing Closing Balance Pay this amount Available
    Balance Balance Balance^ less Instalment by the due date Credit*
    $14,600.00 $0.00 $0.00 $0.00 $0.00 $14,600.00

Without the opening balance no Coles statement could be checked against its own
arithmetic at all, and a card-opening statement with no transactions was
indistinguishable from a failed parse.
"""
from etl.parsers.coles_pdf import summary_opening_balance

ZERO = (
    "Account Summary\n"
    "Credit Limit Opening Instalment Closing Closing Balance Pay this amount Available\n"
    "Balance Balance Balance^ less Instalment by the due date Credit*\n"
    "$14,600.00 $0.00 $0.00 $0.00 $0.00 $14,600.00\n"
    "Closing Balance $0.00\n"
)

ACTIVE = (
    "Account Summary\n"
    "Credit Limit Opening Instalment Closing Closing Balance Pay this amount Available\n"
    "Balance Balance Balance^ less Instalment by the due date Credit*\n"
    "$14,600.00 $1,234.56 $0.00 $2,000.00 $2,000.00 $12,600.00\n"
)


class TestSummaryOpeningBalance:
    def test_zero_opening_is_read(self):
        assert summary_opening_balance(ZERO) == 0.00

    def test_opening_is_the_second_value(self):
        assert summary_opening_balance(ACTIVE) == 1234.56

    def test_values_below_a_wrapped_header(self):
        """The header wraps over several lines before the values appear."""
        text = ("Credit Limit Opening Instalment Closing Closing Balance Pay this amount Available\n"
                "Balance Balance Balance^ less Instalment by the due date Credit*\n"
                "to maintain\n"
                "(not yet due) Balance^^\n"
                "interest free days\n"
                "$14,600.00 $0.00 $0.00 $0.00 $0.00 $14,600.00\n")
        assert summary_opening_balance(text) == 0.00

    def test_absent_summary(self):
        assert summary_opening_balance("no summary here") is None

    def test_header_without_values_is_refused(self):
        text = ("Credit Limit Opening Instalment Closing Closing Balance Pay this amount Available\n"
                "Balance Balance Balance^ less Instalment by the due date Credit*\n")
        assert summary_opening_balance(text) is None
