"""The categoriser: source rules, known merchants, then the nearest labelled merchants.

There are no description patterns. Merchants are labelled from the `merchants:`
list in the fixture config, from the existing ledger, or by a person, and the
vector index guesses merchants it has not seen. Uses the deterministic n-gram
embedder (see conftest), so no model is downloaded.
"""
import pytest

from etl import db
from etl.categorizer import CONFIDENCE, Categorizer
from etl.embedding import NgramEmbedder
from etl.merchant_store import MerchantStore
from etl.models import RawTransaction
from tests.conftest import FIXTURE_DIR


def _txn(description, source_type="ing", amount=-100.0):
    return RawTransaction(date="2025-01-01", description=description, amount=amount,
                          source_type=source_type)


def _name(conn, cat_id):
    return conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()["name"]


def _cat(conn, categorizer, description, **kw):
    cat_id, conf = categorizer.categorize(_txn(description, **kw))
    return _name(conn, cat_id), conf


class TestKnownMerchants:
    """A merchant from the config is recognised whatever noise its description carries."""

    @pytest.mark.parametrize("description, category", [
        ("Namecheap, Inc", "Business: Hosting & Infrastructure"),
        ("ICDSoft (Hong Kong) Limited", "Business: Hosting & Infrastructure"),
        ("Twilio", "Business: Software & Subscriptions"),
        ("Visa Purchase - Receipt 196171 Google GSUITE_example.com Sydney",
         "Business: Software & Subscriptions"),
        ("NETFLIX.COM", "Subscriptions"),
        ("Visa Purchase - Receipt 199938 WOOLWORTHS SYDNEY Date 03/02/25 Card 1234", "Groceries"),
        ("WOOLWORTHS      0101     EXAMPLETOWN  NS", "Groceries"),
        ("Uber Australia Pty Ltd", "Public Transport"),
        ("Internal Transfer - Receipt 998547 Transfer To 100000001", "Transfers"),
        ("BPAY Bill Payment - Receipt 012877", "Transfers"),
        ("General card deposit", "Transfers"),
    ])
    def test_config_merchant(self, conn, categorizer, description, category):
        name, conf = _cat(conn, categorizer, description)
        assert name == category
        assert conf == CONFIDENCE["seed"]

    def test_income_merchant_matches_money_in(self, conn, categorizer):
        name, _ = _cat(conn, categorizer, "Salary Deposit - Receipt 198956 Employer Pty Ltd", amount=2000.0)
        assert name == "Salary"


class TestSourceRules:
    def test_every_airbnb_row_is_airbnb_income(self, conn, categorizer):
        name, conf = _cat(conn, categorizer, "Anything at all", source_type="airbnb", amount=500.0)
        assert name == "Airbnb Income"
        assert conf == CONFIDENCE["source"]


class TestNearestMerchants:
    def test_a_close_variant_borrows_its_neighbours_category(self, conn, categorizer, monkeypatch):
        # Not in the config, but one character off a labelled merchant. The vote
        # share threshold is calibrated on a full ledger; this store has eighteen
        # merchants, so distant ones dilute the vote. The mechanism is what's tested.
        monkeypatch.setattr("etl.categorizer.NEAREST_MIN_SHARE", 0.5)
        name, conf = _cat(conn, categorizer, "WOOLWORTH")
        assert name == "Groceries"
        assert conf == CONFIDENCE["nearest"]

    def test_an_unfamiliar_merchant_stays_uncategorized(self, conn, categorizer):
        name, conf = _cat(conn, categorizer, "ZQXV HOLDINGS")
        assert name == "Uncategorized"
        assert conf == 0.0

    def test_unfamiliar_merchant_still_gets_ranked_suggestions(self, conn, categorizer):
        suggestions = categorizer.store.suggest("WOOLWRTHS METRO", -30.0)
        assert _name(conn, suggestions[0].category_id) == "Groceries"
        assert suggestions == sorted(suggestions, key=lambda s: -s.score)
        assert 0 < suggestions[0].score <= 1

    def test_neighbours_come_from_the_same_direction(self, conn, categorizer):
        # Money in never borrows the category of a merchant only seen as money out.
        store = categorizer.store
        cat = db.get_category_id(conn, "Coffee")
        store.learn_key("ONLY OUTGOING CAFE", "out", cat)
        neighbours = store.nearest("ONLY OUTGOING CAFE", 5.0)
        assert all(n.merchant != "ONLY OUTGOING CAFE" for n in neighbours)


    def test_settled_transactions_are_neighbours_but_guesses_are_not(self, conn, categorizer):
        acct = db.ensure_account(conn, "Test", "ing")
        for i, conf in enumerate((0.95, 0.6)):
            db.insert_transaction(
                conn, account_id=acct, date="2025-01-0%d" % (i + 1),
                description=f"EXAMPLE FLORIST {'TRUSTED' if conf > 0.9 else 'GUESSED'}",
                amount=-30.0, original_amount=None, original_currency=None, fee=0.0,
                category_id=db.get_category_id(conn, "Shopping" if conf > 0.9 else "Health"),
                category_confidence=conf, reference_id=None, source_type="ing",
                dedup_hash=f"florist{i}", source_file="t.csv", raw_data="{}", is_transfer=False)
        conn.commit()
        categorizer.store.sync_transactions()
        found = {n.example: _name(conn, n.category_id) for n in categorizer.store.nearest("EXAMPLE FLORIST", -30.0)}
        assert found.get("EXAMPLE FLORIST TRUSTED") == "Shopping"
        assert "EXAMPLE FLORIST GUESSED" not in found


class TestLearning:
    def test_a_correction_covers_later_visits(self, conn, categorizer):
        store = categorizer.store
        first = "Visa Purchase - Receipt 100001 EXAMPLE CAFE EXAMPLETOWN Date 01/02/25 Card 1234"
        later = "Visa Purchase - Receipt 200002 EXAMPLE CAFE EXAMPLETOWN Date 09/03/25 Card 1234"
        assert _cat(conn, categorizer, later)[0] == "Uncategorized"
        store.learn(first, -5.0, db.get_category_id(conn, "Coffee"))
        name, conf = _cat(conn, categorizer, later)
        assert (name, conf) == ("Coffee", CONFIDENCE["human"])

    def test_a_person_overrides_the_config(self, conn, categorizer):
        categorizer.store.learn("NETFLIX.COM", -20.0, db.get_category_id(conn, "Entertainment"))
        assert _cat(conn, categorizer, "NETFLIX.COM")[0] == "Entertainment"

    def test_reloading_the_config_keeps_a_persons_label(self, conn, categorizer):
        categorizer.store.learn("NETFLIX.COM", -20.0, db.get_category_id(conn, "Entertainment"))
        reloaded = Categorizer(conn, FIXTURE_DIR / "categories.yaml", embedder=NgramEmbedder())
        assert _cat(conn, reloaded, "NETFLIX.COM")[0] == "Entertainment"

    def test_bootstrap_never_overrides_a_person(self, conn, categorizer):
        store = categorizer.store
        store.learn_key("SOME SHOP", "out", db.get_category_id(conn, "Coffee"))
        store.learn_key("SOME SHOP", "out", db.get_category_id(conn, "Shopping"), origin="bootstrap")
        assert _cat(conn, categorizer, "SOME SHOP")[0] == "Coffee"

    def test_forget_drops_a_one_off(self, conn, categorizer):
        store = categorizer.store
        store.learn("ONE OFF STORE", -50.0, db.get_category_id(conn, "Business: Equipment"))
        assert store.forget("ONE OFF STORE", -50.0)
        assert store.lookup("ONE OFF STORE", -50.0) is None
        assert all(n.merchant != "ONE OFF STORE" for n in store.nearest("ONE OFF STORE", -50.0))

    def test_reloading_reseeds_nothing_unchanged(self, conn, categorizer):
        before = conn.execute("SELECT SUM(times_seen) FROM merchant_labels").fetchone()[0]
        Categorizer(conn, FIXTURE_DIR / "categories.yaml", embedder=NgramEmbedder())
        after = conn.execute("SELECT SUM(times_seen) FROM merchant_labels").fetchone()[0]
        assert before == after


class TestSeedFromLedger:
    def _insert(self, conn, description, amount, category, confidence=0.8, n=1, date="2024-06-01"):
        acct = db.ensure_account(conn, "Test", "ing")
        for i in range(n):
            db.insert_transaction(
                conn, account_id=acct, date=date, description=f"{description} Receipt {900000 + i}",
                amount=amount, original_amount=None, original_currency=None, fee=0.0,
                category_id=db.get_category_id(conn, category), category_confidence=confidence,
                reference_id=None, source_type="ing", dedup_hash=f"{description}{amount}{category}{i}{date}",
                source_file="t.csv", raw_data="{}", is_transfer=False)

    def test_majority_wins_and_confirmed_rows_outvote(self, conn, categorizer):
        self._insert(conn, "CORNER STORE", -10.0, "Groceries", n=3)
        self._insert(conn, "CORNER STORE", -10.0, "Shopping", confidence=1.0, n=1)
        categorizer.store.seed_from_transactions()
        assert _cat(conn, categorizer, "CORNER STORE")[0] == "Shopping"

    def test_before_limits_what_is_learned(self, conn, categorizer):
        self._insert(conn, "LATE SHOP", -10.0, "Coffee", date="2025-06-01")
        categorizer.store.seed_from_transactions(before="2025-01-01")
        assert categorizer.store.lookup("LATE SHOP", -10.0) is None

    def test_existing_labels_are_kept(self, conn, categorizer):
        self._insert(conn, "NETFLIX.COM", -20.0, "Entertainment", n=5)
        categorizer.store.seed_from_transactions()
        assert _cat(conn, categorizer, "NETFLIX.COM")[0] == "Subscriptions"


class TestIndexLifecycle:
    def test_changing_embedder_rebuilds_the_index(self, conn, categorizer):
        labels = conn.execute("SELECT COUNT(*) FROM merchant_labels").fetchone()[0]
        store = MerchantStore(conn, NgramEmbedder(dim=256))
        assert store.index.count("merchant") == labels
        assert store.suggest("WOOLWORTH", -10.0)

    def test_old_exact_description_rules_are_carried_over(self, conn):
        conn.execute("CREATE TABLE category_rules_learned (description_pattern TEXT PRIMARY KEY, "
                     "category_id INTEGER, times_seen INTEGER DEFAULT 1)")
        conn.execute("INSERT INTO category_rules_learned VALUES (?, ?, 1)",
                     ("VISA PURCHASE - RECEIPT 123456 OLD FAVOURITE CAFE", db.get_category_id(conn, "Coffee")))
        store = MerchantStore(conn, NgramEmbedder())
        assert store.lookup("Visa Purchase - Receipt 999 OLD FAVOURITE CAFE", -4.0)[1] == "human"
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'category_rules_learned'").fetchone()

    def test_unknown_category_in_config_is_refused(self, conn, tmp_path):
        bad = tmp_path / "categories.yaml"
        bad.write_text("categories: []\nmerchants:\n  SOMEWHERE: Nonexistent\n")
        with pytest.raises(ValueError, match="Nonexistent"):
            Categorizer(conn, bad, embedder=NgramEmbedder())


class TestMixedMerchants:
    def test_mixed_merchant_rows_always_file_for_review(self, conn, categorizer):
        store = categorizer.store
        store.set_mixed("EXAMPLE MARKETPLACE", "out", db.get_category_id(conn, "Shopping"), "mixed")
        name, conf = _cat(conn, categorizer, "EXAMPLE MARKETPLACE")
        assert (name, conf) == ("Shopping", CONFIDENCE["mixed"])

    def test_filing_one_row_never_relabels_a_mixed_merchant(self, conn, categorizer):
        store = categorizer.store
        store.set_mixed("EXAMPLE MARKETPLACE", "out", db.get_category_id(conn, "Shopping"), "mixed")
        store.learn("EXAMPLE MARKETPLACE", -30.0, db.get_category_id(conn, "Business: Equipment"), origin="llm")
        assert _cat(conn, categorizer, "EXAMPLE MARKETPLACE")[0] == "Shopping"

    def test_mixed_merchants_are_not_flagged_for_the_owner(self, conn, categorizer):
        store = categorizer.store
        store.set_mixed("EXAMPLE MARKETPLACE", "out", db.get_category_id(conn, "Shopping"), "mixed")
        store.flag("EXAMPLE MARKETPLACE", "out", None, "unsure")
        assert conn.execute("SELECT needs_review FROM merchant_labels WHERE merchant = 'EXAMPLE MARKETPLACE'").fetchone()[0] == 0
