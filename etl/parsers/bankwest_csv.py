"""Parse Bankwest CSV transaction exports.

Format: BSB Number,Account Number,Transaction Date,Narration,Cheque Number,Debit,Credit,Balance,Transaction Type
Dates: DD/MM/YYYY
"""
import csv
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser


class BankwestCSVParser(BaseParser):
    source_type = "bankwest"

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
        date = self._parse_date(row.get("Transaction Date", ""))
        if not date:
            return None

        description = row.get("Narration", "").strip()
        if not description:
            return None

        debit = self._parse_amount(row.get("Debit", ""))
        credit = self._parse_amount(row.get("Credit", ""))

        if debit:
            amount = -abs(debit)
        elif credit:
            amount = credit
        else:
            return None

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency="AUD",
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
