"""Totalling of manually entered tax figures.

Some amounts belong in a return but never appear as a bank transaction: a
managing agent nets their commission out of the rent before depositing it, so
neither the gross rent nor the fee is ever seen; dividends are paid into a
broker's account; a donation receipt arrives by post.

``manual_entries`` in tax.yaml records those. Each entry carries a ``section``
saying where it belongs in the return, and this module totals them so the
summary can fold them in.
"""

# Where a manual entry lands in the return. An entry with any other section
# (or none) is carried for display only and affects no total.
SECTIONS = ("income", "deductions", "rental")

# Handled separately as the PAYG credit rather than as income or a deduction.
TAX_WITHHELD_LABEL = "Tax withheld"


def summarise(entries: list[dict] | None) -> dict[str, float]:
    """Total manual entries by section.

    ``income`` and ``rental`` amounts follow the same sign convention as
    transactions — money in positive, money out negative — so a rental section
    listing gross rent alongside the agent's fees nets out correctly.

    A ``deductions`` entry always reduces taxable income, so it is counted by
    magnitude and may be written with either sign.
    """
    totals = {section: 0.0 for section in SECTIONS}

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("label") == TAX_WITHHELD_LABEL:
            continue
        section = str(entry.get("section") or "").strip().lower()
        if section not in totals:
            continue
        amount = _as_float(entry.get("amount"))
        totals[section] += abs(amount) if section == "deductions" else amount

    return {section: round(value, 2) for section, value in totals.items()}


def tax_withheld(entries: list[dict] | None) -> float:
    """Total PAYG tax withheld recorded in manual entries."""
    return round(sum(
        _as_float(entry.get("amount"))
        for entry in entries or []
        if isinstance(entry, dict) and entry.get("label") == TAX_WITHHELD_LABEL
    ), 2)


def _as_float(value) -> float:
    """Coerce a config value to a number, treating anything unusable as zero.

    Hand-edited YAML is the only source here, so a stray blank or typo should
    leave the figure out rather than break the whole return.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
