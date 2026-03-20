---
title: Business Splits
description: How business expense allocation works for ATO tax reporting.
---

The business split engine automatically allocates a percentage of personal expenses to your business for ATO deduction purposes. This is configured in `config/tax.yaml` and computed by `etl/splitter.py`.

## How it works

There are two allocation mechanisms:

### 1. Implicit 100% -- Business categories

Any transaction categorised with a name starting with `Business:` is automatically allocated at 100% to your business:

- `Business: Hosting & Infrastructure` -- hosting, domains, DNS
- `Business: Software & Subscriptions` -- SaaS tools, dev subscriptions
- `Business: Equipment` -- cameras, computers, hardware
- `Business: Advertising & Marketing`
- `Business: Other`

No config needed for these -- the split engine detects the `Business:` prefix.

### 2. Partial allocation -- Split rules

Personal expenses that are partially business-use require explicit split rules in `config/tax.yaml`:

```yaml
businesses:
  - name: "My Business"
    abn: "00000000000"
    split_rules:
      - category: Utilities
        tag: internet
        business_pct: 50      # 50% of internet bill
      - category: Utilities
        tag: mobile
        business_pct: 20      # 20% of mobile bill
```

A split rule matches when **both** conditions are true:
1. The transaction's category matches `category`
2. The transaction has a tag matching `tag`

For example, a Vodafone bill categorised as "Utilities" with tag "mobile" would have 20% allocated as a business expense.

## The split pipeline

### At ingest time

If `tax_config` is passed to the normalizer, splits are computed during ingestion:

1. Transaction is categorised and tagged
2. `apply_splits()` checks if category starts with `Business:` (100% allocation) or matches a split rule
3. A row is written to `transaction_splits` with: `transaction_id`, `business_name`, `business_pct`, `business_amount`

### Backfill

More commonly, splits are computed after ingestion using the CLI:

```sh
ledger split --backfill --fy 2025
```

This:
1. Clears all existing splits for the given FY
2. Iterates every non-transfer transaction in the FY date range
3. Loads each transaction's category and tags
4. Computes splits using the rules from `config/tax.yaml`
5. Writes results to `transaction_splits`

### Manual override

Individual splits can be overridden via the API:

```sh
curl -X PATCH http://localhost:5050/api/transactions/42/split \
  -H "Content-Type: application/json" \
  -d '{"business_pct": 75, "business_name": "My Business"}'
```

Set `business_pct` to `0` to remove a split.

## Database schema

```sql
CREATE TABLE transaction_splits (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    business_name TEXT NOT NULL,
    business_pct REAL NOT NULL,
    business_amount REAL NOT NULL,
    UNIQUE(transaction_id, business_name)
);
```

A transaction can have splits for multiple businesses (the unique constraint is per business name).

## Viewing splits

### Dashboard

The Spreadsheet > Outgoing tab shows all expenses with business split columns: percentage, amount, and business name.

### API

`GET /api/spreadsheet/outgoing?fy=2025` returns each expense transaction with a `splits` array and convenience fields `biz_pct`, `biz_amount`, `biz_name`.

### ATO return

`GET /api/ato/return?fy=2025` aggregates splits per business and includes them in the business schedule section with total income, expenses, depreciation, and net profit/loss.

### CLI

```sh
ledger tax --fy 2025
```

Prints business expenses from splits, depreciation items, and manual entries.

## Tags are essential

Split rules depend on tags to distinguish sub-types within a category. Make sure your `config/categories.yaml` has tag rules for the relevant patterns:

```yaml
tag_rules:
  - tag: internet
    pattern: "TPG|SUPERLOOP"
  - tag: mobile
    pattern: "OPTUS|VODAFONE"
```

Without the correct tags, split rules will not match and the expense will not be allocated.

## Example workflow

1. Add a split rule to `config/tax.yaml`:
   ```yaml
   split_rules:
     - category: Utilities
       tag: internet
       business_pct: 50
   ```

2. Ensure a tag rule exists in `config/categories.yaml`:
   ```yaml
   tag_rules:
     - tag: internet
       pattern: "TPG|SUPERLOOP"
   ```

3. Ingest statements: `ledger ingest`

4. Backfill splits: `ledger split --backfill --fy 2025`

5. View results: `ledger tax --fy 2025` or check the dashboard Tax tab
