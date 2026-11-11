"""Write endpoints refuse bad input with a 400 and write nothing.

SQLite keeps whatever it is handed: a split of "abc" lands in a REAL column as
text, and an unknown category name used to be dropped while the API still
answered ok.
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
    (config / "accounts.yaml").write_text((FIXTURES / "accounts.yaml").read_text())
    monkeypatch.setattr(server, "CONFIG_DIR", config)
    conn = db.get_connection()
    db.init_db(conn)
    db.ensure_category(conn, "Groceries")
    acct = db.ensure_account(conn, "Test Checking", "ing")
    db.insert_transaction(
        conn, account_id=acct, date="2026-03-05", description="SUPERMARKET", amount=-80.0,
        original_amount=None, original_currency=None, fee=0.0, category_id=None,
        category_confidence=None, reference_id=None, source_type="test", dedup_hash="h1",
        source_file="t.csv", raw_data=json.dumps({}), is_transfer=False)
    conn.commit()
    conn.close()
    return server.app.test_client()


def _row(sql, *args):
    conn = db.get_connection()
    r = conn.execute(sql, args).fetchone()
    conn.close()
    return r


def test_unknown_category_is_refused_not_ignored(client):
    r = client.patch("/api/transactions/1", json={"category_name": "Grocries"})
    assert r.status_code == 400 and "Grocries" in r.get_json()["error"]
    assert _row("SELECT category_id FROM transactions WHERE id = 1")[0] is None


def test_misspelled_field_is_refused(client):
    r = client.patch("/api/transactions/1", json={"note": "typo"})
    assert r.status_code == 400 and "note" in r.get_json()["error"]


def test_missing_transaction_is_404(client):
    assert client.patch("/api/transactions/999", json={"notes": "x"}).status_code == 404


def test_valid_edit_saves_and_confirms(client):
    assert client.patch("/api/transactions/1", json={"category_name": "Groceries", "notes": "weekly shop"}).status_code == 200
    r = _row("SELECT category_confidence, notes FROM transactions WHERE id = 1")
    assert r["category_confidence"] == 1.0 and r["notes"] == "weekly shop"


def test_split_must_be_a_percentage_number(client):
    client.post("/api/shared-expenses", json={"transaction_id": 1})
    assert client.patch("/api/shared-expenses/1", json={"split_pct": "abc"}).status_code == 400
    assert client.patch("/api/shared-expenses/1", json={"split_pct": 140}).status_code == 400
    assert _row("SELECT split_pct FROM shared_expenses WHERE id = 1")[0] == 50.0
    assert client.patch("/api/shared-expenses/1", json={"split_pct": 40}).status_code == 200
    assert _row("SELECT split_pct FROM shared_expenses WHERE id = 1")[0] == 40.0


def test_settled_must_be_a_boolean(client):
    client.post("/api/shared-expenses", json={"transaction_id": 1})
    assert client.patch("/api/shared-expenses/1", json={"is_settled": "yes"}).status_code == 400
    assert client.patch("/api/shared-expenses/1", json={"is_settled": True}).status_code == 200
    assert _row("SELECT is_settled FROM shared_expenses WHERE id = 1")[0] == 1


def test_sharing_a_missing_transaction_is_refused(client):
    assert client.post("/api/shared-expenses", json={"transaction_id": 999}).status_code == 400


def test_dates_must_be_real_dates(client):
    r = client.post("/api/tax-overrides", json={"fy": 2026, "section": "income", "label": "x", "amount": 1})
    assert r.status_code == 200
    r = client.post("/api/work-trips", json={"fy": 2026, "name": "Trip", "start_date": "2026-13-01", "end_date": "2026-01-02"})
    assert r.status_code == 400 and "start_date" in r.get_json()["error"]


def test_amount_filter_must_be_numeric(client):
    assert client.get("/api/transactions?min=abc").status_code == 400
    assert len(client.get("/api/transactions?min=50").get_json()) == 1
    assert client.get("/api/transactions/summary?direction=in").get_json()["count"] == 0
