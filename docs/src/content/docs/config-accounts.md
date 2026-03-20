---
title: Accounts Configuration
description: How to configure bank accounts, credit cards, and source-of-truth relationships in accounts.yaml.
---

The file `config/accounts.yaml` defines every bank account, credit card, and financial source that Ledger tracks. Copy from the example to get started:

```sh
cp config/accounts.yaml.example config/accounts.yaml
```

## Basic account definition

Each account needs a name, source type, and account type:

```yaml
accounts:
  - name: "My Checking Account"
    source_type: ing
    currency: AUD
    account_type: checking
    file_prefix: my_checking
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name (must be unique) |
| `source_type` | Yes | Parser identifier: `ing`, `paypal`, `bankwest`, `hsbc`, `coles`, `amex`, `airbnb` |
| `currency` | No | Default `AUD` |
| `account_type` | Yes | One of: `checking`, `savings`, `credit`, `loan`, `other` |
| `file_prefix` | No | Matches statement filenames to this account (see below) |
| `source_of_truth` | No | Set to `true` for credit cards / PayPal (see below) |
| `payment_patterns` | No | Regex patterns for payments TO this account from other accounts |
| `display` | No | Set to `0` to hide from the net worth panel |

## File prefix mapping

When you have multiple accounts with the same bank (e.g. multiple ING accounts), `file_prefix` maps statement filenames to accounts:

```yaml
accounts:
  - name: "Everyday Account"
    source_type: ing
    file_prefix: everyday

  - name: "Savings Account"
    source_type: ing
    file_prefix: savings
```

A file named `everyday_2025-01.pdf` in `staging/ing/` will be assigned to "Everyday Account". A file named `savings_2025-01.pdf` goes to "Savings Account".

If no prefix matches, the parser falls back to a default account name.

## Account types

- **checking** -- everyday transaction account
- **savings** -- savings / term deposit
- **credit** -- credit card (balances shown as negative in net worth)
- **loan** -- mortgage / loan (excluded from spending reports, balance shown as debt)
- **other** -- PayPal, Airbnb, etc.

## Source-of-truth accounts

This is the key feature that prevents double-counting. See the dedicated [source-of-truth guide](/docs/source-of-truth) for the full explanation.

When you pay a credit card from your bank account, the payment appears on both sides:
- Your bank: "BPAY COLES MASTERCARD -$500"
- Your credit card: individual purchases totalling $500

Without source-of-truth, both would be counted as spending. The solution:

```yaml
  - name: "My Credit Card"
    source_type: coles
    account_type: credit
    source_of_truth: true
    payment_patterns:
      - "COLES MASTERCARD"
      - "COLES CREDIT"
```

With this config:
- The credit card is marked as source-of-truth (its transactions are the "real" spending)
- Any transaction on a non-source-of-truth account (e.g. ING) matching `"COLES MASTERCARD"` is automatically marked as a **transfer**, not spending

### PayPal

PayPal works the same way. The bank sees "PAYPAL" but PayPal's CSV has the actual merchant names:

```yaml
  - name: "PayPal"
    source_type: paypal
    account_type: other
    source_of_truth: true
    payment_patterns:
      - "PAYPAL"
```

## Full example

```yaml
accounts:
  # Everyday accounts
  - name: "My Checking Account"
    source_type: ing
    currency: AUD
    account_type: checking
    file_prefix: my_checking

  - name: "My Savings"
    source_type: ing
    currency: AUD
    account_type: savings
    file_prefix: my_savings

  # Credit cards (source-of-truth)
  - name: "My Credit Card"
    source_type: bankwest
    currency: AUD
    account_type: credit
    source_of_truth: true
    payment_patterns:
      - "MY CREDIT CARD"
      - "BPAY.*MY CARD"

  # PayPal
  - name: "PayPal"
    source_type: paypal
    currency: AUD
    account_type: other
    source_of_truth: true
    payment_patterns:
      - "PAYPAL"

  # Airbnb (hidden from net worth)
  # - name: "Airbnb"
  #   source_type: airbnb
  #   currency: AUD
  #   account_type: other
  #   display: 0
```

## After editing

Run `ledger init` to reload the account config into the database:

```sh
ledger init
```
