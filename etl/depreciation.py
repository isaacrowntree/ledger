"""Depreciation asset register loader.

Reads config/depreciation.yaml: a per-asset capital-allowances register exported
from the ATO depreciation tool. Each asset carries its written-down value forward
year to year (opening → decline → closing), so this is the source for "what's the
deductible decline in value this FY" and "what's the opening value next FY".

Like etl.ato_returns, nothing here is recomputed from transactions — these are the
tool's calculated figures.
"""
from datetime import date
from pathlib import Path

import yaml


def _round(x: float) -> float:
    return round(float(x or 0.0), 2)


def dv_schedule(cost: float, acquired: date, effective_life: float, taxable_use_pct: float = 100) -> list[dict]:
    """Diminishing-value decline, year by year, the way the ATO depreciation tool works it out.

    Rate is 200% / effective life. The first year counts the days held from
    the acquisition date to 30 June, inclusive, over the days in that
    financial year. Each year is rounded to cents, and once less than $1
    would be left, the rest is written off.
    """
    rate = 2.0 / effective_life
    fy = acquired.year + 1 if acquired.month >= 7 else acquired.year
    fy_start, fy_end = date(fy - 1, 7, 1), date(fy, 6, 30)
    fraction = ((fy_end - acquired).days + 1) / ((fy_end - fy_start).days + 1)
    years, opening = [], round(float(cost), 2)
    while opening > 0:
        decline = round(opening * rate * fraction, 2)
        if opening - decline < 1.0:
            decline = opening
        closing = round(opening - decline, 2)
        years.append({"fy": fy, "opening": opening, "decline": decline,
                      "deductible": round(decline * taxable_use_pct / 100, 2), "closing": closing})
        opening, fy, fraction = closing, fy + 1, 1.0
    return years


def _live_years(asset: dict) -> list[dict]:
    """The asset's years, computed when not given, and none after it was disposed of."""
    years = asset.get("years")
    if not years and asset.get("method", "DV") == "DV" and asset.get("cost") and asset.get("acquired"):
        acquired = asset["acquired"]
        acquired = acquired if isinstance(acquired, date) else date.fromisoformat(str(acquired))
        years = dv_schedule(asset["cost"], acquired, asset["effective_life"], asset.get("taxable_use_pct", 100))
    out = []
    for yr in years or []:
        out.append(yr)
        if yr.get("disposed"):
            break  # a sold asset has no decline after the year it went
    asset["years"] = out
    return out


def asset_payments(config_path: Path) -> list[dict]:
    """The payments that bought registered assets ({date, amount} under each
    asset's `paid:`). Their cost is claimed as decline in value over the
    asset's life, so these payments must not also be claimed as an expense."""
    if not Path(config_path).exists():
        return []
    data = yaml.safe_load(Path(config_path).read_text()) or {}
    out = []
    for reg in data.get("registers", []) or []:
        for asset in reg.get("assets", []) or []:
            for p in asset.get("paid", []) or []:
                out.append({"date": str(p["date"]), "amount": abs(float(p["amount"])),
                            "asset": asset.get("description", "")})
    return out


def load_depreciation(config_path: Path) -> dict:
    """Load depreciation.yaml into {"registers": [...], "fy_totals": {...}}.

    Each register gains a `totals` map {fy: {decline, deductible, n_assets}} and a
    `current_fy`/`next_fy` convenience pair is left to the caller. fy_totals sums
    deductible decline across all registers per FY.
    """
    if not Path(config_path).exists():
        return {"registers": [], "fy_totals": {}}
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {"registers": [], "fy_totals": {}}

    registers = data.get("registers", []) or []
    fy_totals: dict[int, dict] = {}

    for reg in registers:
        # Rental assets are often co-owned; the tool figures are the full-property
        # amount, so the taxpayer only deducts their ownership share.
        pct = reg.get("ownership_pct")
        pct = 100 if pct is None else pct  # absent OR explicit null → full ownership
        reg["ownership_pct"] = pct
        share = pct / 100.0
        totals: dict[int, dict] = {}
        for asset in reg.get("assets", []) or []:
            for yr in _live_years(asset):
                fy = yr.get("fy")
                if fy is None:  # tolerate a malformed/partial year row
                    continue
                # `or 0.0` coerces an explicit-null amount (hand-edited YAML);
                # dict.get's default only fires for absent keys, not null values.
                dec = yr.get("decline") or 0.0
                ded = yr.get("deductible") or 0.0
                t = totals.setdefault(fy, {"decline": 0.0, "deductible": 0.0, "taxpayer_deductible": 0.0, "n_assets": 0})
                t["decline"] += dec
                t["deductible"] += ded
                t["taxpayer_deductible"] += ded * share
                t["n_assets"] += 1
                ft = fy_totals.setdefault(fy, {"decline": 0.0, "deductible": 0.0, "taxpayer_deductible": 0.0})
                ft["decline"] += dec
                ft["deductible"] += ded
                ft["taxpayer_deductible"] += ded * share
        reg["totals"] = {
            fy: {"decline": _round(t["decline"]), "deductible": _round(t["deductible"]),
                 "taxpayer_deductible": _round(t["taxpayer_deductible"]), "n_assets": t["n_assets"]}
            for fy, t in sorted(totals.items())
        }

    fy_totals = {fy: {"decline": _round(t["decline"]), "deductible": _round(t["deductible"]),
                      "taxpayer_deductible": _round(t["taxpayer_deductible"])}
                 for fy, t in sorted(fy_totals.items())}

    return {"registers": registers, "fy_totals": fy_totals}
