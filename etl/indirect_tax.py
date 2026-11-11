"""Taxes and government charges paid through spending, not through payroll.

Income tax and the Medicare levy come off a payslip; most other tax is paid at
the till. Little of it is printed on a bank statement, so most lines here are
ESTIMATES worked out from what was spent, each carrying the basis it was
worked out on. A few are actual amounts paid straight to a government body
(council rates, vehicle registration) or a road operator (tolls), and are
labelled as such.

Rates are Australian (New South Wales for state duties) and can be overridden
in config/tax.yaml under `indirect_taxes:` -- see DEFAULTS.
"""
import sqlite3
from dataclasses import asdict, dataclass

DEFAULTS = {
    # Share of each category's spending that carries GST. Basic food, health,
    # education, financial supplies, residential rent and strata levies, water
    # and council rates are GST-free or input-taxed; most other purchases carry
    # 10%, i.e. 1/11 of the price paid. Unknown categories assume half.
    "gst_share": {
        "Eating Out": 1.0, "Coffee": 1.0, "Shopping": 1.0, "Clothing": 1.0,
        "Electronics": 1.0, "Home & Garden": 1.0, "Entertainment": 1.0,
        "Fitness": 1.0, "Subscriptions": 1.0, "Fuel": 1.0, "Tolls": 1.0,
        "Public Transport": 1.0, "Rental: Maintenance": 1.0, "Rental: Other": 1.0,
        "Business: Equipment": 1.0, "Business: Software & Subscriptions": 1.0,
        "Business: Hosting & Infrastructure": 1.0, "Business: Advertising & Marketing": 1.0,
        "Business: Other": 1.0,
        "Car": 0.8,          # repairs and parts carry GST; registration does not
        "Utilities": 1.0,    # electricity, gas, phone, internet (water removed below)
        "Insurance": 1.0,    # general insurance (health insurance removed below)
        "Travel": 0.5,       # domestic travel carries GST, international does not
        "Groceries": 0.35,   # most supermarket food is GST-free
        "Health": 0.2,       # medical services are GST-free; some pharmacy items are not
        "Uncategorized": 0.5,
        "Cash Withdrawal": 0.5,
    },
    # Fuel excise is charged per litre; litres are estimated from spend.
    "fuel_excise_cents_per_litre": 52.6,
    "fuel_price_per_litre": 2.00,
    # Alcohol excise / wine equalisation tax as a rough share of the price paid.
    "alcohol_tax_share": 0.25,
    # NSW duty on general insurance premiums (car, home); health is exempt.
    "insurance_duty_rate": 0.09,
}


@dataclass
class Line:
    name: str
    amount: float      # paid, as a positive number
    basis: str         # how it was worked out, in plain words
    estimate: bool     # False: an actual amount from your transactions
    kind: str          # "tax" (to government) or "charge" (e.g. tolls, to a road operator)


def _spend_by(conn, sql: str, params: tuple) -> dict[str, float]:
    return {k: -(v or 0.0) for k, v in conn.execute(sql, params)}


def estimate(conn: sqlite3.Connection, date_from: str, date_to: str, config: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    cfg["gst_share"] = {**DEFAULTS["gst_share"], **((config or {}).get("gst_share") or {})}

    # Same definition as /api/summary/period: transfers out, refunds net within
    # their category, loans included.
    by_cat = _spend_by(conn, """
        SELECT COALESCE(c.name, 'Uncategorized'), SUM(t.amount)
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.date BETWEEN ? AND ? AND t.is_transfer = 0
          AND COALESCE(c.is_income, 0) = 0 AND COALESCE(c.name, '') <> 'Transfers'
        GROUP BY 1""", (date_from, date_to))
    by_tag = _spend_by(conn, """
        SELECT g.tag, SUM(t.amount)
        FROM transactions t JOIN transaction_tags g ON g.transaction_id = t.id
        WHERE t.date BETWEEN ? AND ? AND t.is_transfer = 0 AND t.amount < 0
        GROUP BY g.tag""", (date_from, date_to))
    tolls = -(conn.execute("""
        SELECT SUM(t.amount) FROM transactions t
        WHERE t.date BETWEEN ? AND ? AND t.is_transfer = 0 AND t.amount < 0
          AND (UPPER(t.description) LIKE '%LINKT%' OR UPPER(t.description) LIKE '%E-TOLL%'
               OR UPPER(t.description) LIKE '%ETOLL%' OR UPPER(t.description) LIKE '%TRANSURBAN%')
        """, (date_from, date_to)).fetchone()[0] or 0.0)

    lines: list[Line] = []

    # GST: 1/11 of the GST-bearing share of each category, net of the parts
    # known to be GST-free (water inside Utilities, health cover inside
    # Insurance, registration inside Car).
    taxable = 0.0
    for cat, spent in by_cat.items():
        if spent <= 0:
            continue
        share = cfg["gst_share"].get(cat, 0.0)
        exempt = 0.0
        if cat == "Utilities":
            exempt = by_tag.get("water", 0.0)
        elif cat == "Insurance":
            exempt = by_tag.get("health-insurance", 0.0)
        elif cat == "Car":
            exempt = by_tag.get("rego", 0.0)
        taxable += max(spent - exempt, 0.0) * share
    if taxable:
        lines.append(Line("GST", taxable / 11, (
            f"1/11 of the ${taxable:,.0f} of your spending estimated to carry GST. Basic food, "
            "health, rent, strata levies, water, council rates and bank charges are GST-free."),
            True, "tax"))

    fuel = by_cat.get("Fuel", 0.0)
    if fuel > 0:
        litres = fuel / cfg["fuel_price_per_litre"]
        lines.append(Line("Fuel excise", litres * cfg["fuel_excise_cents_per_litre"] / 100, (
            f"About {litres:,.0f} litres (${fuel:,.0f} at ${cfg['fuel_price_per_litre']:.2f}/L) "
            f"× {cfg['fuel_excise_cents_per_litre']:.1f}c excise per litre."), True, "tax"))

    alcohol = by_tag.get("alcohol", 0.0)
    if alcohol > 0:
        lines.append(Line("Alcohol excise and wine tax", alcohol * cfg["alcohol_tax_share"], (
            f"About {cfg['alcohol_tax_share']:.0%} of the ${alcohol:,.0f} spent on alcohol. "
            "The real share varies a lot by drink."), True, "tax"))

    insured = by_tag.get("car-insurance", 0.0) + by_tag.get("home-insurance", 0.0)
    if insured > 0:
        # Premium paid = base × (1 + duty) × 1.1 (GST), so duty = paid × duty / ((1 + duty) × 1.1).
        rate = cfg["insurance_duty_rate"]
        lines.append(Line("Insurance stamp duty", insured * rate / ((1 + rate) * 1.1), (
            f"NSW duty of {rate:.0%} inside ${insured:,.0f} of car and home insurance premiums."),
            True, "tax"))

    rates = by_tag.get("council-rates", 0.0)
    if rates > 0:
        lines.append(Line("Council rates", rates, "Paid to the council, from your transactions. "
                          "Shared bills count at the full amount you paid.", False, "tax"))

    rego = by_tag.get("rego", 0.0)
    if rego > 0:
        lines.append(Line("Vehicle registration", rego, "Paid to Service NSW, from your transactions. "
                          "Includes the compulsory third-party insurance bundled with it.", False, "tax"))

    if tolls > 0:
        lines.append(Line("Road tolls", tolls, "From your Linkt and e-toll charges. A charge, "
                          "mostly to private road operators, rather than a tax.", False, "charge"))

    lines.sort(key=lambda l: -l.amount)
    total_tax = sum(l.amount for l in lines if l.kind == "tax")
    total = sum(l.amount for l in lines)
    return {
        "lines": [{**asdict(l), "amount": round(l.amount, 2)} for l in lines],
        "total_tax": round(total_tax, 2),
        "total": round(total, 2),
    }
