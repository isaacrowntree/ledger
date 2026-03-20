import sqlite3
from pathlib import Path

import pytest

from etl import db
from etl.categorizer import Categorizer
from etl.normalizer import load_payment_patterns

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    """In-memory SQLite database with schema and fixture config loaded."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    db.init_db(connection)
    db.load_categories_from_config(connection, FIXTURE_DIR / "categories.yaml")
    db.load_accounts_from_config(connection, FIXTURE_DIR / "accounts.yaml")
    yield connection
    connection.close()


@pytest.fixture
def categorizer(conn):
    return Categorizer(conn, FIXTURE_DIR / "categories.yaml")


@pytest.fixture
def payment_patterns():
    patterns, _ = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
    return patterns
