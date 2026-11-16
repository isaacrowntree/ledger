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


def test_category_change_files_the_merchant_and_lists_its_other_rows(client):
    conn = db.get_connection()
    acct = conn.execute("SELECT id FROM accounts").fetchone()[0]
    for i, desc in enumerate(["SUPERMARKET   0101   EXAMPLETOWN  NS", "OTHER SHOP"]):
        db.insert_transaction(
            conn, account_id=acct, date="2026-03-06", description=desc, amount=-20.0,
            original_amount=None, original_currency=None, fee=0.0, category_id=None,
            category_confidence=None, reference_id=None, source_type="test", dedup_hash=f"x{i}",
            source_file="t.csv", raw_data="{}", is_transfer=False)
    conn.commit()
    conn.close()
    r = client.patch("/api/transactions/1", json={"category_name": "Groceries"})
    assert r.status_code == 200
    assert r.get_json()["same_merchant"] == [2]           # same merchant, other suburb
    label = _row("SELECT merchant, origin FROM merchant_labels")
    assert tuple(label) == ("SUPERMARKET", "human")


def test_one_off_category_change_teaches_nothing(client):
    r = client.patch("/api/transactions/1", json={"category_name": "Groceries", "learn": False})
    assert r.status_code == 200
    assert _row("SELECT COUNT(*) FROM merchant_labels")[0] == 0


def test_uncategorized_rows_carry_suggestions(client):
    client.patch("/api/transactions/1", json={"category_name": "Groceries"})
    conn = db.get_connection()
    acct = conn.execute("SELECT id FROM accounts").fetchone()[0]
    db.ensure_category(conn, "Uncategorized")
    db.insert_transaction(
        conn, account_id=acct, date="2026-03-07", description="SUPERMARKETS", amount=-30.0,
        original_amount=None, original_currency=None, fee=0.0,
        category_id=db.get_category_id(conn, "Uncategorized"), category_confidence=0.0,
        reference_id=None, source_type="test", dedup_hash="h-sugg", source_file="t.csv",
        raw_data="{}", is_transfer=False)
    conn.commit()
    from etl.merchant_store import MerchantStore
    MerchantStore(conn).sync_transactions()      # the CLI builds the index; the API never does
    conn.close()
    rows = client.get("/api/transactions").get_json()
    new = next(t for t in rows if t["description"] == "SUPERMARKETS")
    assert new["suggestions"][0]["category"] == "Groceries"
    assert next(t for t in rows if t["id"] == 1)["suggestions"] == []


def _flag_supermarket(groceries_vs: str = "Shopping"):
    from etl.merchant_store import MerchantStore
    conn = db.get_connection()
    db.ensure_category(conn, groceries_vs)
    store = MerchantStore(conn)
    store.learn_key("SUPERMARKET", "out", db.get_category_id(conn, "Groceries"), origin="llm")
    store.flag("SUPERMARKET", "out", db.get_category_id(conn, groceries_vs), "could be a department store")
    label_id = conn.execute("SELECT id FROM merchant_labels").fetchone()[0]
    conn.close()
    return label_id


def test_flagged_merchants_are_listed_with_both_choices(client):
    _flag_supermarket()
    [m] = client.get("/api/merchants/review").get_json()
    assert (m["merchant"], m["category"], m["alternative"]) == ("SUPERMARKET", "Groceries", "Shopping")
    assert m["rows"] == 1 and m["total"] == -80.0 and m["examples"] == ["SUPERMARKET"]
    assert m["note"] == "could be a department store"


def test_deciding_files_every_row_and_clears_the_flag(client):
    label_id = _flag_supermarket()
    r = client.post(f"/api/merchants/{label_id}/decide", json={"category_name": "Shopping"})
    assert r.get_json() == {"ok": True, "refiled": 1}
    assert client.get("/api/merchants/review").get_json() == []
    assert tuple(_row("SELECT c.name, t.category_confidence FROM transactions t "
                      "JOIN categories c ON c.id = t.category_id WHERE t.id = 1")) == ("Shopping", 1.0)
    assert _row("SELECT origin FROM merchant_labels WHERE id = ?", label_id)[0] == "human"


def test_deciding_refuses_unknown_category_and_label(client):
    label_id = _flag_supermarket()
    assert client.post(f"/api/merchants/{label_id}/decide", json={"category_name": "Nope"}).status_code == 400
    assert client.post("/api/merchants/999/decide", json={"category_name": "Groceries"}).status_code == 404
