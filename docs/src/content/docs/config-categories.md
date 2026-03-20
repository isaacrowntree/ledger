---
title: Categories Configuration
description: How to define categories, regex matching rules, and tag rules in categories.yaml.
---

The file `config/categories.yaml` defines spending categories, auto-categorisation rules, and tag rules. Copy from the example:

```sh
cp config/categories.yaml.example config/categories.yaml
```

The file has three sections: `categories`, `rules`, and `tag_rules`.

## Categories

Define all spending and income categories:

```yaml
categories:
  # Income
  - name: Salary
    is_income: true
  - name: Interest Income
    is_income: true

  # Expenses with optional monthly budgets
  - name: Groceries
    budget_monthly: 600
  - name: Eating Out
    budget_monthly: 300
  - name: Utilities
    budget_monthly: 300

  # Business (ATO deductible)
  - name: "Business: Hosting & Infrastructure"
  - name: "Business: Software & Subscriptions"

  # Financial
  - name: Transfers
  - name: Uncategorized
```

### Category fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique category name |
| `is_income` | No | Set `true` for income categories |
| `budget_monthly` | No | Monthly budget amount for the budget tracker |

**Naming convention:** Categories starting with `Business:` are automatically allocated at 100% to your business in the split engine. Categories starting with `Rental:` are for rental property expenses.

## Rules

Rules assign categories to transactions automatically. They are matched **top-to-bottom** and **first match wins**.

### Rule types

**Source-based rules** match all transactions from a source:

```yaml
rules:
  - type: source
    source_type: airbnb
    category: Airbnb Income
```

**Regex rules** match against the transaction description:

```yaml
  - type: regex
    pattern: "WOOLWORTHS|COLES |ALDI |IGA |HARRIS FARM|COSTCO"
    category: Groceries
```

Patterns are matched case-insensitively against the uppercase description.

### Rule ordering matters

Rules are evaluated top-to-bottom. Put specific patterns before generic ones:

```yaml
rules:
  # Specific: credit card interest (before mortgage interest)
  - type: regex
    pattern: "INTEREST CHARGED"
    category: Fees & Charges

  # Generic: mortgage interest
  - type: regex
    pattern: "INTEREST CHARGE"
    category: Mortgage
```

Business rules should go near the top so they match before personal categories:

```yaml
rules:
  # Business hosting (matched first)
  - type: regex
    pattern: "NAMECHEAP|DIGITAL ?OCEAN|CLOUDFLARE|AWS"
    category: "Business: Hosting & Infrastructure"

  # Business software
  - type: regex
    pattern: "GITHUB|FIGMA|ADOBE|OPENAI|ANTHROPIC"
    category: "Business: Software & Subscriptions"

  # Personal shopping (matched later)
  - type: regex
    pattern: "AMAZON|EBAY"
    category: Shopping
```

### Adding a new merchant

To add a merchant, append a regex rule. If the merchant name appears differently across banks, use alternation:

```yaml
  - type: regex
    pattern: "UBER ?EATS|DOORDASH|MENULOG|DELIVEROO"
    category: Eating Out
```

Tips for writing patterns:
- Use `|` to match multiple merchants in one rule
- Use `\\b` for word boundaries when a pattern is too broad
- Use `^` to anchor at the start of the description
- Use `\\d` to match digits (e.g. `7-ELEVEN \\d`)
- Use `\\.` to match a literal dot (e.g. `APPLE\\.COM`)
- Spaces matter: `"COLES "` (with trailing space) avoids matching "COLESLAW"

### Confidence levels

The categoriser assigns a confidence score:

| Match type | Confidence |
|-----------|------------|
| Source-based rule | 1.0 |
| Exact match | 0.9 |
| Regex match | 0.8 |
| Learned (from manual override) | 0.7 |
| Uncategorized | 0.0 |

## Tag rules

Tags are **orthogonal to categories** -- a transaction has one category but can have many tags. Tags enable sub-classification for reporting.

```yaml
tag_rules:
  # Transport sub-types
  - tag: flight
    pattern: "JETSTAR|QANTAS|VIRGIN AUSTRALIA"
  - tag: taxi
    pattern: "UBER TRIP|UBER AUSTRALIA|TAXI"
  - tag: train
    pattern: "OPAL|TRANSPORTFORNSW"

  # Insurance sub-types
  - tag: health-insurance
    pattern: "MEDIBANK|AMBULANCE"

  # Business sub-types (for ATO detail)
  - tag: biz-hosting
    pattern: "NAMECHEAP|DIGITAL ?OCEAN|CLOUDFLARE|AWS"
  - tag: biz-software
    pattern: "GITHUB|FIGMA|ADOBE"

  # Utility sub-types
  - tag: internet
    pattern: "TPG"
  - tag: mobile
    pattern: "OPTUS|VODAFONE"
```

Tags are used by the [business split engine](/docs/business-splits) to determine which portion of a bill is business-deductible (e.g. 50% of the `internet`-tagged utility bill).

### Tag rule fields

| Field | Required | Description |
|-------|----------|-------------|
| `tag` | Yes | Tag name (lowercase, hyphenated) |
| `pattern` | Yes | Regex pattern matched against description |
| `source_type` | No | Only match transactions from this source |

## Learned rules

When you manually change a transaction's category in the dashboard, the system stores the description-to-category mapping in the `category_rules_learned` database table. Next time a transaction with the same description appears, it will be auto-categorised with confidence 0.7.

## After editing

Run `ledger init` to reload categories into the database:

```sh
ledger init
```

To re-categorise existing transactions with new rules, re-ingest the archived files or use the dashboard to update individual transactions.
