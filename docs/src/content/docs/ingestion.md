---
title: Ingestion methods
description: Two ways to get bank statements into Ledger — Playwright MCP or manual download.
---

Ledger supports two ingestion paths. They both funnel into the same normalise / dedup / categorise / tag pipeline, so you can mix and match per bank.

| Method | Setup cost | Reliability | Best for |
|---|---|---|---|
| [Playwright MCP](#1-playwright-mcp) | Author a skill once per bank | High once written | Banks you ingest from often |
| [Manual download](#2-manual-download) | None | Always works | One-off catch-ups, banks without a skill |

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
| Coles Mastercard (CSV) | `staging/coles-csv/` | `*.csv` |
| Amex | `staging/amex/` | `*.csv` |
| Airbnb | `staging/airbnb/` | `*.csv` |

4. Prefix filenames with the `file_prefix` from `config/accounts.yaml` so the parser binds to the right account. Example: `business_2025-07-01_to_2025-09-30.pdf`.

5. Run:

```sh
ledger ingest
# or scope to one source
ledger ingest --source ing
# or preview only
ledger ingest --dry-run
```

Processed files are moved to `data/archive/` so re-running is safe.

## After ingestion

Both methods feed the same pipeline:

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
