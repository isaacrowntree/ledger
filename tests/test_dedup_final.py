"""A card row dedup marks as the funding side of a PayPal purchase stays a transfer."""
from etl import db
from etl.categorizer import CONFIDENCE
from etl.dedup import find_duplicates, resolve_duplicates


def test_dedup_settles_the_card_side_for_good(conn):
    paypal = db.ensure_account(conn, "PayPal", "paypal")
    card = db.ensure_account(conn, "Card", "bankwest")
    shop = db.get_category_id(conn, "Shopping")
    ids = []
    for acct, src, desc, d in ((paypal, "paypal", "Example Shop Ltd", "2026-03-02"),
                               (card, "bankwest", "PAYPAL *EXAMPLESHOP 10000000001 AU", "2026-03-03")):
        ids.append(db.insert_transaction(
            conn, account_id=acct, date=d, description=desc, amount=-40.0, original_amount=None,
            original_currency=None, fee=0.0, category_id=shop, category_confidence=CONFIDENCE["llm"],
            reference_id=None, source_type=src, dedup_hash=f"d{src}", source_file="t.csv",
            raw_data="{}", is_transfer=False))
    conn.commit()
    assert resolve_duplicates(conn, find_duplicates(conn)) == 1
    row = conn.execute("SELECT is_transfer, category_confidence FROM transactions WHERE id = ?",
                       (ids[1],)).fetchone()
    # Confidence 1.0 keeps `categorise --rerun` and Claude's imports from undoing it.
    assert tuple(row) == (1, 1.0)
