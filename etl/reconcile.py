"""Reconcile ledger transactions against the balances printed on statements.

Two independent checks:

1. **Anchor check** -- every statement prints a closing balance. Between two
   consecutive statements the ledger's transactions must account for exactly the
   change in that balance. A shortfall means transactions are missing (a gap in
   coverage); an excess means transactions were double-counted. This works even
   for sources that carry no per-record balance, which is the point: those are
   precisely the ones where a silent gap is otherwise invisible.

2. **Running-balance chain check** -- where a source prints a balance on each
   line (ING, the Bankwest CSV export), each row's balance must equal the
   previous row's balance plus that row's amount. This localises a break to the
   exact transaction rather than the statement.

Sign conventions: for asset accounts (checking/savings) the printed balance moves
with the transaction amount. For credit cards and loans the printed balance is
what is OWED, so it moves against it -- a purchase (negative amount) increases
the amount owing.
"""
import json
import sqlite3
from dataclasses import dataclass, field

OWING_TYPES = {"credit", "loan"}
TOLERANCE = 0.005


@dataclass
class Anchor:
    source_file: str
    period_end: str
    closing_balance: float


@dataclass
class AnchorDrift:
    account: str
    from_file: str
    to_file: str
    from_date: str
    to_date: str
    expected_delta: float      # implied by the two printed balances
    actual_delta: float        # what the ledger's transactions actually sum to
    n_transactions: int

    @property
    def drift(self) -> float:
        return round(self.actual_delta - self.expected_delta, 2)


@dataclass
class ChainBreak:
    account: str
    source_file: str
    date: str
    description: str
    amount: float
    prev_balance: float
    balance: float

    @property
    def drift(self) -> float:
        return round(self.balance - (self.prev_balance + self.amount), 2)


@dataclass
class AccountReport:
    account: str
    account_type: str
    anchors: list = field(default_factory=list)
    anchor_drifts: list = field(default_factory=list)
    chain_breaks: list = field(default_factory=list)
    unanchored_from: str | None = None   # transactions after the last statement


def _parse_money(value) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _file_order(conn: sqlite3.Connection, account_id: int, since: str | None = None) -> list[dict]:
    """Every source file for this account, ordered by the period it covers.

    Statements are attributed by FILE, not by date. A card posts transactions with
    a lag, so a row dated before a statement's end can appear on the NEXT
    statement -- partitioning by date therefore never reconciles, while
    partitioning by file matches exactly how the bank drew the statement.
    """
    sql = """
        SELECT r.source_file, t.date, t.amount, r.raw_data
        FROM transactions t JOIN raw_imports r ON r.transaction_id = t.id
        WHERE t.account_id = ?
    """
    params: list = [account_id]
    if since:
        sql += " AND t.date >= ?"
        params.append(since)

    per_file: dict[str, dict] = {}
    for source_file, date, amount, raw_json in conn.execute(sql, params):
        try:
            raw = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            raw = {}
        entry = per_file.setdefault(source_file, {
            "source_file": source_file, "start": date, "end": date,
            "total": 0.0, "n": 0, "closing": None, "last_balance": None, "last_date": "",
            "balances": [],
        })
        entry["total"] += amount
        entry["n"] += 1
        entry["start"] = min(entry["start"], date)
        entry["end"] = max(entry["end"], date)

        closing = _parse_money(raw.get("closing_balance"))
        if closing is not None:
            entry["closing"] = closing
        running = _parse_money(raw.get("balance"))
        if running is not None:
            entry["balances"].append(running)
            if date >= entry["last_date"]:
                entry["last_date"] = date
                entry["last_balance"] = running

    files = sorted(per_file.values(), key=lambda e: (e["end"], e["source_file"]))
    for entry in files:
        entry["total"] = round(entry["total"], 2)
        entry["anchor"] = entry["closing"] if entry["closing"] is not None else entry["last_balance"]
    return files


def _balance_sign(balances: list[float], owing: bool) -> int:
    """Which way this file writes the balance of an owing account.

    ING prints the same mortgage as "-$307,416.41" on one statement layout and
    "307,153.72" on another -- the same debt, opposite signs. Comparing an anchor
    from one layout against the other otherwise implies an ~800k movement that
    never happened. Normalise each file to owing-positive.
    """
    if not owing:
        return 1
    known = [b for b in balances if b is not None]
    if not known:
        return 1
    negative = sum(1 for b in known if b < 0)
    return -1 if negative > len(known) / 2 else 1


def collect_anchors(conn: sqlite3.Connection, account_id: int, account_type: str = "checking",
                    since: str | None = None) -> list[Anchor]:
    """One anchor per statement file that prints a balance, normalised so that
    every file expresses an owing account's balance the same way."""
    owing = account_type in OWING_TYPES
    files = _file_order(conn, account_id, since)
    anchors = []
    for i in _advancing_anchors(files):
        e = files[i]
        sign = _balance_sign(e["balances"], owing)
        anchors.append(Anchor(e["source_file"], e["end"], e["anchor"] * sign))
    return anchors


def _advancing_anchors(files: list[dict]) -> list[int]:
    """Positions of files usable as anchors.

    A statement only anchors a period if it ADVANCES coverage. A re-download of a
    statement already held, or an interim statement that re-covers an earlier one
    from the start, contributes a balance for a period another file already
    anchors -- and because dedup splits the shared rows between the two files,
    neither file's rows then sum to its own statement period. Such a file still
    counts toward the surrounding spans; it just cannot be an anchor itself.
    """
    positions: list[int] = []
    last_start = last_end = None
    for i, entry in enumerate(files):
        if entry["anchor"] is None:
            continue
        if last_end is not None and entry["end"] <= last_end:
            continue  # does not advance past the previous statement
        if last_start is not None and entry["start"] <= last_start:
            continue  # re-covers the previous statement from its beginning
        positions.append(i)
        last_start, last_end = entry["start"], entry["end"]
    return positions


def check_anchors(conn: sqlite3.Connection, account_id: int, account_name: str,
                  account_type: str, since: str | None = None) -> list[AnchorDrift]:
    """Between two statements, the transactions on every file in between must
    account for exactly the change in printed balance."""
    owing = account_type in OWING_TYPES
    files = _file_order(conn, account_id, since)
    anchor_positions = _advancing_anchors(files)

    drifts = []
    for prev_i, cur_i in zip(anchor_positions, anchor_positions[1:]):
        prev, cur = files[prev_i], files[cur_i]
        prev_anchor = prev["anchor"] * _balance_sign(prev["balances"], owing)
        cur_anchor = cur["anchor"] * _balance_sign(cur["balances"], owing)
        spanned = files[prev_i + 1: cur_i + 1]
        actual = round(sum(e["total"] for e in spanned), 2)
        n = sum(e["n"] for e in spanned)
        printed_delta = cur_anchor - prev_anchor
        # For a credit card the printed balance is what is owed, so it moves
        # opposite to the transaction amounts.
        expected = round(-printed_delta if owing else printed_delta, 2)
        if abs(actual - expected) > TOLERANCE:
            drifts.append(AnchorDrift(
                account=account_name, from_file=prev["source_file"], to_file=cur["source_file"],
                from_date=prev["end"], to_date=cur["end"],
                expected_delta=expected, actual_delta=actual, n_transactions=n,
            ))
    return drifts


def check_running_chain(conn: sqlite3.Connection, account_id: int, account_name: str,
                        account_type: str = "checking", since: str | None = None) -> list[ChainBreak]:
    """Within each file that prints a per-record balance, verify the chain."""
    sql = """
        SELECT r.source_file, t.date, t.description, t.amount, r.raw_data, t.id
        FROM transactions t JOIN raw_imports r ON r.transaction_id = t.id
        WHERE t.account_id = ?
    """
    params = [account_id]
    if since:
        sql += " AND t.date >= ?"
        params.append(since)
    sql += " ORDER BY r.source_file, t.date, t.id"

    owing = account_type in OWING_TYPES
    signs = {e["source_file"]: _balance_sign(e["balances"], owing)
             for e in _file_order(conn, account_id, since)}

    breaks = []
    prev_file = None
    prev_balance = None
    for source_file, date, description, amount, raw_json, _tid in conn.execute(sql, params):
        try:
            raw = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            raw = {}
        balance = _parse_money(raw.get("balance"))
        if balance is not None and owing:
            # Work in signed-balance terms so a rise in debt is a fall in balance,
            # matching the sign of the transaction amount.
            balance = -abs(balance) if signs.get(source_file, 1) == -1 else -balance
        if source_file != prev_file:
            prev_file, prev_balance = source_file, balance
            continue
        if balance is not None and prev_balance is not None:
            if abs(balance - (prev_balance + amount)) > TOLERANCE:
                breaks.append(ChainBreak(
                    account=account_name, source_file=source_file, date=date,
                    description=description, amount=amount,
                    prev_balance=prev_balance, balance=balance,
                ))
        if balance is not None:
            prev_balance = balance
    return breaks


def reconcile_account(conn: sqlite3.Connection, account_id: int, account_name: str,
                      account_type: str, since: str | None = None,
                      chain: bool = True) -> AccountReport:
    anchors = collect_anchors(conn, account_id, account_type, since)
    report = AccountReport(account=account_name, account_type=account_type, anchors=anchors)
    if len(anchors) >= 2:
        report.anchor_drifts = check_anchors(conn, account_id, account_name, account_type, since)
    if chain:
        report.chain_breaks = check_running_chain(conn, account_id, account_name, account_type, since)
    if anchors:
        trailing = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND date > ?",
            (account_id, anchors[-1].period_end),
        ).fetchone()[0]
        if trailing:
            report.unanchored_from = anchors[-1].period_end
    return report
