"""Rental property allocator.

Reads rental_properties from tax.yaml and computes Isaac's deductible share
of expenses (and assessable share of income) using per-rule apply modes:
    [ownership, area, time]

Apply factors:
    ownership : ownership_pct / 100
    area      : floor_area_pct / 100
    time      : rental_weeks_for_fy / 52
    (empty)   : 1.0  (100% deductible — e.g. repairs done because of tenant)

This module is the FY26+ replacement for the single "percentage of house rented"
factor used in the historic spreadsheets.
"""
import sqlite3
from typing import Optional


def _factor(rental: dict, modes: list[str], fy: int) -> float:
    """Compute composite multiplier for the given apply modes."""
    f = 1.0
    for mode in modes:
        if mode == "ownership":
            f *= rental.get("ownership_pct", 100) / 100.0
        elif mode == "area":
            f *= rental.get("floor_area_pct", 100) / 100.0
        elif mode == "time":
            weeks = rental.get("rental_weeks", {}).get(fy)
            if weeks is None:
                raise ValueError(f"rental_weeks for FY{fy} not configured")
            f *= weeks / 52.0
        else:
            raise ValueError(f"unknown apply mode: {mode!r}")
    return f


def _match_transaction(row: sqlite3.Row, mapping: dict) -> bool:
    """True if this transaction matches the mapping (by category or tag)."""
    cat = mapping.get("category")
    tag = mapping.get("tag")
    if cat and row["category_name"] == cat:
        return True
    if tag and row["tag_match"] == 1:
        return True
    return False


def compute_rental_deductions(
    conn: sqlite3.Connection,
    tax_config: dict,
    fy: int,
) -> dict:
    """For each rental property, compute Isaac's share of income + per-ATO-label deductions.

    Returns:
      {
        "MASCOT": {
            "income": {"gross": 14753.37, "isaac_share": 7376.69},
            "deductions": [
                {"ato_label": "Body corporate fees", "gross": 8404.55,
                 "factor": 0.5, "isaac_share": 4202.28, "n_txns": 4},
                ...
            ],
            "total_deductions_isaac": 22871.0,
            "net": -15494.31,
        }
      }
    """
    fy_start = f"{fy - 1}-07-01"
    fy_end = f"{fy}-06-30"

    results: dict[str, dict] = {}

    for rental in tax_config.get("rental_properties", []):
        name = rental["name"]
        income_cat = rental.get("income_category")
        ownership_f = rental.get("ownership_pct", 100) / 100.0

        # --- Income ---
        income_gross = 0.0
        if income_cat:
            row = conn.execute(
                """SELECT COALESCE(SUM(t.amount), 0) AS gross
                FROM transactions t
                JOIN categories c ON c.id = t.category_id
                WHERE c.name = ? AND t.date >= ? AND t.date <= ?
                  AND t.is_transfer = 0""",
                (income_cat, fy_start, fy_end),
            ).fetchone()
            income_gross = float(row[0] or 0.0)

        # --- Deductions per mapping ---
        deductions = []
        total_isaac_deductions = 0.0
        for mapping in rental.get("expense_mapping", []):
            ato_label = mapping["ato_label"]
            apply_modes = mapping.get("apply", ["ownership"])
            factor = _factor(rental, apply_modes, fy)

            cat = mapping.get("category")
            tag = mapping.get("tag")

            if cat:
                rows = conn.execute(
                    """SELECT COALESCE(SUM(t.amount), 0) AS gross, COUNT(*) AS n
                    FROM transactions t
                    JOIN categories c ON c.id = t.category_id
                    WHERE c.name = ? AND t.date >= ? AND t.date <= ?
                      AND t.is_transfer = 0""",
                    (cat, fy_start, fy_end),
                ).fetchall()
            elif tag:
                rows = conn.execute(
                    """SELECT COALESCE(SUM(t.amount), 0) AS gross, COUNT(*) AS n
                    FROM transactions t
                    JOIN transaction_tags tt ON tt.transaction_id = t.id
                    WHERE tt.tag = ? AND t.date >= ? AND t.date <= ?
                      AND t.is_transfer = 0""",
                    (tag, fy_start, fy_end),
                ).fetchall()
            else:
                continue

            gross = float(rows[0]["gross"] if rows else 0.0)
            n = int(rows[0]["n"] if rows else 0)
            if gross == 0:
                continue

            # gross from SQL is negative for expenses — take absolute for deduction display
            gross_abs = abs(gross)
            isaac_share = round(gross_abs * factor, 2)
            total_isaac_deductions += isaac_share

            deductions.append({
                "ato_label": ato_label,
                "gross": round(gross_abs, 2),
                "factor": round(factor, 4),
                "apply": list(apply_modes),
                "isaac_share": isaac_share,
                "n_txns": n,
            })

        # --- Depreciation (rental capital allowances) ---
        depreciation = 0.0
        for sched in tax_config.get("depreciation_schedules", []):
            if sched.get("type") == "rental" and sched.get("property") == name:
                for item in sched.get("items", []):
                    if item.get("fy") == fy:
                        depreciation += float(item["amount"])
        if depreciation:
            total_isaac_deductions += depreciation
            deductions.append({
                "ato_label": "Depreciation (capital allowances)",
                "gross": round(depreciation, 2),
                "factor": 1.0,
                "apply": ["depreciation"],
                "isaac_share": round(depreciation, 2),
                "n_txns": 0,
            })

        results[name] = {
            "income": {
                "gross": round(income_gross, 2),
                "factor": round(ownership_f, 4),
                "isaac_share": round(income_gross * ownership_f, 2),
            },
            "deductions": deductions,
            "total_deductions_isaac": round(total_isaac_deductions, 2),
            "net": round(income_gross * ownership_f - total_isaac_deductions, 2),
        }

    return results


def format_summary(results: dict) -> str:
    """Human-readable formatter for the CLI."""
    out = []
    for name, data in results.items():
        out.append(f"\n=== Rental: {name} ===")
        inc = data["income"]
        out.append(f"  Income (gross):     ${inc['gross']:>12,.2f}")
        out.append(f"    × ownership ({inc['factor']:.2%}) → Isaac: ${inc['isaac_share']:>10,.2f}")
        out.append(f"\n  Deductions:")
        out.append(f"  {'ATO label':<35} {'gross':>11} {'factor':>8} {'apply':<25} {'Isaac':>10}")
        for d in data["deductions"]:
            apply_str = "+".join(d["apply"]) if d["apply"] else "100%"
            out.append(
                f"  {d['ato_label']:<35} {d['gross']:>11,.2f} {d['factor']:>8.4f} "
                f"{apply_str:<25} {d['isaac_share']:>10,.2f}"
            )
        out.append(f"\n  Total Isaac deductions: ${data['total_deductions_isaac']:>12,.2f}")
        out.append(f"  NET rental position:    ${data['net']:>12,.2f}")
    return "\n".join(out)
