---
title: CLI Reference
description: All Ledger CLI commands with usage examples.
---

The `ledger` CLI is the main entry point for the ETL pipeline. It is installed when you run `pip install -e .` and is available inside the virtualenv.

## Commands

### `ledger init`

Initialise the SQLite database and load config files.

```sh
ledger init
```

Creates `data/ledger.db` if it does not exist. Loads categories from `config/categories.yaml` and accounts from `config/accounts.yaml`. Safe to run multiple times -- existing data is not deleted.

### `ledger ingest`

Parse statement files from `staging/` and insert transactions into the database.

```sh
# Ingest all sources
ledger ingest

# Ingest only ING PDF statements
ledger ingest --source ing

# Ingest only PayPal CSV files
ledger ingest --source paypal

# Preview without writing to the database
ledger ingest --dry-run
```

**Available sources:** `paypal`, `airbnb`, `ing`, `ing-csv`, `hsbc`, `coles`, `bankwest`, `bankwest-csv`, `amex`

After successful ingestion, files are moved from `staging/<source>/` to `data/archive/<source>/`.

The pipeline for each transaction:
1. Parse the source file into `RawTransaction` objects
2. Compute a dedup hash (SHA-256) to skip already-imported transactions
3. Check source-of-truth rules to auto-mark credit card payments as transfers
4. Apply category rules (regex matching)
5. Apply tag rules (multiple tags per transaction)
6. Insert into SQLite with raw data audit trail

### `ledger split`

Compute business expense splits for transactions in a financial year.

```sh
# Backfill splits for FY 2024-25
ledger split --backfill --fy 2025
```

This reads `config/tax.yaml` split rules and creates entries in the `transaction_splits` table. The `--backfill` flag is required -- it clears and recomputes all splits for the given FY.

### `ledger tax`

Print an ATO tax summary to the terminal.

```sh
# Tax summary for FY 2024-25
ledger tax --fy 2025
```

Output includes:
- Income by category (salary, interest, rental, freelance)
- Business expenses from the splits table
- Depreciation items from config
- Manual entries from config

### `ledger connect`

Connect bank accounts via the Basiq open banking API. Generates a consent URL to link your banks.

```sh
ledger connect
```

Requires a Basiq API key in your environment. After connecting, use `ledger sync` to pull transactions.

### `ledger sync`

Sync transactions from connected Basiq bank accounts.

```sh
# Sync all connected banks
ledger sync

# Sync only ING
ledger sync --source ing

# Sync transactions since a specific date
ledger sync --since 2025-01-01

# Preview without writing
ledger sync --dry-run
```

Automatically detects the last synced transaction date and only fetches new ones.

## Starting the API server

The API server is not part of the `ledger` CLI -- it runs as a Python module:

```sh
python -m api
```

This starts the Flask server on [http://localhost:5050](http://localhost:5050) with the frontend dashboard.

## Running tests

```sh
python -m pytest tests/ -v
```
