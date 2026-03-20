import csv
from pathlib import Path

from etl.models import RawTransaction
from etl.parsers.base import BaseParser

# Known column name variations Airbnb uses
COLUMN_ALIASES = {
    "date": ["date", "payout date", "payment date", "start date"],
    "type": ["type", "transaction type", "payout type"],
    "gross": ["gross earnings", "amount", "gross", "total"],
    "host_fee": ["host service fee", "host fee", "service fee"],
    "cleaning_fee": ["cleaning fee", "clean fee"],
    "tax": ["occupancy taxes", "tax withheld", "taxes", "withholding tax"],
    "net": ["net payout", "net", "paid out", "payout"],
    "listing": ["listing", "listing name", "property"],
    "confirmation": ["confirmation code", "confirmation", "reservation code"],
    "guest": ["guest", "guest name"],
}


class AirbnbCSVParser(BaseParser):
    """Parse Airbnb payout/earnings CSV exports."""

    source_type = "airbnb"

    def parse(self, file_path: Path) -> list[RawTransaction]:
        rows, header_map = self._read_csv(file_path)
        transactions = []

        for row in rows:
            txn = self._build_transaction(row, header_map, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _build_transaction(
        self, row: dict, header_map: dict[str, str], file_path: Path
    ) -> RawTransaction | None:
        date = self._parse_date(self._get(row, header_map, "date"))
        if not date:
            return None

        net = self._parse_amount(self._get(row, header_map, "net"))
        gross = self._parse_amount(self._get(row, header_map, "gross"))
        host_fee = abs(self._parse_amount(self._get(row, header_map, "host_fee")))

        # Use net payout as the amount (what actually hits the bank account)
        amount = net if net else gross

        if amount == 0:
            return None

        listing = self._get(row, header_map, "listing")
        confirmation = self._get(row, header_map, "confirmation")
        guest = self._get(row, header_map, "guest")
        txn_type = self._get(row, header_map, "type")

        parts = [p for p in ["Airbnb", txn_type, listing, guest] if p]
        description = " - ".join(parts)

        return RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency="AUD",
            fee=host_fee,
            reference_id=confirmation or None,
            source_type=self.source_type,
            source_file=str(file_path),
            raw_data=dict(row),
        )

    def _read_csv(self, file_path: Path) -> tuple[list[dict], dict[str, str]]:
        """Read CSV, auto-detecting header row and mapping column names."""
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            # Read all lines to find the header
            lines = f.readlines()

        header_idx = self._find_header_row(lines)
        if header_idx is None:
            return [], {}

        # Re-parse from header row
        content = "".join(lines[header_idx:])
        reader = csv.DictReader(content.splitlines())
        rows = list(reader)

        # Build header mapping
        header_map = self._build_header_map(reader.fieldnames or [])
        return rows, header_map

    def _find_header_row(self, lines: list[str]) -> int | None:
        """Find the header row by looking for known column names."""
        known_headers = {"date", "type", "amount", "payout", "gross", "net",
                         "listing", "confirmation", "earnings", "paid"}
        for i, line in enumerate(lines[:20]):  # Check first 20 lines
            lower = line.lower()
            matches = sum(1 for h in known_headers if h in lower)
            if matches >= 3:
                return i
        return 0  # Fall back to first row

    def _build_header_map(self, fieldnames: list[str]) -> dict[str, str]:
        """Map our canonical field names to actual CSV column names."""
        header_map = {}
        for canonical, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                for field in fieldnames:
                    if field.strip().lower() == alias:
                        header_map[canonical] = field
                        break
                if canonical in header_map:
                    break
        return header_map

    def _get(self, row: dict, header_map: dict[str, str], field: str) -> str:
        """Get a value from a row using the header map."""
        col = header_map.get(field)
        if col and col in row:
            return row[col].strip()
        return ""

    def _parse_date(self, date_str: str) -> str:
        """Parse various date formats to YYYY-MM-DD."""
        if not date_str:
            return ""
        # Try DD/MM/YYYY
        if "/" in date_str:
            parts = date_str.split("/")
            if len(parts) == 3:
                day, month, year = parts
                if len(year) == 2:
                    year = "20" + year
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        # Try YYYY-MM-DD (already correct)
        if len(date_str) == 10 and date_str[4] == "-":
            return date_str
        return date_str

    def _parse_amount(self, amount_str: str) -> float:
        if not amount_str:
            return 0.0
        amount_str = amount_str.strip().replace(",", "").replace("$", "").replace(" ", "")
        if not amount_str:
            return 0.0
        return float(amount_str)
