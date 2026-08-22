"""Reading a labelled balance off a statement header.

Two traps, both of which produced a discrepancy of exactly twice the balance --
the signature of a sign error:

* A "CR" suffix means the account is in CREDIT. On a card statement the balance
  is what is OWED, so "Opening balance $1.64 CR" is -1.64 owing, not +1.64.
* Labels and values sometimes sit in separate blocks on the page, so a match that
  spans a newline pairs a label with an unrelated number.
"""
from etl.parsers.base import labelled_balance


class TestLabelledBalance:
    def test_plain_balance(self):
        assert labelled_balance("Closing balance $1,587.91", "Closing balance") == 1587.91

    def test_cr_suffix_means_in_credit(self):
        assert labelled_balance("Opening balance $1.64 CR", "Opening balance") == -1.64

    def test_cr_is_case_insensitive_and_tolerates_spacing(self):
        assert labelled_balance("Opening balance $1.64cr", "Opening balance") == -1.64

    def test_dr_suffix_is_a_debit(self):
        assert labelled_balance("Opening balance $50.00 DR", "Opening balance") == 50.00

    def test_explicit_minus_is_honoured(self):
        assert labelled_balance("Opening balance -$307,416.41", "Opening balance") == -307416.41

    def test_label_and_value_on_different_lines_do_not_pair(self):
        """ING's quarterly layout lists all labels, then all values."""
        text = "Opening balance\nTotal money in\nClosing balance\n$693.43\n$6,354.81"
        assert labelled_balance(text, "Opening balance") is None

    def test_takes_the_labelled_value_not_a_nearby_one(self):
        text = "Credit limit $8,000.00\nOpening balance $1.64 CR\nMinimum payment $31.75"
        assert labelled_balance(text, "Opening balance") == -1.64

    def test_absent_label(self):
        assert labelled_balance("nothing here", "Opening balance") is None

    def test_case_insensitive_label(self):
        assert labelled_balance("OPENING BALANCE $10.00", "Opening balance") == 10.00
