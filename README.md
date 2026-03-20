# Ledger

A personal finance tool that ingests bank statements from multiple Australian banks, categorises transactions, and generates ATO-ready tax reports — all from your terminal and a local web dashboard.

[![Tests](https://github.com/isaacrowntree/ledger/actions/workflows/test.yml/badge.svg)](https://github.com/isaacrowntree/ledger/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)

## What it does

- **Multi-source ingestion** — parse statements from ING, PayPal, Bankwest, HSBC, Coles Mastercard, and Amex (PDF + CSV)
- **Auto-categorisation** — regex-based rules assign categories to transactions; learns from manual overrides
- **Source-of-truth dedup** — prevents double-counting when transactions appear on both a bank account and a credit card
- **Business splits** — automatically allocate percentages of expenses to businesses for tax reporting
- **ATO tax return view** — structured output matching Australian individual tax return sections (salary, rental, business schedule, deductions)
- **Financial year view** — outgoing/incoming/rental/work-trip sub-tabs replacing the manual Excel spreadsheet
- **Net worth dashboard** — accounts, credit cards, property, vehicles with statement-sourced balances
- **Tags** — orthogonal to categories; sub-classify transactions for reporting (e.g. `flight`, `biz-hosting`, `rental-income`)
- **Local-first** — SQLite database, no cloud dependency, your data stays on your machine

## Quick start

```sh
# Clone and set up
git clone https://github.com/isaacrowntree/ledger.git
cd ledger
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Copy and customise config
cp config/accounts.yaml.example config/accounts.yaml
cp config/categories.yaml.example config/categories.yaml
cp config/tax.yaml.example config/tax.yaml
# Edit these files with your accounts, merchant rules, and tax details

# Initialise database
ledger init

# Place statement files in staging/<source>/ and ingest
mkdir -p staging/ing staging/paypal
# Drop your PDF/CSV statements into the appropriate folders
ledger ingest

# Start the dashboard
python -m api
# Open http://localhost:5050
```

## Supported formats

| Source | Format | Parser |
|--------|--------|--------|
| ING Australia | PDF statements | `etl/parsers/ing_pdf.py` |
| ING Australia | CSV export | `etl/parsers/ing_csv.py` |
| PayPal | CSV activity download | `etl/parsers/paypal_csv.py` |
| Bankwest | PDF eStatements | `etl/parsers/bankwest_pdf.py` |
| Bankwest | CSV export | `etl/parsers/bankwest_csv.py` |
| HSBC | PDF statements | `etl/parsers/hsbc_pdf.py` |
| Coles Mastercard | PDF statements | `etl/parsers/coles_pdf.py` |
| Amex | CSV download | `etl/parsers/amex_csv.py` |
| Airbnb | CSV payout report | `etl/parsers/airbnb_csv.py` |

Adding a new bank: implement `BaseParser` in `etl/parsers/`, add to `PARSERS` in `etl/cli.py`.

## Configuration

All config lives in `config/` (gitignored — copy from `.example` files).

### `accounts.yaml`

Define your bank accounts, their types, and source-of-truth relationships:

```yaml
accounts:
  - name: "My Everyday Account"
    source_type: ing
    account_type: checking
    file_prefix: my_everyday    # matches statement filenames

  - name: "My Credit Card"
    source_type: bankwest
    account_type: credit
    source_of_truth: true       # this account has the real transaction detail
    payment_patterns:           # patterns on OTHER accounts that are payments to this card
      - "MY CREDIT CARD"
```

### `categories.yaml`

Categories, regex matching rules, and tag rules:

```yaml
categories:
  - name: Groceries
    budget_monthly: 600

rules:
  - type: regex
    pattern: "WOOLWORTHS|COLES |ALDI"
    category: Groceries

tag_rules:
  - tag: flight
    pattern: "JETSTAR|QANTAS|VIRGIN AUSTRALIA"
```

### `tax.yaml`

ATO-specific config — businesses, rental properties, depreciation, WFH:

```yaml
businesses:
  - name: "My Business"
    abn: "00000000000"
    split_rules:
      - category: Utilities
        tag: internet
        business_pct: 50

rental_properties:
  - name: "MY PROPERTY"
    ownership_pct: 50
    rental_weeks: { 2025: 20 }
```

## CLI commands

```sh
ledger init                      # Initialise database and load config
ledger ingest                    # Ingest all sources from staging/
ledger ingest --source ing       # Ingest only ING statements
ledger ingest --dry-run          # Preview without writing to DB
ledger split --backfill --fy 2025  # Compute business splits
ledger tax --fy 2025             # Print ATO tax summary
```

## API endpoints

The Flask API serves data to the dashboard at `http://localhost:5050`.

| Endpoint | Description |
|----------|-------------|
| `GET /api/transactions` | All transactions with filters |
| `GET /api/summary/monthly` | Monthly income vs expenses |
| `GET /api/summary/category` | Spending by category |
| `GET /api/spreadsheet/outgoing?fy=2025` | FY expenses with business splits |
| `GET /api/spreadsheet/incoming?fy=2025` | FY income by category |
| `GET /api/spreadsheet/rental?fy=2025` | Rental property schedule |
| `GET /api/ato/return?fy=2025` | Structured ATO return data |
| `GET /api/accounts/summary` | Account balances and net worth |
| `PATCH /api/transactions/<id>` | Update category or notes |
| `PATCH /api/transactions/<id>/split` | Override business split % |

## Architecture

```
config/              # Your personal config (gitignored)
  accounts.yaml      # Bank accounts and source-of-truth rules
  categories.yaml    # Categories, regex rules, tag rules
  tax.yaml           # ATO tax config (businesses, rental, depreciation)

etl/                 # Extract-Transform-Load pipeline
  parsers/           # One parser per bank format (PDF/CSV)
  categorizer.py     # Regex-based category matching
  tagger.py          # Multi-tag assignment
  splitter.py        # Business expense split engine
  normalizer.py      # Dedup, source-of-truth, insert pipeline
  cli.py             # CLI entry point

api/                 # Flask REST API
  server.py          # All endpoints

frontend/            # Vite + TypeScript dashboard
  src/main.ts        # Dashboard, transactions, budget, trends, tax views
  src/spreadsheet.ts # Financial year view (outgoing/incoming/rental/trips)
  src/api.ts         # API client and types

data/                # SQLite database (gitignored)
staging/             # Drop statement files here (gitignored)
tests/               # Tests with generic fixtures
scripts/             # Utility scripts (reingest, etc.)
```

## Development

```sh
# Run tests
python -m pytest tests/ -v

# Frontend dev server (hot reload)
cd frontend && npm run dev

# API server (auto-reload)
python -m api
```

## How it works

1. **Ingest**: Drop bank statement PDFs/CSVs into `staging/<source>/`. Run `ledger ingest`.
2. **Parse**: Source-specific parsers extract date, description, amount, and balance from each format.
3. **Normalise**: Dedup hashing prevents re-importing. Source-of-truth system marks ING payments to credit cards as transfers.
4. **Categorise**: Regex rules assign a category. Manual overrides in the UI train the learning system.
5. **Tag**: Multiple tags assigned per transaction for sub-classification.
6. **Split**: Business split engine allocates percentages based on category + tag rules.
7. **View**: Dashboard shows spending, income, budgets, trends, and ATO-ready tax reports.

## Author

Created by [Isaac Rowntree](https://github.com/isaacrowntree).
