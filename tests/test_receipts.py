"""`ledger receipts` against a stand-in mailbox."""
import pytest

from etl import db, receipts
from etl.receipts import Account, items_from, match

PERSONAL = Account("personal", "All")


class FakeMail:
    def __init__(self, envelopes, bodies):
        self.envelopes, self.bodies, self.queries = envelopes, bodies, []

    def search(self, account, mailbox, query):
        self.queries.append(query)
        word = query.split()[1]
        return [e for e in self.envelopes if word in e["from"][0]["email"]]

    def read(self, account, mailbox, message_id):
        return self.bodies[message_id]


def _env(i, sender, date, subject):
    return {"id": i, "from": [{"email": sender}], "date": f"{date}T01:00:00Z", "subject": subject}


@pytest.fixture
def ledger(conn):
    acct = db.ensure_account(conn, "Card", "bankwest")
    pp = db.ensure_account(conn, "PayPal", "paypal")
    unc = db.get_category_id(conn, "Uncategorized")
    rows = {}
    for i, (a, src, d, desc, amt, ref) in enumerate([
            (acct, "bankwest", "2026-03-05", "AMAZON AU MARKETPLACE SYDNEY", -32.98, None),
            (acct, "bankwest", "2026-03-20", "AMAZON AU MARKETPLACE SYDNEY", -15.00, None),
            (pp, "paypal", "2026-04-01", "Example Lens Store", -450.00, "1AB23456CD7890123")]):
        rows[i] = db.insert_transaction(
            conn, account_id=a, date=d, description=desc, amount=amt, original_amount=None,
            original_currency=None, fee=0.0, category_id=unc, category_confidence=0.0,
            reference_id=ref, source_type=src, dedup_hash=f"r{i}", source_file="t.csv",
            raw_data="{}", is_transfer=False)
    conn.commit()
    return conn, rows


AMAZON = "Shipped\n* Example Tyre Inflator\nQuantity: 1\n26.99 AUD\nTotal\n32.98 AUD"
PAYPAL = ("You've paid $450.00 AUD to Example Lens Store\nDescription\nUnit price\nQty\nAmount\n"
          "Example 50mm prime lens\n$450.00 AUD\n1\n$450.00 AUD\nSubtotal\n$450.00 AUD\n"
          "Transaction ID: 1AB23456CD7890123")


def test_amazon_by_exact_amount_and_paypal_by_transaction_id(ledger):
    conn, rows = ledger
    mail = FakeMail([_env("a1", "auto@amazon.com.au", "2026-03-03", "Shipped: 'Example Tyre...'"),
                     _env("p1", "service@paypal.com.au", "2026-04-01", "Receipt for Your Payment")],
                    {"a1": AMAZON, "p1": PAYPAL})
    r = match(conn, [PERSONAL], "2026-01-01", mail=mail)
    assert (r["amount"], r["paypal-id"]) == (1, 1)
    got = dict(conn.execute("SELECT transaction_id, items FROM receipts").fetchall())
    assert got[rows[0]] == "Example Tyre Inflator"
    assert got[rows[2]] == "Example 50mm prime lens"
    assert rows[1] not in got                      # $15.00 appears in no email


def test_one_search_per_merchant_word(ledger):
    conn, _ = ledger
    mail = FakeMail([], {})
    match(conn, [PERSONAL], "2026-01-01", mail=mail)
    amazon = [q for q in mail.queries if q.startswith("from amazon")]
    assert len(amazon) == 1 and "after 2026-02-23" in amazon[0] and "not after 2026-03-23" in amazon[0]


def test_dry_run_writes_nothing(ledger):
    conn, _ = ledger
    mail = FakeMail([_env("a1", "auto@amazon.com.au", "2026-03-03", "Shipped")], {"a1": AMAZON})
    assert match(conn, [PERSONAL], "2026-01-01", dry_run=True, mail=mail)["amount"] == 1
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_receipt_items_go_with_the_export(ledger):
    from pathlib import Path
    from etl.categorise_cmd import pending_payload
    from etl.categorizer import Categorizer
    from etl.embedding import NgramEmbedder
    conn, rows = ledger
    mail = FakeMail([_env("a1", "auto@amazon.com.au", "2026-03-03", "Shipped")], {"a1": AMAZON})
    match(conn, [PERSONAL], "2026-01-01", mail=mail)
    cat = Categorizer(conn, Path(__file__).parent / "fixtures" / "categories.yaml", embedder=NgramEmbedder())
    item = next(i for i in pending_payload(conn, cat)["items"] if i["id"] == rows[0])
    assert item["receipt"]["items"] == ["Example Tyre Inflator"]


def test_items_fall_back_to_the_subject():
    assert items_from("Thanks for your order", "Your Example order") == ["Your Example order"]


def test_config_since_as_yaml_date(tmp_path):
    from etl.receipts import load_config
    p = tmp_path / "receipts.yaml"
    p.write_text("accounts:\n  - name: personal\nsince: 2025-07-01\n")
    accounts, since = load_config(p)
    assert since == "2025-07-01" and accounts[0].mailbox == "INBOX"
