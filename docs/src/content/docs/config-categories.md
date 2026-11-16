---
title: Categories Configuration
description: How to define categories, source rules, merchants, and tag rules in categories.yaml.
---

The file `config/categories.yaml` defines spending categories, source rules, the merchants that seed the categoriser, and tag rules. Copy from the example:

```sh
cp config/categories.yaml.example config/categories.yaml
```

The file has four sections: `categories`, `rules`, `merchants`, and `tag_rules` (plus optional `shared_rules`).

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

## How a transaction gets its category

The categoriser tries these in order and stops at the first that answers:

1. **Source rules** -- every row from a source (e.g. Airbnb payouts) belongs to one category.
2. **The merchant's own label** -- if this merchant has been filed before, it goes where the merchant is filed.
3. **The nearest labelled merchants** -- a merchant seen for the first time is compared with the ones you have already filed, using a local vector index. If the closest ones are similar enough and mostly agree, their category is used.
4. Otherwise the row stays **Uncategorized**, and the Transactions view shows the nearest merchants' categories as one-click suggestions.

There are no description patterns to maintain. Filing a transaction in the dashboard labels its merchant, which covers every later visit to it.

### Merchant keys

A merchant is matched on a normalised key, not the raw description. Receipt numbers, dates, card digits, reference codes, foreign-currency amounts and trailing suburbs or states are stripped, so these are all the same merchant:

```
WOOLWORTHS      0101     EXAMPLETOWN       NS
WOOLWORTHS 1234 SYDNEY NSW
WOOLWORTHS
```

Money in and money out are kept apart, so a merchant's refunds can be filed differently from its charges.

### Similarity

Descriptions are embedded locally through fastembed (no GPU, no network after the first model download), and the vectors live in the same SQLite file through sqlite-vec. Each labelled merchant and each settled transaction is indexed from its raw description; guesses are never used as neighbours. Choose the model with `LEDGER_EMBEDDER`:

| Value | Model | Size |
|---|---|---|
| `bge-small` (default) | BAAI/bge-small-en-v1.5, 384 dims | ~67 MB |
| `bge-base` | BAAI/bge-base-en-v1.5, 768 dims | ~210 MB, about 3x slower; no better in testing |
| `ngram` | character n-grams, no model | deterministic; used by the tests |

If a model can't be loaded the next one down is used. Changing embedder rebuilds the index on the next run. Compare them on your own ledger with `ledger categorise --evaluate`.

## Source rules

`rules:` holds only source rules. Each matches every transaction from a source:

```yaml
rules:
  - type: source
    source_type: airbnb
    category: Airbnb Income
```

The old `type: regex` and `type: exact` category rules no longer exist. List the merchants under `merchants:` instead.

## Merchants

`merchants:` maps a description, written as it appears on a statement, to a category. These seed the categoriser before it has seen your own filing:

```yaml
merchants:
  WOOLWORTHS: Groceries
  COLES: Groceries
  UBER EATS: Eating Out
  NETFLIX.COM: Subscriptions
  NAMECHEAP.COM: "Business: Hosting & Infrastructure"
  SALARY DEPOSIT EMPLOYER PTY LTD: Salary
```

Tips:
- Copy the description from a real statement line. Noise is ignored: receipt numbers, dates, card digits, reference codes, trailing suburbs and states, and how it was paid (`Visa Purchase`, `Direct Debit`, `Osko Payment` and similar), so one entry covers every bank that prints the merchant.
- You do not need every merchant. A few per category is enough for the nearest-neighbour step to file similar ones.
- A category you set in the dashboard always wins over this list. Every category named here must exist under `categories:`.

### Where labels come from

Each merchant label records its origin. A weaker origin never overwrites a stronger one:

| Origin | Set by | Confidence written to rows |
|--------|--------|------------|
| `human` | You, changing a category in the dashboard or choosing on "Merchants to check" | 0.95 |
| `seed` | The `merchants:` list in this file | 0.9 |
| `llm` | Claude, when it was confident (`ledger categorise --import`) | 0.85 |
| `bootstrap` | The categories already in your ledger (`ledger categorise --seed`) | 0.9 |

Source rules give 0.95, a nearest-merchant guess 0.6, and Uncategorized 0.0. Confidence 1.0 is reserved for rows a person confirmed, which keeps the "needs review" flag working.

When you change a category in the dashboard, the merchant is labelled and the dashboard offers to move its other unconfirmed rows too. Corrections from the old `category_rules_learned` table are imported once as `human` labels and the table is dropped.

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

## After editing

Run `ledger init` to reload categories into the database:

```sh
ledger init
```

New `merchants:` entries are labelled the next time the categoriser runs (on ingest or any `ledger categorise` command). To apply them to rows already in the ledger:

```sh
ledger categorise --rerun --dry-run   # show what would move
ledger categorise --rerun             # recategorise every row you have not confirmed
```

See the [CLI reference](/docs/cli#ledger-categorise) for the rest of `ledger categorise`.
