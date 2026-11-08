"""Repair two ING account-routing defects found by `ledger reconcile`.

1. **FY23 CSV exports filed under the mortgage.** ING CSV exports carry no account
   number, and four FY23 exports ("personal Transactions.csv", ...) matched
   no file_prefix, so they fell back to a default account that was later renamed
   "ING Orange Advantage" (the loan). Every one of those rows is already in the
   right everyday/savings account from the quarterly PDF statements, so they are
   pure duplicates. Before deleting each one, the work done on it is carried to
   its PDF twin: tags, a shared-expense marking, and the CSV's description (which
   holds the payer's memo, e.g. "Rent 20-27 Mar", that the PDF drops) is kept in
   the twin's notes.

2. **Two loan splits merged into one account.** Both splits' statements matched
   the loan's file prefix, so reconcile compared one loan's closing balance
   against the other's. Rows from the split's statements (files starting with
   --split-prefix) move to --split-account, with their dedup hash recomputed for
   the new account from the archived statement (identity includes the account).
   Add the split account to config/accounts.yaml with that file_prefix first.

Neither changes a tax figure: loan accounts are excluded from income/expense
summaries, and no duplicated row carries a rental-mapped tag or category.
The script prints the FY tax-relevant totals before and after to prove it.

Usage:  python scripts/repair_ing_account_routing.py --loan-account NAME \
            --split-account NAME --split-prefix PREFIX [--apply]
"""
import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl import db  # noqa: E402
from etl.engine import row_identity  # noqa: E402
from etl.parsers.ing_pdf import INGPDFParser  # noqa: E402

ARCHIVE = ROOT / "data" / "archive" / "ing"
RECEIPT = re.compile(r"Receipt\s+0*(\d+)", re.I)


def receipt(desc: str) -> int | None:
    m = RECEIPT.search(desc or "")
    return int(m.group(1)) if m else None


def fy_totals(conn) -> list[tuple]:
    """Totals every tax summary reads: non-transfer, non-loan, by FY and category."""
    return conn.execute("""
        SELECT CASE WHEN substr(t.date,6,2) >= '07' THEN CAST(substr(t.date,1,4) AS INT)+1
                    ELSE CAST(substr(t.date,1,4) AS INT) END AS fy,
               c.name, ROUND(SUM(t.amount), 2)
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.is_transfer = 0 AND a.account_type NOT IN ('loan')
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()


def rental_totals(conn) -> list[tuple]:
    """Totals the rental schedule reads by category/tag (it does NOT exclude loans)."""
    return conn.execute("""
        SELECT substr(t.date,1,4), COALESCE(c.name,''), COALESCE(tt.tag,''), ROUND(SUM(t.amount),2)
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN transaction_tags tt ON tt.transaction_id = t.id
        WHERE t.is_transfer = 0 AND (c.name IN ('Mortgage','Strata','Rental: Maintenance',
              'Rental: Other','Airbnb Income') OR tt.tag LIKE 'rental-%'
              OR tt.tag IN ('council-rates','water','airbnb-improvement'))
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """).fetchall()


def plan_duplicates(conn, loan_id: int):
    dups = conn.execute("""
        SELECT t.*, r.source_file FROM transactions t
        JOIN raw_imports r ON r.transaction_id = t.id
        WHERE t.account_id = ? AND r.source_file LIKE '%/ing-csv/%'
        ORDER BY t.date, t.id
    """, (loan_id,)).fetchall()

    by_file: dict[str, list] = {}
    for d in dups:
        by_file.setdefault(d["source_file"], []).append(d)

    pairs, problems = [], []
    for f, rows in by_file.items():
        # The file's true account is whichever non-loan account holds its rows.
        votes = conn.execute(f"""
            SELECT o.account_id, COUNT(*) FROM transactions o
            JOIN accounts a ON a.id = o.account_id AND a.account_type != 'loan'
            WHERE (o.date, o.amount) IN ({','.join('(?,?)' for _ in rows)})
            GROUP BY o.account_id ORDER BY 2 DESC LIMIT 1
        """, [v for r in rows for v in (r["date"], r["amount"])]).fetchone()
        target = votes[0]
        used: set[int] = set()
        for d in rows:
            cands = [c for c in conn.execute(
                "SELECT * FROM transactions WHERE account_id=? AND date=? AND amount=? ORDER BY id",
                (target, d["date"], d["amount"])) if c["id"] not in used]
            rc = receipt(d["description"])
            by_rc = [c for c in cands if rc is not None and receipt(c["description"]) == rc]
            pick = (by_rc or cands or [None])[0]
            if pick is None:
                problems.append((f, d["id"], d["date"], d["amount"], d["description"]))
                continue
            used.add(pick["id"])
            pairs.append((d, pick))
        print(f"  {Path(f).name}: {len(rows)} rows -> account {target}")
    return pairs, problems


def apply_duplicates(conn, pairs) -> dict:
    n = {"tags": 0, "shared": 0, "notes": 0, "category_diffs": 0}
    for d, o in pairs:
        # "interest" is excluded: the tagger matched it on the CSV's "Savings Maxi"
        # wording for round-up transfers (160 rows), which is not interest.
        n["tags"] += conn.execute(
            "INSERT OR IGNORE INTO transaction_tags(transaction_id, tag) "
            "SELECT ?, tag FROM transaction_tags WHERE transaction_id = ? AND tag != 'interest'",
            (o["id"], d["id"])).rowcount
        if conn.execute("SELECT 1 FROM shared_expenses WHERE transaction_id=?", (o["id"],)).fetchone() is None:
            n["shared"] += conn.execute(
                "UPDATE shared_expenses SET transaction_id=? WHERE transaction_id=?",
                (o["id"], d["id"])).rowcount
        memo = d["description"]
        if d["notes"]:
            memo = f"{memo} | {d['notes']}"
        if memo != o["description"] and not o["notes"]:
            conn.execute("UPDATE transactions SET notes=? WHERE id=?",
                         (f"ING CSV export: {memo}", o["id"]))
            n["notes"] += 1
        if d["category_id"] != o["category_id"] and (d["category_confidence"] or 0) >= 1.0:
            n["category_diffs"] += 1  # a manual category on the duplicate only; reported, not copied
        for table in ("transaction_tags", "transaction_splits", "work_trip_expenses",
                      "shared_expenses", "raw_imports"):
            conn.execute(f"DELETE FROM {table} WHERE transaction_id=?", (d["id"],))
        conn.execute("DELETE FROM transactions WHERE id=?", (d["id"],))
    return n


def move_split(conn, loan_id: int, new_id: int, prefix: str) -> int:
    parser = INGPDFParser()
    moved = 0
    files = conn.execute("""
        SELECT DISTINCT r.source_file FROM raw_imports r JOIN transactions t ON t.id = r.transaction_id
        WHERE t.account_id = ? AND r.source_file LIKE ?
    """, (loan_id, f"%/{prefix}%")).fetchall()
    for (src,) in files:
        statement = parser.parse_statement(ARCHIVE / Path(src).name)
        stored = conn.execute("""
            SELECT t.* FROM transactions t JOIN raw_imports r ON r.transaction_id = t.id
            WHERE t.account_id = ? AND r.source_file = ? ORDER BY t.id
        """, (loan_id, src)).fetchall()
        remaining = list(stored)
        for row in statement.rows:
            hit = next((s for s in remaining if s["date"] == row.date and s["description"] == row.description
                        and round(s["amount"], 2) == round(row.amount, 2)), None)
            if hit is None:
                raise SystemExit(f"{Path(src).name}: statement row not in ledger: {row.date} {row.description} {row.amount}")
            remaining.remove(hit)
            conn.execute("UPDATE transactions SET account_id=?, dedup_hash=? WHERE id=?",
                         (new_id, row_identity(statement, row, new_id), hit["id"]))
            moved += 1
        if remaining:
            raise SystemExit(f"{Path(src).name}: {len(remaining)} ledger rows not on the statement")
        print(f"  {Path(src).name}: {len(stored)} rows moved")
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan-account", required=True)
    ap.add_argument("--split-account", required=True)
    ap.add_argument("--split-prefix", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = db.get_connection()
    conn.execute("PRAGMA foreign_keys=ON")
    loan_id = conn.execute("SELECT id FROM accounts WHERE name=?", (args.loan_account,)).fetchone()[0]
    before_fy, before_rental = fy_totals(conn), rental_totals(conn)

    print("1. FY23 ING CSV rows in the loan account")
    pairs, problems = plan_duplicates(conn, loan_id)
    print(f"  {len(pairs)} paired with their PDF twin, {len(problems)} unpaired")
    for p in problems:
        print("   UNPAIRED", p)
    if problems:
        raise SystemExit("Refusing: every duplicate must have a twin before anything is deleted.")

    if not args.apply:
        print("\nDry run. Re-run with --apply.")
        return

    backup = ROOT / "data" / f"ledger.db.bak-before-ing-routing-{datetime.now():%Y%m%d-%H%M%S}"
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(db.DB_PATH, backup)
    print(f"\nBackup: {backup}")

    try:
        conn.execute("BEGIN")
        n = apply_duplicates(conn, pairs)
        print(f"  deleted {len(pairs)}; carried {n['tags']} tags, {n['shared']} shared-expense marks, "
              f"{n['notes']} memos; {n['category_diffs']} manual-category differences (not copied)")

        print(f"2. Move {args.split_prefix}* statements to {args.split_account}")
        new_id = conn.execute(
            "INSERT INTO accounts (name, source_type, currency, account_type, display) "
            "VALUES (?, 'ing', 'AUD', 'loan', 1)", (args.split_account,)).lastrowid
        print(f"  {move_split(conn, loan_id, new_id, args.split_prefix)} rows moved to account {new_id}")

        after_fy, after_rental = fy_totals(conn), rental_totals(conn)
        if after_fy != before_fy or after_rental != before_rental:
            raise SystemExit("Tax-relevant totals changed -- rolling back.")
        print("  tax-relevant totals unchanged (income/expense by FY and rental schedule inputs)")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    print("Done.")


if __name__ == "__main__":
    main()
