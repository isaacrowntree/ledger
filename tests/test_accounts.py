"""Tests for account configuration and file prefix mapping."""
from pathlib import Path

from etl import db
from etl.cli import _build_file_prefix_map, _resolve_account_from_file

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class TestFilePrefixMap:
    def test_loads_prefixes(self):
        prefix_map = _build_file_prefix_map(FIXTURE_DIR / "accounts.yaml")
        assert "test_checking" in prefix_map
        assert prefix_map["test_checking"] == "Test Checking"

    def test_resolves_correct_account(self):
        prefix_map = _build_file_prefix_map(FIXTURE_DIR / "accounts.yaml")
        assert _resolve_account_from_file(
            "test_joint_2025-01-01.pdf", prefix_map, "fallback"
        ) == "Test Joint"

    def test_resolves_savings(self):
        prefix_map = _build_file_prefix_map(FIXTURE_DIR / "accounts.yaml")
        assert _resolve_account_from_file(
            "test_savings_2025-01-01.pdf", prefix_map, "fallback"
        ) == "Test Savings"

    def test_fallback_for_unknown_prefix(self):
        prefix_map = _build_file_prefix_map(FIXTURE_DIR / "accounts.yaml")
        assert _resolve_account_from_file(
            "unknown_file.pdf", prefix_map, "FallbackAccount"
        ) == "FallbackAccount"


class TestAccountTypes:
    def test_accounts_have_correct_types(self, conn):
        rows = conn.execute("SELECT name, account_type FROM accounts").fetchall()
        by_name = {r["name"]: r["account_type"] for r in rows}

        assert by_name["Test Checking"] == "checking"
        assert by_name["Test Savings"] == "savings"
        assert by_name["Test Mortgage"] == "loan"
        assert by_name["Test Credit Card"] == "credit"
        assert by_name["PayPal"] == "other"

    def test_source_of_truth_accounts_exist(self, conn):
        for name in ["PayPal", "Test Credit Card", "Test Bankwest", "Test HSBC"]:
            row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
            assert row is not None, f"Account '{name}' should exist"
