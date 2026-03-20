"""Tests for the business split engine."""
import sqlite3
from pathlib import Path

import pytest

from etl import db
from etl.splitter import compute_splits, write_splits, backfill_splits, load_tax_config

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def tax_config():
    return load_tax_config(FIXTURE_DIR / "tax.yaml")


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    db.init_db(connection)
    db.load_categories_from_config(connection, FIXTURE_DIR / "categories.yaml")
    db.load_accounts_from_config(connection, FIXTURE_DIR / "accounts.yaml")
    yield connection
    connection.close()


class TestComputeSplits:
    def test_business_category_gets_100_pct(self, tax_config):
        splits = compute_splits(None, 1, "Business: Hosting & Infrastructure", [], tax_config)
        assert len(splits) == 1
        assert splits[0]["business_name"] == "Test Business"
        assert splits[0]["business_pct"] == 100.0

    def test_business_software_gets_100_pct(self, tax_config):
        splits = compute_splits(None, 1, "Business: Software & Subscriptions", [], tax_config)
        assert len(splits) == 1
        assert splits[0]["business_pct"] == 100.0

    def test_utilities_with_internet_tag_gets_split(self, tax_config):
        splits = compute_splits(None, 1, "Utilities", ["internet"], tax_config)
        assert len(splits) == 1
        assert splits[0]["business_pct"] == 2

    def test_utilities_with_mobile_tag_gets_split(self, tax_config):
        splits = compute_splits(None, 1, "Utilities", ["mobile"], tax_config)
        assert len(splits) == 1
        assert splits[0]["business_pct"] == 2

    def test_utilities_without_tag_no_split(self, tax_config):
        splits = compute_splits(None, 1, "Utilities", [], tax_config)
        assert len(splits) == 0

    def test_personal_category_no_split(self, tax_config):
        splits = compute_splits(None, 1, "Groceries", [], tax_config)
        assert len(splits) == 0

    def test_none_category_no_split(self, tax_config):
        splits = compute_splits(None, 1, None, [], tax_config)
        assert len(splits) == 0


class TestWriteSplits:
    def test_writes_split_to_db(self, conn, tax_config):
        account_id = db.ensure_account(conn, "Test", "test")
        cat_id = db.ensure_category(conn, "Business: Hosting & Infrastructure")
        txn_id = db.insert_transaction(
            conn, account_id, "2024-08-01", "NAMECHEAP", -50.0, None, None, 0,
            cat_id, 1.0, None, "test", "hash1", "test.csv", "{}"
        )
        conn.commit()

        splits = [{"business_name": "Test Business", "business_pct": 100.0}]
        write_splits(conn, txn_id, -50.0, splits)
        conn.commit()

        row = conn.execute(
            "SELECT * FROM transaction_splits WHERE transaction_id = ?", (txn_id,)
        ).fetchone()
        assert row is not None
        assert row["business_name"] == "Test Business"
        assert row["business_pct"] == 100.0
        assert row["business_amount"] == -50.0


class TestBackfillSplits:
    def test_backfill_creates_splits(self, conn, tax_config):
        account_id = db.ensure_account(conn, "Test", "test")
        cat_id = db.ensure_category(conn, "Business: Software & Subscriptions")

        db.insert_transaction(
            conn, account_id, "2024-10-15", "GITHUB", -10.0, None, None, 0,
            cat_id, 1.0, None, "test", "hash_bf1", "test.csv", "{}"
        )
        conn.commit()

        count = backfill_splits(conn, tax_config, fy=2025)
        assert count >= 1

        rows = conn.execute("SELECT * FROM transaction_splits").fetchall()
        assert len(rows) >= 1
        assert rows[0]["business_name"] == "Test Business"
