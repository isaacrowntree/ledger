"""Tests for the source-of-truth system that prevents double-counting.

When a non-SOT account (e.g. ING) has a transaction that's a payment to a
source-of-truth account (PayPal, credit cards), it should be marked as a transfer.
The real purchase detail lives on the source-of-truth account's side.
"""
import re
from pathlib import Path

from etl.models import RawTransaction
from etl.normalizer import load_payment_patterns, is_payment_to_source_of_truth

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class TestPaymentPatternLoading:
    def test_loads_patterns_from_config(self):
        patterns, sot_types = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
        assert len(patterns) > 0
        assert all(isinstance(p, re.Pattern) for p in patterns)

    def test_loads_source_of_truth_types(self):
        _, sot_types = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
        assert "paypal" in sot_types
        assert "coles" in sot_types
        assert "bankwest" in sot_types
        assert "hsbc" in sot_types
        assert "ing" not in sot_types

    def test_includes_paypal(self):
        patterns, _ = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
        assert any(p.search("PAYPAL") for p in patterns)

    def test_includes_bankwest_credit(self):
        patterns, _ = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
        assert any(p.search("BANKWEST CREDIT") for p in patterns)

    def test_includes_hsbc(self):
        patterns, _ = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
        assert any(p.search("HSBC CARDS") for p in patterns)


class TestSourceOfTruthDetection:
    """ING transactions mentioning PayPal/credit cards → transfer."""

    def _make_txn(self, description, source_type="ing"):
        return RawTransaction(
            date="2025-01-01", description=description,
            amount=-100.0, source_type=source_type,
        )

    def _get_patterns(self):
        return load_payment_patterns(FIXTURE_DIR / "accounts.yaml")

    def test_ing_paypal_direct_debit_is_transfer(self):
        patterns, sot = self._get_patterns()
        txn = self._make_txn("Direct Debit - Receipt 101571 Paypal Australia")
        assert is_payment_to_source_of_truth(txn, patterns, sot)

    def test_ing_paypal_purchase_is_transfer(self):
        patterns, sot = self._get_patterns()
        txn = self._make_txn("EFTPOS Purchase - Receipt 062595 PAYPAL *SOMETHING")
        assert is_payment_to_source_of_truth(txn, patterns, sot)

    def test_ing_bpay_bankwest_is_transfer(self):
        patterns, sot = self._get_patterns()
        txn = self._make_txn("BPAY Bill Payment - Bankwest Credit Card 52298")
        assert is_payment_to_source_of_truth(txn, patterns, sot)

    def test_ing_bpay_hsbc_is_transfer(self):
        patterns, sot = self._get_patterns()
        txn = self._make_txn("BPAY Bill Payment - Hsbc Cards 4265571")
        assert is_payment_to_source_of_truth(txn, patterns, sot)

    def test_ing_woolworths_is_not_transfer(self):
        """Normal ING purchase should NOT be matched."""
        patterns, sot = self._get_patterns()
        txn = self._make_txn("Visa Purchase - WOOLWORTHS SYDNEY")
        assert not is_payment_to_source_of_truth(txn, patterns, sot)

    def test_ing_internal_transfer_is_not_matched(self):
        patterns, sot = self._get_patterns()
        txn = self._make_txn("Internal Transfer - To Orange Everyday")
        assert not is_payment_to_source_of_truth(txn, patterns, sot)

    def test_paypal_own_txn_is_not_suppressed(self):
        """PayPal's own transactions must NOT be marked as transfer."""
        patterns, sot = self._get_patterns()
        txn = self._make_txn("Twilio", source_type="paypal")
        assert not is_payment_to_source_of_truth(txn, patterns, sot)

    def test_coles_own_txn_is_not_suppressed(self):
        """Credit card's own transactions are never suppressed."""
        patterns, sot = self._get_patterns()
        txn = self._make_txn("WOOLWORTHS SYDNEY", source_type="coles")
        assert not is_payment_to_source_of_truth(txn, patterns, sot)
