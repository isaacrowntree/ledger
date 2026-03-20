"""Tests for the tag system."""
from pathlib import Path

from etl.models import RawTransaction
from etl.tagger import Tagger

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _make_txn(description, source_type="ing"):
    return RawTransaction(
        date="2025-01-01", description=description,
        amount=-100.0, source_type=source_type,
    )


class TestTagger:
    def setup_method(self):
        self.tagger = Tagger(FIXTURE_DIR / "categories.yaml")

    # --- Transport ---
    def test_jetstar_tagged_as_flight(self):
        tags = self.tagger.get_tags(_make_txn("Jetstar Airways", source_type="paypal"))
        assert "flight" in tags

    def test_uber_tagged_as_taxi(self):
        tags = self.tagger.get_tags(_make_txn("Uber Australia Pty Ltd", source_type="paypal"))
        assert "taxi" in tags

    def test_opal_tagged_as_train(self):
        tags = self.tagger.get_tags(_make_txn("TRANSPORTFORNSW TAP SYDNEY"))
        assert "train" in tags

    # --- Insurance ---
    def test_medibank_tagged_health_insurance(self):
        tags = self.tagger.get_tags(_make_txn("Direct Debit - Medibank Private"))
        assert "health-insurance" in tags

    # --- Business ---
    def test_twilio_has_biz_software(self):
        tags = self.tagger.get_tags(_make_txn("Twilio", source_type="paypal"))
        assert "biz-software" in tags

    def test_namecheap_has_biz_hosting(self):
        tags = self.tagger.get_tags(_make_txn("Namecheap, Inc", source_type="paypal"))
        assert "biz-hosting" in tags

    # --- Multi-tag ---
    def test_multiple_tags_possible(self):
        # Namecheap matches both biz-hosting and ato-deductible
        tags = self.tagger.get_tags(_make_txn("Namecheap, Inc", source_type="paypal"))
        assert len(tags) >= 2

    def test_no_tags_for_woolworths(self):
        tags = self.tagger.get_tags(_make_txn("WOOLWORTHS SYDNEY"))
        assert "flight" not in tags
        assert "health-insurance" not in tags

    # --- Income ---
    def test_salary_tagged(self):
        tags = self.tagger.get_tags(_make_txn("Salary Deposit - Receipt 198956"))
        assert "salary" in tags

    def test_interest_tagged(self):
        tags = self.tagger.get_tags(_make_txn("Interest Credit - Receipt 985525"))
        assert "interest" in tags

    # --- Food ---
    def test_uber_eats_tagged_delivery(self):
        tags = self.tagger.get_tags(_make_txn("PAYPAL *UBEREATS AU Sydney"))
        assert "delivery" in tags

    # --- Utility ---
    def test_internet_tagged(self):
        tags = self.tagger.get_tags(_make_txn("TPG INTERNET"))
        assert "internet" in tags

    def test_mobile_tagged(self):
        tags = self.tagger.get_tags(_make_txn("VODAFONE AUSTRALIA"))
        assert "mobile" in tags
