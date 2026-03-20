import csv
from collections import defaultdict
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser


class PayPalCSVParser(BaseParser):
    """
    Parse PayPal CSV exports with multi-currency handling.

    Handles two CSV formats:
    - Activity report: columns Description, Currency, Gross, Fee, Net, Name, Transaction ID, Reference Txn ID
    - Legacy/test: columns Type, Status, Currency, Gross, Fee, Net, Name, Transaction ID, Reference Txn ID

    PayPal creates 2-3 rows for currency conversions:
    - Row 1: "Pre-approved payment" (USD, -13.89) — the parent
    - Row 2: "General Currency Conversion" (USD, +13.89) — references parent
    - Row 3: "General Currency Conversion" (AUD, -20.15) — references parent (AUD equivalent)

    We collapse these into a single transaction with original_amount/original_currency.
    """

    source_type = "paypal"

    CONVERSION_TYPES = {
        "General Currency Conversion",
        "General currency conversion",
    }

    # Skip these transaction types entirely
    SKIP_TYPES = {
        "General Currency Conversion",
        "General currency conversion",
        "Temporary Hold",
        "General Authorization",
    }

    def parse(self, file_path: Path) -> list[RawTransaction]:
        rows = self._read_csv(file_path)

        # Normalize column names across formats
        rows = [self._normalize_row(r) for r in rows]

        # Filter: skip non-completed if status column exists, skip conversion rows
        rows = [r for r in rows if self._should_process(r)]

        # Index by Transaction ID
        by_txn_id: dict[str, dict] = {}
        for row in rows:
            txn_id = row.get("_txn_id", "")
            if txn_id:
                by_txn_id[txn_id] = row

        # Group conversion rows by Reference Txn ID
        all_rows = [self._normalize_row(r) for r in self._read_csv(file_path)]
        conversions_by_ref: dict[str, list[dict]] = defaultdict(list)
        conversion_txn_ids = set()
        for row in all_rows:
            txn_type = row.get("_type", "")
            ref_id = row.get("_ref_txn_id", "")
            if txn_type in self.CONVERSION_TYPES and ref_id:
                conversions_by_ref[ref_id].append(row)
                conversion_txn_ids.add(row.get("_txn_id", ""))

        transactions = []
        for row in rows:
            txn_id = row.get("_txn_id", "")
            txn_type = row.get("_type", "")

            # Skip conversion rows — merged into parent
            if txn_id in conversion_txn_ids:
                continue
            if txn_type in self.CONVERSION_TYPES:
                continue

            txn = self._build_transaction(row, file_path, conversions_by_ref.get(txn_id, []))
            if txn:
                transactions.append(txn)

        return transactions

    def _normalize_row(self, row: dict) -> dict:
        """Normalize column names across PayPal CSV formats."""
        normalized = dict(row)

        # Transaction type: "Description" (activity report) or "Type" (legacy)
        normalized["_type"] = (
            row.get("Description", "") or row.get("Type", "")
        ).strip()

        # Merchant name: "Name" in both formats
        normalized["_name"] = (row.get("Name", "")).strip()

        # Transaction ID
        normalized["_txn_id"] = (row.get("Transaction ID", "")).strip()

        # Reference Transaction ID
        normalized["_ref_txn_id"] = (row.get("Reference Txn ID", "")).strip()

        # Status (only in legacy format)
        normalized["_status"] = (row.get("Status", "")).strip()

        return normalized

    def _should_process(self, row: dict) -> bool:
        """Determine if a row should be processed."""
        status = row.get("_status", "")
        # If status column exists, only process completed
        if status and status not in ("Completed", ""):
            return False
        txn_type = row.get("_type", "")
        if txn_type in self.SKIP_TYPES:
            return False
        return True

    def _build_transaction(
        self, row: dict, file_path: Path, conversion_rows: list[dict]
    ) -> RawTransaction | None:
        date = self._parse_date(row.get("Date", ""))
        if not date:
            return None

        # Use merchant name if available, fall back to transaction type
        description = row.get("_name", "") or row.get("_type", "")
        if not description:
            return None

        currency = row.get("Currency", "AUD").strip()
        amount = self._parse_amount(row.get("Gross", "0"))
        fee = abs(self._parse_amount(row.get("Fee", "0")))
        txn_id = row.get("_txn_id", "")

        original_amount = None
        original_currency = None
        aud_amount = amount

        if conversion_rows:
            # Find the AUD conversion row
            aud_row = None
            for cr in conversion_rows:
                cr_currency = cr.get("Currency", "").strip()
                cr_amount = self._parse_amount(cr.get("Gross", "0"))
                if cr_currency == "AUD" and cr_amount != 0:
                    aud_row = cr
                    break

            if aud_row and currency != "AUD":
                original_amount = amount
                original_currency = currency
                aud_amount = self._parse_amount(aud_row.get("Gross", "0"))
                currency = "AUD"
        elif currency != "AUD":
            # Foreign currency with no conversion — try to find rate from
            # a related transaction (e.g. refund references original purchase)
            original_amount = amount
            original_currency = currency
            # Keep aud_amount as the foreign amount — it's approximate but
            # better than nothing. The FX rate can be corrected later.

        return RawTransaction(
            date=date,
            description=description,
            amount=aud_amount,
            currency="AUD" if conversion_rows else currency,
            original_amount=original_amount,
            original_currency=original_currency,
            fee=fee,
            reference_id=txn_id,
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data={k: v for k, v in row.items() if not k.startswith("_")},
        )

    def _read_csv(self, file_path: Path) -> list[dict]:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def _parse_date(self, date_str: str) -> str:
        """Parse PayPal date format D/MM/YYYY or DD/MM/YYYY to YYYY-MM-DD."""
        date_str = date_str.strip()
        if not date_str:
            return ""
        parts = date_str.split("/")
        if len(parts) == 3:
            day, month, year = parts
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return date_str

    def _parse_amount(self, amount_str: str) -> float:
        """Parse PayPal amount string, handling commas and spaces."""
        amount_str = amount_str.strip().replace(",", "").replace(" ", "")
        if not amount_str:
            return 0.0
        return float(amount_str)
