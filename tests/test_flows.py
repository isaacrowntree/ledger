"""One definition of income and spending (etl/flows.py), and business income from splits."""
import sqlite3
from pathlib import Path

import pytest
import yaml

from etl import db
from etl.flows import default_kind
from etl.splitter import backfill_splits, compute_splits

FIXTURES = Path(__file__).parent / "fixtures"


def _add(c, date, amount, desc, cat, n=[0]):
    n[0] += 1
    return db.insert_transaction(
        c, account_id=db.ensure_account(c, "Everyday", "ing"), date=date, description=desc, amount=amount,
        original_amount=None, original_currency=None, fee=0.0, category_id=db.get_category_id(c, cat),
        category_confidence=1.0, reference_id=None, source_type="ing", dedup_hash=f"fl{n[0]}",
        source_file="t.csv", raw_data="{}")


def test_default_kinds():
    assert default_kind("Salary", True) == "income"
    assert default_kind("Transfers", False) == "neutral"
    assert default_kind("Personal (non-assessable)", False) == "neutral"
    assert default_kind("Employer Reimbursements", False) == "neutral"
    assert default_kind("Groceries", False) == "expense"


def test_an_old_database_gets_kinds_from_is_income(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        parent_id INTEGER, is_income INTEGER NOT NULL DEFAULT 0, budget_monthly REAL);
        INSERT INTO categories (name, is_income) VALUES ('Salary', 1), ('Transfers', 0), ('Groceries', 0);""")
    old.commit(); old.close()
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    db.init_db(c)
    assert dict(c.execute("SELECT name, kind FROM categories").fetchall()) == {
        "Salary": "income", "Transfers": "neutral", "Groceries": "expense"}


def test_config_kind_wins_and_bad_kinds_are_refused(conn, tmp_path):
    cfg = tmp_path / "categories.yaml"
    cfg.write_text(yaml.safe_dump({"categories": [{"name": "Gifts Received", "kind": "neutral"},
                                                  {"name": "Salary", "is_income": True}]}))
    db.load_categories_from_config(conn, cfg)
    assert conn.execute("SELECT kind, is_income FROM categories WHERE name='Gifts Received'").fetchone()[:] == ("neutral", 0)
    cfg.write_text(yaml.safe_dump({"categories": [{"name": "Salary", "kind": "neutral"}]}))
    db.load_categories_from_config(conn, cfg)          # reclassified by config
    assert conn.execute("SELECT kind, is_income FROM categories WHERE name='Salary'").fetchone()[:] == ("neutral", 0)
    with pytest.raises(ValueError, match="kind"):
        db.ensure_category(conn, "Odd", kind="savings")


@pytest.fixture
def server_db(tmp_path, monkeypatch):
    from api import server
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    c = db.get_connection(); db.init_db(c)
    for name, inc in (("Salary", True), ("Other Income", True), ("Freelance Income", True),
                      ("Groceries", False), ("Shopping", False), ("Transfers", False),
                      ("Personal (non-assessable)", False), ("Employer Reimbursements", False)):
        db.ensure_category(c, name, is_income=inc)
    return server, c


def test_every_report_agrees_on_income_and_spending(server_db):
    server, c = server_db
    _add(c, "2026-03-01", 5000.0, "SALARY", "Salary")
    _add(c, "2026-03-02", -200.0, "EXAMPLE GROCER", "Groceries")
    _add(c, "2026-03-03", -100.0, "EXAMPLE SHOP", "Shopping")
    _add(c, "2026-03-04", 40.0, "EXAMPLE SHOP REFUND", "Shopping")              # nets Shopping
    _add(c, "2026-03-05", 30000.0, "FAMILY HOUSE MONEY", "Personal (non-assessable)")
    _add(c, "2026-03-06", 250.0, "EMPLOYER EXPENSES", "Employer Reimbursements")
    _add(c, "2026-03-07", -999.0, "TO SAVINGS", "Transfers")
    c.commit(); c.close()
    client = server.app.test_client()
    [month] = client.get("/api/summary/monthly?year=2026").get_json()
    assert (month["income"], month["expenses"]) == (5000.0, -260.0)
    review = client.get("/api/summary/year-review?year=2026").get_json()
    assert review["total_income"] == 5000.0 and review["total_expenses"] == 260.0
    totals = client.get("/api/summary/period?from=2026-03-01&to=2026-03-31").get_json()["totals"]
    assert (totals["income"], totals["spending"]) == (5000.0, 260.0)


def test_business_income_is_only_what_is_attributed_to_the_business(conn):
    for name in ("Freelance Income", "Other Income"):
        db.ensure_category(conn, name, is_income=True)
    db.ensure_category(conn, "Business: Equipment")
    _add(conn, "2025-08-01", 1500.0, "CLIENT INVOICE 12", "Freelance Income")
    _add(conn, "2025-08-02", 150000.0, "HOUSE SALE PROCEEDS", "Other Income")
    _add(conn, "2025-08-03", -300.0, "EXAMPLE LENS", "Business: Equipment")
    conn.commit()
    one = {"businesses": [{"name": "Studio"}]}
    backfill_splits(conn, one, fy=2026)
    rows = dict(conn.execute("""SELECT t.description, ts.business_amount FROM transaction_splits ts
                                JOIN transactions t ON t.id = ts.transaction_id""").fetchall())
    assert rows == {"CLIENT INVOICE 12": 1500.0, "EXAMPLE LENS": -300.0}
    # With two businesses nothing is assumed; each lists its own income.
    two = {"businesses": [{"name": "A"}, {"name": "B", "income_categories": ["Other Income"]}]}
    assert compute_splits(conn, 0, "Freelance Income", [], two) == []
    assert compute_splits(conn, 0, "Other Income", [], two) == [{"business_name": "B", "business_pct": 100.0}]
