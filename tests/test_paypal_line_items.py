"""PayPal rows that are not money movements.

A PayPal export lists more than transactions. For a single payment it also
writes a "Shopping Cart Item" line carrying the SAME transaction ID and the
same amount with the opposite sign -- a line item, not a second payment. And an
authorisation is a hold placed before the payment settles, not a charge.

Treated as transactions these produce phantom rows that cancel or double real
spending, and because the cart line shares the payment's transaction ID it
cannot be told apart by identity either.

The Australian export spells it "Authorisation"; the skip list only had the US
"Authorization", so every hold was being imported.
"""
import csv
import tempfile
from pathlib import Path

from etl.parsers.paypal_csv import PayPalCSVParser

HEADERS = ["Date", "Time", "Time Zone", "Name", "Type", "Status", "Currency",
           "Gross", "Fee", "Net", "From Email Address", "To Email Address",
           "Transaction ID", "Reference Txn ID"]


def _csv(rows):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    w = csv.DictWriter(f, fieldnames=HEADERS)
    w.writeheader()
    for r in rows:
        w.writerow({h: r.get(h, "") for h in HEADERS})
    f.close()
    return Path(f.name)


def _row(type_, gross, txn_id, status="Completed", name="Uber Australia Pty Ltd"):
    return {"Date": "25/03/2026", "Time": "21:58:56", "Time Zone": "AEDT",
            "Name": name, "Type": type_, "Status": status, "Currency": "AUD",
            "Gross": gross, "Fee": "0.00", "Net": gross, "Transaction ID": txn_id}


class TestLineItemsAreNotTransactions:
    def test_shopping_cart_item_is_skipped(self):
        rows = _csv([
            _row("Pre-approved Payment Bill User Payment", "-24.60", "TXN1"),
            _row("Shopping Cart Item", "24.60", "TXN1"),
        ])
        txns = PayPalCSVParser().parse_statement(rows).rows
        assert len(txns) == 1
        assert txns[0].amount == -24.60

    def test_the_payment_survives_alone(self):
        rows = _csv([
            _row("Express Checkout Payment", "-50.00", "TXN2"),
            _row("Shopping Cart Item", "50.00", "TXN2"),
        ])
        assert [t.amount for t in PayPalCSVParser().parse_statement(rows).rows] == [-50.00]

    def test_australian_spelling_of_authorisation_is_skipped(self):
        """The export writes "Authorisation"; only "Authorization" was listed."""
        rows = _csv([_row("General Authorisation", "-24.60", "TXN3")])
        assert PayPalCSVParser().parse_statement(rows).rows == []

    def test_us_spelling_still_skipped(self):
        rows = _csv([_row("General Authorization", "-24.60", "TXN4")])
        assert PayPalCSVParser().parse_statement(rows).rows == []

    def test_an_authorisation_does_not_double_the_payment(self):
        """Both appear for one purchase; only the settled payment is spending."""
        rows = _csv([
            _row("General Authorisation", "-24.60", "TXN5"),
            _row("Shopping Cart Item", "24.60", "TXN5"),
            _row("Pre-approved Payment Bill User Payment", "-24.60", "TXN6"),
            _row("Shopping Cart Item", "24.60", "TXN6"),
        ])
        txns = PayPalCSVParser().parse_statement(rows).rows
        assert [t.amount for t in txns] == [-24.60]

    def test_a_real_refund_is_still_kept(self):
        """Skipping cart lines must not swallow genuine incoming money."""
        rows = _csv([_row("Payment Refund", "12.34", "TXN7")])
        assert [t.amount for t in PayPalCSVParser().parse_statement(rows).rows] == [12.34]
