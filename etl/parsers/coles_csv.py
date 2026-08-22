"""Parse Coles Mastercard CSV transaction exports (from the Coles/NAB app).

Format: Date,Amount,Account Number,,Transaction Type,Transaction Details,Category,Merchant Name,Processed On
Dates: "DD Month YY" (e.g. "08 July 26").
Amounts are already in Ledger's convention — purchases negative, payments positive —
so no sign flip is needed. (Verified against "CREDIT CARD PAYMENT" rows, which are positive.)
Reports source_type "coles" so its rows sit in the same account as the PDF parser.
"""
import csv
from datetime import datetime
from pathlib import Path

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.models import RawTransaction
from etl.parsers.base import BaseParser, chronological, money


class ColesCSVParser(BaseParser):
    source_type = "coles"

    balance_convention = BalanceConvention.NONE

    def parse_statement(self, file_path: Path) -> ParsedStatement:
        transactions = chronological(self._read(file_path))
        rows = [
            ParsedRow(
                index=i,
                date=t.date,
                description=t.description,
                amount=t.amount,
                balance=None,
                raw=t.raw_data or {},
                currency=t.currency,
                original_amount=t.original_amount,
                original_currency=t.original_currency,
                fee=t.fee,
                reference_id=t.reference_id,
            )
            for i, t in enumerate(transactions)
        ]
        return self.build(file_path, rows)

    def _read(self, file_path: Path) -> list[RawTransaction]:
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

        # "Transaction Details" carries the merchant + location; richer than "Merchant Name".
        description = (row.get("Transaction Details") or "").strip()
        if not description:
            description = (row.get("Merchant Name") or "").strip()
        if not description:
            return None

        amount = self._parse_amount(row.get("Amount", ""))
        if amount is None:
            return None

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,          # already signed: purchases negative, payments positive
            currency="AUD",
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data=dict(row),
        )

    def _parse_date(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        # e.g. "08 July 26" -> 2026-07-08
        for fmt in ("%d %B %y", "%d %b %y"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""

    def _parse_amount(self, s: str) -> float | None:
        s = (s or "").strip().replace(",", "").replace("$", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
