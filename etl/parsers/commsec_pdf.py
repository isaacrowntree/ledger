"""Parser for CommSec trade confirmations (contract notes).

A contract note is the ATO's evidence for a capital gains calculation — it
carries the trade date, the units, the consideration and the brokerage, which
together give the cost base of a parcel or the proceeds of a disposal.

    BUY
    WE HAVE BOUGHT THE FOLLOWING SECURITIES FOR YOU
    COMPANY: EXAMPLE INDEX FUND
    XYZ
    SECURITY: EXCHANGE TRADED FUND UNITS FULLY PAID
    DATE: 10/09/2020
    TOTAL UNITS: 125
    CONSIDERATION (AUD): $2,326.25
    BROKERAGE & COSTS INCL GST: $19.95
    TOTAL COST: $2,346.20

Unlike a bank statement these are not transactions to be ledgered — one note
is one parcel, and parcels are matched against each other to produce CGT
events. See ``etl.cgt``.
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pdfplumber

BUY, SELL = "buy", "sell"

_BOUGHT_RE = re.compile(r"WE HAVE BOUGHT", re.IGNORECASE)
_SOLD_RE = re.compile(r"WE HAVE SOLD", re.IGNORECASE)
_COMPANY_RE = re.compile(r"^COMPANY:?\s*(.+)$", re.IGNORECASE)
_SECURITY_RE = re.compile(r"^SECURITY:?\s", re.IGNORECASE)
_DATE_RE = re.compile(r"^DATE:\s*(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE)
_SETTLEMENT_RE = re.compile(r"SETTLEMENT DATE:\s*(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE)
_UNITS_RE = re.compile(r"TOTAL UNITS:\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_CONSIDERATION_RE = re.compile(r"CONSIDERATION\s*\(AUD\):\s*\$?([\d,]+\.\d{2})", re.IGNORECASE)
_BROKERAGE_RE = re.compile(r"BROKERAGE & COSTS[^$]*\$([\d,]+\.\d{2})", re.IGNORECASE)
_CONFIRMATION_RE = re.compile(r"CONFIRMATION NO:\s*(\d+)", re.IGNORECASE)
# A bare ticker sits on its own line between the company and security lines.
_CODE_RE = re.compile(r"^[A-Z0-9]{3,5}$")


class ContractNoteError(Exception):
    """Raised when a contract note is missing a figure the CGT maths needs."""


@dataclass(frozen=True)
class Trade:
    """One parcel bought or sold."""
    side: str                  # buy | sell
    code: str                  # ASX ticker
    name: str
    trade_date: date
    units: float
    consideration: float       # units x price, before costs
    brokerage: float
    confirmation_no: str
    settlement_date: date | None
    source_file: str

    @property
    def cost_base(self) -> float:
        """What the parcel cost, including brokerage (buys)."""
        return round(self.consideration + self.brokerage, 2)

    @property
    def proceeds(self) -> float:
        """What the disposal returned, net of brokerage (sells)."""
        return round(self.consideration - self.brokerage, 2)


class CommSecContractNoteParser:
    """Parse a CommSec trade confirmation PDF into a Trade."""

    source_type = "commsec"

    def parse(self, file_path: Path) -> Trade:
        return self.parse_text(self._extract_text(file_path), file_path)

    def parse_text(self, text: str, file_path: Path) -> Trade:
        """Parse an already-extracted contract note.

        Separated from PDF reading so the note layout can be tested without
        committing a real contract note.
        """
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

        side = self._side(text, file_path)
        name, code = self._security(lines, file_path)

        return Trade(
            side=side,
            code=code,
            name=name,
            trade_date=self._date(_DATE_RE, lines, file_path, "trade date"),
            units=self._number(_UNITS_RE, text, file_path, "total units"),
            consideration=self._number(_CONSIDERATION_RE, text, file_path, "consideration"),
            # A note with no brokerage line is possible; absent means zero.
            brokerage=self._number(_BROKERAGE_RE, text, file_path, "brokerage", default=0.0),
            confirmation_no=self._match(_CONFIRMATION_RE, text) or "",
            settlement_date=self._optional_date(_SETTLEMENT_RE, text),
            source_file=str(file_path),
        )

    def parse_all(self, paths: list[Path]) -> list[Trade]:
        """Parse many notes, oldest trade first."""
        return sorted((self.parse(p) for p in paths), key=lambda t: (t.trade_date, t.code))

    # -- internals ----------------------------------------------------------

    def _extract_text(self, file_path: Path) -> str:
        with pdfplumber.open(file_path) as pdf:
            return "\n".join((page.extract_text(x_tolerance=1.5) or "") for page in pdf.pages)

    def _side(self, text: str, file_path: Path) -> str:
        if _BOUGHT_RE.search(text):
            return BUY
        if _SOLD_RE.search(text):
            return SELL
        raise ContractNoteError(f"{file_path.name}: cannot tell a buy from a sell")

    def _security(self, lines: list[str], file_path: Path) -> tuple[str, str]:
        """Read the company name and the ticker printed beneath it."""
        for i, line in enumerate(lines):
            m = _COMPANY_RE.match(line)
            if not m:
                continue
            name = m.group(1).strip()
            for candidate in lines[i + 1:i + 4]:
                if _SECURITY_RE.match(candidate):
                    break
                if _CODE_RE.match(candidate):
                    return name, candidate
            raise ContractNoteError(f"{file_path.name}: no ticker found under '{name}'")
        raise ContractNoteError(f"{file_path.name}: no company line found")

    def _match(self, pattern: re.Pattern, text: str) -> str | None:
        m = pattern.search(text)
        return m.group(1) if m else None

    def _number(
        self, pattern: re.Pattern, text: str, file_path: Path, label: str, default: float | None = None
    ) -> float:
        raw = self._match(pattern, text)
        if raw is None:
            if default is not None:
                return default
            raise ContractNoteError(f"{file_path.name}: no {label} found")
        return float(raw.replace(",", ""))

    def _date(self, pattern: re.Pattern, lines: list[str], file_path: Path, label: str) -> date:
        for line in lines:
            m = pattern.match(line)
            if m:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        raise ContractNoteError(f"{file_path.name}: no {label} found")

    def _optional_date(self, pattern: re.Pattern, text: str) -> date | None:
        m = pattern.search(text)
        if not m:
            return None
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
