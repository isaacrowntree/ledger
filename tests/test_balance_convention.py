"""Deciding which way a statement's balances point, from the balances themselves.

This must never be inferred from prose on the page. An ING everyday statement
mentions "Orange Advantage" in a product footer, and sniffing for that word
classified a transaction account as a mortgage and inverted every amount on it.

The balances are evidence and the prose is not: whichever convention the running
balance actually follows is the one the statement uses.
"""
from etl.contract import BalanceConvention as BC
from etl.parsers.ing_pdf import detect_convention, resign_unsigned_rows


class _Row:
    def __init__(self, amount, balance):
        self.amount = amount
        self.balance = balance


class TestDetectConvention:
    def test_everyday_account_is_signed(self):
        """Spend $10, balance falls $10."""
        rows = [_Row(-10.0, 90.0), _Row(-20.0, 70.0), _Row(5.0, 75.0)]
        assert detect_convention(rows, opening=100.0) is BC.SIGNED

    def test_loan_printing_unsigned_amounts_is_owing(self):
        """A repayment reduces the debt, so the printed balance falls while the
        amount is printed positive."""
        rows = [_Row(1143.88, 306501.93), _Row(1143.88, 305358.05)]
        assert detect_convention(rows, opening=407645.81) is BC.OWING

    def test_a_product_name_in_the_text_cannot_decide_it(self):
        """Regression: the balances win, whatever the page says."""
        rows = [_Row(-0.52, 99.48), _Row(-129.62, -30.14)]
        assert detect_convention(rows, opening=100.0) is BC.SIGNED

    def test_no_balances_leaves_it_signed(self):
        assert detect_convention([_Row(-10.0, None)], opening=None) is BC.SIGNED


class TestResignUnsignedRows:
    def test_interest_on_an_owing_statement_becomes_negative(self):
        """Interest increases the debt. Printed unsigned, it must be stored
        negative -- this is the mortgage bug."""
        rows = [_Row(1143.88, 305358.05), _Row(2058.36, 307416.41)]
        resign_unsigned_rows(rows, BC.OWING, opening=306501.93)
        assert rows[0].amount == 1143.88     # repayment already correct
        assert rows[1].amount == -2058.36    # interest corrected

    def test_signed_statement_is_left_alone(self):
        rows = [_Row(-10.0, 90.0), _Row(-20.0, 70.0)]
        resign_unsigned_rows(rows, BC.SIGNED, opening=100.0)
        assert [r.amount for r in rows] == [-10.0, -20.0]

    def test_only_the_sign_changes_never_the_magnitude(self):
        """The amount must keep coming from the printed figure. Deriving it from
        the balance would make validation tautological and hide dropped rows."""
        # A debt rising 305,358.05 -> 307,416.41 is interest of 2,058.36.
        rows = [_Row(2058.36, 307416.41)]
        resign_unsigned_rows(rows, BC.OWING, opening=305358.05)
        assert abs(rows[0].amount) == 2058.36
        assert rows[0].amount == -2058.36

    def test_a_row_whose_magnitude_disagrees_is_untouched(self):
        """If the printed amount does not match the balance movement, something
        is missing -- do not paper over it by rewriting the amount."""
        rows = [_Row(50.00, 307416.41)]
        resign_unsigned_rows(rows, BC.OWING, opening=305358.05)
        assert rows[0].amount == 50.00

    def test_missing_balance_is_skipped(self):
        rows = [_Row(10.0, None), _Row(20.0, 100.0)]
        resign_unsigned_rows(rows, BC.OWING, opening=None)
        assert rows[0].amount == 10.0
