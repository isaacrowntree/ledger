import sqlite3
from etl.models import RawTransaction
from etl import db


def extract_fx_rates(transactions: list[RawTransaction], conn: sqlite3.Connection) -> None:
    """Extract and store FX rates from transactions that have currency conversion data."""
    for txn in transactions:
        if (
            txn.original_amount
            and txn.original_currency
            and txn.original_currency != "AUD"
            and txn.amount != 0
            and txn.original_amount != 0
        ):
            rate = abs(txn.amount) / abs(txn.original_amount)
            db.insert_currency_rate(
                conn,
                date=txn.date,
                from_currency=txn.original_currency,
                to_currency="AUD",
                rate=round(rate, 6),
            )


# A row PayPal exported with no AUD conversion keeps its foreign amount in the
# AUD column (a ₩24,700 Uber ride reads as $24,700). These are converted at the
# rate implied by converted rows in the same currency nearest in date.
FX_WINDOW_DAYS = 120
FX_NOTE = "FX estimated"


def convert_unconverted(conn: sqlite3.Connection, dry_run: bool = False) -> list[tuple]:
    """Convert foreign-currency rows still holding their foreign amount.

    Returns (id, currency, foreign amount, AUD amount, rate) for each row converted.
    Rows with no converted neighbour within FX_WINDOW_DAYS are left alone.
    """
    from datetime import date
    from statistics import median

    known = conn.execute(
        """SELECT date, original_currency AS cur, ABS(amount) / ABS(original_amount) AS rate
           FROM transactions
           WHERE original_currency IS NOT NULL AND original_currency != 'AUD'
             AND original_amount != 0 AND ABS(amount - original_amount) >= 0.005""").fetchall()
    todo = conn.execute(
        """SELECT id, date, amount, original_currency AS cur FROM transactions
           WHERE original_currency IS NOT NULL AND original_currency != 'AUD'
             AND ABS(amount - original_amount) < 0.005 AND amount != 0""").fetchall()
    out = []
    for r in todo:
        d = date.fromisoformat(r["date"])
        near = sorted((abs((date.fromisoformat(k["date"]) - d).days), k["rate"])
                      for k in known if k["cur"] == r["cur"])
        near = [rate for days, rate in near if days <= FX_WINDOW_DAYS][:5]
        if not near:
            continue
        rate = median(near)
        aud = round(r["amount"] * rate, 2)
        out.append((r["id"], r["cur"], r["amount"], aud, rate))
        if not dry_run:
            note = f"{FX_NOTE}: {r['cur']} {abs(r['amount']):,.2f} at {rate:.6g}"
            conn.execute(
                """UPDATE transactions SET amount = ?,
                       notes = CASE WHEN COALESCE(notes, '') = '' THEN ? ELSE notes || ' · ' || ? END
                   WHERE id = ?""", (aud, note, note, r["id"]))
    if not dry_run:
        conn.commit()
    return out
