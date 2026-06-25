"""Lodged ATO tax returns — historical archive loader.

Reads config/ato_returns.yaml: an archival record of what was actually LODGED
each financial year, keyed by ATO label code. Unlike etl.rental / api.ato_return,
nothing here is recomputed from transactions — these are the filed figures, used
for reference (TFN/ABN/spouse) and to surface carry-forward balances (net capital
losses [18V], deferred non-commercial losses [P9]) that flow into a later return.

The archive holds one or more TAXPAYERS (e.g. a couple lodging linked returns,
each with their own TFN), under a top-level `taxpayers:` list. A single-taxpayer
file may instead put `reference:`/`lodged:` at the top level; that legacy shape is
normalised into a one-entry taxpayers list on load.
"""
import re
from pathlib import Path

import yaml


def _slug(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _carry_forward_for(lodged: list[dict]) -> list[dict]:
    """Carry-forward balances from the most recent lodged year in `lodged`.

    These are the figures you apply when preparing the *next* return (e.g. last
    year's net capital loss). `lodged` must be sorted most-recent-first. Each item
    is annotated with the FY it came from.
    """
    if not lodged:
        return []
    latest = lodged[0]
    return [
        {**item, "from_fy": latest.get("fy"), "from_fy_label": latest.get("fy_label")}
        for item in (latest.get("carry_forward") or [])
    ]


def _normalise_taxpayer(tp: dict, index: int) -> dict:
    ref = tp.get("reference", {}) or {}
    lodged = list(tp.get("lodged", []) or [])
    # Most-recent-first so the UI and carry-forward logic are order-independent.
    lodged.sort(key=lambda y: y.get("fy", 0), reverse=True)
    tp_id = tp.get("id") or _slug(ref.get("name")) or f"taxpayer-{index + 1}"
    return {
        "id": tp_id,
        "name": ref.get("name") or tp_id,
        "reference": ref,
        "lodged": lodged,
        "latest_carry_forward": _carry_forward_for(lodged),
    }


def load_ato_returns(config_path: Path) -> dict:
    """Load ato_returns.yaml into {"taxpayers": [...]}.

    Missing/empty file yields an empty archive. Both the `taxpayers:` shape and
    the legacy top-level `reference:`/`lodged:` shape are accepted.
    """
    if not Path(config_path).exists():
        return {"taxpayers": []}
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    raw = data.get("taxpayers")
    if raw is None:
        # Legacy single-taxpayer shape.
        if not data.get("reference") and not data.get("lodged"):
            return {"taxpayers": []}
        raw = [{"reference": data.get("reference", {}), "lodged": data.get("lodged", [])}]

    return {"taxpayers": [_normalise_taxpayer(tp, i) for i, tp in enumerate(raw)]}
