import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

from etl.models import RawTransaction
from etl import db
from etl.categorizer import Categorizer
from etl.tagger import Tagger, insert_tags
from etl.splitter import apply_splits


def load_payment_patterns(config_path: Path) -> tuple[list[re.Pattern], set[str]]:
    """Load compiled payment patterns and source-of-truth source types from config.

    Returns (patterns, source_of_truth_types).
    Any transaction from a non-source-of-truth account whose description
    matches a pattern is a payment TO the source-of-truth account and
    should be marked as a transfer.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)
    patterns = []
    sot_types = set()
    for acct in config.get("accounts", []):
        if acct.get("source_of_truth"):
            sot_types.add(acct["source_type"])
            for p in acct.get("payment_patterns", []):
                patterns.append(re.compile(p, re.IGNORECASE))
    return patterns, sot_types


def is_payment_to_source_of_truth(txn: RawTransaction, patterns: list[re.Pattern], source_of_truth_types: set[str]) -> bool:
    """Check if a transaction is a payment to a source-of-truth account.

    Only applies to non-source-of-truth source types (e.g. ING).
    Source-of-truth accounts' own transactions are never suppressed.
    """
    if txn.source_type in source_of_truth_types:
        return False

    desc_upper = txn.description.upper()
    return any(p.search(desc_upper) for p in patterns)


def compute_dedup_hash(txn: RawTransaction) -> str:
    """Compute a dedup hash based on source type."""
    if txn.reference_id and txn.reference_id.startswith("basiq:"):
        data = txn.reference_id
    elif txn.source_type == "paypal" and txn.reference_id:
        data = txn.reference_id
    elif txn.source_type == "ing":
        # Include source_file so same-amount transfers between ING accounts don't collide
        source_stem = Path(txn.source_file).stem if txn.source_file else ""
        data = f"{txn.date}|{txn.description}|{txn.amount}|{source_stem}"
    else:
        data = f"{txn.date}|{txn.description}|{txn.amount}"
    return hashlib.sha256(data.encode()).hexdigest()


def normalize_and_insert(
    conn: sqlite3.Connection,
    transactions: list[RawTransaction],
    account_id: int,
    categorizer: Categorizer,
    payment_patterns: list[re.Pattern] | None = None,
    source_of_truth_types: set[str] | None = None,
    tagger: Tagger | None = None,
    tax_config: dict | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Normalize raw transactions, categorize, dedup, and insert into DB.
    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped = 0
    transfers_cat_id = db.get_category_id(conn, "Transfers")

    for txn in transactions:
        dedup_hash = compute_dedup_hash(txn)

        if db.hash_exists(conn, dedup_hash):
            skipped += 1
            continue

        # Source-of-truth check: if this ING transaction is a payment to
        # PayPal/credit card, override to Transfer before categorization
        if payment_patterns and is_payment_to_source_of_truth(txn, payment_patterns, source_of_truth_types or set()):
            category_id = transfers_cat_id
            confidence = 1.0
            is_transfer = True
        else:
            category_id, confidence = categorizer.categorize(txn)
            cat_name = _get_category_name(conn, category_id)
            is_transfer = cat_name == "Transfers"

        if dry_run:
            cat_name = _get_category_name(conn, category_id)
            label = "[TRANSFER] " if is_transfer else ""
            print(f"  [DRY RUN] {label}{txn.date}  {txn.amount:>10.2f}  {txn.description[:40]:<40}  → {cat_name}")
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
            is_transfer=is_transfer,
        )

        if txn_id:
            inserted += 1
            tags = []
            if tagger:
                tags = tagger.get_tags(txn)
                if tags:
                    insert_tags(conn, txn_id, tags)
            if tax_config:
                cat_name = _get_category_name(conn, category_id)
                apply_splits(conn, txn_id, cat_name, tags, txn.amount, tax_config)
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
