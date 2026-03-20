---
title: Tax Configuration
description: How to configure ATO tax settings including businesses, rental properties, depreciation, and WFH deductions.
---

The file `config/tax.yaml` configures everything needed for Australian Tax Office (ATO) reporting. Copy from the example:

```sh
cp config/tax.yaml.example config/tax.yaml
```

## Financial year

Set the current financial year. FY 2025 means the year ending 30 June 2025 (1 Jul 2024 -- 30 Jun 2025):

```yaml
financial_year: 2025
```

## Taxpayer

Basic taxpayer info used in the ATO return view:

```yaml
taxpayer:
  name: "Your Name"
  spouse:
    name: "Partner Name"
    taxable_income: 0
```

## Businesses

Define businesses for the ATO Business & Professional Items schedule. Each business has an ABN and split rules:

```yaml
businesses:
  - name: "My Business"
    abn: "00000000000"
    activity: "Web development and design"
    split_rules:
      - category: Utilities
        tag: internet
        business_pct: 50
      - category: Utilities
        tag: mobile
        business_pct: 20
```

### How split rules work

There are two ways transactions get allocated to a business:

1. **Implicit 100%** -- any category starting with `Business:` is automatically allocated at 100% to the first matching business. No split rule needed.

2. **Partial allocation via split rules** -- personal categories that are partially business-use. Each rule matches on both `category` and `tag`:

```yaml
split_rules:
  - category: Utilities     # matches the transaction's category
    tag: internet            # AND matches a tag on the transaction
    business_pct: 50         # allocate 50% as business expense
```

See the [business splits guide](/docs/business-splits) for the full explanation.

## Rental properties

Define rental properties for the ATO rental schedule (Item 21):

```yaml
rental_properties:
  - name: "MY PROPERTY"
    address: "123 Main St, Suburb NSW 2000"
    ownership_pct: 50
    rental_weeks: { 2025: 20 }
    income_category: "Airbnb Income"
    expense_mapping:
      - ato_label: "Body corporate fees"
        category: "Strata"
      - ato_label: "Interest on loans"
        category: "Mortgage"
      - ato_label: "Water charges"
        tag: "water"
      - ato_label: "Repairs and maintenance"
        category: "Rental: Maintenance"
```

### Rental property fields

| Field | Description |
|-------|-------------|
| `name` | Property identifier |
| `address` | Street address (for ATO return) |
| `ownership_pct` | Your ownership share (e.g. 50 for joint ownership) |
| `rental_weeks` | Map of FY to weeks rented (e.g. `{ 2025: 20 }`) |
| `income_category` | Category name for rental income transactions |
| `expense_mapping` | Maps expense categories/tags to ATO labels |

### Expense mapping

Each mapping entry links a transaction category or tag to an ATO rental schedule label. The system automatically sums matching transactions and applies the ownership percentage:

- Use `category` to match by spending category
- Use `tag` to match by transaction tag (useful when one category like "Utilities" has sub-types)

## Work-from-home deductions

Configure WFH using the ATO fixed-rate method ($0.67/hour):

```yaml
work_deductions:
  wfh:
    rate_per_hour: 0.67
    entries:
      2025: { weeks: 48, allocation_pct: 100 }
```

The calculation is: `weeks x (allocation_pct / 100) x 38 hours/week x rate_per_hour`

For example, 48 weeks at 100% = 48 x 1.0 x 38 x $0.67 = $1,220.64

## Depreciation schedules

Add depreciating assets for business or rental:

```yaml
depreciation_schedules:
  - name: "Business equipment"
    business: "My Business"
    type: "business"
    items:
      - description: "Laptop"
        amount: 500
        fy: 2025
      - description: "Camera"
        amount: 300
        fy: 2025

  - name: "Rental fixtures"
    property: "MY PROPERTY"
    type: "rental"
    items:
      - description: "Air conditioner"
        amount: 200
        fy: 2025
```

The `business` or `property` field links the schedule to a business or rental property. The `type` field is either `"business"` or `"rental"`.

## Manual entries

For amounts that do not appear in bank transactions (e.g. tax withheld from payment summaries):

```yaml
manual_entries:
  2025:
    - label: "Tax withheld"
      amount: 50000
      section: "income"
      notes: "From payment summary"
```

## Running the tax summary

From the CLI:

```sh
ledger tax --fy 2025
```

Or view the full ATO return structure in the dashboard under the Tax tab, which calls `GET /api/ato/return?fy=2025`.
