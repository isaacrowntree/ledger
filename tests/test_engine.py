"""The ingestion engine owns identity, idempotency and validation.

A parser cannot opt out of any of this, and no parser gets to define what makes a
transaction unique.
"""
import pytest

from etl import db
from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.engine import IngestResult, ingest_statement, row_identity


def _stmt(rows, source_file="jan.pdf", source_type="test", **kw):
    kw.setdefault("balance_convention", BalanceConvention.NONE)
    return ParsedStatement(source_file=source_file, source_type=source_type,
                           rows=rows, **kw)


def _row(i, date, description, amount, balance=None):
    return ParsedRow(index=i, date=date, description=description,
                     amount=amount, balance=balance)


@pytest.fixture
def account(conn):
    return db.ensure_account(conn, "Engine Test", "test", account_type="credit")


class TestIdentity:
    def test_filename_is_not_part_of_identity(self):
        """The same statement re-downloaded under another name must not
        double-count -- this was the original defect."""
        a = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0)], source_file="a.pdf")
        b = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0)], source_file="b.pdf")
        assert row_identity(a, a.rows[0], 1) == row_identity(b, b.rows[0], 1)

    def test_account_scopes_identity(self):
        s = _stmt([_row(0, "2026-01-01", "TRANSFER", -100.0)])
        assert row_identity(s, s.rows[0], 1) != row_identity(s, s.rows[0], 2)

    def test_running_balance_separates_same_day_repeats(self):
        s = _stmt([_row(0, "2026-01-01", "Direct Debit", -23.14, balance=100.0),
                   _row(1, "2026-01-01", "Direct Debit", -23.14, balance=76.86)])
        assert row_identity(s, s.rows[0], 1) != row_identity(s, s.rows[1], 1)

    def test_repeats_without_a_balance_stay_distinct(self):
        """Two identical tolls on a statement printing no balance are still two
        transactions."""
        s = _stmt([_row(0, "2026-01-01", "Linkt", -20.20),
                   _row(1, "2026-01-01", "Linkt", -20.20)])
        assert row_identity(s, s.rows[0], 1) != row_identity(s, s.rows[1], 1)


class TestIdempotency:
    def test_reingesting_the_same_statement_changes_nothing(self, conn, account, categorizer):
        s = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0),
                   _row(1, "2026-01-02", "LUNCH", -12.0)])
        first = ingest_statement(conn, s, account, categorizer)
        second = ingest_statement(conn, s, account, categorizer)
        assert (first.inserted, first.skipped) == (2, 0)
        assert (second.inserted, second.skipped) == (0, 2)

    def test_reingest_under_a_new_filename_still_dedups(self, conn, account, categorizer):
        rows = [_row(0, "2026-01-01", "COFFEE", -5.0)]
        ingest_statement(conn, _stmt(rows, source_file="a.pdf"), account, categorizer)
        again = ingest_statement(conn, _stmt(rows, source_file="b.pdf"), account, categorizer)
        assert (again.inserted, again.skipped) == (0, 1)

    def test_overlapping_statements_insert_only_what_is_new(self, conn, account, categorizer):
        jan = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0)], source_file="jan.pdf")
        both = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0),
                      _row(1, "2026-01-02", "LUNCH", -12.0)], source_file="both.pdf")
        ingest_statement(conn, jan, account, categorizer)
        result = ingest_statement(conn, both, account, categorizer)
        assert (result.inserted, result.skipped) == (1, 1)


class TestValidationGate:
    def _bad(self):
        return _stmt([_row(0, "2026-01-01", "COFFEE", -5.0, balance=95.0)],
                     opening_balance=100.0, closing_balance=70.0,
                     balance_convention=BalanceConvention.SIGNED)

    def test_a_statement_that_does_not_add_up_is_refused(self, conn, account, categorizer):
        result = ingest_statement(conn, self._bad(), account, categorizer)
        assert result.rejected
        assert result.inserted == 0
        assert "balance_mismatch" in [i.code for i in result.issues]

    def test_refusal_writes_nothing(self, conn, account, categorizer):
        ingest_statement(conn, self._bad(), account, categorizer)
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0

    def test_force_ingests_anyway_and_still_reports(self, conn, account, categorizer):
        result = ingest_statement(conn, self._bad(), account, categorizer, force=True)
        assert result.inserted == 1
        assert not result.rejected
        assert [i.code for i in result.issues] == ["balance_mismatch"]

    def test_a_clean_statement_reports_no_issues(self, conn, account, categorizer):
        s = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0, balance=95.0)],
                  opening_balance=100.0, closing_balance=95.0,
                  balance_convention=BalanceConvention.SIGNED)
        result = ingest_statement(conn, s, account, categorizer)
        assert result.issues == [] and result.inserted == 1


class TestTagging:
    def test_tags_are_applied_to_inserted_rows(self, conn, account, categorizer):
        """A row ingested without its tags is missing from every tag-based
        report. Passing tagger=None in every test hid a wrong method name here."""
        from etl.tagger import Tagger
        from tests.conftest import FIXTURE_DIR

        s = _stmt([_row(0, "2026-01-01", "JETSTAR AIRWAYS", -220.0)])
        result = ingest_statement(conn, s, account, categorizer,
                                  tagger=Tagger(FIXTURE_DIR / "categories.yaml"))
        assert result.inserted == 1
        tags = [r[0] for r in conn.execute("SELECT tag FROM transaction_tags")]
        assert tags, "expected the tagger to run"


class TestWindow:
    def test_window_clips_to_the_requested_range(self, conn, account, categorizer):
        s = _stmt([_row(0, "2026-01-01", "OLD", -5.0),
                   _row(1, "2026-02-01", "NEW", -6.0)])
        result = ingest_statement(conn, s, account, categorizer, date_from="2026-01-15")
        assert (result.inserted, result.dropped_by_window) == (1, 1)

    def test_window_does_not_affect_validation(self, conn, account, categorizer):
        """Validation judges the statement as printed, not the clipped subset."""
        s = _stmt([_row(0, "2026-01-01", "A", -5.0, balance=95.0),
                   _row(1, "2026-02-01", "B", -5.0, balance=90.0)],
                  opening_balance=100.0, closing_balance=90.0,
                  balance_convention=BalanceConvention.SIGNED)
        result = ingest_statement(conn, s, account, categorizer, date_from="2026-01-15")
        assert result.issues == [] and result.inserted == 1


class TestDryRun:
    def test_dry_run_reports_but_writes_nothing(self, conn, account, categorizer):
        s = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0)])
        result = ingest_statement(conn, s, account, categorizer, dry_run=True)
        assert result.inserted == 1
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0

    def test_dry_run_counts_duplicates_against_the_database(self, conn, account, categorizer):
        """A dry run that cannot see existing rows reports a meaningless zero --
        the old one did exactly that."""
        s = _stmt([_row(0, "2026-01-01", "COFFEE", -5.0)])
        ingest_statement(conn, s, account, categorizer)
        result = ingest_statement(conn, s, account, categorizer, dry_run=True)
        assert (result.inserted, result.skipped) == (0, 1)
