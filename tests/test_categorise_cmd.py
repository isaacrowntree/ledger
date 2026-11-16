"""`ledger categorise`: seed, rerun, and the export/import round trip with Claude."""
import json
from pathlib import Path

import pytest

from etl import db
from etl import categorise_cmd
from etl.categorizer import CONFIDENCE

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = FIXTURES / "categories.yaml"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, CONFIG)
    acct = db.ensure_account(conn, "Everyday", "ing")
    unc = db.get_category_id(conn, "Uncategorized")

    def add(description, amount, category=None, confidence=0.0):
        cat = db.get_category_id(conn, category) if category else unc
        return db.insert_transaction(
            conn, account_id=acct, date="2026-03-01", description=description, amount=amount,
            original_amount=None, original_currency=None, fee=0.0, category_id=cat,
            category_confidence=confidence, reference_id=None, source_type="ing",
            dedup_hash=f"{description}{amount}{category}", source_file="t.csv", raw_data="{}",
            is_transfer=False)

    conn.commit()
    yield conn, add
    conn.close()


def _row(conn, txn_id):
    return conn.execute(
        "SELECT c.name, t.category_confidence, t.is_transfer FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE t.id = ?", (txn_id,)).fetchone()


def test_export_lists_pending_rows_with_context(ledger, tmp_path):
    conn, add = ledger
    pending = add("Visa Purchase - Receipt 111 EXAMPLE LAKSA HOUSE EXAMPLETOWN", -18.0)
    add("NETFLIX.COM", -20.0, "Subscriptions", confidence=0.9)        # settled
    add("CONFIRMED THING", -5.0, "Coffee", confidence=1.0)            # confirmed
    conn.commit()
    out = tmp_path / "pending.json"
    categorise_cmd.run(CONFIG, export=str(out))
    data = json.loads(out.read_text())
    assert [i["id"] for i in data["items"]] == [pending]
    item = data["items"][0]
    assert item["direction"] == "out" and item["amount"] == 18.0
    assert item["similar_filed"] and {"description", "category", "similarity"} <= item["similar_filed"][0].keys()
    assert "Uncategorized" not in data["categories"]
    assert "WOOLWORTHS" in " ".join(data["category_examples"]["Groceries"]).upper()


def test_import_acts_on_each_bucket(ledger, tmp_path):
    conn, add = ledger
    sure = add("Visa Purchase - Receipt 111 EXAMPLE LAKSA HOUSE EXAMPLETOWN", -18.0)
    likely = add("ZQXV HOLDINGS", -60.0)
    unsure = add("EXAMPLE TRADING PTY LTD", -900.0)
    conn.commit()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        str(sure): {"category": "Eating Out", "bucket": "sure"},
        str(likely): {"category": "Shopping", "bucket": "likely"},
        str(unsure): {"category": "Shopping", "bucket": "unsure",
                      "alternative": "Business: Equipment", "note": "could be work gear"},
    }))
    categorise_cmd.run(CONFIG, import_=str(answers))
    review = categorise_cmd.LLM_REVIEW_CONFIDENCE
    assert tuple(_row(conn, sure))[:2] == ("Eating Out", CONFIDENCE["llm"])
    assert tuple(_row(conn, likely))[:2] == ("Shopping", review)
    assert tuple(_row(conn, unsure))[:2] == ("Shopping", review)
    labels = {r[0]: r[1:] for r in conn.execute(
        "SELECT l.merchant, l.origin, l.needs_review, a.name, l.note FROM merchant_labels l "
        "LEFT JOIN categories a ON a.id = l.alt_category_id")}
    assert labels["EXAMPLE LAKSA HOUSE EXAMPLETOWN"] == ("llm", 0, None, None)
    assert "ZQXV HOLDINGS" not in labels                     # likely: nothing learned
    assert labels["EXAMPLE TRADING PTY LTD"] == ("llm", 1, "Business: Equipment", "could be work gear")


def test_a_flagged_merchant_files_later_rows_for_review(ledger, tmp_path):
    conn, add = ledger
    t = add("EXAMPLE TRADING PTY LTD", -900.0)
    conn.commit()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({str(t): {"category": "Shopping", "bucket": "unsure"}}))
    categorise_cmd.run(CONFIG, import_=str(answers))
    from etl.categorizer import Categorizer
    from etl.embedding import NgramEmbedder
    from etl.models import RawTransaction
    cat = Categorizer(conn, CONFIG, embedder=NgramEmbedder())
    _, conf = cat.categorize(RawTransaction(date="2026-04-01", description="EXAMPLE TRADING PTY LTD",
                                            amount=-50.0, source_type="ing"))
    assert conf == CONFIDENCE["review"]
    assert all(n.merchant != "EXAMPLE TRADING PTY LTD" for n in cat.store.nearest("EXAMPLE TRADING", -50.0))


def test_import_refuses_a_missing_bucket(ledger, tmp_path):
    conn, add = ledger
    t = add("SOMETHING", -1.0)
    conn.commit()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({str(t): {"category": "Shopping", "confidence": 0.99}}))
    with pytest.raises(SystemExit, match="bucket"):
        categorise_cmd.run(CONFIG, import_=str(answers))
    assert _row(conn, t)["name"] == "Uncategorized"


def test_import_never_touches_a_row_a_person_confirmed(ledger, tmp_path):
    conn, add = ledger
    mine = add("MY CAFE", -4.0, "Coffee", confidence=1.0)
    conn.commit()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({str(mine): {"category": "Eating Out", "bucket": "sure"}}))
    categorise_cmd.run(CONFIG, import_=str(answers))
    assert _row(conn, mine)["name"] == "Coffee"


def test_import_refuses_unknown_categories_and_writes_nothing(ledger, tmp_path):
    conn, add = ledger
    t = add("SOMETHING", -1.0)
    conn.commit()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({str(t): {"category": "Eatng Out", "bucket": "sure"}}))
    with pytest.raises(SystemExit, match="Eatng Out"):
        categorise_cmd.run(CONFIG, import_=str(answers))
    assert _row(conn, t)["name"] == "Uncategorized"


def test_import_into_transfers_sets_the_flag(ledger, tmp_path):
    conn, add = ledger
    t = add("Osko Payment - Receipt 1 Transfer To Example Savings", -500.0)
    conn.commit()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({str(t): {"category": "Transfers", "bucket": "sure"}}))
    categorise_cmd.run(CONFIG, import_=str(answers))
    assert _row(conn, t)["is_transfer"] == 1


def test_seed_then_rerun_reproduces_the_ledger(ledger, capsys):
    conn, add = ledger
    for i in range(3):
        add(f"Visa Purchase - Receipt {100 + i} CORNER BAKERY EXAMPLETOWN", -9.0, "Eating Out", confidence=0.8)
    conn.commit()
    categorise_cmd.run(CONFIG, seed=True)
    categorise_cmd.run(CONFIG, rerun=True, dry_run=True)
    assert "0 would change category" in capsys.readouterr().out
