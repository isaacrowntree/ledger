---
title: Adding a Bank Parser
description: How to implement a new parser for an unsupported bank or statement format.
---

Ledger supports adding new banks by implementing a parser class. Each parser converts a bank-specific statement format (PDF or CSV) into a list of `RawTransaction` objects.

## Steps

### 1. Create the parser file

Add a new file in `etl/parsers/`. Name it `<bank>_<format>.py`:

```
etl/parsers/my_bank_csv.py
```

### 2. Implement the BaseParser

Every parser must extend `BaseParser` from `etl/parsers/base.py`:

```python
from abc import ABC, abstractmethod
from pathlib import Path
from etl.models import RawTransaction


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_path: Path) -> list[RawTransaction]:
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        ...
```

Here is a minimal CSV parser:

```python
import csv
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser


class MyBankCSVParser(BaseParser):
    source_type = "mybank"

    def parse(self, file_path: Path) -> list[RawTransaction]:
        transactions = []
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                txn = self._build_transaction(row, file_path)
                if txn:
                    transactions.append(txn)
        return transactions

    def _build_transaction(self, row: dict, file_path: Path) -> RawTransaction | None:
        # Parse date -- convert to YYYY-MM-DD format
        date = self._parse_date(row.get("Date", ""))
        if not date:
            return None

        description = row.get("Description", "").strip()
        if not description:
            return None

        # Parse amount -- positive = income, negative = expense
        amount = float(row.get("Amount", "0").replace(",", ""))

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency="AUD",
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data=dict(row),  # Store the full row for auditing
        )

    def _parse_date(self, s: str) -> str:
        """Convert DD/MM/YYYY to YYYY-MM-DD."""
        s = s.strip()
        if not s:
            return ""
        parts = s.split("/")
        if len(parts) == 3:
            day, month, year = parts
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return ""
```

### 3. The RawTransaction model

Every parser produces `RawTransaction` objects (defined in `etl/models.py`):

```python
@dataclass
class RawTransaction:
    date: str                          # YYYY-MM-DD (required)
    description: str                   # Transaction description (required)
    amount: float                      # Positive = income, negative = expense (required)
    currency: str = "AUD"
    original_amount: float | None = None    # For foreign currency transactions
    original_currency: str | None = None
    fee: float = 0.0                   # Transaction fees (e.g. PayPal fees)
    reference_id: str | None = None    # Unique ID from the source (used for dedup)
    source_type: str = ""              # Must match your parser's source_type
    source_file: str = ""              # File path (set automatically)
    raw_data: dict = field(default_factory=dict)  # Original row data for auditing
```

Key points:
- `date` must be in `YYYY-MM-DD` format
- `amount` should be positive for income, negative for expenses
- `raw_data` should contain the original row/record -- it is stored in the `raw_imports` table for auditing and balance extraction
- `reference_id` is used for dedup hashing if set (important for sources like PayPal that have unique transaction IDs)

### 4. Register the parser in cli.py

Add your parser to the `PARSERS` dict in `etl/cli.py`:

```python
from etl.parsers.my_bank_csv import MyBankCSVParser

PARSERS = {
    # ... existing parsers ...
    "mybank": (MyBankCSVParser, "mybank", "*.csv"),
}
```

The tuple is `(ParserClass, staging_subdirectory, glob_pattern)`.

Add a default account name:

```python
ACCOUNT_NAMES = {
    # ... existing names ...
    "mybank": "My Bank",
}
```

### 5. Add the account to config

Add an entry to `config/accounts.yaml`:

```yaml
  - name: "My Bank"
    source_type: mybank
    currency: AUD
    account_type: checking
```

### 6. Create the staging directory

```sh
mkdir -p staging/mybank
```

### 7. Test

Drop a statement file into `staging/mybank/` and run:

```sh
ledger ingest --source mybank --dry-run
```

Check that transactions are parsed correctly, then run without `--dry-run`.

## Tips for PDF parsers

For PDF statements, use `pdfplumber` (already a dependency):

```python
import pdfplumber
from pathlib import Path

with pdfplumber.open(file_path) as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        # Parse lines from text...

        # Or extract tables:
        tables = page.extract_tables()
```

PDF parsing is trickier than CSV because:
- Statement layouts vary between banks and even between statement periods
- You need to handle multi-line descriptions, page breaks, and headers
- Balance columns help verify you have parsed amounts correctly

Look at `etl/parsers/ing_pdf.py`, `etl/parsers/hsbc_pdf.py`, or `etl/parsers/coles_pdf.py` for real-world examples.

## The principle: the statement is the oracle

Every parser bug found in this codebase came from the parser *guessing* at
something the statement already stated:

| Guessed | Consequence | Resolved instead by |
|---------|-------------|---------------------|
| Year, from the statement's end month | Transactions in the wrong **financial year** | The printed statement period |
| Credit vs charge, from description keywords | Credits booked as spending | The Debit/Credit **column position** |
| Sign of an unsigned amount | Mortgage interest recorded as reducing the debt | The **running balance** movement |
| Which way balances point, from product wording | A transaction account read as a mortgage, inverting every amount | Counting which convention the balances follow |
| That an empty parse means failure | Dormant statements reported as broken | The statement's own opening/closing |
| That rows are chronological | Value-dated rows flagged as disordered | The balance chain vouching for the order |

So the rule for a new parser: **never infer from prose what the numbers already
say.** A statement prints its period, its opening and closing balances, and often
a running balance. Those are ground truth, they are internally consistent, and the
engine checks them on every ingest. Anything derived from them is verifiable;
anything guessed from wording is not.

The shared toolkit exists for exactly this:

| Helper | Resolves |
|--------|----------|
| `dates.parse_period` / `dates.resolve_year` | The year a line omits |
| `base.labelled_balance` | A balance beside its label, honouring `CR` |
| `base.detect_convention` | Whether balances move with or against amounts |
| `base.resign_unsigned_rows` | The sign of an amount printed without one |
| `base.chronological` | Newest-first exports, and section-grouped rows |

`resign_unsigned_rows` only ever changes a sign, never a magnitude. Deriving
amounts from balances would make validation tautological and hide the dropped rows
it exists to catch.

## The parser contract

A parser has one job: describe faithfully what a statement says. It does not decide
what makes a transaction unique, whether a file was fully captured, or how to
handle a re-download -- the engine owns all of that, once, for every source.

Implement a single entry point:

```python
class MyBankPDFParser(BaseParser):
    source_type = "mybank"
    balance_convention = BalanceConvention.OWING   # or SIGNED, or NONE

    def parse_statement(self, file_path: Path) -> ParsedStatement:
        rows = [...]                                # chronological, indexed from 0
        return self.build(file_path, rows,
                          opening_balance=..., closing_balance=...)
```

What you must get right:

| Field | Why it matters |
|-------|----------------|
| `balance_convention` | `SIGNED` if the balance moves with the amount, `OWING` if it is a debt that grows as you spend, `NONE` if the source prints no balance. Must be stated -- guessing is what stored years of mortgage interest with the wrong sign. |
| chronological rows | A running balance only reads forwards. Several banks export newest-first; call `chronological()` on them. |
| `opening_balance` / `closing_balance` | These let the engine prove at ingest time that no row was dropped. Supply them whenever the statement prints them. |
| `reference_id` | If the source gives each transaction a unique id, set it -- it is the strongest identity available. |

Anything the statement prints that is not a transaction -- a rate-change notice, a
summary line -- must be excluded. Left in, its number is read as an amount and
becomes a phantom transaction that breaks the balance chain.

## Validation

`ledger ingest` refuses a statement whose rows do not account for its own printed
balances, rather than ingesting it partially:

```
  ! balance_mismatch: rows sum to -1,234.56 but opening 1,000.00 -> closing 2,204.58 implies -1,204.58 (-29.98)
  REFUSED -- statement does not account for its own printed balances; nothing was ingested.
```

That is a parser bug in almost every case. `--force` overrides it deliberately and
still reports the discrepancy.

## Identity and idempotency

The engine derives the dedup key in `etl/engine.py:row_identity()` from intrinsic
fields only: account, source type, date, description, amount, the running balance
where printed, and the row's position among identical rows in its own file. The
filename is deliberately excluded, so the same statement re-downloaded under
another name does not double-count. A statement-level figure such as a closing
balance is also excluded, because it identifies the file rather than the row.

The occurrence index is what allows two genuinely identical transactions on one
day -- two identical tolls, say -- to both be stored, on a source that prints no
running balance to tell them apart.

## Testing a new parser

Add real statements to `data/archive/<source>/`. `tests/test_statement_corpus.py`
picks them up automatically and holds every one to the contract. Hand-written
fixtures are useful for edge cases, but they encode the layout you already handle
-- the archive is what catches the layout you do not.

## Adding category rules

After adding a parser, you will likely need to add regex rules in `config/categories.yaml` for the merchant names that appear in that bank's statements. Different banks format merchant names differently, so the same purchase might need multiple patterns.
