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
