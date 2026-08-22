"""Re-derive stored transactions from the statements they came from.

Fixing a parser does not fix rows already in the ledger: those still carry
whatever the parser produced at the time -- a wrong year, a phantom row read from
a rate-change notice, an inverted sign, or nothing at all where a row was dropped.
This re-reads every archived statement with the current parsers and makes the
ledger agree with them.

Matching is deliberate about preserving your work:

1. Exact match on (date, description, amount) -- untouched.
2. Same description and amount, different date -- the date is CORRECTED in place,
   so the category, tags and splits on that row survive. This is the common case
   after a year-inference fix.
2b. Same date and description, amount differing only in SIGN -- corrected in
   place too. A credit that was read as a charge keeps whatever categorisation
   work was done on it.
3. In the ledger but not on the statement -- deleted. The statement is the
   authority for its own rows; anything else the parser once produced (a phantom
   row from a notice line) is not real.
4. On the statement but not in the ledger -- inserted.

A statement that fails contract validation is SKIPPED, not applied: rewriting the
ledger from a statement we cannot parse correctly would replace known-bad data
with unknown-bad data.

Usage:  python scripts/rederive_from_statements.py [--apply] [--source ing]
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl import db  # noqa: E402
from etl.categorizer import Categorizer  # noqa: E402
from etl.tagger import Tagger, insert_tags  # noqa: E402
from etl.engine import row_identity  # noqa: E402
from etl.parsers.amex_csv import AmexCSVParser  # noqa: E402
from etl.parsers.bankwest_csv import BankwestCSVParser  # noqa: E402
from etl.parsers.bankwest_pdf import BankwestPDFParser  # noqa: E402
from etl.parsers.coles_csv import ColesCSVParser  # noqa: E402
from etl.parsers.coles_pdf import ColesCreditPDFParser  # noqa: E402
from etl.parsers.hsbc_csv import HSBCCSVParser  # noqa: E402
from etl.parsers.hsbc_pdf import HSBCPDFParser  # noqa: E402
from etl.parsers.ing_csv import INGCSVParser  # noqa: E402
from etl.parsers.ing_pdf import INGPDFParser  # noqa: E402
from etl.parsers.paypal_csv import PayPalCSVParser  # noqa: E402

PARSERS = {
    "ing": INGPDFParser, "ing-csv": INGCSVParser,
    "hsbc": HSBCPDFParser, "hsbc-csv": HSBCCSVParser,
    "coles": ColesCreditPDFParser, "coles-csv": ColesCSVParser,
    "bankwest": BankwestPDFParser, "bankwest-csv": BankwestCSVParser,
    "amex": AmexCSVParser, "paypal": PayPalCSVParser,
}
ARCHIVE = ROOT / "data" / "archive"


def locate(source_file: str) -> tuple[Path, str] | None:
    """Find a statement and the source it belongs to, wherever it now lives."""
    name = Path(source_file).name
    for source in PARSERS:
        candidate = ARCHIVE / source / name
        if candidate.exists():
            return candidate, source
    original = Path(source_file)
    if original.exists():
        for source in PARSERS:
            if f"/{source}/" in source_file:
                return original, source
    moved = ARCHIVE / "downloads-2026-08-22" / name
    if moved.exists():
        for source in PARSERS:
            if f"/{source}/" in source_file or f"/{source}-" in source_file:
                return moved, source
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--source", help="Limit to one source")
    ap.add_argument("--db", default=str(ROOT / "data" / "ledger.db"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    categorizer = Categorizer(conn, ROOT / "config" / "categories.yaml")
    # A recovered row needs its tags like any other: without them it is missing
    # from every tag-based report.
    tagger = Tagger(ROOT / "config" / "categories.yaml")

    files = [r["source_file"] for r in conn.execute(
        "SELECT DISTINCT source_file FROM raw_imports ORDER BY source_file")]

    stats = Counter()
    actions = {"corrected": [], "deleted": [], "inserted": [], "skipped": [], "missing_file": []}

    for source_file in files:
        found = locate(source_file)
        if not found:
            stats["file_not_found"] += 1
            actions["missing_file"].append(source_file)
            continue
        path, source = found
        if args.source and source != args.source:
            continue

        try:
            statement = PARSERS[source]().parse_statement(path)
        except Exception as exc:
            stats["parse_error"] += 1
            actions["skipped"].append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue

        issues = statement.validate()
        if issues:
            stats["skipped_invalid"] += 1
            actions["skipped"].append(f"{path.name}: {issues[0]}")
            continue

        stored = conn.execute("""
            SELECT t.id, t.account_id, t.date, t.description, t.amount
            FROM transactions t JOIN raw_imports r ON r.transaction_id = t.id
            WHERE r.source_file = ? ORDER BY t.date, t.id""", (source_file,)).fetchall()
        if not stored:
            continue
        account_id = stored[0]["account_id"]

        # A statement often covers more than was taken from it -- a windowed
        # import, or a period another format supplied -- so INSERTING its whole
        # contents would double-count. Only inserts are confined to the span this
        # file already contributed.
        #
        # Matching is NOT confined: a row whose stored year was wrong sits
        # outside that span once corrected, and clipping it away turned a
        # year correction into a deletion of real data.
        covered_from = min(r["date"] for r in stored)
        covered_to = max(r["date"] for r in stored)

        exact = defaultdict(list)
        for row in stored:
            exact[(row["date"], row["description"], round(row["amount"], 2))].append(row)

        unmatched_parsed = []
        for row in statement.rows:
            key = (row.date, row.description, round(row.amount, 2))
            if exact.get(key):
                exact[key].pop()
                stats["unchanged"] += 1
            else:
                unmatched_parsed.append(row)

        leftover_stored = [r for rows in exact.values() for r in rows]

        # A changed date on an otherwise identical row is a correction, not a
        # delete-and-reinsert: correcting in place keeps its category and tags.
        by_desc_amount = defaultdict(list)
        for row in leftover_stored:
            by_desc_amount[(row["description"], round(row["amount"], 2))].append(row)

        # A row whose sign was wrong (a credit read as a charge) is the same
        # transaction, so fix it rather than delete and re-insert it.
        by_date_desc_magnitude = defaultdict(list)
        for row in leftover_stored:
            by_date_desc_magnitude[
                (row["date"], row["description"], round(abs(row["amount"]), 2))].append(row)

        still_missing = []
        for row in unmatched_parsed:
            signed = by_date_desc_magnitude.get(
                (row.date, row.description, round(abs(row.amount), 2)))
            if signed:
                stored_row = signed.pop()
                if stored_row in leftover_stored:
                    leftover_stored.remove(stored_row)
                    by_desc_amount[(stored_row["description"],
                                    round(stored_row["amount"], 2))].remove(stored_row)
                    stats["corrected"] += 1
                    actions["corrected"].append(
                        f"{path.name}: sign {stored_row['amount']:,.2f} -> {row.amount:,.2f}  "
                        f"{row.description[:40]}")
                    if args.apply:
                        conn.execute(
                            "UPDATE transactions SET amount = ?, dedup_hash = ? WHERE id = ?",
                            (row.amount, row_identity(statement, row, account_id),
                             stored_row["id"]))
                    continue

            bucket = by_desc_amount.get((row.description, round(row.amount, 2)))
            if bucket:
                stored_row = bucket.pop()
                leftover_stored.remove(stored_row)
                stats["corrected"] += 1
                actions["corrected"].append(
                    f"{path.name}: {stored_row['date']} -> {row.date}  "
                    f"{row.description[:40]}  {row.amount:,.2f}")
                if args.apply:
                    conn.execute(
                        "UPDATE transactions SET date = ?, dedup_hash = ? WHERE id = ?",
                        (row.date, row_identity(statement, row, account_id), stored_row["id"]))
            else:
                still_missing.append(row)

        for row in leftover_stored:
            stats["deleted"] += 1
            actions["deleted"].append(
                f"{path.name}: {row['date']} {row['description'][:40]} {row['amount']:,.2f}")
            if args.apply:
                conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (row["id"],))
                conn.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (row["id"],))
                # shared_expenses references transactions; leaving it behind
                # orphans the row and breaks the Shared Expenses view.
                conn.execute("DELETE FROM shared_expenses WHERE transaction_id = ?", (row["id"],))
                conn.execute("DELETE FROM raw_imports WHERE transaction_id = ?", (row["id"],))
                conn.execute("DELETE FROM transactions WHERE id = ?", (row["id"],))

        for row in still_missing:
            if not (covered_from <= row.date <= covered_to):
                continue  # outside what this file contributed; not ours to add
            dedup_hash = row_identity(statement, row, account_id)
            if db.hash_exists(conn, dedup_hash):
                continue
            stats["inserted"] += 1
            actions["inserted"].append(
                f"{path.name}: {row.date} {row.description[:40]} {row.amount:,.2f}")
            if args.apply:
                category_id, confidence = categorizer.categorize(
                    _as_txn(statement, row))
                txn_id = db.insert_transaction(
                    conn=conn, account_id=account_id, date=row.date,
                    description=row.description, amount=row.amount,
                    original_amount=row.original_amount, original_currency=row.original_currency,
                    fee=row.fee, category_id=category_id, category_confidence=confidence,
                    reference_id=row.reference_id, source_type=statement.source_type,
                    dedup_hash=dedup_hash, source_file=source_file,
                    raw_data=json.dumps(row.raw, default=str),
                )
                if txn_id is not None:
                    insert_tags(conn, txn_id, tagger.get_tags(_as_txn(statement, row)))

    if args.apply:
        conn.commit()

    print(f"{'APPLIED' if args.apply else 'DRY RUN'}\n")
    for key in ("unchanged", "corrected", "deleted", "inserted",
                "skipped_invalid", "parse_error", "file_not_found"):
        if stats[key]:
            print(f"  {key:18s} {stats[key]}")
    for label in ("corrected", "deleted", "inserted"):
        rows = actions[label]
        if rows:
            print(f"\n{label.upper()} ({len(rows)}) -- first 15:")
            for line in rows[:15]:
                print(f"  {line}")
    if actions["skipped"]:
        print(f"\nSKIPPED, statement does not validate ({len(actions['skipped'])}) -- first 10:")
        for line in actions["skipped"][:10]:
            print(f"  {line}")
    return 0


def _as_txn(statement, row):
    from etl.engine import as_raw_transaction
    return as_raw_transaction(statement, row)


if __name__ == "__main__":
    sys.exit(main())
