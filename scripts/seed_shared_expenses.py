#!/usr/bin/env python3
"""One-time script to seed shared_expenses with existing utility, strata, car, and council transactions.
All seeded items are marked as settled since they're paid up to now.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ledger.db"


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Ensure shared_expenses table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_expenses (
            id INTEGER PRIMARY KEY,
            transaction_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
            split_pct REAL NOT NULL DEFAULT 50.0,
            is_settled INTEGER NOT NULL DEFAULT 0,
            settled_date TEXT
        )
    """)

    # Categories to include wholesale
    shared_categories = ("Utilities", "Strata", "Car")

    # Find transactions in these categories (expenses only, amount < 0)
    rows = conn.execute("""
        SELECT t.id, t.date, t.description, t.amount, c.name as category
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE c.name IN (?, ?, ?)
          AND t.amount < 0
        ORDER BY t.date
    """, shared_categories).fetchall()

    # Also find Bayside council payments (may be in a different category)
    council_rows = conn.execute("""
        SELECT t.id, t.date, t.description, t.amount, c.name as category
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE UPPER(t.description) LIKE '%BAYSIDE%CNCL%'
          AND t.amount < 0
          AND t.id NOT IN (SELECT transaction_id FROM shared_expenses)
        ORDER BY t.date
    """).fetchall()

    all_rows = list(rows) + list(council_rows)
    seen_ids = set()
    inserted = 0
    skipped = 0

    for r in all_rows:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        try:
            conn.execute(
                """INSERT INTO shared_expenses (transaction_id, split_pct, is_settled, settled_date)
                   VALUES (?, 50.0, 1, '2026-03-23')""",
                (r["id"],),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()

    total = conn.execute("SELECT COUNT(*) as n FROM shared_expenses").fetchone()["n"]
    total_amount = conn.execute("""
        SELECT COALESCE(SUM(ABS(t.amount) * se.split_pct / 100.0), 0) as total
        FROM shared_expenses se
        JOIN transactions t ON se.transaction_id = t.id
    """).fetchone()["total"]

    print(f"Inserted: {inserted}, Skipped (already existed): {skipped}")
    print(f"Total shared expenses: {total}")
    print(f"Total shared amount (50% splits): ${total_amount:,.2f}")

    conn.close()


if __name__ == "__main__":
    main()
