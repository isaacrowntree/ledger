"""Parse American Express CSV activity exports.

Format: Date,Description,Card Member,Account #,Amount,Foreign Spend Amount,Commission,Exchange Rate
Dates: DD/MM/YYYY
Amounts are positive for purchases (we flip to negative).
"""
import csv
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser


class AmexCSVParser(BaseParser):
    source_type = "amex"

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
        date = self._parse_date(row.get("Date", ""))
        if not date:
            return None

        description = row.get("Description", "").strip()
        if not description:
            return None

        amount = self._parse_amount(row.get("Amount", ""))
        if amount is None:
            return None

        # Amex: positive = purchase (expense), negative = credit/payment
        # Flip sign so purchases are negative (our convention)
        amount = -amount

        # Foreign currency info
        foreign_amount = self._parse_amount(row.get("Foreign Spend Amount", ""))
        original_amount = None
        original_currency = None
        if foreign_amount:
            original_amount = -foreign_amount  # Match sign convention
            # Amex doesn't specify currency code in CSV, but we have the amount

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency="AUD",
            original_amount=original_amount,
            original_currency=original_currency,
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data=dict(row),
        )

    def _parse_date(self, s: str) -> str:
        s = s.strip()
        if not s:
            return ""
        parts = s.split("/")
        if len(parts) == 3:
            day, month, year = parts
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return ""

    def _parse_amount(self, s: str) -> float | None:
        s = s.strip().replace(",", "").replace("$", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
