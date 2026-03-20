import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'AUD',
    account_type TEXT NOT NULL DEFAULT 'checking',
    display INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES categories(id),
    is_income INTEGER NOT NULL DEFAULT 0,
    budget_monthly REAL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    original_amount REAL,
    original_currency TEXT,
    fee REAL NOT NULL DEFAULT 0,
    category_id INTEGER REFERENCES categories(id),
    category_confidence REAL,
    reference_id TEXT,
    notes TEXT,
    source_type TEXT NOT NULL,
    is_transfer INTEGER NOT NULL DEFAULT 0,
    dedup_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS raw_imports (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER REFERENCES transactions(id),
    source_file TEXT NOT NULL,
    raw_data TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS currency_rates (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    from_currency TEXT NOT NULL,
    to_currency TEXT NOT NULL,
    rate REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'paypal',
    UNIQUE(date, from_currency, to_currency)
);

CREATE TABLE IF NOT EXISTS category_rules_learned (
    description_pattern TEXT PRIMARY KEY,
    category_id INTEGER REFERENCES categories(id),
    times_seen INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transaction_tags (
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    tag TEXT NOT NULL,
    PRIMARY KEY (transaction_id, tag)
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY,
    asset_type TEXT NOT NULL,  -- shares, property, super, crypto, other
    name TEXT NOT NULL,
    ticker TEXT,               -- ASX/NYSE ticker for shares
    units REAL DEFAULT 0,
    cost_basis REAL DEFAULT 0,
    current_value REAL,
    as_at_date TEXT,
    notes TEXT,
    UNIQUE(asset_type, name)
);

CREATE TABLE IF NOT EXISTS asset_events (
    id INTEGER PRIMARY KEY,
    holding_id INTEGER NOT NULL REFERENCES holdings(id),
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- buy, sell, dividend, split, grant, valuation
    units REAL,
    price_per_unit REAL,
    total_value REAL NOT NULL,
    fees REAL DEFAULT 0,
    reference TEXT,            -- trade ID, grant number, etc.
    source_file TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transaction_splits (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    business_name TEXT NOT NULL,
    business_pct REAL NOT NULL,
    business_amount REAL NOT NULL,
    UNIQUE(transaction_id, business_name)
);

CREATE TABLE IF NOT EXISTS work_trips (
    id INTEGER PRIMARY KEY,
    fy INTEGER NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    notes TEXT,
    UNIQUE(fy, name)
);

CREATE TABLE IF NOT EXISTS work_trip_expenses (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES work_trips(id),
    transaction_id INTEGER REFERENCES transactions(id),
    expense_type TEXT NOT NULL,  -- flights, accommodation, car, meals, other
    amount REAL NOT NULL,
    description TEXT,
    UNIQUE(trip_id, transaction_id, expense_type)
);

CREATE TABLE IF NOT EXISTS tax_overrides (
    id INTEGER PRIMARY KEY,
    fy INTEGER NOT NULL,
    section TEXT NOT NULL,       -- income, deduction, rental, business
    label TEXT NOT NULL,
    amount REAL NOT NULL,
    notes TEXT,
    UNIQUE(fy, section, label)
);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def ensure_account(conn: sqlite3.Connection, name: str, source_type: str, currency: str = "AUD", account_type: str = "checking", display: int = 1) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO accounts (name, source_type, currency, account_type, display) VALUES (?, ?, ?, ?, ?)",
        (name, source_type, currency, account_type, display),
    )
    conn.commit()
    return cursor.lastrowid


def ensure_category(conn: sqlite3.Connection, name: str, is_income: bool = False, budget_monthly: Optional[float] = None) -> int:
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO categories (name, is_income, budget_monthly) VALUES (?, ?, ?)",
        (name, int(is_income), budget_monthly),
    )
    conn.commit()
    return cursor.lastrowid


def get_category_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def hash_exists(conn: sqlite3.Connection, dedup_hash: str) -> bool:
    row = conn.execute("SELECT 1 FROM transactions WHERE dedup_hash = ?", (dedup_hash,)).fetchone()
    return row is not None


def insert_transaction(
    conn: sqlite3.Connection,
    account_id: int,
    date: str,
    description: str,
    amount: float,
    original_amount: Optional[float],
    original_currency: Optional[str],
    fee: float,
    category_id: Optional[int],
    category_confidence: Optional[float],
    reference_id: Optional[str],
    source_type: str,
    dedup_hash: str,
    source_file: str,
    raw_data: str,
    is_transfer: bool = False,
) -> Optional[int]:
    if hash_exists(conn, dedup_hash):
        return None
    cursor = conn.execute(
        """INSERT INTO transactions
        (account_id, date, description, amount, original_amount, original_currency,
         fee, category_id, category_confidence, reference_id, source_type, is_transfer, dedup_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (account_id, date, description, amount, original_amount, original_currency,
         fee, category_id, category_confidence, reference_id, source_type, int(is_transfer), dedup_hash),
    )
    txn_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO raw_imports (transaction_id, source_file, raw_data) VALUES (?, ?, ?)",
        (txn_id, source_file, raw_data),
    )
    return txn_id


def insert_currency_rate(conn: sqlite3.Connection, date: str, from_currency: str, to_currency: str, rate: float) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO currency_rates (date, from_currency, to_currency, rate)
        VALUES (?, ?, ?, ?)""",
        (date, from_currency, to_currency, rate),
    )


def load_categories_from_config(conn: sqlite3.Connection, config_path: Path) -> None:
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f)
    for cat in config.get("categories", []):
        ensure_category(
            conn,
            cat["name"],
            is_income=cat.get("is_income", False),
            budget_monthly=cat.get("budget_monthly"),
        )


def load_accounts_from_config(conn: sqlite3.Connection, config_path: Path) -> None:
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f)
    for acct in config.get("accounts", []):
        ensure_account(conn, acct["name"], acct["source_type"], acct.get("currency", "AUD"), acct.get("account_type", "checking"), acct.get("display", 1))
