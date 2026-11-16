"""Find deposits from people that pay back one of the owner's purchases.

A friend's $60 a few days after a $300 dinner is a fifth of the bill. Filing it
under the purchase's category (Eating Out) makes that category net of what was
paid back, and keeps a business deduction net when someone repaid part of it.

A deposit matches a purchase when it is the whole cost or an even share of it
(1/2 .. 1/6, within 1% or 50c), the purchase was made up to 30 days before or a
week after (14 days for a third or less: small shares fit too much by chance),
and the purchase still has that much unrepaid. A match is clear when the deposit
fits exactly one purchase, or exactly one within 14 days.

Group splits come first: a shared bill (a birthday dinner for twelve) is paid
back by several people within a few days, each sending a common per-head amount
or a multiple of it (a couple pays for two), sometimes with a little extra on
top. Those deposits are all linked to the bill, as long as together they don't
come to more than it cost.

Not repayments, so never matched: the same payer sending the same amount three
or more times (rent or board on a schedule, which fits any purchase of that
size by coincidence), and refunds from Medicare or insurers. Clear ones
are linked automatically; the rest are left for Claude to decide
(`ledger reimbursements --export / --import`).
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from etl.merchant import merchant_key

WINDOW_BEFORE, WINDOW_AFTER, CLEAR_DAYS = 30, 7, 14
SMALL_SHARE_DAYS = 14          # for a third or less of a purchase
SCHEDULE_REPEATS = 3
# A repayment against these changes a deduction, so it is never linked
# automatically. For a jointly owned rental it must not be linked at all: each
# owner claims their ownership share of the cost whoever paid, and etl/rental.py
# applies that share to the category total, so a co-owner's repayment filed
# there would take their half off twice. Co-owner repayments stay in Personal.
TAX_RELEVANT = ("Business:", "Rental:", "Strata", "Mortgage")
NOT_PEOPLE = ("MCARE", "MEDICARE", "BUPA", "MEDIBANK", "NIB ", "HCF", "INSURANCE", "CLAIM", "REFUND")
SHARES = (1, 2, 3, 4, 5, 6)
CONFIDENCE = 0.85
# Repayments are not looked for against these: they are not purchases.
NOT_PURCHASES = ("Transfers", "Personal (non-assessable)", "Mortgage", "Tax", "Investments",
                 "Uncategorized")


@dataclass
class Candidate:
    purchase_id: int
    share: int            # the deposit is 1/share of the purchase
    days: int             # deposit date minus purchase date
    category_id: int
    description: str
    cost: float


def _tolerance(amount: float) -> float:
    return max(0.01 * amount, 0.5)


def _deposits(conn):
    """Money in from people, not yet linked, and not on a schedule."""
    rows = conn.execute(
        """SELECT t.id, t.date, t.amount, t.description FROM transactions t
           JOIN categories c ON c.id = t.category_id
           WHERE t.amount > 0 AND t.is_transfer = 0
             AND c.name IN ('Personal (non-assessable)', 'Uncategorized')
             -- Rows the owner confirmed as Personal count too: confirming said "money
             -- from a person", and repayments are to offset what they repay.
             AND COALESCE(t.notes, '') NOT LIKE 'partner: house equity%'
             AND COALESCE(t.notes, '') NOT LIKE 'partner: money for her shares%'
             AND NOT EXISTS (SELECT 1 FROM reimbursements r WHERE r.deposit_id = t.id)
           ORDER BY t.date, t.id""").fetchall()
    rows = [r for r in rows if not any(w in r["description"].upper() for w in NOT_PEOPLE)]
    return [r for r in rows if not _on_schedule(conn, r)]


def _on_schedule(conn, dep) -> bool:
    """The same payer has sent this same amount several times: rent or board, not a repayment."""
    payer = merchant_key(dep["description"])
    tol = _tolerance(dep["amount"])
    same = conn.execute("SELECT description FROM transactions WHERE amount BETWEEN ? AND ?",
                        (dep["amount"] - tol, dep["amount"] + tol)).fetchall()
    return sum(1 for r in same if merchant_key(r[0]) == payer) >= SCHEDULE_REPEATS


def _unrepaid(conn, purchase_id: int, cost: float) -> float:
    repaid = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM reimbursements WHERE purchase_id = ?",
                          (purchase_id,)).fetchone()[0]
    return cost - repaid


def candidates(conn, deposit) -> list[Candidate]:
    d = date.fromisoformat(deposit["date"])
    lo = (d - timedelta(days=WINDOW_BEFORE)).isoformat()
    hi = (d + timedelta(days=WINDOW_AFTER)).isoformat()
    placeholders = ",".join("?" * len(NOT_PURCHASES))
    rows = conn.execute(
        f"""SELECT t.id, t.date, t.amount, t.description, t.category_id FROM transactions t
            JOIN categories c ON c.id = t.category_id
            WHERE t.amount < 0 AND t.is_transfer = 0 AND t.date BETWEEN ? AND ?
              AND COALESCE(c.is_income, 0) = 0     -- a refund out of income is not a purchase
              AND c.name NOT IN ({placeholders})""", (lo, hi, *NOT_PURCHASES)).fetchall()
    out = []
    amount, tol = deposit["amount"], _tolerance(deposit["amount"])
    for p in rows:
        cost = -p["amount"]
        days = (d - date.fromisoformat(p["date"])).days
        for share in SHARES:
            if share >= 3 and abs(days) > SMALL_SHARE_DAYS:
                break
            if abs(cost / share - amount) <= tol:
                if _unrepaid(conn, p["id"], cost) + tol >= amount:
                    out.append(Candidate(p["id"], share, days,
                                         p["category_id"], p["description"], cost))
                break
    out.sort(key=lambda c: (abs(c.days), c.share))
    return out


def clear_match(cands: list[Candidate]) -> Candidate | None:
    if len(cands) == 1:
        return cands[0]
    near = [c for c in cands if abs(c.days) <= CLEAR_DAYS]
    return near[0] if len(near) == 1 else None


def link(conn, deposit_id: int, purchase_id: int, by: str) -> None:
    """Record the repayment and file the deposit under the purchase's category."""
    dep = conn.execute("SELECT amount FROM transactions WHERE id = ?", (deposit_id,)).fetchone()
    p = conn.execute("SELECT date, amount, description, category_id FROM transactions WHERE id = ?",
                     (purchase_id,)).fetchone()
    share = round(-p["amount"] / dep["amount"])
    conn.execute("INSERT INTO reimbursements (deposit_id, purchase_id, amount, matched_by) VALUES (?, ?, ?, ?)",
                 (deposit_id, purchase_id, dep["amount"], by))
    part = "repays" if share <= 1 else f"repays 1/{share} of"
    note = f"{part} {p['date']} {p['description'][:40].strip()}"
    conn.execute(
        """UPDATE transactions SET category_id = ?, is_transfer = 0,
               category_confidence = MAX(COALESCE(category_confidence, 0), ?),
               notes = CASE WHEN COALESCE(notes, '') = '' THEN ? ELSE notes || ' · ' || ? END
           WHERE id = ?""", (p["category_id"], CONFIDENCE, note, note, deposit_id))


def _category_name(conn, category_id: int) -> str:
    row = conn.execute("SELECT name FROM categories WHERE id = ?", (category_id,)).fetchone()
    return row[0] if row else ""


GROUP_DAYS_BEFORE, GROUP_DAYS_AFTER = 1, 4     # deposits from the day before to 4 days after
GROUP_MIN_EXACT = 3     # at least this many payers send exactly the share (or a multiple)
GROUP_MIN_COST = 100.0
GROUP_EXTRA = 0.2       # a payer may add up to 20% of the share on top (a tip)
GROUP_MIN_SHARE = 10.0
GROUP_MAX_PEOPLE = 25
# What groups actually share. Insurance premiums, rent and cash are left to the
# one-to-one matching, where a coincidence needs an exact amount.
SHAREABLE = ("Eating Out", "Entertainment", "Travel", "Coffee", "Groceries", "Fitness",
             "Public Transport", "Car")


def _fits(amount: float, unit: float) -> tuple[bool, bool]:
    """(fits, exactly): amount is 1-3 shares, exactly or with a little extra on top."""
    for m in (1, 2, 3):
        over = amount - m * unit
        if abs(over) <= max(0.01 * m * unit, 0.5):
            return True, True
        if 0 < over <= GROUP_EXTRA * unit:
            return True, False
    return False, False


def group_matches(conn) -> list[tuple[int, list]]:
    """(purchase id, deposits) for bills paid back by a group, each paying a common share."""
    deposits = _deposits(conn)
    placeholders = ",".join("?" * len(NOT_PURCHASES))
    bills = conn.execute(
        f"""SELECT t.id, t.date, t.amount, t.category_id FROM transactions t
            JOIN categories c ON c.id = t.category_id
            WHERE t.amount <= ? AND t.is_transfer = 0 AND c.name NOT IN ({placeholders})""",
        (-GROUP_MIN_COST, *NOT_PURCHASES)).fetchall()
    options = []        # (exact payers, bill id, members)
    for bill in bills:
        if _category_name(conn, bill["category_id"]) not in SHAREABLE:
            continue
        d = date.fromisoformat(bill["date"])
        near = [x for x in deposits
                if -GROUP_DAYS_BEFORE <= (date.fromisoformat(x["date"]) - d).days <= GROUP_DAYS_AFTER]
        if len(near) < GROUP_MIN_EXACT:
            continue
        cost = -bill["amount"]
        for unit in {round(x["amount"], 2) for x in near}:
            if unit < GROUP_MIN_SHARE or not GROUP_MIN_EXACT + 1 <= cost / unit <= GROUP_MAX_PEOPLE:
                continue            # the owner pays a share too, hence the + 1
            members, exact_payers = [], set()
            for x in near:
                fits, exactly = _fits(x["amount"], unit)
                if fits:
                    members.append(x)
                    if exactly:
                        exact_payers.add(merchant_key(x["description"]))
            if len(exact_payers) < GROUP_MIN_EXACT:
                continue
            if sum(x["amount"] for x in members) > cost - unit + 0.01 * cost:
                continue
            options.append((len(exact_payers), len(members), bill["id"], members))
    # The bill that explains the most payers exactly wins its deposits; then the next.
    options.sort(key=lambda o: (-o[0], -o[1]))
    taken: set[int] = set()
    out = []
    for _, _, bill_id, members in options:
        if any(x["id"] in taken for x in members) or bill_id in {b for b, _ in out}:
            continue
        taken |= {x["id"] for x in members}
        out.append((bill_id, members))
    return out


@dataclass
class Result:
    linked: int
    ambiguous: list           # (deposit row, candidates) for Claude
    unmatched: int


def match(conn: sqlite3.Connection, dry_run: bool = False) -> Result:
    linked, ambiguous, unmatched = 0, [], 0
    for purchase_id, members in group_matches(conn):
        for dep in members:
            link(conn, dep["id"], purchase_id, "auto")
            linked += 1
    for dep in _deposits(conn):
        cands = candidates(conn, dep)
        if not cands:
            unmatched += 1
            continue
        best = clear_match(cands)
        if best is not None and _category_name(conn, best.category_id).startswith(TAX_RELEVANT):
            best = None
        if best is None:
            ambiguous.append((dep, cands))
            continue
        linked += 1
        if not dry_run:
            link(conn, dep["id"], best.purchase_id, "auto")
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return Result(linked, ambiguous, unmatched)


def run(export: str | None = None, import_: str | None = None, dry_run: bool = False) -> None:
    """`ledger reimbursements`: link clear matches; export or import the rest for Claude."""
    import json
    from pathlib import Path
    from etl import db
    conn = db.get_connection()
    db.init_db(conn)
    if import_:
        answers = json.loads(Path(import_).read_text())
        ask = answers.pop("ask_owner", [])
        done = skipped = 0
        for dep_id, purchase_id in answers.items():
            dep = conn.execute("SELECT id, date, amount, description FROM transactions WHERE id = ?",
                               (int(dep_id),)).fetchone()
            already = conn.execute("SELECT 1 FROM reimbursements WHERE deposit_id = ?", (int(dep_id),)).fetchone()
            if purchase_id is None or dep is None or already:
                skipped += 1
                continue
            if purchase_id not in {c.purchase_id for c in candidates(conn, dep)}:
                # Taken by a group split or another link since the export.
                print(f"deposit {dep_id}: purchase {purchase_id} is no longer a candidate; skipped")
                skipped += 1
                continue
            if not dry_run:
                link(conn, dep["id"], purchase_id, "claude")
            done += 1
        if not dry_run:
            conn.commit()
        print(f"{done} linked, {skipped} left as they are" + (" (dry run)" if dry_run else ""))
        if ask:
            print(f"{len(ask)} for the owner to decide (tax-relevant): {ask}")
        return
    result = match(conn, dry_run=dry_run or bool(export))
    print(f"{result.linked} clear matches {'found' if dry_run or export else 'linked'}, "
          f"{len(result.ambiguous)} ambiguous, {result.unmatched} with no matching purchase")
    if export:
        names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM categories")}
        Path(export).write_text(json.dumps({
            "instructions": (
                "Each deposit from a person could repay more than one of the owner's purchases. "
                "Pick the purchase it repays, or null if none clearly fits (a round amount near "
                "several purchases, or a deposit that is a gift or a loan). A purchase in a "
                "Business, Rental, Strata or Mortgage category changes a tax deduction: answer null "
                "unless it is plainly a repayment of that bill, and list it under \"ask_owner\". Answer "
                "{\"<deposit id>\": <purchase id or null>}."),
            "deposits": [{
                "id": d["id"], "date": d["date"], "amount": d["amount"], "description": d["description"],
                "candidates": [{"purchase_id": c.purchase_id, "days_before": c.days, "share": f"1/{c.share}",
                                "cost": c.cost, "category": names[c.category_id],
                                "description": c.description} for c in cands[:8]]}
                for d, cands in result.ambiguous],
        }, indent=1))
        print(f"Exported {len(result.ambiguous)} ambiguous deposits to {export}")
