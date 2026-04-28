---
title: Ingestion methods
description: Three ways to get bank statements into Ledger — Playwright MCP, manual download, or Basiq API.
---

Ledger supports three ingestion paths. They all funnel into the same normalise / dedup / categorise / tag pipeline, so you can mix and match per bank.

| Method | Setup cost | Reliability | Best for |
|---|---|---|---|
| [Playwright MCP](#1-playwright-mcp) | Author a skill once per bank | High once written | Banks you ingest from often |
| [Manual download](#2-manual-download) | None | Always works | One-off catch-ups, banks without a skill |
| [Basiq API](#3-basiq-api) | One-time consent flow | Flaky — connections expire | Quick sync when it happens to be working |

## 1. Playwright MCP

Browser automation via the [Playwright MCP server](https://github.com/microsoft/playwright-mcp). You log in (so 2FA / random keypads / captcha all work normally), and Claude Code automates everything after that — navigating to the statements page, iterating accounts, intercepting downloads, and saving files into `staging/<source>/` with the correct filename prefix.

### Setup

```sh
claude mcp add playwright -- npx -y @playwright/mcp@latest
```

Then invoke from Claude Code:

```
/ingest-bank-statements
```

This runs the umbrella skill at `.claude/skills/ingest-bank-statements.md`, which routes to a bank-specific skill if one exists.

### Existing bank skills

- **ING Australia** — `.claude/skills/ing-statements.md`. Handles login pause for the randomised keypad, iterates each account in the dropdown, downloads up to 7 years of statements per account.

### Adding a new bank

Copy `ing-statements.md` and adapt:

1. **Login** — Navigate to the bank's login URL. Fill the username field. **Stop and ask the user** to handle the password, 2FA, or keypad. Never store or type credentials.
2. **Navigate** — Find selectors for the e-statements / exports page. Many bank sites use Web Components, where `page.evaluate()` can't see the DOM but `getByRole({ name })` accessibility selectors still work.
3. **Download loop** — Use `browser_run_code` with `Promise.all([page.waitForEvent('download'), <click>])` to intercept download events. Save with `download.saveAs(stagingDir + filename)` because most banks serve generic filenames like `Statement.pdf`.
4. **Filename** — `{file_prefix}_{YYYY-MM-DD}_to_{YYYY-MM-DD}.pdf` (or `.csv`). The `file_prefix` must match an entry in `config/accounts.yaml` so the parser binds to the right account.

### Partial flow (no skill yet)

If there is no skill for your bank, you can still get help once you're logged in:

1. Open the bank's site via Playwright MCP.
2. Log in manually and navigate to the e-statements page.
3. Ask Claude to take a snapshot, find the statement list, and run a download loop to save them all into `staging/<source>/`.

If it goes well, capture what worked into a new skill file under `.claude/skills/`.

## 2. Manual download

Always works, no setup. The fallback when nothing else does.

1. Log into the bank in a normal browser.
2. Download statements (PDF or CSV — whichever the parser supports for that source).
3. Drop files into the matching `staging/` folder:

| Source | Folder | Format |
|---|---|---|
| ING | `staging/ing/` | `*.pdf` |
| ING (CSV export) | `staging/ing-csv/` | `*.csv` |
| PayPal | `staging/paypal/` | `*.csv` |
| Bankwest | `staging/bankwest/` | `*.pdf` |
| Bankwest (CSV) | `staging/bankwest-csv/` | `*.csv` |
| HSBC | `staging/hsbc/` | `*.pdf` |
| Coles Mastercard | `staging/coles/` | `*.pdf` |
| Amex | `staging/amex/` | `*.csv` |
| Airbnb | `staging/airbnb/` | `*.csv` |

4. Prefix filenames with the `file_prefix` from `config/accounts.yaml` so the parser binds to the right account. Example: `isaac_business_2025-07-01_to_2025-09-30.pdf`.

5. Run:

```sh
ledger ingest
# or scope to one source
ledger ingest --source ing
# or preview only
ledger ingest --dry-run
```

Processed files are moved to `data/archive/` so re-running is safe.

## 3. Basiq API

[Basiq](https://basiq.io) is an Australian open-banking aggregator. One consent flow gives Ledger read-only access to ING, HSBC, Bankwest, and Coles.

It's the fastest sync **when it works**. In practice, connections expire, MFA re-prompts on the bank side, and Basiq returns 5xx more often than you'd want. Treat it as opportunistic — if the next sync fails, fall back to Playwright or manual download instead of debugging.

### One-time setup

Add your API key to `.env`:

```
BASIQ_API_KEY=your-base64-encoded-api-key
```

Then run:

```sh
ledger connect
```

This prints a consent URL. Open it in a browser, link your bank accounts, then come back to the terminal. State is persisted to `data/basiq_state.json`.

### Sync

```sh
ledger sync
# scope to one source
ledger sync --source ing
# only fetch since a date
ledger sync --since 2025-01-01
# preview only
ledger sync --dry-run
```

`since` is auto-detected from the most recent `basiq:%` transaction in the DB if you don't pass it.

### Supported institutions

Defined in `etl/basiq.py`:

| Source | Basiq institution ID |
|---|---|
| ING | `AU00201` |
| HSBC | `AU07201` |
| Bankwest | `AU00401` |
| Coles | `AU15301` |

Other banks need Method 1 or 2.

### Troubleshooting

- **"No bank connections found"** — run `ledger connect`; consent may have expired.
- **HTTP 401** — check `BASIQ_API_KEY`.
- **Partial history** — some institutions return only ~90 days. Backfill earlier periods using Method 1 or 2.

## After ingestion

All three methods feed the same pipeline:

1. **Dedup** — SHA-256 transaction hash.
2. **Source-of-truth** — credit-card payments from your bank are auto-marked as transfers (see [Source of Truth](/docs/source-of-truth)).
3. **Categorise** — regex rules from `config/categories.yaml`.
4. **Tag** — multi-tag rules.

Then:

```sh
ledger split --backfill --fy 2025  # business splits for tax
ledger dedup                        # cross-account duplicate resolution
python -m api                       # dashboard
```
