# Ledger

Personal-finance tool: ingests Australian bank and card statements, reconciles
them, and estimates ATO tax returns. Python ETL (`etl/`), Flask API (`api/`),
React + TanStack dashboard (`frontend/`).

## This repo is public

Real financial data lives only in gitignored places: `config/*.yaml` (each has
a `.example` template), `data/` (the SQLite database and statement archive) and
`staging/`. Nothing personal goes into tracked files: code, tests, fixtures,
docs or commit messages.

- No real names (the owner's partner, tenants, family), addresses, account or
  card numbers, ABN/TFN, emails, or real transaction amounts and dates.
- Tests and examples use made-up values ("Partner", "Example St", round
  amounts). Write the example first rather than copying a real row and editing it.
- Committed history has been rewritten once to remove personal details. Avoid
  needing to do that again.

## Getting changes into main

Never commit straight to `main`. For every change:

1. Work on a branch.
2. Before merging, check the whole diff and the commit messages for personal
   data (see the list above), e.g.
   `git diff main... | rg '^\+' | rg -i '<names, addresses, amounts>'`.
3. Check the licence of any new dependency (Python or npm). It must be
   compatible with the repo's MIT licence.
4. Run the tests and the frontend build (below).
5. Merge into `main` with `--no-ff` and push.

## Running things

- Tests:
  `uv run --no-project --with pyyaml --with pdfplumber --with flask --with flask-cors --with python-dotenv --with pydantic --with pytest python -m pytest -q`
  (takes a few minutes). Dependencies are listed in `pyproject.toml`.
- API and dashboard: the same `uv run ...` without pytest, then `python -m api`.
  It serves on http://127.0.0.1:5050. Use 127.0.0.1, because `localhost` can
  resolve to IPv6, where another tool may be listening on the same port.
- Frontend: `cd frontend && npm run build` (type-checks, then builds into
  `frontend/dist`, which the API serves). `npm run dev` proxies `/api` to the
  Flask server.
- Read-only SQLite checks: `sqlite3 "file:data/ledger.db?mode=ro"`. Don't use
  `immutable=1`: the database runs in WAL mode, and immutable ignores the WAL,
  so recent changes don't show up.
- Back up `data/ledger.db` before any bulk change to the data.

## Conventions

- Every API endpoint that writes takes its body through a strict pydantic
  model in `api/schemas.py`. Add one for any new write endpoint.
- Changing a transaction's category also teaches a merchant rule
  (`category_rules_learned`). For one-off recategorisations (one Amazon order
  that was business equipment), delete the learned rule afterwards.
- Depreciating assets go in `config/depreciation.yaml` with a `paid:` entry, so
  the purchase is claimed as decline in value and not also as a business
  expense. Diminishing-value years are worked out when `years:` is omitted.
- The dashboard's shared layout pieces live in `frontend/src/components/layout.tsx`
  (page layout, period picker, status tag, meter, links to transactions). Reuse
  them rather than building one-off versions.
