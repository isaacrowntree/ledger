---
title: Dashboard
description: Overview of the web dashboard tabs and what each view shows.
---

The Ledger dashboard is a Vite + TypeScript single-page application served by the Flask API at [http://localhost:5050](http://localhost:5050).

Start it with:

```sh
python -m api
```

### Running it on another machine

To keep the dashboard running on a home server (a Raspberry Pi, a NAS) and open
it from other devices, set where it listens:

```sh
LEDGER_HOST=0.0.0.0 LEDGER_PORT=5050 python -m api
```

| Variable | Default | Meaning |
|---|---|---|
| `LEDGER_HOST` | `127.0.0.1` | Address to listen on. `0.0.0.0` serves your local network |
| `LEDGER_PORT` | `5050` | Port |
| `LEDGER_DEBUG` | on for `127.0.0.1`, off otherwise | Flask's debugger and auto-reload. Refused on any non-loopback address, because its error page runs any code typed into it |

The dashboard has no login. Serve it only on a network you trust, and never
forward it to the internet; reach it from outside through a VPN or an SSH
tunnel instead.

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
- **Edit** category inline -- this labels the merchant, so its later transactions are filed the same way, and offers to move its other unconfirmed rows too
- **Suggestions** on Uncategorized rows -- one-click chips with the categories of the most similar merchants you have filed
- **Edit** notes on individual transactions
- Transfer transactions can be shown or hidden

## Merchants to check

Under Money. Lists merchants flagged for a person's decision, for example where Claude filed a merchant one way and the old rules filed it another, or where Claude was unsure. Each card shows the merchant, how many rows and how much money it covers, a few example descriptions, and the candidate categories.

Choosing a category labels the merchant as yours (origin `human`) and refiles every unconfirmed row from it at confidence 1.0. The biggest amounts are listed first.

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
