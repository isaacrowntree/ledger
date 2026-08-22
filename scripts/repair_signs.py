"""Repair transactions stored with an inverted sign.

Two defects, both found by `ledger reconcile` and both confirmed against the
running balance printed on the statements:

1. **Loan interest and fees** (ING Orange Advantage / mortgage splits). Interest
   and facility fees always INCREASE the amount owed, so they must be negative.
   The ING statements come in two layouts -- one printing balances as negative
   (asset-style) and one as positive (owing-style) -- and interest was captured
   from a different column in each, landing positive in both. Confirmed by the
   balance chain: each of these rows is off by exactly twice its own amount.

2. **HSBC credits.** HSBC marks a credit with a minus BEFORE the dollar sign
   ("BPAY PAYMENT -$4,267.00"). The old amount regex could not see a minus across
   the "$", so credits were read as purchases and negated. The parser was fixed;
   this repairs rows ingested before that.

Amounts are part of the dedup hash, so run
scripts/rederive_from_statements.py --apply afterwards to bring the stored hashes
back in step (this script reminds you).

Superseded in practice by rederive_from_statements.py, which corrects signs from
the statements themselves. Kept as the record of a one-off repair that has been
applied; re-running it now finds nothing.

Usage:  python scripts/repair_signs.py [--apply]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ledger.db"

# Interest and fees on a loan always increase the debt -> must be negative.
LOAN_CHARGE_SQL = """
    SELECT t.id, t.date, t.description, t.amount, a.name
    FROM transactions t JOIN accounts a ON a.id = t.account_id
    WHERE a.account_type = 'loan' AND t.amount > 0
      AND (UPPER(t.description) LIKE '%INTEREST CHARGE%'
        OR UPPER(t.description) LIKE '%FACILITY FEE%'
        OR UPPER(t.description) LIKE '%SETTLEMENT FEE%')
    ORDER BY t.date
"""

# HSBC prints "-$" before the amount on credits; those rows were stored negative.
HSBC_CREDIT_SQL = r"""
    SELECT t.id, t.date, t.description, t.amount, a.name
    FROM transactions t JOIN accounts a ON a.id = t.account_id
    WHERE a.source_type = 'hsbc' AND t.amount < 0
      AND t.description LIKE '%-$%'
    ORDER BY t.date
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the corrected signs")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    groups = [
        ("Loan interest & fees (should be negative -- they increase the debt)", LOAN_CHARGE_SQL),
        ("HSBC credits (should be positive -- they reduce the balance owed)", HSBC_CREDIT_SQL),
    ]

    total = 0
    to_flip = []
    for title, sql in groups:
        rows = conn.execute(sql).fetchall()
        if not rows:
            print(f"\n{title}\n  nothing to repair")
            continue
        swing = sum(-2 * r["amount"] for r in rows)
        print(f"\n{title}")
        print(f"  {len(rows)} rows, net change {swing:+,.2f}")
        by_year: dict[str, list] = {}
        for r in rows:
            by_year.setdefault(r["date"][:4], []).append(r)
        for year in sorted(by_year):
            yr = by_year[year]
            print(f"    {year}: {len(yr):3d} rows  {sum(-2 * x['amount'] for x in yr):>14,.2f}")
        to_flip.extend(r["id"] for r in rows)
        total += len(rows)

    if not to_flip:
        return 0

    if not args.apply:
        print(f"\nDry run -- {total} rows would be corrected. Re-run with --apply.")
        return 0

    with conn:
        conn.executemany("UPDATE transactions SET amount = -amount WHERE id = ?",
                         [(i,) for i in to_flip])
    print(f"\nCorrected {total} rows.")
    print("Amounts feed the dedup hash -- now run:")
    print("    python scripts/rederive_from_statements.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
