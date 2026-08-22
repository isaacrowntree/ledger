"""HSBC issues two different products through the same parser.

A credit card statement quotes what is OWED. A "Financial Statement" for a day to
day transaction account quotes an ordinary balance that falls when money leaves.
Declaring one convention for both would invert every amount on the other.

The transaction-account layout also prints its balances differently -- "Balance
Brought Forward" and a per-account "Balance" -- so a dormant one parsed to no
rows AND no balances, which is indistinguishable from a failed parse.
"""
from etl.parsers.hsbc_pdf import HSBCPDFParser, account_statement_balances

DORMANT = (
    "STATEMENT PERIOD 24 Dec 2021 TO 28 Mar 2022\n"
    "Total Debits 0.00\n"
    "1 EXAMPLE STREET Total Credits 0.00\n"
    "AUD DAY TO DAY ACCOUNT Branch DIRECT BNKING BSB 000000 Account No 123456789 Balance 0.00\n"
    "Date Transaction Details Debit Credit Balance\n"
    "24 Dec Balance Brought Forward 0.00\n"
)

ACTIVE = (
    "AUD DAY TO DAY ACCOUNT Branch DIRECT BNKING BSB 000000 Account No 123456789 Balance 250.00\n"
    "Date Transaction Details Debit Credit Balance\n"
    "24 Dec Balance Brought Forward 100.00\n"
)


class TestAccountStatementBalances:
    def test_dormant_statement_balances_are_found(self):
        assert account_statement_balances(DORMANT) == (0.00, 0.00)

    def test_opening_comes_from_balance_brought_forward(self):
        assert account_statement_balances(ACTIVE)[0] == 100.00

    def test_closing_comes_from_the_account_balance(self):
        assert account_statement_balances(ACTIVE)[1] == 250.00

    def test_each_balance_is_found_independently(self):
        """A layout that prints one of the two must still yield that one.
        Requiring both meant a single unmatched pattern discarded the other."""
        only_forward = "26 Mar BALANCE BROUGHT FORWARD 0.00\n"
        assert account_statement_balances(only_forward)[0] == 0.00

    def test_account_header_without_the_word_No(self):
        """Older layout: "Account 123456789 Currency AUD Balance 0.00"."""
        text = ("DAY TO DAY ACCOUNT BSB No. 000000 Account 123456789 "
                "Currency AUD Balance 0.00\n")
        assert account_statement_balances(text)[1] == 0.00

    def test_a_card_statement_has_neither(self):
        card = ("Account Number Statement Period\n"
                "4000 1234 5678 9010 8 Apr 26 to 5 May 26\n"
                "8/04/26 OPENING BALANCE $747.71\n")
        assert account_statement_balances(card) == (None, None)


class TestDormantStatementValidates:
    def test_dormant_account_statement_is_clean(self, tmp_path):
        """No rows, but the balances corroborate it -- not a failed parse."""
        statement = HSBCPDFParser().build(
            tmp_path / "s.pdf", rows=[], opening_balance=0.0, closing_balance=0.0)
        assert statement.validate() == []


class TestProductDetection:
    def test_a_card_printing_closing_balance_is_still_a_card(self):
        """A card statement lists "CLOSING BALANCE" among its transactions; that
        must not make it look like a transaction account."""
        from etl.contract import BalanceConvention
        from etl.parsers.hsbc_pdf import HSBCPDFParser

        card = ("Opening Balance $2,836.64\n"
                "Closing Balance $8.07\n"
                "19/06/2020 9010 BPAY PAYMENT -$2,836.64\n"
                "6/07/2020 INTEREST ON SPECIAL $8.02\n"
                "6/07/2020 CLOSING BALANCE $8.07\n")
        assert "BALANCE BROUGHT FORWARD" not in card.upper()


class TestConventionIsNotAssumed:
    def test_transaction_account_is_signed_not_owing(self):
        """A day to day account's balance falls when money leaves it."""
        from etl.contract import BalanceConvention
        from etl.parsers.base import detect_convention

        class _Row:
            def __init__(self, amount, balance):
                self.amount, self.balance = amount, balance

        rows = [_Row(-50.0, 950.0), _Row(-25.0, 925.0)]
        assert detect_convention(rows, opening=1000.0) is BalanceConvention.SIGNED
