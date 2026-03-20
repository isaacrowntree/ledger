---
title: API Reference
description: REST API endpoints served by the Flask backend at localhost:5050.
---

The Flask API serves data to the dashboard and can be used directly for custom reporting. All endpoints return JSON.

## Transactions

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/transactions` | List transactions with filters |
| `PATCH` | `/api/transactions/<id>` | Update category or notes |
| `PATCH` | `/api/transactions/<id>/split` | Override business split percentage |

### GET /api/transactions

Query parameters:

| Param | Description |
|-------|-------------|
| `from` | Start date (YYYY-MM-DD) |
| `to` | End date (YYYY-MM-DD) |
| `category` | Filter by category name |
| `account` | Filter by account name |
| `search` | Search description (LIKE match) |
| `exclude_loans` | Exclude loan accounts (default: `true`) |
| `exclude_transfers` | Exclude transfer transactions (default: `true`) |
| `limit` | Max results |

Example:

```sh
curl "http://localhost:5050/api/transactions?from=2025-01-01&category=Groceries&limit=50"
```

### PATCH /api/transactions/\<id\>

Update a transaction's category or notes. When changing category, the system stores a learned rule for future auto-categorisation.

```sh
curl -X PATCH http://localhost:5050/api/transactions/42 \
  -H "Content-Type: application/json" \
  -d '{"category_name": "Eating Out", "notes": "Dinner with friends"}'
```

### PATCH /api/transactions/\<id\>/split

Override the business split percentage for a transaction.

```sh
curl -X PATCH http://localhost:5050/api/transactions/42/split \
  -H "Content-Type: application/json" \
  -d '{"business_pct": 50, "business_name": "My Business"}'
```

Set `business_pct` to `0` to remove the split.

## Summaries

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/summary/monthly` | Monthly income vs expenses |
| `GET` | `/api/summary/category` | Spending totals by category |
| `GET` | `/api/summary/trends` | Monthly spending by category over time |
| `GET` | `/api/summary/tax` | FY tax summary by category |
| `GET` | `/api/summary/year-review` | Comprehensive year-in-review stats |
| `GET` | `/api/summary/top-merchants` | Top merchants by spend |
| `GET` | `/api/budget-vs-actual` | Budget vs actual for budgeted categories |

Common query parameters: `year`, `from`, `to`, `account`, `fy` (financial year, e.g. `2025`).

### GET /api/budget-vs-actual

```sh
curl "http://localhost:5050/api/budget-vs-actual?month=2025-03"
```

Returns each budgeted category with `budget`, `actual`, and `remaining` amounts.

## Spreadsheet (Financial Year)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/spreadsheet/outgoing?fy=2025` | All FY expenses with business splits |
| `GET` | `/api/spreadsheet/incoming?fy=2025` | All FY income by category |
| `GET` | `/api/spreadsheet/rental?fy=2025` | Rental property schedule |
| `GET` | `/api/spreadsheet/work-trips?fy=2025` | Work trips and WFH deductions |

## ATO Return

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/ato/return?fy=2025` | Structured ATO return data |

Returns a JSON object with sections: `income` (salary, interest, tax withheld), `rental` (per-property schedule), `business` (per-business P&L), `deductions` (WFH, work trips), `manual_entries`, and `spouse` info.

## Reference Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/categories` | All categories with budgets |
| `GET` | `/api/accounts` | All accounts |
| `GET` | `/api/accounts/summary` | Account balances and net worth |
| `GET` | `/api/tags` | All tags with counts and totals |

## Holdings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/holdings` | All holdings (shares, property, super) |
| `POST` | `/api/holdings` | Create or update a holding |
| `GET` | `/api/holdings/<id>/events` | Asset events for a holding |
| `POST` | `/api/holdings/<id>/events` | Add an asset event (buy, sell, dividend, valuation) |

## Work Trips

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/work-trips?fy=2025` | List work trips |
| `POST` | `/api/work-trips` | Create a work trip with expenses |
| `DELETE` | `/api/work-trips/<id>` | Delete a work trip |

## Tax Overrides

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tax-overrides?fy=2025` | List tax overrides |
| `POST` | `/api/tax-overrides` | Create/update a tax override |
| `DELETE` | `/api/tax-overrides/<id>` | Delete a tax override |
