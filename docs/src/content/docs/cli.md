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

# Only the rows in a window (stitching a CSV onto a PDF's tail)
ledger ingest --source bankwest-csv --from 2026-08-05 --until 2026-08-31

# Ingest even though the statement failed validation
ledger ingest --force
```

**Available sources:** `paypal`, `airbnb`, `ing`, `ing-csv`, `hsbc`, `hsbc-csv`,
`coles`, `coles-csv`, `bankwest`, `bankwest-csv`, `bankwest-loan`,
`bankwest-offset`, `cba`, `cba-cc`, `amex`

After successful ingestion, files are moved from `staging/<source>/` to `data/archive/<source>/`.

The pipeline for each statement:
1. Parse the source file into a `ParsedStatement` -- its period, its printed balances, and its rows
2. Validate it against its own balances, and refuse it if the rows do not add up (see [Adding a Bank](/docs/adding-a-bank#validation))
3. Compute a dedup hash to skip already-imported transactions
4. Check source-of-truth rules to auto-mark credit card payments as transfers
5. Categorise: source rules, then the merchant's label, then the nearest labelled merchants (see [Categories](/docs/config-categories#how-a-transaction-gets-its-category))
6. Apply tag rules (multiple tags per transaction)
7. Insert into SQLite with raw data audit trail

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

### `ledger shared`

Apply shared-expense rules -- the household costs split with a partner.

```sh
ledger shared --backfill
ledger shared --since 2026-01-01 --dry-run
```

### `ledger reconcile`

Check the ledger against the balances printed on statements. This is what catches
a silent gap that dedup cannot: between two consecutive statements, the
transactions in between must account for exactly the change in closing balance.

```sh
ledger reconcile
ledger reconcile --since 2026-01-01 --account Bankwest
ledger reconcile --no-chain      # skip the per-row running-balance check
```

### `ledger cgt`

Capital gains for a financial year, from the CommSec contract notes in
`staging/commsec/`.

```sh
ledger cgt --fy 2025
ledger cgt --fy 2025 --carried-forward-losses 9000
```

### `ledger dedup`

Find and resolve duplicates that appear on two accounts -- a purchase on the card
and its payment on the everyday account.

```sh
ledger dedup --dry-run
ledger dedup
```

### `ledger categorise`

Label merchants, recategorise rows, and measure how well the categoriser does. Pick one mode per run.

```sh
# Label every merchant from the categories already in the ledger (origin "bootstrap")
ledger categorise --seed

# How many merchants are labelled, by origin
ledger categorise --stats

# Recategorise every row you have not confirmed (confirmed rows are left alone)
ledger categorise --rerun --dry-run
ledger categorise --rerun

# Learn only from rows before a date, then score the guesses for rows from it.
# Works on an in-memory copy, so nothing is written.
ledger categorise --evaluate 2025-07-01

# Have Claude file the rows the categoriser could not settle
ledger categorise --export pending.json
ledger categorise --import answers.json --dry-run
ledger categorise --import answers.json
```

Run `--seed` once after upgrading from the old regex rules, so the merchants already in your ledger are labelled before new statements arrive.

`--evaluate` reports accuracy for source rules, known merchants and nearest-merchant guesses, how often the right category is in the top three suggestions, the trade-off at other similarity thresholds, and the most common misses.

**The export/import round trip.** `--export` writes every pending row (Uncategorized, or a nearest-merchant guess nobody confirmed) as JSON. Each row carries its 10 most similar filed merchants and their categories, and each category carries a few example merchants, so Claude can follow your own conventions. Answer in the form:

```json
{"1234": {"category": "Eating Out", "confidence": 0.95}}
```

`--import` files each answer. At confidence 0.9 or above the row is filed and its merchant is learned (origin `llm`). Below that the row is filed at confidence 0.5, so it stays flagged for review and teaches nothing. Rows a person confirmed since the export are skipped, and an unknown category name stops the import before anything is written.

### `ledger tag`

Bulk tag or recategorise every transaction matching a description pattern.

```sh
ledger tag --pattern "UBER" --tag transport --dry-run
ledger tag --pattern "JETSTAR" --category Travel --fy 2025
```

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
