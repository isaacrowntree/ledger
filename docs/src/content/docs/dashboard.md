---
title: Dashboard
description: Overview of the web dashboard tabs and what each view shows.
---

The Ledger dashboard is a Vite + TypeScript single-page application served by the Flask API at [http://localhost:5050](http://localhost:5050).

Start it with:

```sh
python -m api
```

## Dashboard tab

The main overview with:

- **Monthly income vs expenses** bar chart for the selected year
- **Category breakdown** showing spending by category as a donut/bar chart
- **Budget vs actual** tracker for categories with `budget_monthly` set in your config
- **Top merchants** by total spend
- **Year-over-year comparison** against the previous year

Filter by year and account. Transfers and loan transactions are excluded by default.

## Transactions tab

A searchable, filterable table of all transactions with:

- Date, description, amount, category, account, tags
- **Search** by description text
- **Filter** by date range, category, and account
- **Edit** category inline -- the system learns from your manual overrides for future auto-categorisation
- **Edit** notes on individual transactions
- Transfer transactions can be shown or hidden

## Spreadsheet tab

A financial-year view that replaces the traditional Excel tracking spreadsheet. Has sub-tabs:

### Outgoing

All expense transactions for the selected FY, ordered by date. Each row shows:
- Date, description, amount, category, account
- Business split percentage and amount (if applicable)
- Business name for split transactions

### Incoming

All income transactions for the selected FY, grouped by category (salary, interest, rental income, freelance, etc.).

### Rental

Rental property schedule matching the ATO format:
- Gross rental income and your ownership share
- Expenses mapped to ATO labels (body corporate, interest, water, repairs)
- Depreciation items from config
- Net rental income/loss calculation

### Work trips

Work-related travel expenses grouped by trip:
- Flight, accommodation, car, meals breakdowns
- WFH deduction calculation using the ATO fixed-rate method

## Tax tab

Structured ATO individual tax return view:

- **Item 1: Salary** -- total salary income and tax withheld
- **Item 10: Interest** -- bank interest income
- **Item 21: Rental** -- full rental schedule per property with income, expenses, depreciation, and net rent
- **Business schedule** -- per-business income, expenses (from splits), depreciation, and net profit/loss
- **Deductions** -- WFH amount, work trip totals
- **Manual entries** -- items from `tax.yaml` that are not in bank transactions

Select the financial year to view different periods. All amounts are computed from your transaction data plus config entries.

## Net worth / Accounts tab

Account balances and portfolio summary:

- **Bank accounts** with last known balance (extracted from statement data)
- **Credit cards** showing outstanding balance as negative
- **Holdings** -- shares, property, super, other assets with cost basis and current value
- **Total net worth** calculation

Balances are derived from the most recent transaction's raw statement data (closing balance field), falling back to sum of all transaction amounts.
