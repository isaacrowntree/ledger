"""Typed request bodies for every API endpoint that writes to the database.

SQLite stores whatever it is given: a split of "abc" goes into a REAL column
as text, and a misspelled category name used to be ignored while the API
still answered {"ok": true}. Each body is parsed into one of these models
first, so a wrong type, an out-of-range number, a malformed date or an
unexpected field is refused with a 400 that names the field, and nothing is
written.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Optional, TypeVar

from flask import abort, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Pct = Annotated[float, Field(ge=0, le=100)]
Text = Annotated[str, Field(max_length=2000)]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class Body(BaseModel):
    # Strict: "50" is not a number and 1 is not a boolean. Unknown fields are
    # refused, so a typo in a field name cannot silently do nothing.
    model_config = ConfigDict(strict=True, extra="forbid")


class TransactionUpdate(Body):
    category_name: Optional[Name] = None
    notes: Optional[Text] = None
    reviewed: Optional[bool] = None

    @model_validator(mode="after")
    def _something(self):
        if not self.model_fields_set:
            raise ValueError("give at least one of category_name, notes, reviewed")
        return self


class SplitUpdate(Body):
    business_name: Optional[Name] = None
    business_pct: Pct = 0


class SharedExpenseCreate(Body):
    transaction_id: int
    split_pct: Pct = 50.0


class SharedExpenseUpdate(Body):
    is_settled: Optional[bool] = None
    split_pct: Optional[Pct] = None


class HoldingCreate(Body):
    asset_type: Literal["property", "vehicle", "shares", "super", "crypto", "other"]
    name: Name
    ticker: Optional[Annotated[str, Field(max_length=20)]] = None
    units: float = 0
    cost_basis: float = 0
    current_value: Optional[float] = None
    as_at_date: Optional[date] = None
    notes: Optional[Text] = None


class AssetEventCreate(Body):
    date: date
    event_type: Literal["buy", "sell", "dividend", "split", "grant", "valuation"]
    units: Optional[float] = None
    price_per_unit: Optional[float] = None
    total_value: float
    fees: float = 0
    reference: Optional[Text] = None
    source_file: Optional[Text] = None
    notes: Optional[Text] = None


class WorkTripExpense(Body):
    expense_type: Literal["flights", "accommodation", "car", "meals", "other"]
    amount: float
    description: Optional[Text] = None


class WorkTripCreate(Body):
    fy: Annotated[int, Field(ge=2000, le=2100)]
    name: Name
    start_date: date
    end_date: date
    notes: Optional[Text] = None
    expenses: list[WorkTripExpense] = []

    @model_validator(mode="after")
    def _order(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        return self


class TaxOverrideCreate(Body):
    fy: Annotated[int, Field(ge=2000, le=2100)]
    section: Literal["income", "deduction", "rental", "business"]
    label: Name
    amount: float
    notes: Optional[Text] = None


M = TypeVar("M", bound=BaseModel)


def body(model: type[M]) -> M:
    """The request's JSON body parsed as `model`, or a 400 naming what is wrong.

    Parsed from the raw JSON text: in strict mode that still refuses "50" for
    a number or 1 for a boolean, but accepts ISO date strings, since JSON has
    no date type of its own.
    """
    if not isinstance(request.get_json(silent=True), dict):
        abort(400, description="expected a JSON object body")
    try:
        return model.model_validate_json(request.get_data())
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'body'}: {err['msg']}" for err in e.errors())
        abort(400, description=problems)


def sql_value(v):
    """A parsed value as SQLite should store it (dates as ISO text)."""
    return v.isoformat() if isinstance(v, date) else v
