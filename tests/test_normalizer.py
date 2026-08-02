"""Tests for the normalizer: dedup, transfer marking, end-to-end insert."""
from pathlib import Path

from etl import db
from etl.categorizer import Categorizer
from etl.models import RawTransaction
from etl.normalizer import (
    compute_dedup_hash,
    load_payment_patterns,
    normalize_and_insert,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _make_txn(description, amount=-50.0, source_type="ing", source_file="test.pdf", reference_id=None):
    return RawTransaction(
        date="2025-06-15", description=description, amount=amount,
        source_type=source_type, source_file=source_file,
        reference_id=reference_id,
    )


class TestDedupHash:
    def test_ing_includes_source_file(self):
        txn_a = _make_txn("Transfer to savings", source_file="account_a_2025.pdf")
        txn_b = _make_txn("Transfer to savings", source_file="account_b_2025.pdf")
        assert compute_dedup_hash(txn_a) != compute_dedup_hash(txn_b)

    def test_paypal_uses_reference_id(self):
        txn = _make_txn("Twilio", source_type="paypal", reference_id="TXN123")
        assert compute_dedup_hash(txn) == compute_dedup_hash(txn)
        txn2 = _make_txn("Twilio", source_type="paypal", reference_id="TXN456")
        assert compute_dedup_hash(txn) != compute_dedup_hash(txn2)

    def test_same_txn_same_hash(self):
        txn = _make_txn("WOOLWORTHS", source_file="account_a_2025.pdf")
        assert compute_dedup_hash(txn) == compute_dedup_hash(txn)

    def test_first_occurrence_hash_is_unchanged(self):
        """Occurrence 0 must hash as before, so existing imports still match."""
        txn = _make_txn("WOOLWORTHS", source_file="account_a_2025.pdf")
        assert compute_dedup_hash(txn, 0) == compute_dedup_hash(txn)

    def test_repeat_occurrence_gets_its_own_hash(self):
        txn = _make_txn("RIVERSIDE BOWLING CLUB", amount=-8.0, source_type="cba-cc")
        assert compute_dedup_hash(txn, 1) != compute_dedup_hash(txn, 0)


class TestRepeatedTransactions:
    def test_identical_same_day_transactions_both_insert(self, conn, categorizer, payment_patterns):
        """Two rounds at the same bar bill identically but are not duplicates."""
        txns = [
            _make_txn("RIVERSIDE BOWLING CLUB", amount=-8.0, source_type="cba-cc"),
            _make_txn("RIVERSIDE BOWLING CLUB", amount=-8.0, source_type="cba-cc"),
        ]
        inserted, skipped = normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        assert (inserted, skipped) == (2, 0)

    def test_reingesting_the_same_statement_still_skips(self, conn, categorizer, payment_patterns):
        """Occurrence is positional, so a re-import matches rather than doubling."""
        txns = [
            _make_txn("RIVERSIDE BOWLING CLUB", amount=-8.0, source_type="cba-cc"),
            _make_txn("RIVERSIDE BOWLING CLUB", amount=-8.0, source_type="cba-cc"),
        ]
        normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        inserted, skipped = normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        assert (inserted, skipped) == (0, 2)


class TestIdempotency:
    def test_duplicate_insert_is_skipped(self, conn, categorizer, payment_patterns):
        txns = [_make_txn("WOOLWORTHS SYDNEY", reference_id=None)]
        inserted, skipped = normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        assert inserted == 1 and skipped == 0

        inserted, skipped = normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        assert inserted == 0 and skipped == 1

    def test_different_accounts_same_description_not_deduped(self, conn, categorizer, payment_patterns):
        txn_a = [_make_txn("Transfer to savings", source_file="account_a_2025.pdf")]
        txn_b = [_make_txn("Transfer to savings", source_file="account_b_2025.pdf")]
        inserted_a, _ = normalize_and_insert(conn, txn_a, 1, categorizer, payment_patterns)
        inserted_b, _ = normalize_and_insert(conn, txn_b, 2, categorizer, payment_patterns)
        assert inserted_a == 1 and inserted_b == 1


class TestSourceOfTruthIntegration:
    """End-to-end: ING PayPal transaction becomes transfer, PayPal transaction gets categorized."""

    def test_ing_paypal_twilio_marked_as_transfer(self, conn, categorizer, payment_patterns):
        txns = [_make_txn("Intl Atmpurchase - Receipt 127425 PAYPAL *TWILIO 4029357733")]
        inserted, _ = normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        assert inserted == 1

        row = conn.execute("""
            SELECT t.is_transfer, c.name as cat
            FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.description LIKE '%TWILIO%'
        """).fetchone()
        assert row["is_transfer"] == 1
        assert row["cat"] == "Transfers"

    def test_paypal_twilio_categorized_as_business(self, conn, categorizer, payment_patterns):
        txns = [_make_txn("Twilio", source_type="paypal", reference_id="TXN_TWILIO_001")]
        inserted, _ = normalize_and_insert(conn, txns, 1, categorizer, payment_patterns)
        assert inserted == 1

        row = conn.execute("""
            SELECT t.is_transfer, c.name as cat
            FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.description = 'Twilio'
        """).fetchone()
        assert row["is_transfer"] == 0
        assert row["cat"] == "Business: Software & Subscriptions"

    def test_no_double_counting(self, conn, categorizer, payment_patterns):
        ing_txns = [_make_txn("Intl Atmpurchase - PAYPAL *TWILIO", amount=-17.46)]
        pp_txns = [_make_txn("Twilio", amount=-17.46, source_type="paypal", reference_id="TXN_T1")]

        normalize_and_insert(conn, ing_txns, 1, categorizer, payment_patterns)
        normalize_and_insert(conn, pp_txns, 2, categorizer, payment_patterns)

        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM transactions
            WHERE description LIKE '%Twilio%' OR description LIKE '%TWILIO%'
        """).fetchone()
        assert row["cnt"] == 2

        non_transfer = conn.execute("""
            SELECT COUNT(*) as cnt, SUM(amount) as total FROM transactions
            WHERE (description LIKE '%Twilio%' OR description LIKE '%TWILIO%')
              AND is_transfer = 0
        """).fetchone()
        assert non_transfer["cnt"] == 1
        assert abs(non_transfer["total"] - (-17.46)) < 0.01
