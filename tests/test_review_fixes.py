"""Regressions for the code review of the merchant-categoriser branch."""
import shutil
from pathlib import Path

import pytest

from etl import cli, db, reimburse
from etl.categorizer import CONFIDENCE, Categorizer
from etl.embedding import NgramEmbedder

FIXTURES = Path(__file__).parent / "fixtures"


def _add(conn, desc, amount, category, conf, date="2026-03-01", n=[0], transfer=False):
    n[0] += 1
    acct = db.ensure_account(conn, "Everyday", "ing")
    return db.insert_transaction(
        conn, account_id=acct, date=date, description=desc, amount=amount, original_amount=None,
        original_currency=None, fee=0.0, category_id=db.get_category_id(conn, category),
        category_confidence=conf, reference_id=None, source_type="ing", dedup_hash=f"rv{n[0]}",
        source_file="t.csv", raw_data="{}", is_transfer=transfer)


def test_ingest_runs_to_the_end(tmp_path, monkeypatch, capsys):
    """The FX conversion used to run after the connection was closed."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    config = tmp_path / "config"; config.mkdir()
    for f in ("accounts.yaml", "categories.yaml"):
        shutil.copy(FIXTURES / f, config / f)
    monkeypatch.setattr(cli, "CONFIG_DIR", config)
    monkeypatch.setattr(cli, "STAGING_DIR", tmp_path / "staging")
    cli.cmd_ingest()
    assert "Done. Total inserted: 0" in capsys.readouterr().out


def test_a_linked_repayment_is_never_evidence(conn):
    cat = Categorizer(conn, FIXTURES / "categories.yaml", embedder=NgramEmbedder())
    dinner = _add(conn, "EXAMPLE RESTAURANT", -300.0, "Eating Out", 0.95)
    back = _add(conn, "Osko Deposit - Receipt 1 Example Friend", 100.0, "Personal (non-assessable)", 0.85)
    conn.commit()
    reimburse.link(conn, back, dinner, "auto"); conn.commit()
    cat.store.sync_transactions()
    assert cat.store.seed_from_transactions() == 1             # only the restaurant
    assert cat.store.lookup("Osko Deposit - Receipt 2 Example Friend", 50.0) is None
    assert all(n.example != "Osko Deposit - Receipt 1 Example Friend"
               for n in cat.store.nearest("Osko Deposit Example Friend", 50.0))


def test_guesses_are_never_seed_votes(conn):
    cat = Categorizer(conn, FIXTURES / "categories.yaml", embedder=NgramEmbedder())
    for conf in (0.6, 0.55, 0.5):
        _add(conn, f"EXAMPLE GUESS {int(conf * 100)}", -10.0, "Shopping", conf)
    conn.commit()
    assert cat.store.seed_from_transactions() == 0


def test_rerun_leaves_decisions_and_repayments_alone(tmp_path, monkeypatch):
    from etl import categorise_cmd
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    c = db.get_connection(); db.init_db(c); db.load_categories_from_config(c, FIXTURES / "categories.yaml")
    likely = _add(c, "EXAMPLE UNKNOWN SHOP", -40.0, "Home & Garden", 0.5)
    dinner = _add(c, "EXAMPLE RESTAURANT", -300.0, "Eating Out", 0.95)
    back = _add(c, "Osko Deposit - Receipt 1 Example Friend", 100.0, "Personal (non-assessable)", 0.85)
    c.commit(); reimburse.link(c, back, dinner, "auto"); c.commit(); c.close()
    categorise_cmd.run(FIXTURES / "categories.yaml", rerun=True)
    c = db.get_connection()
    names = dict(c.execute("SELECT t.id, cat.name FROM transactions t JOIN categories cat "
                           "ON cat.id = t.category_id").fetchall())
    assert names[likely] == "Home & Garden" and names[back] == "Eating Out"


def test_a_persons_category_change_settles_a_flagged_merchant(conn):
    cat = Categorizer(conn, FIXTURES / "categories.yaml", embedder=NgramEmbedder())
    s = cat.store
    s.learn_key("EXAMPLE TRADING", "out", db.get_category_id(conn, "Shopping"), origin="llm")
    s.flag("EXAMPLE TRADING", "out", db.get_category_id(conn, "Coffee"), "unsure")
    s.learn_key("EXAMPLE TRADING", "out", db.get_category_id(conn, "Coffee"), origin="human")
    row = conn.execute("SELECT needs_review, alt_category_id FROM merchant_labels "
                       "WHERE merchant = 'EXAMPLE TRADING'").fetchone()
    assert tuple(row) == (0, None)


def test_a_transfer_is_never_a_repayment(conn):
    _add(conn, "EXAMPLE GROCER", -100.0, "Groceries", 0.95)
    t = _add(conn, "Osko Deposit - Receipt 9 Example Partner", 50.0, "Personal (non-assessable)", 0.85,
             transfer=True)
    conn.commit()
    reimburse.match(conn)
    assert conn.execute("SELECT COUNT(*) FROM reimbursements WHERE deposit_id = ?", (t,)).fetchone()[0] == 0


@pytest.mark.parametrize("text, found", [
    ("Total $5.00", True), ("Total $15.00", False), ("Order 105.00 AUD", False),
    ("Total 5.00 AUD", True), ("Total $5.001", False)])
def test_receipt_amount_matches_whole_numbers_only(text, found):
    from etl.receipts import _has_amount
    assert _has_amount(text, 5.0) is found


def test_receipt_amount_with_thousands():
    from etl.receipts import _has_amount
    assert _has_amount("Total $1,234.50", 1234.5) and _has_amount("Total 1234.50", 1234.5)
