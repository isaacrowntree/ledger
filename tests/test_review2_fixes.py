"""Regressions for the second code review of the categoriser."""
import json
import subprocess
from pathlib import Path

import pytest

from etl import categorise_cmd, cli, db, reimburse
from etl.categorizer import Categorizer
from etl.embedding import NgramEmbedder
from etl.mailtext import to_text
from etl.merchant import merchant_key
from etl.merchant_store import MerchantStore, follow_purchases
from etl.receipts import Himalaya

FIXTURES = Path(__file__).parent / "fixtures"
P = "Personal (non-assessable)"


def _add(conn, desc, amount, category, conf=0.85, date="2026-03-01", n=[0]):
    n[0] += 1
    acct = db.ensure_account(conn, "Everyday", "ing")
    return db.insert_transaction(
        conn, account_id=acct, date=date, description=desc, amount=amount, original_amount=None,
        original_currency=None, fee=0.0, category_id=db.get_category_id(conn, category),
        category_confidence=conf, reference_id=None, source_type="ing", dedup_hash=f"r2-{n[0]}",
        source_file="t.csv", raw_data="{}")


def _cat(conn, tid):
    return conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id "
                        "WHERE t.id = ?", (tid,)).fetchone()[0]


@pytest.fixture
def cat(conn):
    db.load_categories_from_config(conn, FIXTURES / "categories.yaml")
    return Categorizer(conn, FIXTURES / "categories.yaml", embedder=NgramEmbedder())


def test_deciding_a_merchant_leaves_its_repayments_with_their_purchase(conn, cat):
    dinner = _add(conn, "EXAMPLE RESTAURANT", -300.0, "Eating Out", 0.95)
    back = _add(conn, "Osko Deposit - Receipt 1 Example Friend", 100.0, P)
    other = _add(conn, "Osko Deposit - Receipt 2 Example Friend", 40.0, P, date="2026-05-01")
    conn.commit()
    reimburse.link(conn, back, dinner, "auto"); conn.commit()
    key = merchant_key("Osko Deposit - Receipt 2 Example Friend")
    cat.store.learn_key(key, "in", db.get_category_id(conn, P), origin="llm")
    label = conn.execute("SELECT id FROM merchant_labels WHERE merchant = ?", (key,)).fetchone()[0]
    moved = cat.store.decide(label, db.get_category_id(conn, "Entertainment"))
    assert moved == [other]
    assert _cat(conn, back) == "Eating Out"


def test_repayments_follow_a_refiled_purchase(conn, cat):
    dinner = _add(conn, "EXAMPLE RESTAURANT", -300.0, "Eating Out", 0.95)
    back = _add(conn, "Osko Deposit - Receipt 1 Example Friend", 100.0, P)
    conn.commit()
    reimburse.link(conn, back, dinner, "auto")
    conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?",
                 (db.get_category_id(conn, "Entertainment"), dinner))
    assert follow_purchases(conn) == 1
    assert _cat(conn, back) == "Entertainment"


def test_a_mixed_merchant_offers_no_bulk_move(tmp_path, monkeypatch):
    from api import server
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    conn = db.get_connection(); db.init_db(conn)
    db.load_categories_from_config(conn, FIXTURES / "categories.yaml")
    a = _add(conn, "AMAZON MARKETPLACE AU", -30.0, "Shopping", 0.6)
    _add(conn, "AMAZON MARKETPLACE AU", -90.0, "Shopping", 0.6, date="2026-03-05")
    store = MerchantStore(conn, embedder=NgramEmbedder())
    store.set_mixed(merchant_key("AMAZON MARKETPLACE AU"), "out", db.get_category_id(conn, "Shopping"), "sells everything")
    conn.commit(); conn.close()
    res = server.app.test_client().patch(f"/api/transactions/{a}",
                                         json={"category_name": "Business: Equipment", "learn": True})
    assert res.status_code == 200 and res.get_json()["same_merchant"] == []


def test_unsure_answer_does_not_flag_a_label_it_could_not_change(conn, cat):
    tid = _add(conn, "EXAMPLE HARDWARE STORE", -55.0, "Uncategorized", 0.0)
    conn.commit()
    key = cat.store.learn("EXAMPLE HARDWARE STORE", -55.0, db.get_category_id(conn, "Shopping"))   # human
    categorise_cmd.import_answers(conn, cat, {str(tid): {"category": "Business: Equipment", "bucket": "unsure",
                                                         "alternative": "Shopping"}}, dry_run=False)
    row = conn.execute("SELECT needs_review FROM merchant_labels WHERE merchant = ?", (key,)).fetchone()
    assert row[0] == 0


def test_tag_category_is_final(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    conn = db.get_connection(); db.init_db(conn)
    db.load_categories_from_config(conn, FIXTURES / "categories.yaml")
    tid = _add(conn, "EXAMPLE GYM", -20.0, "Shopping", 0.6)
    conn.commit(); conn.close()
    cli.cmd_tag("EXAMPLE GYM", category="Fitness")
    conn = db.get_connection()
    assert conn.execute("SELECT category_confidence FROM transactions WHERE id = ?", (tid,)).fetchone()[0] == 1.0


def test_reimbursement_import_never_links_a_tax_relevant_purchase(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    conn = db.get_connection(); db.init_db(conn)
    db.load_categories_from_config(conn, FIXTURES / "categories.yaml")
    lens = _add(conn, "EXAMPLE CAMERA SHOP", -200.0, "Business: Equipment", 0.95)
    back = _add(conn, "Osko Deposit - Receipt 9 Example Friend", 100.0, P, date="2026-03-02")
    conn.commit(); conn.close()
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({str(back): lens}))
    reimburse.run(import_=str(answers))
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM reimbursements").fetchone()[0] == 0
    assert "tax-relevant" in capsys.readouterr().out


def test_quoted_printable_utf8_and_stray_closing_tags():
    raw = "<p>Don=E2=80=99t</p></style><p>Total =3D $42.00</p><p>caf=\n=C3=A9</p>"
    assert to_text(raw) == "Don’t\nTotal = $42.00\ncafé"
    assert to_text("<p>A=42 already decoded ’</p>") == "A=42 already decoded ’"


def test_himalaya_search_pages_and_reports_failure(monkeypatch):
    pages = {"1": [{"id": i} for i in range(3)], "2": [{"id": 9}]}

    def fake_run(args, **kw):
        page = args[args.index("-p") + 1]
        return subprocess.CompletedProcess(args, 0, json.dumps({"envelopes": pages.get(page, [])}), "")

    monkeypatch.setattr(Himalaya, "PAGE", 3)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert [e["id"] for e in Himalaya().search("a", "INBOX", "from shop")] == [0, 1, 2, 9]
    monkeypatch.setattr(subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "auth failed"))
    with pytest.raises(RuntimeError, match="auth failed"):
        Himalaya().search("a", "INBOX", "from shop")
