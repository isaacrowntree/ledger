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
from pathlib import Path

from etl.parsers.commsec_pdf import BUY, SELL, CommSecContractNoteParser, Trade

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

    def as_dict(self) -> dict:
        """JSON-ready form, dates as ISO strings."""
        return {
            "code": self.code,
            "units": self.units,
            "acquired": self.acquired.isoformat(),
            "disposed": self.disposed.isoformat(),
            "cost_base": self.cost_base,
            "proceeds": self.proceeds,
            "gain": self.gain,
            "discountable": self.discountable,
        }


def events_from_notes(notes_dir: Path) -> list[CGTEvent]:
    """Parse every contract note in a directory and match the disposals.

    Returns nothing when there are no notes, so a ledger holding no shares
    needs no special handling by its callers.
    """
    files = sorted(notes_dir.glob("*.pdf")) + sorted(notes_dir.glob("*.PDF"))
    if not files:
        return []
    return match_disposals(CommSecContractNoteParser().parse_all(files))


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


# --- Assets without contract notes, and losses carried between years -------

def events_from_config(items: list[dict] | None) -> list[CGTEvent]:
    """CGT events for assets with no contract notes (gold, crypto, collectibles).

    Each item in tax.yaml `cgt_events` gives asset, acquired, disposed,
    cost_base and proceeds (dates ISO). Several purchases sold together can be
    one event, dated from the first purchase: for a loss the holding period
    changes nothing, and for a gain list the parcels separately.
    """
    events = []
    for i, item in enumerate(items or []):
        missing = [k for k in ("asset", "acquired", "disposed", "cost_base", "proceeds") if k not in item]
        if missing:
            raise CGTError(f"cgt_events[{i}]: missing {', '.join(missing)}")
        acquired, disposed = _as_date(item["acquired"]), _as_date(item["disposed"])
        if disposed < acquired:
            raise CGTError(f"cgt_events[{i}] {item['asset']}: disposed before it was acquired")
        events.append(CGTEvent(code=str(item["asset"]), units=float(item.get("units", 1)),
                               acquired=acquired, disposed=disposed,
                               cost_base=round(float(item["cost_base"]), 2),
                               proceeds=round(float(item["proceeds"]), 2)))
    return events


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def lodged_losses_carried_forward(lodged: list[dict]) -> dict[int, float]:
    """Net capital losses each lodged return carried forward (label 18V).

    A lodged year with no 18V entry carried forward nothing, so it reads as 0.
    """
    out = {}
    for year in lodged or []:
        if not year.get("fy"):
            continue
        out[int(year["fy"])] = sum(float(c.get("value") or 0) for c in (year.get("carry_forward") or [])
                                   if str(c.get("label", "")).upper() == "18V")
    return out


def losses_brought_forward(fy: int, events: list[CGTEvent], anchors: dict[int, float]) -> dict:
    """Net capital losses available at the start of `fy`.

    Starts from the latest known balance before `fy` (an `anchors` entry: what a
    lodged return carried forward, or a correction in tax.yaml) and rolls it
    through any later years with the ledger's own CGT events. With no anchor it
    rolls from the first event.
    """
    earlier = [y for y in anchors if y < fy]
    if earlier:
        start = max(earlier)
        balance, basis = float(anchors[start]), start
        start += 1
    else:
        balance, basis = 0.0, None
        start = min((e.financial_year for e in events), default=fy)
    for year in range(start, fy):
        balance = summarise(events, year, balance)["losses_carried_forward"]
    return {"amount": round(balance, 2), "from_fy": basis}


def unrecorded_losses(events: list[CGTEvent], lodged: dict[int, float],
                      corrections: dict[int, float], upto_fy: int) -> list[dict]:
    """Lodged years whose carried-forward loss is smaller than the ledger's.

    Each year is rolled from the year before it (as lodged, or as corrected)
    with the ledger's events. A larger ledger figure means a loss the lodged
    return doesn't show: an asset sold at a loss that wasn't reported, or a
    balance dropped between returns. Only shortfalls are listed: the ledger
    may not know every gain (employee shares, for instance).
    """
    anchors = {**lodged, **corrections}
    out = []
    for year in sorted(y for y in lodged if y <= upto_fy):
        prior = {y: v for y, v in anchors.items() if y < year}
        opening = losses_brought_forward(year, events, prior)["amount"]
        ledger = summarise(events, year, opening)["losses_carried_forward"]
        if ledger - lodged[year] > 1:
            out.append({"fy": year, "lodged": lodged[year], "ledger": round(ledger, 2),
                        "corrected": corrections.get(year)})
    return out


def capital_gains_for(fy: int, notes_dir: Path | None, tax_config: dict, lodged: list[dict]) -> dict:
    """Everything the return needs for item 18, for both the API and the CLI.

    Events come from the contract notes and tax.yaml `cgt_events`. Losses
    brought forward start from the lodged returns (18V), with tax.yaml
    `capital_losses_carried_forward` ({fy: amount}) correcting a lodged figure.
    """
    events = events_from_notes(notes_dir) if notes_dir and notes_dir.is_dir() else []
    events += events_from_config(tax_config.get("cgt_events"))
    lodged_cf = lodged_losses_carried_forward(lodged)
    corrections = {int(k): float(v) for k, v in (tax_config.get("capital_losses_carried_forward") or {}).items()}
    brought = losses_brought_forward(fy, events, {**lodged_cf, **corrections})
    summary = summarise(events, fy, brought["amount"])
    return {**summary,
            "brought_forward": brought["amount"], "brought_forward_from_fy": brought["from_fy"],
            "unrecorded_losses": unrecorded_losses(events, lodged_cf, corrections, fy)}
