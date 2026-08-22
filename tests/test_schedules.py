"""Tests for the generic recurring schedule engine."""
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from etl import db
from etl.schedules import (
    load_schedules_config,
    occurrence_dates,
    next_occurrence,
    compute_schedule,
    compute_schedules,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config():
    return load_schedules_config(FIXTURE_DIR / "schedules.yaml")


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    db.init_db(connection)
    db.load_categories_from_config(connection, FIXTURE_DIR / "categories.yaml")
    db.load_accounts_from_config(connection, FIXTURE_DIR / "accounts.yaml")
    yield connection
    connection.close()


def _add_payment(conn, dt, amount, tag=None):
    account_id = db.ensure_account(conn, "Test", "test")
    txn_id = db.insert_transaction(
        conn, account_id, dt, "PARTNER TRANSFER", amount, None, None, 0,
        None, 1.0, None, "test", f"hash-{dt}-{amount}", "test.csv", "{}",
    )
    if tag:
        conn.execute(
            "INSERT INTO transaction_tags (transaction_id, tag) VALUES (?, ?)",
            (txn_id, tag),
        )
    conn.commit()
    return txn_id


class TestOccurrenceDates:
    def test_fortnightly(self):
        dates = occurrence_dates(date(2026, 6, 1), "fortnightly", date(2026, 6, 30))
        assert dates == [date(2026, 6, 1), date(2026, 6, 15), date(2026, 6, 29)]

    def test_weekly(self):
        dates = occurrence_dates(date(2026, 6, 1), "weekly", date(2026, 6, 21))
        assert dates == [date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15)]

    def test_monthly_clamps_day(self):
        dates = occurrence_dates(date(2026, 1, 31), "monthly", date(2026, 3, 31))
        # Feb has no 31st — clamps to 28
        assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]

    def test_start_after_until_is_empty(self):
        assert occurrence_dates(date(2026, 7, 1), "fortnightly", date(2026, 6, 1)) == []

    def test_unknown_frequency_raises(self):
        with pytest.raises(ValueError):
            occurrence_dates(date(2026, 6, 1), "daily", date(2026, 6, 30))


class TestNextOccurrence:
    def test_strictly_after(self):
        # as_of lands exactly on an occurrence — next is the following one
        assert next_occurrence(date(2026, 6, 1), "fortnightly", date(2026, 6, 15)) == date(2026, 6, 29)

    def test_before_start_returns_start(self):
        assert next_occurrence(date(2026, 6, 1), "fortnightly", date(2026, 5, 1)) == date(2026, 6, 1)

    def test_monthly(self):
        # occurrences fall on the 15th; first strictly after 14 Jun is 15 Jun
        assert next_occurrence(date(2026, 1, 15), "monthly", date(2026, 6, 14)) == date(2026, 6, 15)
        # and strictly after the 15th rolls to next month
        assert next_occurrence(date(2026, 1, 15), "monthly", date(2026, 6, 15)) == date(2026, 7, 15)


class TestComputeSchedule:
    def test_partner_share_and_expected(self, config):
        sched = config["schedules"][0]
        result = compute_schedule(None, sched, date(2026, 6, 14))
        # 40% of 1177.81 = 471.124 -> 471.12
        assert result["their_share"] == 471.12
        # Only the 1 Jun occurrence is due by 14 Jun
        assert result["num_due"] == 1
        assert result["expected_to_date"] == 471.12
        assert result["next_due"] == "2026-06-15"
        # No conn -> no payments matched, full balance owing
        assert result["paid"] == 0.0
        assert result["balance_owing"] == 471.12

    def test_payments_reconciled(self, conn, config):
        sched = config["schedules"][0]
        # Three fortnights due by 30 Jun (1, 15, 29 Jun) = 3 * 471.12 expected
        _add_payment(conn, "2026-06-02", 471.12, tag="partner-rent")
        # An untagged transfer must NOT count
        _add_payment(conn, "2026-06-03", 471.12, tag=None)
        result = compute_schedule(conn, sched, date(2026, 6, 30))
        assert result["num_due"] == 3
        assert result["expected_to_date"] == 1413.36
        assert result["paid"] == 471.12
        assert result["balance_owing"] == 942.24
        assert len(result["payments"]) == 1

    def test_payment_before_start_excluded(self, conn, config):
        sched = config["schedules"][0]
        _add_payment(conn, "2026-05-20", 471.12, tag="partner-rent")
        result = compute_schedule(conn, sched, date(2026, 6, 14))
        assert result["paid"] == 0.0

    def test_settle_disabled_when_no_match_rule(self, config):
        sched = config["schedules"][1]  # monthly sub, no settle block
        result = compute_schedule(None, sched, date(2026, 6, 14))
        assert result["settle_enabled"] is False


class TestComputeSchedules:
    def test_all_schedules_returned(self, conn, config):
        results = compute_schedules(conn, config, date(2026, 6, 14))
        assert len(results) == 2
        assert {r["name"] for r in results} == {"Mortgage – Test", "Monthly subscription"}

    def test_empty_config(self, conn):
        assert compute_schedules(conn, {"schedules": []}, date(2026, 6, 14)) == []
