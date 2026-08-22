"""Telling an ING loan statement from an everyday one.

This decides the balance convention, and getting it wrong inverts every amount
on the statement -- which is how mortgage interest came to be recorded as though
it reduced the debt.

Two earlier attempts failed:

* Searching the whole statement for "Orange Advantage" matched a product footer
  printed on an EVERYDAY statement, classifying a transaction account as a
  mortgage.
* Counting which convention the balances vote for fails on a statement where
  charges outnumber repayments -- a new loan's first quarter is mostly interest
  and fees, so the charges outvote the repayments and win with the wrong answer.

The header settles it: a loan statement says so in its first few lines, and a
footer sits far below them.
"""
from etl.parsers.ing_pdf import is_loan_statement

LOAN_HEADER = "\n".join([
    "Loan", "statement", "Client number: 12345678", "Mr I Example",
    "1 Example Street BSB number: 000 000", "SUBURB NSW 2000",
    "Loan type: Orange Advantage P&I", "Loan account number: 123456789",
])

EVERYDAY_WITH_FOOTER = "\n".join(
    ["Orange Everyday", "statement", "Client number: 12345678", "Mr I Example"]
    + ["Transactions"] * 40
    + ["Want to buy a home? Ask about an Orange Advantage home loan."]
)


class TestIsLoanStatement:
    def test_loan_statement_header_is_recognised(self):
        assert is_loan_statement(LOAN_HEADER)

    def test_everyday_statement_is_not_a_loan(self):
        """Regression: a product footer must not classify a transaction account
        as a mortgage and invert every amount on it."""
        assert not is_loan_statement(EVERYDAY_WITH_FOOTER)

    def test_savings_statement_is_not_a_loan(self):
        assert not is_loan_statement("Savings Maximiser\nstatement\nClient number: 1")

    def test_home_loan_wording(self):
        assert is_loan_statement("Home Loan statement\nAccount 1")

    def test_empty_text(self):
        assert not is_loan_statement("")
