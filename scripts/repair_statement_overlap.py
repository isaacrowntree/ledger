"""Make the ledger agree with statements where formats overlap.

A card's PDF statement, its CSV export and an online-banking export describe the
same transactions with different text and often a different date (transaction vs
posting date), so dedup -- keyed on exact text and date -- cannot pair them. Where
two formats were ingested for the same days, `ledger reconcile` shows a drift.
Two failure shapes, both seen in practice:

* **Surplus copies.** A purchase on the statement is held twice or three times:
  once from the PDF and again from a CSV or online export a day apart.
* **Missing rows.** An online export taken mid-cycle omits purchases that had
  not posted yet, and the statement was then ingested clipped to the days after
  the export -- so the rows in between are on neither.

For each statement given, this pairs every statement row with a ledger row of
the same amount (same date first, then up to 4 days apart, preferring rows
already sourced from a PDF statement). Then:

1. A ledger row from a NON-statement source (CSV/online) left unpaired, with the
   same amount as a statement row within 4 days, is a surplus copy. Its tags,
   shared-expense mark, transfer flag and notes are carried to the statement's
   row, then it is deleted. A row sourced from a PDF is never deleted: an
   unpaired PDF row belongs to an adjacent statement (posting lag).
2. A statement row with no ledger row at all is inserted, attributed to that
   statement's file so reconcile counts it there.
3. A ledger row the statement holds but that is stored under another file -- a
   CSV export, or an interim the quarterly supersedes -- is re-attributed to the
   statement (its original file is kept in raw_data). Reconcile attributes rows
   to statements by file, so a year of rows left under one CSV drifted every
   statement around it.

   Pairing never takes a row that a NEIGHBOURING statement holds, even one dated
   inside this period by posting lag: a purchase on the 3rd once "matched" the
   previous statement's identical purchase on the 1st and was never inserted.
   Another PDF's rows are eligible only when that file lies wholly inside this
   statement's period. An export (CSV) pairs with any statement row.

   This also imports a statement or export for days the ledger lacks: every
   row is "missing" and is inserted with the engine's own classification.

4. The stored closing balance of the statement's rows is refreshed from the
   current parser. Reconcile reads that stored value; a parser fixed after
   ingest left stale balances behind (HSBC printed "-$156.47" and the old
   reader dropped the sign).

A statement that fails contract validation is skipped. Dry run by default;
--apply backs up the DB first and prints the per-FY change in non-transfer
totals, since inserting real purchases and removing copies moves spending.

Usage:  python scripts/repair_statement_overlap.py --account NAME --source KEY FILE... [--apply]
"""
import argparse
import json
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl import db  # noqa: E402
from etl.categorizer import Categorizer  # noqa: E402
from etl.engine import as_raw_transaction, classify, row_identity  # noqa: E402
from etl.normalizer import load_payment_patterns  # noqa: E402
from etl.parsers.bankwest_csv import BankwestCSVParser  # noqa: E402
from etl.parsers.bankwest_pdf import BankwestPDFParser  # noqa: E402
from etl.parsers.coles_csv import ColesCSVParser  # noqa: E402
from etl.parsers.coles_pdf import ColesCreditPDFParser  # noqa: E402
from etl.parsers.hsbc_csv import HSBCCSVParser  # noqa: E402
from etl.parsers.hsbc_pdf import HSBCPDFParser  # noqa: E402
from etl.parsers.ing_pdf import INGPDFParser  # noqa: E402
from etl.shared import SharedRules, mark_shared  # noqa: E402
from etl.tagger import Tagger, insert_tags  # noqa: E402

PARSERS = {"bankwest": BankwestPDFParser, "coles": ColesCreditPDFParser, "hsbc": HSBCPDFParser,
           "ing": INGPDFParser, "bankwest-csv": BankwestCSVParser, "coles-csv": ColesCSVParser,
           "hsbc-csv": HSBCCSVParser}
LAG_DAYS = 4


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _is_statement(source_file: str) -> bool:
    # Bank PDFs (statements and interims) are the authority; CSV and online
    # exports are not.
    return source_file.lower().endswith(".pdf")


def _shift(d: str, days: int) -> str:
    return date.fromordinal(date.fromisoformat(d).toordinal() + days).isoformat()


def plan(conn, account_id: int, statement, path: Path):
    rows = statement.rows
    if not rows:
        return [], [], None, []
    start = str(getattr(statement, "period_start", None) or min(r.date for r in rows))
    end = str(getattr(statement, "period_end", None) or max(r.date for r in rows))
    ledger = [dict(r) for r in conn.execute("""
        SELECT t.*, r.source_file FROM transactions t JOIN raw_imports r ON r.transaction_id = t.id
        WHERE t.account_id = ? AND t.date BETWEEN ? AND ? ORDER BY t.date, t.id""",
        (account_id, _shift(start, -LAG_DAYS), _shift(end, LAG_DAYS)))]

    def own(cand):
        return Path(cand["source_file"]).name == path.name

    # Another PDF whose rows lie wholly inside this statement's period re-covers
    # the same days -- an interim the quarterly supersedes -- and its rows are
    # this statement's rows. Any other PDF is a neighbouring statement: its rows
    # are its own, even one dated inside this period by posting lag.
    spans = conn.execute("""
        SELECT r.source_file, MIN(t.date), MAX(t.date) FROM transactions t
        JOIN raw_imports r ON r.transaction_id = t.id
        WHERE t.account_id = ? GROUP BY r.source_file""", (account_id,)).fetchall()
    superseded = {f for f, lo, hi in spans
                  if _is_statement(f) and Path(f).name != path.name and start <= lo and hi <= end}

    def eligible(cand):
        # Pairing with a neighbouring statement's row let a purchase on the 3rd
        # "match" the previous statement's identical purchase on the 1st, so one
        # of them was never inserted.
        if own(cand) or not _is_statement(cand["source_file"]):
            return True
        if not _is_statement(str(path)):
            return True  # an export pairs with whatever statement holds the row
        return cand["source_file"] in superseded

    def rank(cand, row):
        # Same date beats a lagged date; this statement's own rows, then other
        # statements, beat CSV/online copies.
        return (cand["date"] != row.date, not own(cand), not _is_statement(cand["source_file"]),
                _days(cand["date"], row.date), cand["id"])

    # Exact dates are paired across the whole statement BEFORE any lagged
    # match: otherwise a purchase on the 12th takes the ledger's copy of an
    # identical purchase on the 14th, and the 14th is then reported missing.
    free = [c for c in ledger if eligible(c)]
    pairs, unpaired = [], []
    for max_lag in (0, LAG_DAYS):
        unpaired = []
        for row in sorted(rows if max_lag == 0 else pending, key=lambda r: r.date):
            cands = [c for c in free if round(c["amount"], 2) == round(row.amount, 2)
                     and _days(c["date"], row.date) <= max_lag]
            if not cands:
                unpaired.append(row)
                continue
            best = min(cands, key=lambda c: rank(c, row))
            free.remove(best)
            pairs.append((row, best))
        pending = unpaired
    missing = unpaired

    twin_of = {}
    for row, cand in pairs:
        twin_of.setdefault((round(row.amount, 2), row.date), cand)
    surplus = []
    for c in free:
        if _is_statement(c["source_file"]) or not (start <= c["date"] <= end):
            continue
        twins = [cand for row, cand in pairs if round(row.amount, 2) == round(c["amount"], 2)
                 and _days(row.date, c["date"]) <= LAG_DAYS]
        if twins:
            surplus.append((c, min(twins, key=lambda t: _days(t["date"], c["date"]))))
    # Rows this statement holds that are stored under another file (a CSV export,
    # or an interim it supersedes). Reconcile attributes rows to statements by
    # FILE, so a year of rows left under one CSV lands in a single span and
    # drifts every statement around it.
    adopt = [cand for _row, cand in pairs if not own(cand)]
    return surplus, missing, (start, end), adopt


def fy_spend(conn, account_id: int) -> dict:
    out = {}
    for fy, total in conn.execute("""
        SELECT CASE WHEN substr(date,6,2) >= '07' THEN CAST(substr(date,1,4) AS INT)+1
                    ELSE CAST(substr(date,1,4) AS INT) END, ROUND(SUM(amount), 2)
        FROM transactions WHERE account_id = ? AND is_transfer = 0 GROUP BY 1""", (account_id,)):
        out[fy] = total
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--source", required=True, choices=PARSERS)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = db.get_connection()
    account_id = conn.execute("SELECT id FROM accounts WHERE name = ?", (args.account,)).fetchone()[0]
    categorizer = Categorizer(conn, ROOT / "config" / "categories.yaml")
    tagger = Tagger(ROOT / "config" / "categories.yaml")
    shared = SharedRules(ROOT / "config" / "categories.yaml")
    transfers_id = db.get_category_id(conn, "Transfers")
    payment_patterns, sot_types = load_payment_patterns(ROOT / "config" / "accounts.yaml")
    before = fy_spend(conn, account_id)

    if args.apply:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup = ROOT / "data" / f"ledger.db.bak-before-overlap-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(db.DB_PATH, backup)
        print(f"Backup: {backup}")

    totals = defaultdict(int)
    for path in args.files:
        statement = PARSERS[args.source]().parse_statement(path)
        issues = statement.validate()
        if issues:
            print(f"SKIP {path.name}: {issues[0]}")
            continue
        surplus, missing, span, adopt = plan(conn, account_id, statement, path)
        if not _is_statement(str(path)):
            adopt = []  # an export never takes rows away from a statement

        # Rows already attributed to this statement, under whatever path it was ingested from.
        stored = conn.execute("""
            SELECT r.id, r.source_file, r.raw_data FROM raw_imports r
            JOIN transactions t ON t.id = r.transaction_id
            WHERE t.account_id = ? AND r.source_file LIKE ?""",
            (account_id, f"%/{path.name}")).fetchall()
        source_file = stored[0]["source_file"] if stored else str(path)
        stale = []
        if statement.closing_balance is not None:
            for r in stored:
                raw = json.loads(r["raw_data"] or "{}")
                if "closing_balance" in raw and raw["closing_balance"] != str(statement.closing_balance):
                    stale.append((r["id"], raw))

        if not (surplus or missing or stale or adopt):
            continue
        print(f"\n{path.name}  {span[0]}..{span[1]}")
        for c, twin in surplus:
            print(f"  - surplus  {c['date']} {c['amount']:>9.2f}  {c['description'][:40]:40s} "
                  f"[{Path(c['source_file']).name}]  copy of #{twin['id']} {twin['date']}")
        for row in missing:
            print(f"  + missing  {row.date} {row.amount:>9.2f}  {row.description[:40]}")
        if stale:
            print(f"  ~ closing balance {stale[0][1]['closing_balance']} -> {statement.closing_balance} "
                  f"on {len(stale)} rows")
        totals["surplus"] += len(surplus)
        totals["missing"] += len(missing)
        totals["stale"] += len(stale)
        totals["adopt"] += len(adopt)
        if adopt:
            print(f"  ~ {len(adopt)} rows re-attributed to this statement from "
                  f"{sorted({Path(c['source_file']).name for c in adopt})}")
        if not args.apply:
            continue

        for c, twin in surplus:
            conn.execute("INSERT OR IGNORE INTO transaction_tags(transaction_id, tag) "
                         "SELECT ?, tag FROM transaction_tags WHERE transaction_id = ?",
                         (twin["id"], c["id"]))
            if conn.execute("SELECT 1 FROM shared_expenses WHERE transaction_id = ?",
                            (twin["id"],)).fetchone() is None:
                conn.execute("UPDATE shared_expenses SET transaction_id = ? WHERE transaction_id = ?",
                             (twin["id"], c["id"]))
            if c["is_transfer"] and not twin["is_transfer"]:
                conn.execute("UPDATE transactions SET is_transfer = 1 WHERE id = ?", (twin["id"],))
            if c["notes"] and not twin["notes"]:
                conn.execute("UPDATE transactions SET notes = ? WHERE id = ?", (c["notes"], twin["id"]))
            for table in ("transaction_tags", "transaction_splits", "work_trip_expenses",
                          "shared_expenses", "raw_imports"):
                conn.execute(f"DELETE FROM {table} WHERE transaction_id = ?", (c["id"],))
            conn.execute("DELETE FROM transactions WHERE id = ?", (c["id"],))

        for row in missing:
            txn = as_raw_transaction(statement, row)
            # The engine's own rule, so a payment to a card is a transfer here too.
            category_id, confidence, is_transfer = classify(
                conn, txn, categorizer, payment_patterns, sot_types, transfers_id)
            # Exactly what the engine stores: the parser's raw row plus the parsed
            # running balance. Stamping the statement's closing balance onto an
            # export's rows made reconcile read the bank's own sign (-4,296.70 on
            # a card) as a balance in credit.
            raw = dict(txn.raw_data or {})
            txn_id = db.insert_transaction(
                conn=conn, account_id=account_id, date=row.date, description=row.description,
                amount=row.amount, original_amount=row.original_amount,
                original_currency=row.original_currency, fee=row.fee or 0,
                category_id=category_id, category_confidence=confidence,
                reference_id=row.reference_id, source_type=statement.source_type,
                dedup_hash=row_identity(statement, row, account_id),
                source_file=source_file, raw_data=json.dumps(raw, default=str),
                is_transfer=is_transfer)
            if txn_id is None:
                print(f"  ! not inserted (identity already held): {row.date} {row.description}")
                continue
            insert_tags(conn, txn_id, tagger.get_tags(txn))
            split = shared.split_for(txn)
            if split is not None:
                mark_shared(conn, txn_id, split)

        for cand in adopt:
            r = conn.execute("SELECT id, raw_data FROM raw_imports WHERE transaction_id = ?",
                             (cand["id"],)).fetchone()
            raw = json.loads(r["raw_data"] or "{}")
            raw.setdefault("original_source_file", cand["source_file"])
            conn.execute("UPDATE raw_imports SET source_file = ?, raw_data = ? WHERE id = ?",
                         (source_file, json.dumps(raw), r["id"]))

        for raw_id, raw in stale:
            raw["closing_balance"] = str(statement.closing_balance)
            conn.execute("UPDATE raw_imports SET raw_data = ? WHERE id = ?", (json.dumps(raw), raw_id))
        conn.commit()

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {totals['surplus']} surplus copies, "
          f"{totals['missing']} missing rows, {totals['stale']} stale closing balances, "
          f"{totals['adopt']} rows re-attributed")
    if args.apply:
        after = fy_spend(conn, account_id)
        for fy in sorted(set(before) | set(after)):
            delta = round(after.get(fy, 0) - before.get(fy, 0), 2)
            if delta:
                print(f"  FY{fy} non-transfer total on {args.account}: {delta:+,.2f}")


if __name__ == "__main__":
    main()
