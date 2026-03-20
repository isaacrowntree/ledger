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

## Tips for dedup hashing

The default dedup hash uses `date|description|amount`. This works for most banks but can collide if you have two identical transactions on the same day (e.g. two $5.00 coffees at the same cafe).

If your bank provides a unique transaction ID, set `reference_id` on the `RawTransaction` and the normalizer will use it for hashing instead.

For banks with multiple accounts in the same format (like ING), include the source filename in the hash -- see `compute_dedup_hash()` in `etl/normalizer.py`.

## Adding category rules

After adding a parser, you will likely need to add regex rules in `config/categories.yaml` for the merchant names that appear in that bank's statements. Different banks format merchant names differently, so the same purchase might need multiple patterns.
