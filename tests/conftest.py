import os
import sqlite3
from pathlib import Path

# The character n-gram embedder needs no model download and gives the same
# vectors on every machine. Set before anything builds the default embedder.
os.environ["LEDGER_EMBEDDER"] = "ngram"

import pytest

from etl import db
from etl.categorizer import Categorizer
from etl.embedding import NgramEmbedder
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
    return Categorizer(conn, FIXTURE_DIR / "categories.yaml", embedder=NgramEmbedder())


@pytest.fixture
def payment_patterns():
    patterns, _ = load_payment_patterns(FIXTURE_DIR / "accounts.yaml")
    return patterns
