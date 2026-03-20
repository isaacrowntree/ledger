from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class RawTransaction:
    """A transaction as parsed directly from a source file, before normalization."""
    date: str                          # YYYY-MM-DD
    description: str
    amount: float                      # In source currency
    currency: str = "AUD"
    original_amount: Optional[float] = None
    original_currency: Optional[str] = None
    fee: float = 0.0
    reference_id: Optional[str] = None
    source_type: str = ""              # ing, paypal, airbnb
    source_file: str = ""
    raw_data: dict = field(default_factory=dict)

    def raw_data_json(self) -> str:
        return json.dumps(self.raw_data, default=str)
