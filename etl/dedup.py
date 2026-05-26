"""Post-ingestion duplicate detection and resolution.

Finds transactions that appear on multiple accounts (e.g. PayPal purchase
also showing on Bankwest as "PAYPAL *MERCHANT") and marks the lower-priority
side as a transfer to prevent double-counting.
"""

import sqlite3
from dataclasses import dataclass


@dataclass
class DuplicatePair:
    """A pair of transactions suspected to be the same purchase."""
    id1: int
    id2: int
    date: str
    amount: float
    desc1: str
    desc2: str
    account1: str
    account2: str
    source_type1: str
    source_type2: str


# Priority order: higher number = keep this side, mark the other as transfer.
# The account with more transaction detail should win.
SOURCE_PRIORITY = {
    "paypal": 10,    # PayPal has merchant detail
    "amex": 8,
    "coles": 8,
    "hsbc": 8,
    "bankwest": 7,
    "airbnb": 6,
    "ing": 5,        # ING shows less detail for credit card payments
}

# Known patterns where one account's description references another account.
# If Bankwest description contains "PAYPAL", the PayPal side has the real detail.
CROSS_ACCOUNT_PATTERNS = [
    # (pattern_in_description, source_type_of_description_holder)
    # If Bankwest says "PAYPAL *...", and PayPal has a matching txn, it's a dup.
    ("PAYPAL", "bankwest"),
    ("PAYPAL", "coles"),
    ("PAYPAL", "hsbc"),
    ("PAYPAL", "ing"),
]


def find_duplicates(conn: sqlite3.Connection) -> list[DuplicatePair]:
    """Find likely duplicate transactions across accounts.

    Criteria:
    - Same date
    - Same amount (within $0.01)
    - Different accounts
    - Neither currently marked as transfer
    - One side's description references the other account (e.g. "PAYPAL *...")
      OR both are from ING (linked account issue)
    """
    rows = conn.execute("""
        SELECT t1.id as id1, t2.id as id2,
               t1.date, t1.amount,
               t1.description as desc1, t2.description as desc2,
               a1.name as account1, a2.name as account2,
               a1.source_type as st1, a2.source_type as st2
        FROM transactions t1
        JOIN transactions t2 ON t1.date = t2.date
            AND ABS(t1.amount - t2.amount) < 0.01
            AND t1.id < t2.id
            AND t1.account_id != t2.account_id
        JOIN accounts a1 ON t1.account_id = a1.id
        JOIN accounts a2 ON t2.account_id = a2.id
        WHERE t1.amount < 0
          AND t1.is_transfer = 0
          AND t2.is_transfer = 0
        ORDER BY t1.date DESC
    """).fetchall()

    dupes = []
    for r in rows:
        pair = DuplicatePair(
            id1=r["id1"], id2=r["id2"],
            date=r["date"], amount=r["amount"],
            desc1=r["desc1"], desc2=r["desc2"],
            account1=r["account1"], account2=r["account2"],
            source_type1=r["st1"], source_type2=r["st2"],
        )
        if _is_likely_duplicate(pair):
            dupes.append(pair)

    return dupes


def _is_likely_duplicate(pair: DuplicatePair) -> bool:
    """Determine if a pair of transactions is likely a duplicate.

    Returns True if one side references the other account, or if both
    are from ING (linked account issue).
    """
    d1 = pair.desc1.upper()
    d2 = pair.desc2.upper()

    # PayPal ↔ bank: bank side says "PAYPAL *..."
    for pattern, source_type in CROSS_ACCOUNT_PATTERNS:
        if pair.source_type2 == source_type and pattern in d2:
            return True
        if pair.source_type1 == source_type and pattern in d1:
            return True

    # ING ↔ ING: same transaction on linked accounts
    if pair.source_type1 == "ing" and pair.source_type2 == "ing":
        # Check for common ING duplicate patterns
        # Same receipt number is a strong signal
        receipt1 = _extract_receipt_number(d1)
        receipt2 = _extract_receipt_number(d2)
        if receipt1 and receipt2 and receipt1 == receipt2:
            return True

        # Same merchant name appearing on both accounts
        # ING linked accounts show the same transaction with slightly different formatting
        merchant1 = _extract_merchant(d1)
        merchant2 = _extract_merchant(d2)
        if merchant1 and merchant2 and (merchant1 in merchant2 or merchant2 in merchant1):
            return True

    return False


def _extract_receipt_number(desc: str) -> str | None:
    """Extract receipt number from ING-style descriptions."""
    import re
    match = re.search(r'RECEIPT\s+(\d{4,})', desc)
    return match.group(1) if match else None


def _extract_merchant(desc: str) -> str | None:
    """Extract merchant name, stripping ING prefixes."""
    import re
    # Remove common ING prefixes
    cleaned = re.sub(
        r'^(EFTPOS PURCHASE|INTL ATMPURCHASE|INTL ATM WITHDRAWAL|'
        r'VISA PURCHASE|VISA CASH WITHDRAW|DIRECT DEBIT|'
        r'INTERNATIONAL TRANSACTION FEE)\s*-?\s*(RECEIPT\s+\d+\s*)?',
        '', desc, flags=re.IGNORECASE
    ).strip()
    # Take first meaningful chunk (at least 4 chars)
    if len(cleaned) >= 4:
        return cleaned[:30]
    return None


def resolve_duplicates(
    conn: sqlite3.Connection,
    dupes: list[DuplicatePair],
    dry_run: bool = False,
) -> int:
    """Mark the lower-priority side of each duplicate pair as a transfer.

    Returns the number of transactions updated.
    """
    transfers_cat_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Transfers'"
    ).fetchone()
    if not transfers_cat_id:
        raise ValueError("'Transfers' category not found")
    transfers_cat_id = transfers_cat_id["id"]

    updated = 0
    for pair in dupes:
        # Determine which side to mark as transfer (lower priority)
        p1 = SOURCE_PRIORITY.get(pair.source_type1, 0)
        p2 = SOURCE_PRIORITY.get(pair.source_type2, 0)

        if p1 >= p2:
            mark_id = pair.id2  # Mark side 2 as transfer
            keep_desc = pair.desc1[:50]
            mark_desc = pair.desc2[:50]
            mark_acct = pair.account2
        else:
            mark_id = pair.id1  # Mark side 1 as transfer
            keep_desc = pair.desc2[:50]
            mark_desc = pair.desc1[:50]
            mark_acct = pair.account1

        if dry_run:
            print(f"  [DRY RUN] {pair.date}  ${abs(pair.amount):>9,.2f}  "
                  f"KEEP: {keep_desc}  |  MARK TRANSFER: {mark_desc} ({mark_acct})")
        else:
            conn.execute(
                "UPDATE transactions SET is_transfer = 1, category_id = ? WHERE id = ?",
                (transfers_cat_id, mark_id),
            )

        updated += 1

    if not dry_run:
        conn.commit()

    return updated
