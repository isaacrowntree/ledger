"""The period summary: one definition of income and spending for every page.

Before it, the dashboard's totals summed every negative and every positive
amount while its category table netted refunds and dropped Uncategorized, so
one year showed $138k of expenses above a $117k table.
"""
import json
from pathlib import Path

import pytest

from etl import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api import server

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    config = tmp_path / "config"
    config.mkdir()
    accounts = (FIXTURES / "accounts.yaml").read_text()
    (config / "accounts.yaml").write_text(
        'partner:\n  name: "Sam"\n  patterns: ["SAM PARTNER"]\n\n' + accounts)
    monkeypatch.setattr(server, "CONFIG_DIR", config)

    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, FIXTURES / "categories.yaml")
    db.load_accounts_from_config(conn, FIXTURES / "accounts.yaml")
    for name in ("Salary", "Groceries", "Transfers", "Mortgage"):
        db.ensure_category(conn, name, is_income=(name == "Salary"))
    checking = db.ensure_account(conn, "Test Checking", "ing")
    loan = db.ensure_account(conn, "Test Mortgage", "ing", account_type="loan")
    cat = lambda n: db.get_category_id(conn, n)  # noqa: E731
    rows = [
        (checking, "2026-03-01", "PAY", 5000.0, "Salary", 0),
        (checking, "2026-03-05", "SUPERMARKET", -200.0, "Groceries", 0),
        (checking, "2026-03-06", "SUPERMARKET REFUND", 50.0, "Groceries", 0),
        (checking, "2026-03-07", "MYSTERY", -30.0, None, 0),
        (checking, "2026-03-08", "TO SAVINGS", -1000.0, "Transfers", 1),
        (checking, "2026-03-09", "SAM PARTNER SHARED", 400.0, "Transfers", 1),
        (loan, "2026-03-31", "INTEREST CHARGE", -900.0, "Mortgage", 0),
        (loan, "2026-03-15", "REPAYMENT", 1500.0, "Transfers", 1),
    ]
    for i, (acct, date, desc, amt, category, transfer) in enumerate(rows):
        db.insert_transaction(
            conn, account_id=acct, date=date, description=desc, amount=amt,
            original_amount=None, original_currency=None, fee=0.0,
            category_id=cat(category) if category else None, category_confidence=None,
            reference_id=None, source_type="test", dedup_hash=f"h{i}",
            source_file="t.csv", raw_data=json.dumps({}), is_transfer=bool(transfer))
    conn.commit()
    conn.close()
    return server.app.test_client()


def _period(client):
    return client.get("/api/summary/period?from=2026-01-01&to=2026-12-31").get_json()


def test_totals_equal_the_category_breakdown(client):
    p = _period(client)
    income = sum(c["total"] for c in p["categories"] if c["is_income"])
    spending = -sum(c["total"] for c in p["categories"] if not c["is_income"])
    assert p["totals"]["income"] == income == 5000.0
    assert p["totals"]["spending"] == round(spending, 2)


def test_refund_reduces_its_category_not_counted_as_income(client):
    groceries = next(c for c in _period(client)["categories"] if c["category"] == "Groceries")
    assert groceries["total"] == -150.0


def test_uncategorized_is_spending_not_dropped(client):
    cats = {c["category"]: c["total"] for c in _period(client)["categories"]}
    assert cats["Uncategorized"] == -30.0


def test_mortgage_interest_counts_but_transfers_do_not(client):
    p = _period(client)
    cats = {c["category"] for c in p["categories"]}
    assert "Transfers" not in cats
    assert p["totals"]["spending"] == 150.0 + 30.0 + 900.0


def test_partner_contributions_reported_beside_spending(client):
    t = _period(client)["totals"]
    assert t["partner_name"] == "Sam"
    assert t["partner_contributions"] == 400.0
    assert t["net_with_partner"] == round(t["net"] + 400.0, 2)
