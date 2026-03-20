---
title: Source-of-Truth System
description: How deduplication and source-of-truth accounts prevent double-counting of transactions.
---

The source-of-truth system is the mechanism that prevents double-counting when the same spending appears on multiple accounts.

## The problem

When you pay a credit card from your bank account, the payment appears twice:

1. **Bank account (ING):** "BPAY COLES MASTERCARD" for -$500
2. **Credit card (Coles):** 20 individual purchases totalling $500

Without intervention, you would count $1,000 of spending when only $500 was actually spent.

Similarly with PayPal: your bank sees "PAYPAL -$50" but PayPal's CSV shows the actual merchant "SPOTIFY SUBSCRIPTION -$15.99" and "UBER EATS -$34.01".

## The solution

Mark the account with the **detailed transaction data** as the "source of truth". The lump-sum payment on the other account is automatically reclassified as a transfer.

### Configuration

In `config/accounts.yaml`, set `source_of_truth: true` on the detailed account, and add `payment_patterns` listing how the payment appears on other accounts:

```yaml
accounts:
  # Bank account (NOT source-of-truth)
  - name: "My Checking Account"
    source_type: ing
    account_type: checking
    file_prefix: my_checking

  # Credit card (IS source-of-truth)
  - name: "My Credit Card"
    source_type: coles
    account_type: credit
    source_of_truth: true
    payment_patterns:
      - "COLES MASTERCARD"
      - "COLES CREDIT"
```

### How it works at ingest time

The logic lives in `etl/normalizer.py` in the `is_payment_to_source_of_truth()` function:

1. When a transaction is being inserted, the system checks if it comes from a **non-source-of-truth** account (e.g. ING)
2. If the transaction description matches any `payment_patterns` from a source-of-truth account, it is marked as a **transfer** with category "Transfers" and `is_transfer = 1`
3. Transactions from source-of-truth accounts are **never suppressed** -- they always keep their real category

This means:
- ING's "BPAY COLES MASTERCARD" becomes category "Transfers" and is excluded from spending reports
- Coles's "WOOLWORTHS" stays as "Groceries"

### What gets excluded

Throughout the API, transfers are excluded from spending calculations by default:

```sql
WHERE t.is_transfer = 0 AND a.account_type NOT IN ('loan')
```

The `exclude_transfers` and `exclude_loans` query parameters on API endpoints control this (both default to `true`).

## Deduplication

Separately from source-of-truth, every transaction gets a **dedup hash** (SHA-256) to prevent the same transaction from being imported twice.

The hash is computed in `etl/normalizer.py` in `compute_dedup_hash()`:

| Source | Hash input |
|--------|-----------|
| PayPal | Transaction reference ID (unique per PayPal transaction) |
| ING | `date|description|amount|source_file_stem` (includes filename to differentiate same-amount transfers between ING accounts) |
| Basiq | Basiq transaction ID |
| All others | `date|description|amount` |

The `dedup_hash` column in the `transactions` table has a `UNIQUE` constraint. If a hash already exists, the transaction is silently skipped during ingest.

## Common patterns

### Multiple credit cards

Each credit card gets its own source-of-truth entry:

```yaml
  - name: "Bankwest Credit"
    source_type: bankwest
    account_type: credit
    source_of_truth: true
    payment_patterns:
      - "BANKWEST CREDIT"

  - name: "HSBC Credit"
    source_type: hsbc
    account_type: credit
    source_of_truth: true
    payment_patterns:
      - "HSBC CARDS"
      - "HSBC CREDIT"
```

### PayPal

PayPal transactions often appear as "PAYPAL" on bank statements but have full merchant details in the PayPal CSV export:

```yaml
  - name: "PayPal"
    source_type: paypal
    account_type: other
    source_of_truth: true
    payment_patterns:
      - "PAYPAL"
```

### Accounts that are NOT source-of-truth

Regular bank accounts (checking, savings) do not need any special config. They just need `name`, `source_type`, `account_type`, and optionally `file_prefix`.

## Debugging

Run `ledger ingest --dry-run` to preview what would happen. Transactions auto-marked as transfers will show `[TRANSFER]` in the output:

```
  [DRY RUN] [TRANSFER] 2025-01-15   -500.00  BPAY COLES MASTERCARD                     -> Transfers
  [DRY RUN] 2025-01-15    -45.00  WOOLWORTHS BONDI                          -> Groceries
```
