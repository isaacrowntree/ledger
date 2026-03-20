---
title: Installation
description: How to install Ledger and its dependencies on your machine.
---

## Requirements

- **Python 3.11+**
- **Node.js 18+** (for the frontend dashboard)
- **pip** (comes with Python)

## Clone and install

```sh
git clone https://github.com/isaacrowntree/ledger.git
cd ledger
```

### Python backend

Create a virtual environment and install in editable mode:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs all Python dependencies defined in `pyproject.toml`:

- `pdfplumber` -- PDF statement parsing
- `pyyaml` -- config file loading
- `flask` + `flask-cors` -- REST API
- `python-dotenv` -- environment variable loading

After installation the `ledger` CLI command is available inside the virtualenv.

### Frontend dashboard

```sh
cd frontend
npm install
npm run build
cd ..
```

The build output goes to `frontend/dist/` which the Flask API serves automatically.

For development with hot reload:

```sh
cd frontend
npm run dev
```

## Copy config files

Config files are gitignored. Copy the examples and customise them:

```sh
cp config/accounts.yaml.example config/accounts.yaml
cp config/categories.yaml.example config/categories.yaml
cp config/tax.yaml.example config/tax.yaml
```

See the [accounts](/docs/config-accounts), [categories](/docs/config-categories), and [tax](/docs/config-tax) guides for how to fill these in.

## Create staging directories

Statement files go into source-specific folders under `staging/`:

```sh
mkdir -p staging/ing staging/paypal staging/bankwest staging/hsbc staging/coles staging/amex staging/airbnb
mkdir -p staging/ing-csv staging/bankwest-csv
```

## Verify installation

```sh
ledger init
```

This creates the SQLite database at `data/ledger.db` and loads your config. You should see:

```
Database initialized and config loaded.
```

## Project structure

```
config/              # Your personal config (gitignored)
data/                # SQLite database (gitignored)
staging/             # Drop statement files here (gitignored)
etl/                 # Python ETL pipeline
api/                 # Flask REST API
frontend/            # Vite + TypeScript dashboard
tests/               # Test suite
scripts/             # Utility scripts
```
