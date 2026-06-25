"""Depreciation asset register loader.

Reads config/depreciation.yaml: a per-asset capital-allowances register exported
from the ATO depreciation tool. Each asset carries its written-down value forward
year to year (opening → decline → closing), so this is the source for "what's the
deductible decline in value this FY" and "what's the opening value next FY".

Like etl.ato_returns, nothing here is recomputed from transactions — these are the
tool's calculated figures.
"""
from pathlib import Path

import yaml


def _round(x: float) -> float:
    return round(float(x or 0.0), 2)


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
            for yr in asset.get("years", []) or []:
                fy = yr.get("fy")
                if fy is None:  # tolerate a malformed/partial year row
                    continue
                ded = yr.get("deductible", 0.0)
                t = totals.setdefault(fy, {"decline": 0.0, "deductible": 0.0, "taxpayer_deductible": 0.0, "n_assets": 0})
                t["decline"] += yr.get("decline", 0.0)
                t["deductible"] += ded
                t["taxpayer_deductible"] += ded * share
                t["n_assets"] += 1
                ft = fy_totals.setdefault(fy, {"decline": 0.0, "deductible": 0.0, "taxpayer_deductible": 0.0})
                ft["decline"] += yr.get("decline", 0.0)
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
