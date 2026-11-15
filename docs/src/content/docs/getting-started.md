---
title: Getting Started
description: First-time walkthrough from config setup to viewing your dashboard.
---

This guide walks you through your first ingestion and dashboard session after [installation](/docs/installation).

## 1. Configure your accounts

Edit `config/accounts.yaml` to define your bank accounts. At minimum you need one account:

```yaml
accounts:
  - name: "My Checking Account"
    source_type: ing
    currency: AUD
    account_type: checking
    file_prefix: my_checking
```

The `file_prefix` maps statement filenames to accounts. A file named `my_checking_2025-01.pdf` will be matched to "My Checking Account".

See the [accounts config guide](/docs/config-accounts) for credit cards, source-of-truth setup, and more.

## 2. Configure categories and merchants

Edit `config/categories.yaml`. The example file has sensible defaults for Australian expenses (groceries, transport, utilities, etc.). List a few merchants you use, written as they appear on a statement:

```yaml
merchants:
  WOOLWORTHS: Groceries
  COLES: Groceries
  UBER EATS: Eating Out
```

Receipt numbers, dates, card digits and suburbs are ignored. A merchant you have not listed is filed like the most similar ones you have, and you teach it the rest from the dashboard. See the [categories guide](/docs/config-categories).

## 3. Configure tax (optional)

If you want ATO tax reporting, edit `config/tax.yaml` with your business details, rental properties, and WFH deductions. See the [tax config guide](/docs/config-tax).

## 4. Initialise the database

```sh
ledger init
```

This creates the SQLite database at `data/ledger.db` and loads categories and accounts from your config files.

## 5. Drop in your first statements

Place statement files in the appropriate `staging/` subfolder:

| Source | Folder | Accepted files |
|--------|--------|---------------|
| ING PDF | `staging/ing/` | `*.pdf` |
| ING CSV | `staging/ing-csv/` | `*.csv` |
| PayPal CSV | `staging/paypal/` | `*.csv` |
| Bankwest PDF | `staging/bankwest/` | `*.pdf` |
| Bankwest CSV | `staging/bankwest-csv/` | `*.csv` |
| HSBC PDF | `staging/hsbc/` | `*.pdf` |
| Coles Mastercard PDF | `staging/coles/` | `*.pdf` |
| Amex CSV | `staging/amex/` | `*.csv` |
| Airbnb CSV | `staging/airbnb/` | `*.csv` |

## 6. Ingest

Preview what will happen without writing to the database:

```sh
ledger ingest --dry-run
```

When you are happy, run the real ingest:

```sh
ledger ingest
```

Each processed file is moved from `staging/` to `data/archive/` so it will not be re-imported.

The ETL pipeline for each transaction:
1. **Parse** -- extract date, description, amount from the source format
2. **Dedup** -- SHA-256 hash prevents re-importing the same transaction
3. **Source-of-truth** -- payments to credit cards from your bank account are auto-marked as transfers
4. **Categorise** -- source rules, then the merchant's label, then the nearest labelled merchants (or "Uncategorized")
5. **Tag** -- multiple tags applied for sub-classification
6. **Insert** -- written to SQLite with full audit trail

## 7. Start the dashboard

```sh
python -m api
```

Open [http://localhost:5050](http://localhost:5050) in your browser. You will see:

- **Dashboard** -- monthly income vs expenses chart, category breakdown, budget tracking
- **Transactions** -- searchable, filterable list of all transactions
- **Spreadsheet** -- financial year view with outgoing/incoming/rental tabs
- **Tax** -- ATO return view with structured sections

## 8. Fix uncategorized transactions

In the Transactions view, filter by category "Uncategorized". Each row shows suggestion chips from the most similar merchants you have filed; click one, or change the category yourself. Either way the merchant is labelled, so its later transactions are filed the same way, and the dashboard offers to move its other rows too.

If you already have a categorised ledger, label every merchant in it once, then recategorise the rows you have not confirmed:

```sh
ledger categorise --seed
ledger categorise --rerun
```

For a large backlog, `ledger categorise --export` and `--import` let Claude file the pending rows (see the [CLI reference](/docs/cli#ledger-categorise)).

## Next steps

- Set up [business splits](/docs/business-splits) for tax deductions
- Understand the [source-of-truth system](/docs/source-of-truth) to prevent double-counting
- Learn about [all CLI commands](/docs/cli)
- Browse the [API reference](/docs/api)
