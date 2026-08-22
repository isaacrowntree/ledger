"""Tests for reconciling ledger transactions against statement balances."""
import json

import pytest

from etl import db
from etl.reconcile import reconcile_account


def _add(conn, account_id, date, description, amount, source_file,
         closing_balance=None, balance=None, idx=[0]):
    idx[0] += 1
    raw = {"date": date, "description": description}
    if closing_balance is not None:
        raw["closing_balance"] = closing_balance
    if balance is not None:
        raw["balance"] = balance
    txn_id = db.insert_transaction(
        conn, account_id=account_id, date=date, description=description, amount=amount,
        original_amount=None, original_currency=None, fee=0.0,
        category_id=None, category_confidence=None, reference_id=None,
        source_type="test", dedup_hash=f"hash-{idx[0]}", source_file=source_file,
        raw_data=json.dumps(raw),
    )
    return txn_id  # insert_transaction writes the raw_imports row itself


@pytest.fixture
def card(conn):
    return db.ensure_account(conn, "Test Card", "test", account_type="credit")


@pytest.fixture
def savings(conn):
    return db.ensure_account(conn, "Test Savings", "test", account_type="savings")


class TestAnchorReconciliation:
    def test_complete_statements_reconcile(self, conn, card):
        """Owing rises by exactly what was spent -> no drift."""
        _add(conn, card, "2026-01-10", "COFFEE", -100.00, "jan.pdf", closing_balance="100.00")
        _add(conn, card, "2026-02-10", "GROCERIES", -50.00, "feb.pdf", closing_balance="150.00")
        report = reconcile_account(conn, card, "Test Card", "credit")
        assert report.anchor_drifts == []

    def test_missing_transaction_detected(self, conn, card):
        """Statement says $150 was spent but only $50 of it is in the ledger."""
        _add(conn, card, "2026-01-10", "COFFEE", -100.00, "jan.pdf", closing_balance="100.00")
        _add(conn, card, "2026-02-10", "GROCERIES", -50.00, "feb.pdf", closing_balance="250.00")
        report = reconcile_account(conn, card, "Test Card", "credit")
        assert len(report.anchor_drifts) == 1
        assert report.anchor_drifts[0].drift == 100.00

    def test_double_counted_transaction_detected(self, conn, card):
        _add(conn, card, "2026-01-10", "COFFEE", -100.00, "jan.pdf", closing_balance="100.00")
        _add(conn, card, "2026-02-10", "GROCERIES", -50.00, "feb.pdf", closing_balance="150.00")
        _add(conn, card, "2026-02-11", "GROCERIES AGAIN", -50.00, "feb.pdf")
        report = reconcile_account(conn, card, "Test Card", "credit")
        assert report.anchor_drifts[0].drift == -50.00

    def test_asset_account_balance_moves_with_amount(self, conn, savings):
        """For a savings account the printed balance follows the amount sign."""
        _add(conn, savings, "2026-01-10", "OPENING", 500.00, "jan.pdf", closing_balance="500.00")
        _add(conn, savings, "2026-02-10", "DEPOSIT", 250.00, "feb.pdf", closing_balance="750.00")
        report = reconcile_account(conn, savings, "Test Savings", "savings")
        assert report.anchor_drifts == []

    def test_attribution_is_by_file_not_date(self, conn, card):
        """A row dated before a statement's end can still belong to the next
        statement -- the bank posts with a lag. Attribution must follow the file."""
        _add(conn, card, "2026-01-10", "COFFEE", -100.00, "jan.pdf", closing_balance="100.00")
        # Dated inside January's range but printed on February's statement.
        _add(conn, card, "2026-01-28", "LATE POST", -40.00, "feb.pdf", closing_balance="140.00")
        report = reconcile_account(conn, card, "Test Card", "credit")
        assert report.anchor_drifts == []

    def test_unanchored_tail_reported(self, conn, card):
        _add(conn, card, "2026-01-10", "COFFEE", -100.00, "jan.pdf", closing_balance="100.00")
        _add(conn, card, "2026-02-10", "GROCERIES", -50.00, "feb.pdf", closing_balance="150.00")
        _add(conn, card, "2026-02-20", "INTERIM", -10.00, "interim.csv")
        report = reconcile_account(conn, card, "Test Card", "credit")
        assert report.unanchored_from == "2026-02-10"

    def test_file_without_any_balance_is_not_an_anchor(self, conn, card):
        """CSV exports print no balance; they must not be treated as anchors, but
        their transactions still count toward the surrounding statements."""
        _add(conn, card, "2026-01-10", "COFFEE", -100.00, "jan.pdf", closing_balance="100.00")
        _add(conn, card, "2026-01-20", "CSV ROW", -25.00, "export.csv")
        _add(conn, card, "2026-02-10", "GROCERIES", -50.00, "feb.pdf", closing_balance="175.00")
        report = reconcile_account(conn, card, "Test Card", "credit")
        assert len(report.anchors) == 2
        assert report.anchor_drifts == []


class TestRunningChain:
    def test_intact_chain_has_no_breaks(self, conn, savings):
        _add(conn, savings, "2026-01-01", "A", -10.00, "s.pdf", balance="90.00")
        _add(conn, savings, "2026-01-02", "B", -20.00, "s.pdf", balance="70.00")
        _add(conn, savings, "2026-01-03", "C", 5.00, "s.pdf", balance="75.00")
        report = reconcile_account(conn, savings, "Test Savings", "savings")
        assert report.chain_breaks == []

    def test_break_localised_to_the_transaction(self, conn, savings):
        _add(conn, savings, "2026-01-01", "A", -10.00, "s.pdf", balance="90.00")
        _add(conn, savings, "2026-01-02", "B", -20.00, "s.pdf", balance="55.00")  # 15 unaccounted
        report = reconcile_account(conn, savings, "Test Savings", "savings")
        assert len(report.chain_breaks) == 1
        assert report.chain_breaks[0].description == "B"
        assert report.chain_breaks[0].drift == -15.00

    def test_inverted_sign_shows_as_double_the_amount(self, conn, savings):
        """A transaction stored with the wrong sign is off by exactly 2x itself --
        the signature seen on ING loan 'Interest Charge' rows."""
        _add(conn, savings, "2026-01-01", "A", -10.00, "s.pdf", balance="90.00")
        _add(conn, savings, "2026-01-31", "Interest Charge", 100.00, "s.pdf", balance="-10.00")
        report = reconcile_account(conn, savings, "Test Savings", "savings")
        assert report.chain_breaks[0].drift == -200.00

    def test_chain_not_checked_across_files(self, conn, savings):
        """Two statements' balances are continuous, but a gap between files is the
        anchor check's job -- the chain check must not report a false break."""
        _add(conn, savings, "2026-01-01", "A", -10.00, "one.pdf", balance="90.00")
        _add(conn, savings, "2026-02-01", "B", -20.00, "two.pdf", balance="50.00")
        report = reconcile_account(conn, savings, "Test Savings", "savings")
        assert report.chain_breaks == []
