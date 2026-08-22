"""Reading ING's balance summary block.

ING prints the summary as labels and values in SEPARATE blocks, in two layouts:

    Opening balance Total money in Total money out Closing balance
    $0.00 $0.00 $0.00 $0.00

and one label per line, followed by the values:

    Opening balance
    Total money in
    Total money out
    Closing balance
    $693.43
    $6,354.81
    ...

Pairing a label with the next number on the page would silently marry unrelated
figures, so the block is read positionally: the four values follow the four
labels in the order the labels appear.
"""
from etl.parsers.ing_pdf import summary_balances


class TestSummaryBalances:
    def test_single_line_labels(self):
        text = ("Balance\n"
                "Opening balance Total money in Total money out Closing balance\n"
                "$0.00 $0.00 $0.00 $0.00\n")
        assert summary_balances(text) == (0.00, 0.00)

    def test_dormant_account_is_all_zeroes(self):
        text = ("Opening balance Total money in Total money out Closing balance\n"
                "$0.00 $0.00 $0.00 $0.00\n"
                "There were no transactions on your Savings Maximiser account\n")
        assert summary_balances(text) == (0.00, 0.00)

    def test_label_per_line_layout(self):
        text = ("Balance\n"
                "Opening balance\n"
                "Total money in\n"
                "Total money out\n"
                "Closing balance\n"
                "$693.43\n"
                "$6,354.81\n"
                "$6,377.81\n"
                "$670.43\n")
        assert summary_balances(text) == (693.43, 670.43)

    def test_negative_balances(self):
        text = ("Opening balance Total money in Total money out Closing balance\n"
                "-$307,893.19 $2,180.34 $4,029.51 -$309,742.36\n")
        assert summary_balances(text) == (-307893.19, -309742.36)

    def test_thousands_separators(self):
        text = ("Opening balance Total money in Total money out Closing balance\n"
                "$1,443.96 $10,000.00 $9,000.00 $2,443.96\n")
        assert summary_balances(text) == (1443.96, 2443.96)

    def test_absent_block(self):
        assert summary_balances("no summary here") == (None, None)

    def test_incomplete_block_is_refused(self):
        """Three values where four are expected must not be guessed at."""
        text = ("Opening balance Total money in Total money out Closing balance\n"
                "$1.00 $2.00 $3.00\n")
        assert summary_balances(text) == (None, None)
