"""Capital gains from matched share parcels.

Disposals are matched against acquisitions first-in-first-out, which is the
default the ATO accepts when parcels are not specifically identified. Each
match becomes a CGT event carrying its own cost base, proceeds and holding
period, because the 50% discount is decided per parcel: one sale can span a
parcel held four years and another held four months.

Sign conventions follow the contract notes — a buy's cost base includes
brokerage, a sale's proceeds are net of it, so brokerage reduces the gain at
both ends as the ATO intends.
"""
from dataclasses import dataclass
from datetime import date

from etl.parsers.commsec_pdf import BUY, SELL, Trade

# Individuals discount an eligible gain by half.
DISCOUNT_RATE = 0.5


class CGTError(Exception):
    """Raised when disposals cannot be matched to acquisitions."""


@dataclass(frozen=True)
class CGTEvent:
    """One disposal matched against one acquisition parcel."""
    code: str
    units: float
    acquired: date
    disposed: date
    cost_base: float
    proceeds: float

    @property
    def gain(self) -> float:
        return round(self.proceeds - self.cost_base, 2)

    @property
    def discountable(self) -> bool:
        """A gain qualifies only if the parcel was held more than 12 months."""
        return self.gain > 0 and self.disposed > _twelve_months_after(self.acquired)

    @property
    def financial_year(self) -> int:
        """Australian FY ending 30 June, labelled by the closing year."""
        return self.disposed.year + 1 if self.disposed.month > 6 else self.disposed.year


def match_disposals(trades: list[Trade]) -> list[CGTEvent]:
    """Match sells against buys FIFO, per security."""
    holdings: dict[str, list[dict]] = {}
    events: list[CGTEvent] = []

    for trade in sorted(trades, key=lambda t: (t.trade_date, 0 if t.side == BUY else 1)):
        if trade.side == BUY:
            holdings.setdefault(trade.code, []).append({
                "units": trade.units,
                "date": trade.trade_date,
                # Per-unit so a partly-consumed parcel apportions cleanly.
                "cost_per_unit": trade.cost_base / trade.units,
            })
            continue

        if trade.side != SELL:
            continue

        remaining = trade.units
        proceeds_per_unit = trade.proceeds / trade.units
        parcels = holdings.get(trade.code, [])

        while remaining > 0:
            if not parcels:
                raise CGTError(
                    f"{trade.code}: sold {trade.units:,.0f} units on "
                    f"{trade.trade_date.isoformat()} but only "
                    f"{trade.units - remaining:,.0f} were held — an earlier "
                    f"contract note is missing"
                )
            parcel = parcels[0]
            taken = min(remaining, parcel["units"])
            events.append(CGTEvent(
                code=trade.code,
                units=taken,
                acquired=parcel["date"],
                disposed=trade.trade_date,
                cost_base=round(parcel["cost_per_unit"] * taken, 2),
                proceeds=round(proceeds_per_unit * taken, 2),
            ))
            parcel["units"] -= taken
            remaining -= taken
            if parcel["units"] <= 0:
                parcels.pop(0)

    return events


def summarise(events: list[CGTEvent], fy: int, carried_forward_losses: float = 0.0) -> dict:
    """Total a year's CGT events into the figures a return needs.

    Losses are applied against undiscounted gains first, which is the order
    that leaves the taxpayer best off: a dollar of loss cancels a whole dollar
    there, but only half a dollar of benefit against a gain about to be
    halved anyway.
    """
    in_year = [e for e in events if e.financial_year == fy]

    discountable = sum(e.gain for e in in_year if e.discountable)
    other_gains = sum(e.gain for e in in_year if e.gain > 0 and not e.discountable)
    losses = sum(-e.gain for e in in_year if e.gain < 0)

    available = losses + max(carried_forward_losses, 0.0)

    applied_to_other = min(available, other_gains)
    available -= applied_to_other
    applied_to_discountable = min(available, discountable)
    available -= applied_to_discountable

    net_gain = round(
        (other_gains - applied_to_other)
        + (discountable - applied_to_discountable) * (1 - DISCOUNT_RATE),
        2,
    )

    return {
        "fy": fy,
        "events": in_year,
        "gross_gains": round(discountable + other_gains, 2),
        "discountable_gains": round(discountable, 2),
        "other_gains": round(other_gains, 2),
        "losses": round(losses, 2),
        "losses_applied": round(applied_to_other + applied_to_discountable, 2),
        "discount": round((discountable - applied_to_discountable) * DISCOUNT_RATE, 2),
        "net_capital_gain": max(net_gain, 0.0),
        # Unused losses carry forward to a later year (label 18V).
        "losses_carried_forward": round(available, 2),
    }


def _twelve_months_after(day: date) -> date:
    """The same date a year later, tolerating 29 February."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)
