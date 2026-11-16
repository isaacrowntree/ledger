"""`ledger categorise --llm` with a stand-in for the Claude client (no network)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from etl import categorise_cmd, db
from etl.categorizer import CONFIDENCE, Categorizer
from etl.embedding import NgramEmbedder
from etl.llm_file import file_with_claude

FIXTURES = Path(__file__).parent / "fixtures" / "categories.yaml"


class FakeClaude:
    """Answers each batch from a table, or declines when told to."""
    def __init__(self, table, refuse=False):
        self.table, self.refuse, self.requests = table, refuse, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        if self.refuse:
            return SimpleNamespace(stop_reason="refusal", content=[])
        asked = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
        answers = [{"id": it["id"], **self.table[it["description"]]} for it in asked] + \
                  [{"id": 999999, "category": "Coffee", "bucket": "sure", "alternative": "", "note": ""}]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"answers": answers}))])


@pytest.fixture
def ledger(conn):
    acct = db.ensure_account(conn, "Everyday", "ing")
    unc = db.get_category_id(conn, "Uncategorized")
    ids = {}
    for i, desc in enumerate(["EXAMPLE LAKSA HOUSE", "EXAMPLE TRADING PTY LTD"]):
        ids[desc] = db.insert_transaction(
            conn, account_id=acct, date="2026-03-01", description=desc, amount=-20.0 - i,
            original_amount=None, original_currency=None, fee=0.0, category_id=unc,
            category_confidence=0.0, reference_id=None, source_type="ing", dedup_hash=f"l{i}",
            source_file="t.csv", raw_data="{}", is_transfer=False)
    conn.commit()
    return conn, ids, Categorizer(conn, FIXTURES, embedder=NgramEmbedder())


TABLE = {
    "EXAMPLE LAKSA HOUSE": {"category": "Eating Out", "bucket": "sure", "alternative": "", "note": "laksa"},
    "EXAMPLE TRADING PTY LTD": {"category": "Shopping", "bucket": "unsure",
                                "alternative": "Business: Equipment", "note": "unknown goods"},
}


def _row(conn, i):
    return tuple(conn.execute("SELECT c.name, t.category_confidence FROM transactions t "
                              "JOIN categories c ON c.id = t.category_id WHERE t.id = ?", (i,)).fetchone())


def test_answers_are_filed_by_bucket(ledger):
    conn, ids, cat = ledger
    fake = FakeClaude(TABLE)
    totals = file_with_claude(conn, cat, client=fake)
    assert _row(conn, ids["EXAMPLE LAKSA HOUSE"]) == ("Eating Out", CONFIDENCE["llm"])
    assert _row(conn, ids["EXAMPLE TRADING PTY LTD"]) == ("Shopping", categorise_cmd.LLM_REVIEW_CONFIDENCE)
    assert totals["sure"] == 1 and totals["unsure"] == 1 and totals["missing"] == 0
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    schema = req["output_config"]["format"]["schema"]
    assert "Eating Out" in schema["properties"]["answers"]["items"]["properties"]["category"]["enum"]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_a_declined_batch_stays_pending(ledger):
    conn, ids, cat = ledger
    totals = file_with_claude(conn, cat, client=FakeClaude(TABLE, refuse=True))
    assert totals["refused_batches"] == 1
    assert _row(conn, ids["EXAMPLE LAKSA HOUSE"])[0] == "Uncategorized"


def test_dry_run_writes_nothing(ledger):
    conn, ids, cat = ledger
    file_with_claude(conn, cat, client=FakeClaude(TABLE), dry_run=True)
    assert _row(conn, ids["EXAMPLE LAKSA HOUSE"])[0] == "Uncategorized"


def test_the_guide_is_sent(ledger, tmp_path):
    conn, ids, cat = ledger
    guide = tmp_path / "guide.md"
    guide.write_text("Cafés are Coffee.")
    fake = FakeClaude(TABLE)
    file_with_claude(conn, cat, client=fake, guide=guide)
    assert "Cafés are Coffee." in fake.requests[0]["system"][0]["text"]


def test_setup_seeds_and_points_the_way(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    c = db.get_connection(); db.init_db(c); db.load_categories_from_config(c, FIXTURES)
    acct = db.ensure_account(c, "Everyday", "ing")
    db.insert_transaction(c, account_id=acct, date="2026-03-01", description="EXAMPLE BAKERY",
                          amount=-9.0, original_amount=None, original_currency=None, fee=0.0,
                          category_id=db.get_category_id(c, "Eating Out"), category_confidence=1.0,
                          reference_id=None, source_type="ing", dedup_hash="s1", source_file="t.csv",
                          raw_data="{}", is_transfer=False)
    c.commit(); c.close()
    categorise_cmd.run(FIXTURES, setup=True)
    out = capsys.readouterr().out
    assert "Labelled 1 merchants" in out and "--evaluate" in out and "--llm" in out
