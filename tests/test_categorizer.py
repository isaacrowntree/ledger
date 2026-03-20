"""Tests for the categorizer rules."""
from etl.models import RawTransaction


def _make_txn(description, source_type="ing"):
    return RawTransaction(
        date="2025-01-01", description=description,
        amount=-100.0, source_type=source_type,
    )


class TestBusinessCategories:
    """Business expenses should be categorized correctly."""

    def test_twilio_is_business(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("Twilio", source_type="paypal"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Business: Software & Subscriptions"

    def test_namecheap_is_business(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("Namecheap, Inc", source_type="paypal"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Business: Hosting & Infrastructure"

    def test_icdsoft_is_business(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("ICDSoft (Hong Kong) Limited", source_type="paypal"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Business: Hosting & Infrastructure"

    def test_google_gsuite_is_business(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(
            _make_txn("Visa Purchase - Receipt 196171 Google GSUITE_mydomain.com Sydney")
        )
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Business: Software & Subscriptions"


class TestPersonalCategories:
    def test_netflix_is_subscription(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("NETFLIX.COM", source_type="paypal"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Subscriptions"

    def test_woolworths_is_groceries(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("Visa Purchase - Receipt 199938 WOOLWORTHS SYDNEY"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Groceries"

    def test_salary_is_salary(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("Salary Deposit - Receipt 198956 Employer Pty Ltd"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Salary"

    def test_uber_australia_is_transport(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("Uber Australia Pty Ltd", source_type="paypal"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Public Transport"


class TestTransferCategories:
    def test_internal_transfer_is_transfer(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("Internal Transfer - Receipt 998547 To Orange Everyday"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Transfers"

    def test_bpay_is_transfer(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("BPAY Bill Payment - Receipt 012877 Someone"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Transfers"

    def test_paypal_general_deposit_is_transfer(self, conn, categorizer):
        cat_id, _ = categorizer.categorize(_make_txn("General card deposit", source_type="paypal"))
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        assert cat["name"] == "Transfers"
