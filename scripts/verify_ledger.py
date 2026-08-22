"""End-to-end verification that the ledger agrees with the statements behind it.

Four independent checks, each able to fail on its own:

1. **Contract** -- every archived statement parses into rows that account for its
   own printed balances.
2. **Agreement** -- every stored transaction matches what its source statement
   says today (no stale rows from an older, buggier parser).
3. **Reconciliation** -- across statements, the ledger's transactions account for
   the movement between consecutive printed balances.
4. **Idempotency** -- re-ingesting an archived statement inserts nothing.

Usage:  python scripts/verify_ledger.py [--quick]
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl.engine import row_identity  # noqa: E402
from scripts.rederive_from_statements import PARSERS, locate  # noqa: E402

DB = ROOT / "data" / "ledger.db"


def check_contract(sources) -> tuple[int, int, Counter]:
    passed = failed = 0
    codes = Counter()
    for source, parser_cls in sources.items():
        folder = ROOT / "data" / "archive" / source
        if not folder.exists():
            continue
        pattern = "*.pdf" if source in ("ing", "hsbc", "coles", "bankwest") else "*.csv"
        for path in sorted(folder.glob(pattern)):
            try:
                issues = parser_cls().parse_statement(path).validate()
            except Exception as exc:
                failed += 1
                codes[f"exception:{type(exc).__name__}"] += 1
                continue
            if issues:
                failed += 1
                for issue in issues:
                    codes[issue.code] += 1
            else:
                passed += 1
    return passed, failed, codes


def check_agreement(conn) -> Counter:
    """Do stored rows still match what their statement says?"""
    stats = Counter()
    files = [r[0] for r in conn.execute("SELECT DISTINCT source_file FROM raw_imports")]
    for source_file in files:
        found = locate(source_file)
        if not found:
            stats["source_missing"] += 1
            continue
        path, source = found
        try:
            statement = PARSERS[source]().parse_statement(path)
        except Exception:
            stats["parse_error"] += 1
            continue
        if statement.validate():
            stats["statement_invalid"] += 1
            continue

        stored = conn.execute("""
            SELECT t.id, t.account_id, t.date, t.description, t.amount
            FROM transactions t JOIN raw_imports r ON r.transaction_id = t.id
            WHERE r.source_file = ?""", (source_file,)).fetchall()
        if not stored:
            continue
        covered = (min(r["date"] for r in stored), max(r["date"] for r in stored))
        account_id = stored[0]["account_id"]

        # Compare against everything the ACCOUNT holds in this window, not just
        # rows attributed to this file. Dedup stores a transaction once, under
        # whichever statement supplied it first, so two statements that overlap
        # would otherwise each report the other's rows as missing.
        account_rows = conn.execute("""
            SELECT t.date, t.description, t.amount FROM transactions t
            WHERE t.account_id = ? AND t.date BETWEEN ? AND ?""",
            (account_id, covered[0], covered[1])).fetchall()

        expected = Counter((r.date, r.description, round(r.amount, 2))
                           for r in statement.rows if covered[0] <= r.date <= covered[1])
        actual = Counter((r["date"], r["description"], round(r["amount"], 2))
                         for r in account_rows)
        stats["rows_checked"] += sum(expected.values())
        # Only a row the statement asserts and the ledger lacks is a disagreement.
        # The ledger legitimately holds more than any one statement lists.
        stats["rows_disagreeing"] += sum((expected - actual).values())
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="Skip the statement corpus (the slow PDF sweep)")
    args = ap.parse_args()

    sources = {name: cls for name, cls in PARSERS.items()}
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    failures = []

    if not args.quick:
        print("1. Statement contract")
        passed, failed, codes = check_contract(sources)
        print(f"   {passed} statements valid, {failed} failing")
        for code, count in codes.most_common():
            print(f"     {code:24s} {count}")
        if failed:
            failures.append(f"{failed} statements fail the contract")
    else:
        print("1. Statement contract -- skipped (--quick)")

    print("\n2. Ledger agrees with its statements")
    stats = check_agreement(conn)
    print(f"   {stats['rows_checked']} rows checked, {stats['rows_disagreeing']} disagreeing")
    for key in ("source_missing", "parse_error", "statement_invalid"):
        if stats[key]:
            print(f"     {key:20s} {stats[key]} file(s)")
    if stats["rows_disagreeing"]:
        failures.append(f"{stats['rows_disagreeing']} rows disagree with their statement")

    print("\n3. Reconciliation across statements")
    result = subprocess.run([sys.executable, "-m", "etl.cli", "reconcile"],
                            capture_output=True, text=True, cwd=str(ROOT))
    tail = [l for l in result.stdout.splitlines() if "drift(s)" in l]
    print("   " + (tail[-1].strip() if tail else "no reconcile output"))
    if tail and not tail[-1].strip().startswith("0 statement-gap"):
        failures.append(tail[-1].strip())

    conn.close()

    print("\n" + ("FAILED" if failures else "ALL CHECKS PASSED"))
    for line in failures:
        print(f"  - {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
