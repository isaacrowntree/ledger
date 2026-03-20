"""Tests for ATO tax config and transaction integration."""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from etl import db
from etl.splitter import load_tax_config, backfill_splits

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


def _insert_txn(conn, date, desc, amount, category, source="test", dedup_suffix=""):
    account_id = db.ensure_account(conn, "Test", source)
    cat_id = db.ensure_category(conn, category, is_income=(amount > 0))
    dedup = f"{date}|{desc}|{amount}|{dedup_suffix}"
    h = hashlib.sha256(dedup.encode()).hexdigest()
    return db.insert_transaction(
        conn, account_id, date, desc, amount, None, None, 0,
        cat_id, 1.0, None, source, h, "test.csv", "{}"
    )


class TestTaxConfig:
    def test_config_loads(self, tax_config):
        assert tax_config["financial_year"] == 2025
        assert tax_config["taxpayer"]["name"] == "Test User"
        assert len(tax_config["businesses"]) >= 1
        assert tax_config["businesses"][0]["name"] == "Test Business"

    def test_rental_properties_configured(self, tax_config):
        props = tax_config["rental_properties"]
        assert len(props) >= 1
        assert props[0]["name"] == "TEST PROPERTY"
        assert props[0]["ownership_pct"] == 50

    def test_depreciation_schedules(self, tax_config):
        schedules = tax_config["depreciation_schedules"]
        assert len(schedules) >= 2
        rental_sched = next(s for s in schedules if s["type"] == "rental")
        assert rental_sched["property"] == "TEST PROPERTY"
        biz_sched = next(s for s in schedules if s["type"] == "business")
        assert biz_sched["business"] == "Test Business"


class TestWithTransactions:
    def test_salary_income_detected(self, conn):
        _insert_txn(conn, "2024-08-15", "SALARY DEPOSIT", 5000, "Salary")
        conn.commit()

        total = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) as total
            FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
            WHERE c.name = 'Salary' AND t.date >= '2024-07-01' AND t.date <= '2025-06-30'
        """).fetchone()["total"]
        assert total == 5000

    def test_business_splits_with_backfill(self, conn, tax_config):
        _insert_txn(conn, "2024-09-01", "NAMECHEAP", -120, "Business: Hosting & Infrastructure", dedup_suffix="1")
        _insert_txn(conn, "2024-10-01", "GITHUB", -8, "Business: Software & Subscriptions", dedup_suffix="2")
        conn.commit()

        count = backfill_splits(conn, tax_config, fy=2025)
        assert count == 2

        total = conn.execute("""
            SELECT COALESCE(SUM(business_amount), 0) as total
            FROM transaction_splits WHERE business_name = 'Test Business'
        """).fetchone()["total"]
        assert total == -128

    def test_utilities_partial_split(self, conn, tax_config):
        txn_id = _insert_txn(conn, "2024-08-01", "TPG INTERNET", -80, "Utilities")
        conn.execute("INSERT INTO transaction_tags (transaction_id, tag) VALUES (?, 'internet')", (txn_id,))
        conn.commit()

        count = backfill_splits(conn, tax_config, fy=2025)
        assert count == 1

        row = conn.execute(
            "SELECT * FROM transaction_splits WHERE transaction_id = ?", (txn_id,)
        ).fetchone()
        assert row["business_pct"] == 2
        assert row["business_amount"] == -1.6


class TestWorkTrips:
    def test_work_trip_crud(self, conn):
        conn.execute(
            "INSERT INTO work_trips (fy, name, start_date, end_date) VALUES (?, ?, ?, ?)",
            (2025, "Test Trip", "2024-11-01", "2024-11-10"),
        )
        conn.commit()

        trip = conn.execute("SELECT * FROM work_trips WHERE fy = 2025").fetchone()
        assert trip["name"] == "Test Trip"

        conn.execute(
            "INSERT INTO work_trip_expenses (trip_id, expense_type, amount, description) VALUES (?, ?, ?, ?)",
            (trip["id"], "flights", 1500, "Return flights"),
        )
        conn.commit()

        expenses = conn.execute(
            "SELECT * FROM work_trip_expenses WHERE trip_id = ?", (trip["id"],)
        ).fetchall()
        assert len(expenses) == 1
        assert expenses[0]["amount"] == 1500
