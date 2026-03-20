# Contributing to Ledger

Thanks for your interest in contributing! Ledger is a personal finance tool for Australian bank statements and ATO tax reporting.

## Getting started

```sh
git clone https://github.com/isaacrowntree/ledger.git
cd ledger
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pip install pytest

# Copy example configs
cp config/accounts.yaml.example config/accounts.yaml
cp config/categories.yaml.example config/categories.yaml
cp config/tax.yaml.example config/tax.yaml

# Run tests
python -m pytest tests/ -v
```

## Project structure

```
etl/                 # ETL pipeline (parsers, categoriser, normaliser, splitter)
  parsers/           # One parser per bank format
api/                 # Flask REST API
frontend/            # Vite + TypeScript dashboard
config/              # Personal config (gitignored, .example templates in repo)
tests/               # Tests with generic fixtures in tests/fixtures/
docs/                # Starlight documentation site
scripts/             # Utility scripts
```

## How to contribute

### Adding a new bank parser

The most common contribution. See the [Adding a Bank](https://isaacrowntree.github.io/ledger/adding-a-bank/) guide.

1. Create `etl/parsers/your_bank.py` implementing `BaseParser`
2. Register it in `etl/cli.py` under `PARSERS`
3. Add tests
4. Update the README supported formats table

### Improving categorisation rules

The `.example` config has ~50 common Australian merchant patterns. If you have patterns that would be useful to others, add them to `config/categories.yaml.example`.

### Bug fixes and improvements

1. Fork the repo
2. Create a feature branch (`git checkout -b fix/something`)
3. Make your changes
4. Run tests (`python -m pytest tests/ -v`)
5. Submit a PR

## Code style

- Python: follow existing patterns, no strict formatter enforced
- TypeScript: follows existing patterns, Vite handles builds
- Keep it simple — avoid over-engineering

## Tests

Tests use generic fixture configs in `tests/fixtures/` (not personal data). All tests should pass without any real bank statements or personal config.

```sh
python -m pytest tests/ -v
```

## Documentation

Docs use [Starlight](https://starlight.astro.build/) (Astro). To run locally:

```sh
cd docs
npm install
npm run dev
```

## Important notes

- **Never commit personal data** — `config/*.yaml`, `data/`, and `staging/` are gitignored
- **Config templates** — if you add a new config key, update the corresponding `.example` file
- **No PII in tests** — tests use `tests/fixtures/` with generic data

## License

MIT
