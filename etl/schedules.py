"""Generic recurring schedule engine.

Expands config-defined recurring schedules (rent, loan repayments, shared
subscriptions, ...) into occurrences and computes how much a counterparty owes,
optionally reconciling against tagged/categorised payments they've made.

Config lives in config/schedules.yaml (see schedules.yaml.example). This is the
transaction-table-driven replacement for the per-arrangement tabs in the old
budget spreadsheet: nothing here is rent-specific — rent is just one schedule.

Frequencies: weekly (7d), fortnightly (14d), monthly (same day-of-month, clamped
to month length). All computations take an explicit `as_of` date so they are
deterministic and unit-testable.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml

_STEP_DAYS = {"weekly": 7, "fortnightly": 14}


def load_schedules_config(config_path: Path) -> dict:
    """Load schedules.yaml; missing/empty file yields an empty schedule list."""
    if not Path(config_path).exists():
        return {"schedules": []}
    with open(config_path) as f:
        return yaml.safe_load(f) or {"schedules": []}


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _add_months(start: date, months: int) -> date:
    """start shifted by `months`, clamping the day to the target month length."""
    m = start.month - 1 + months
    year = start.year + m // 12
    month = m % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def occurrence_dates(start: date, frequency: str, until: date) -> list[date]:
    """All occurrence dates from `start` through `until` (both inclusive)."""
    if start > until:
        return []
    dates: list[date] = []
    if frequency in _STEP_DAYS:
        step = timedelta(days=_STEP_DAYS[frequency])
        d = start
        while d <= until:
            dates.append(d)
            d += step
    elif frequency == "monthly":
        i = 0
        d = start
        while d <= until:
            dates.append(d)
            i += 1
            d = _add_months(start, i)
    else:
        raise ValueError(f"unknown frequency: {frequency!r}")
    return dates


def next_occurrence(start: date, frequency: str, after: date) -> date:
    """First occurrence strictly after `after` (returns `start` if it's later)."""
    if start > after:
        return start
    if frequency in _STEP_DAYS:
        step = _STEP_DAYS[frequency]
        n = (after - start).days // step + 1
        return start + timedelta(days=step * n)
    if frequency == "monthly":
        i = 1
        d = _add_months(start, i)
        while d <= after:
            i += 1
            d = _add_months(start, i)
        return d
    raise ValueError(f"unknown frequency: {frequency!r}")


def _matched_payments(conn: sqlite3.Connection, start: date, settle: dict) -> list[dict]:
    """Incoming transactions on/after `start` matching the settle rule."""
    tag = settle.get("match_tag")
    category = settle.get("match_category")
    if not tag and not category:
        return []

    clauses = ["t.amount > 0", "t.is_transfer = 0", "t.date >= ?"]
    params: list = [start.isoformat()]
    join = ""
    if tag:
        join = "JOIN transaction_tags tt ON tt.transaction_id = t.id"
        clauses.append("tt.tag = ?")
        params.append(tag)
    if category:
        clauses.append("c.name = ?")
        params.append(category)

    rows = conn.execute(
        f"""
        SELECT t.date, t.description, t.amount
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        {join}
        WHERE {' AND '.join(clauses)}
        ORDER BY t.date
        """,
        params,
    ).fetchall()
    return [
        {"date": r["date"], "description": r["description"], "amount": round(r["amount"], 2)}
        for r in rows
    ]


def compute_schedule(
    conn: Optional[sqlite3.Connection], schedule: dict, as_of: date
) -> dict:
    """Expand a single schedule and reconcile it against payments as of `as_of`."""
    start = _parse_date(schedule["start"])
    frequency = schedule["frequency"]
    amount = float(schedule["amount"])
    their_pct = float(schedule.get("their_pct", 0))
    their_share = round(amount * their_pct / 100.0, 2)

    due_dates = occurrence_dates(start, frequency, as_of)
    occurrences = [
        {"date": d.isoformat(), "amount": round(amount, 2), "their_share": their_share}
        for d in due_dates
    ]
    expected_to_date = round(their_share * len(due_dates), 2)

    settle = schedule.get("settle") or {}
    payments: list[dict] = []
    if conn is not None:
        payments = _matched_payments(conn, start, settle)
    paid = round(sum(p["amount"] for p in payments), 2)

    return {
        "name": schedule["name"],
        "counterparty": schedule.get("counterparty"),
        "frequency": frequency,
        "amount": round(amount, 2),
        "their_pct": their_pct,
        "their_share": their_share,
        "start": start.isoformat(),
        "notes": schedule.get("notes", ""),
        "occurrences": occurrences,
        "num_due": len(due_dates),
        "expected_to_date": expected_to_date,
        "next_due": next_occurrence(start, frequency, as_of).isoformat(),
        "settle_enabled": bool(settle.get("match_tag") or settle.get("match_category")),
        "payments": payments,
        "paid": paid,
        "balance_owing": round(expected_to_date - paid, 2),
    }


def compute_schedules(
    conn: Optional[sqlite3.Connection], config: dict, as_of: date
) -> list[dict]:
    """Compute every schedule in the config as of `as_of`."""
    return [
        compute_schedule(conn, schedule, as_of)
        for schedule in config.get("schedules", [])
    ]
