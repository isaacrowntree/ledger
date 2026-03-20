import csv
from collections import defaultdict
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser


class PayPalCSVParser(BaseParser):
    """
    Parse PayPal CSV exports with multi-currency handling.

    PayPal creates 3 rows for currency conversions:
    - Row 1: "Payment Received" (USD, +50.00) — the parent
    - Row 2: "General Currency Conversion" (USD, -50.00) — references parent
    - Row 3: "General Currency Conversion" (AUD, +72.50) — references parent

    We collapse these into a single transaction with original_amount/original_currency
    and derive the FX rate.
    """

    source_type = "paypal"

    # Statuses to skip entirely
    SKIP_STATUSES = {"Temporary Hold", "General Authorization", "Pending", "Removed", "Placed"}
    # Transaction types that are currency conversion rows (to be collapsed)
    CONVERSION_TYPES = {"General Currency Conversion"}

    def parse(self, file_path: Path) -> list[RawTransaction]:
        rows = self._read_csv(file_path)
        rows = [r for r in rows if r.get("Status") == "Completed"]

        # Index by Transaction ID for linking
        by_txn_id: dict[str, dict] = {}
        for row in rows:
            txn_id = row.get("Transaction ID", "").strip()
            if txn_id:
                by_txn_id[txn_id] = row

        # Group conversion rows by their Reference Txn ID
        conversions_by_ref: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            txn_type = row.get("Type", "").strip()
            ref_id = row.get("Reference Txn ID", "").strip()
            if txn_type in self.CONVERSION_TYPES and ref_id:
                conversions_by_ref[ref_id].append(row)

        # Track which transaction IDs are conversion rows (to skip them)
        conversion_txn_ids = set()
        for conv_rows in conversions_by_ref.values():
            for row in conv_rows:
                conversion_txn_ids.add(row.get("Transaction ID", "").strip())

        transactions = []
        for row in rows:
            txn_id = row.get("Transaction ID", "").strip()
            txn_type = row.get("Type", "").strip()

            # Skip conversion rows — they'll be merged into their parent
            if txn_id in conversion_txn_ids:
                continue

            txn = self._build_transaction(row, file_path, conversions_by_ref.get(txn_id, []))
            if txn:
                transactions.append(txn)

        return transactions

    def _build_transaction(
        self, row: dict, file_path: Path, conversion_rows: list[dict]
    ) -> RawTransaction | None:
        date = self._parse_date(row.get("Date", ""))
        if not date:
            return None

        description = row.get("Name", "").strip() or row.get("Type", "").strip()
        currency = row.get("Currency", "AUD").strip()
        amount = self._parse_amount(row.get("Gross", "0"))
        fee = abs(self._parse_amount(row.get("Fee", "0")))
        txn_id = row.get("Transaction ID", "").strip()

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
            # Foreign currency transaction without conversion rows —
            # store as-is with original currency info
            original_amount = amount
            original_currency = currency

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
            raw_data=dict(row),
        )

    def _read_csv(self, file_path: Path) -> list[dict]:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def _parse_date(self, date_str: str) -> str:
        """Parse PayPal date format DD/MM/YYYY to YYYY-MM-DD."""
        date_str = date_str.strip()
        if not date_str:
            return ""
        # PayPal AU uses DD/MM/YYYY
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
