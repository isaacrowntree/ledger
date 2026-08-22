"""The ingestion engine.

Parsers describe statements (see etl/contract.py); the engine decides what enters
the ledger. It owns three things no parser may redefine:

**Identity.** The dedup key is derived from intrinsic fields only -- the account,
the source type, the date, description and amount, plus the running balance where
the source prints one, plus the row's position among identical rows in its own
file. Never the filename: the same statement re-downloaded under another name must
not double-count. Never a statement-level figure such as a closing balance: that
identifies the file, not the row.

**Idempotency.** Re-ingesting a statement, or ingesting two statements that
overlap in time, inserts only what is genuinely new. This holds within a single
run as well as across runs, so a file and a subset of itself can be ingested
together safely.

**Shared expenses.** Household bills split with a partner are marked here by rule,
so a new statement does not depend on someone remembering to tick them by hand.

**Validation.** A statement is checked against its own printed balances before
anything is written, and a statement that does not add up is refused outright
rather than partially ingested. Silent partial ingestion is the failure mode that
hid missing transactions for months; `force=True` overrides it deliberately and
still reports what was wrong.
"""
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field

from etl import db
from etl.categorizer import Categorizer
from etl.contract import ParsedRow, ParsedStatement, ValidationIssue
from etl.models import RawTransaction
from etl.shared import SharedRules, mark_shared
from etl.tagger import Tagger, insert_tags


@dataclass
class IngestResult:
    source_file: str
    inserted: int = 0
    skipped: int = 0
    dropped_by_window: int = 0
    rejected: bool = False
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected and not self.issues


def row_identity(statement: ParsedStatement, row: ParsedRow, account_id: int) -> str:
    """The dedup key for one row. See the module docstring for what is excluded.

    Self-contained by design: the occurrence index is derived from the row's own
    position in its statement rather than supplied by the caller, so there is no
    way for a call site to compute identity inconsistently.
    """
    occurrence = _occurrence_of(statement, row)
    if statement.source_type == "paypal" and row.reference_id:
        # PayPal gives every transaction a unique id -- the strongest key there is.
        data = f"paypal|{row.reference_id}"
    else:
        data = (f"{account_id}|{statement.source_type}|{row.date}|"
                f"{row.description}|{row.amount:.2f}")
        if row.balance is not None:
            data = f"{data}|{row.balance:.2f}"
    if occurrence:
        data = f"{data}|#{occurrence}"
    return hashlib.sha256(data.encode()).hexdigest()


def _occurrence_of(statement: ParsedStatement, row: ParsedRow) -> int:
    """How many identical rows precede this one within the same statement.

    Sources that print no running balance cannot distinguish two identical
    transactions on one day -- two identical tolls, say -- but both are real. The
    position among identical rows separates them, and is stable across re-ingests
    of the file, so it does not reopen double-counting.
    """
    identity = (row.date, row.description, round(row.amount, 2), row.balance)
    return sum(
        1 for earlier in statement.rows
        if earlier.index < row.index
        and (earlier.date, earlier.description, round(earlier.amount, 2),
             earlier.balance) == identity
    )


def as_raw_transaction(statement: ParsedStatement, row: ParsedRow) -> RawTransaction:
    """Adapt a contract row for the categoriser and tagger, which read descriptions."""
    raw = dict(row.raw or {})
    if row.balance is not None:
        raw.setdefault("balance", f"{row.balance:.2f}")
    return RawTransaction(
        date=row.date, description=row.description, amount=row.amount,
        currency=row.currency, original_amount=row.original_amount,
        original_currency=row.original_currency, fee=row.fee,
        source_type=statement.source_type, source_file=statement.source_file,
        reference_id=row.reference_id, raw_data=raw,
    )


def _is_payment_to_source_of_truth(txn: RawTransaction, patterns: list[re.Pattern],
                                   source_of_truth_types: set[str]) -> bool:
    if txn.source_type in source_of_truth_types:
        return False
    upper = txn.description.upper()
    return any(p.search(upper) for p in patterns)


def ingest_statement(
    conn: sqlite3.Connection,
    statement: ParsedStatement,
    account_id: int,
    categorizer: Categorizer,
    payment_patterns: list[re.Pattern] | None = None,
    source_of_truth_types: set[str] | None = None,
    tagger: Tagger | None = None,
    shared_rules: SharedRules | None = None,
    dry_run: bool = False,
    force: bool = False,
    date_from: str | None = None,
    date_until: str | None = None,
) -> IngestResult:
    """Validate a statement, then insert whatever part of it is new."""
    result = IngestResult(source_file=statement.source_file)

    # Judge the statement as printed, before any windowing.
    result.issues = statement.validate()
    blocking = [i for i in result.issues if i.code in ("balance_mismatch", "no_rows")]
    if blocking and not force:
        result.rejected = True
        return result

    rows = statement.rows
    if date_from or date_until:
        kept = [r for r in rows
                if (not date_from or r.date >= date_from)
                and (not date_until or r.date <= date_until)]
        result.dropped_by_window = len(rows) - len(kept)
        rows = kept

    transfers_cat_id = db.get_category_id(conn, "Transfers")

    for row in rows:
        dedup_hash = row_identity(statement, row, account_id)
        if db.hash_exists(conn, dedup_hash):
            result.skipped += 1
            continue

        txn = as_raw_transaction(statement, row)

        if payment_patterns and _is_payment_to_source_of_truth(
                txn, payment_patterns, source_of_truth_types or set()):
            category_id, confidence, is_transfer = transfers_cat_id, 1.0, True
        else:
            category_id, confidence = categorizer.categorize(txn)
            is_transfer = _category_name(conn, category_id) == "Transfers"

        if dry_run:
            result.inserted += 1
            continue

        txn_id = db.insert_transaction(
            conn=conn, account_id=account_id, date=row.date, description=row.description,
            amount=row.amount, original_amount=txn.original_amount,
            original_currency=txn.original_currency, fee=txn.fee,
            category_id=category_id, category_confidence=confidence,
            reference_id=txn.reference_id, source_type=statement.source_type,
            dedup_hash=dedup_hash, source_file=statement.source_file,
            raw_data=txn.raw_data_json(), is_transfer=is_transfer,
        )
        if txn_id is None:
            result.skipped += 1
            continue
        if tagger:
            insert_tags(conn, txn_id, tagger.get_tags(txn))
        if shared_rules:
            split_pct = shared_rules.split_for(txn)
            if split_pct is not None:
                mark_shared(conn, txn_id, split_pct)
        result.inserted += 1

    return result


def _category_name(conn: sqlite3.Connection, category_id: int | None) -> str:
    if category_id is None:
        return "Uncategorized"
    row = conn.execute("SELECT name FROM categories WHERE id = ?", (category_id,)).fetchone()
    return row[0] if row else "Uncategorized"
