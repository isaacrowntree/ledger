"""PayPal rows exported without an AUD conversion get one from nearby converted rows."""
from etl import db
from etl.currency import FX_NOTE, convert_unconverted


def _add(conn, acct, date, amount, original, currency, n):
    return db.insert_transaction(
        conn, account_id=acct, date=date, description="EXAMPLE RIDES BV", amount=amount,
        original_amount=original, original_currency=currency, fee=0.0, category_id=None,
        category_confidence=None, reference_id=None, source_type="paypal", dedup_hash=f"fx{n}",
        source_file="t.csv", raw_data="{}", is_transfer=False)


def test_a_won_amount_stored_as_dollars_is_converted(conn):
    acct = db.ensure_account(conn, "PayPal", "paypal")
    _add(conn, acct, "2026-06-10", -10.00, -10000.0, "KRW", 1)       # converted by PayPal
    bad = _add(conn, acct, "2026-06-12", -24700.0, -24700.0, "KRW", 2)  # left in won
    conn.commit()
    [(tid, cur, foreign, aud, rate)] = convert_unconverted(conn)
    assert (tid, cur, aud) == (bad, "KRW", -24.70)
    row = conn.execute("SELECT amount, notes FROM transactions WHERE id = ?", (bad,)).fetchone()
    assert row["amount"] == -24.70 and row["notes"].startswith(FX_NOTE)


def test_no_nearby_rate_leaves_the_row_alone(conn):
    acct = db.ensure_account(conn, "PayPal", "paypal")
    _add(conn, acct, "2020-01-01", -1.00, -1000.0, "KRW", 1)
    lonely = _add(conn, acct, "2026-06-12", -5000.0, -5000.0, "KRW", 2)
    conn.commit()
    assert convert_unconverted(conn) == []
    assert conn.execute("SELECT amount FROM transactions WHERE id = ?", (lonely,)).fetchone()[0] == -5000.0


def test_dry_run_writes_nothing(conn):
    acct = db.ensure_account(conn, "PayPal", "paypal")
    _add(conn, acct, "2026-06-10", -7.00, -10.0, "USD", 1)
    bad = _add(conn, acct, "2026-06-11", -20.0, -20.0, "USD", 2)
    conn.commit()
    assert convert_unconverted(conn, dry_run=True)[0][3] == -14.0
    assert conn.execute("SELECT amount FROM transactions WHERE id = ?", (bad,)).fetchone()[0] == -20.0
