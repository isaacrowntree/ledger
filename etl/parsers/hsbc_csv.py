"""Parse HSBC Australia credit card CSV exports ("TransHist.csv" from online banking).

Format: " Transaction Date ,Posting Date,Description,Amount,"
Dates: "DD Mon YYYY" (e.g. " 22 Aug 2026"), leading/trailing spaces on every field.
Posting Date is often blank for transactions that have not settled yet.

Sign convention matches HSBC's credit card PDF statements: purchases are positive
in the file and payments negative ("BPAY PAYMENT  -4,267.00"), which is the inverse
of Ledger's convention, so amounts are negated on the way in -- the same flip
HSBCPDFParser applies for the single-Amount credit card layout.

Reports source_type "hsbc" so its rows sit in the same account as the PDF parser.
Note the two formats describe the same transaction differently (the PDF prefixes a
card number and appends "$"), so they cannot dedup against each other -- ingest one
format per period, clipping with `ledger ingest --from/--until` at the boundary.
"""
import csv
from datetime import datetime
from pathlib import Path

from etl.contract import BalanceConvention, ParsedRow, ParsedStatement
from etl.models import RawTransaction
from etl.parsers.base import BaseParser, chronological, money


class HSBCCSVParser(BaseParser):
    source_type = "hsbc"

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
            reader.fieldnames = [(name or "").strip() for name in (reader.fieldnames or [])]
            for row in reader:
                txn = self._build_transaction(row, file_path)
                if txn:
                    transactions.append(txn)
        return transactions

    def _build_transaction(self, row: dict, file_path: Path) -> RawTransaction | None:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}

        date = self._parse_date(row.get("Transaction Date", ""))
        if not date:
            return None

        description = row.get("Description", "")
        # HSBC pads descriptions out to fixed columns; collapse the runs of spaces.
        description = " ".join(description.split())
        if not description:
            return None

        amount = self._parse_amount(row.get("Amount", ""))
        if amount is None:
            return None

        return RawTransaction(
            date=date,
            description=description,
            amount=-amount,  # purchases positive in the file, negative in the ledger
            currency="AUD",
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data=dict(row),
        )

    def _parse_date(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        for fmt in ("%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y"):
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
