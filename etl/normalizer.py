import hashlib
import sqlite3
from typing import Optional

from etl.models import RawTransaction
from etl import db
from etl.categorizer import Categorizer


def compute_dedup_hash(txn: RawTransaction) -> str:
    """Compute a dedup hash based on source type."""
    if txn.source_type == "paypal" and txn.reference_id:
        data = txn.reference_id
    else:
        # For ING/Airbnb: hash of date + description + amount
        data = f"{txn.date}|{txn.description}|{txn.amount}"
    return hashlib.sha256(data.encode()).hexdigest()


def normalize_and_insert(
    conn: sqlite3.Connection,
    transactions: list[RawTransaction],
    account_id: int,
    categorizer: Categorizer,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Normalize raw transactions, categorize, dedup, and insert into DB.
    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped = 0

    for txn in transactions:
        dedup_hash = compute_dedup_hash(txn)

        if db.hash_exists(conn, dedup_hash):
            skipped += 1
            continue

        category_id, confidence = categorizer.categorize(txn)

        if dry_run:
            cat_name = _get_category_name(conn, category_id)
            print(f"  [DRY RUN] {txn.date}  {txn.amount:>10.2f}  {txn.description[:40]:<40}  → {cat_name}")
            inserted += 1
            continue

        txn_id = db.insert_transaction(
            conn=conn,
            account_id=account_id,
            date=txn.date,
            description=txn.description,
            amount=txn.amount,
            original_amount=txn.original_amount,
            original_currency=txn.original_currency,
            fee=txn.fee,
            category_id=category_id,
            category_confidence=confidence,
            reference_id=txn.reference_id,
            source_type=txn.source_type,
            dedup_hash=dedup_hash,
            source_file=txn.source_file,
            raw_data=txn.raw_data_json(),
        )

        if txn_id:
            inserted += 1
        else:
            skipped += 1

    if not dry_run:
        conn.commit()

    return inserted, skipped


def _get_category_name(conn: sqlite3.Connection, category_id: Optional[int]) -> str:
    if category_id is None:
        return "Uncategorized"
    row = conn.execute("SELECT name FROM categories WHERE id = ?", (category_id,)).fetchone()
    return row["name"] if row else "Uncategorized"
