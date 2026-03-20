"""Business split engine: allocates transaction amounts to businesses for ATO reporting.

Rules:
- Business:* categories → 100% allocated to matching business
- (category + tag) matches a split_rule → configured % allocated
- Results written to transaction_splits table
"""
import sqlite3
from pathlib import Path
from typing import Optional

import yaml


def load_tax_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def compute_splits(
    conn: sqlite3.Connection,
    txn_id: int,
    category_name: Optional[str],
    tags: list[str],
    tax_config: dict,
) -> list[dict]:
    """Compute business splits for a transaction. Returns list of split dicts."""
    if not category_name:
        return []

    splits = []

    for biz in tax_config.get("businesses", []):
        biz_name = biz["name"]

        # Business:* categories are implicitly 100% for the business
        if category_name.startswith("Business:"):
            splits.append({
                "business_name": biz_name,
                "business_pct": 100.0,
            })
            break

        # Check split rules (category + tag match)
        for rule in biz.get("split_rules", []):
            if rule["category"] == category_name and rule.get("tag") in tags:
                splits.append({
                    "business_name": biz_name,
                    "business_pct": rule["business_pct"],
                })
                break

    return splits


def write_splits(
    conn: sqlite3.Connection,
    txn_id: int,
    amount: float,
    splits: list[dict],
) -> None:
    """Write computed splits to the transaction_splits table."""
    for split in splits:
        biz_amount = round(amount * split["business_pct"] / 100.0, 2)
        conn.execute(
            """INSERT OR REPLACE INTO transaction_splits
            (transaction_id, business_name, business_pct, business_amount)
            VALUES (?, ?, ?, ?)""",
            (txn_id, split["business_name"], split["business_pct"], biz_amount),
        )


def apply_splits(
    conn: sqlite3.Connection,
    txn_id: int,
    category_name: Optional[str],
    tags: list[str],
    amount: float,
    tax_config: dict,
) -> None:
    """Compute and write splits for a single transaction."""
    splits = compute_splits(conn, txn_id, category_name, tags, tax_config)
    if splits:
        write_splits(conn, txn_id, amount, splits)


def backfill_splits(conn: sqlite3.Connection, tax_config: dict, fy: Optional[int] = None) -> int:
    """Backfill splits for existing transactions. Returns count of splits created."""
    fy_int = fy or tax_config.get("financial_year", 2025)
    fy_start = f"{fy_int - 1}-07-01"
    fy_end = f"{fy_int}-06-30"

    # Clear existing splits for this FY
    conn.execute("""
        DELETE FROM transaction_splits WHERE transaction_id IN (
            SELECT id FROM transactions WHERE date >= ? AND date <= ?
        )
    """, (fy_start, fy_end))

    # Get all transactions in the FY with their categories and tags
    rows = conn.execute("""
        SELECT t.id, t.amount, c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.date >= ? AND t.date <= ?
          AND t.is_transfer = 0
    """, (fy_start, fy_end)).fetchall()

    count = 0
    for row in rows:
        txn_id = row["id"]
        amount = row["amount"]
        category_name = row["category_name"]

        # Get tags for this transaction
        tag_rows = conn.execute(
            "SELECT tag FROM transaction_tags WHERE transaction_id = ?", (txn_id,)
        ).fetchall()
        tags = [t["tag"] for t in tag_rows]

        splits = compute_splits(conn, txn_id, category_name, tags, tax_config)
        if splits:
            write_splits(conn, txn_id, amount, splits)
            count += len(splits)

    conn.commit()
    return count
